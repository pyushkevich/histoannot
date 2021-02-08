#
#   PICSL Histology Annotator
#   Copyright (C) 2019 Paul A. Yushkevich
#
#   This program is free software: you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation, either version 3 of the License, or
#   (at your option) any later version.
#
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
import uuid

from flask import(
    Blueprint, flash, g, redirect, render_template, request, url_for, make_response,
    current_app, send_from_directory, session, send_file
)
from werkzeug.exceptions import abort

from histoannot.auth import login_required, project_access_required, task_access_required
from histoannot.db import get_db
from histoannot.project_ref import ProjectRef
from histoannot.slideref import SlideRef, get_slide_ref
from histoannot.project_cli import get_task_data, update_edit_meta, create_edit_meta
from histoannot.delegate import find_delegate_for_slide
from histoannot.dzi import get_affine_matrix, get_slide_raw_dims, forward_to_worker, get_random_patch, dzi_preload, dzi_job_status
from io import BytesIO

import os
import json
import jsonschema
import numpy as np
import click
from flask.cli import with_appcontext
import pandas
import math
import svgwrite
import sys
import urllib
import urllib2


bp = Blueprint('slide', __name__)

# Get the where clause for slide selection corresponding to a task
def get_task_slide_where_clause(task):
    if 'stains' in task and len(task['stains']) > 0:
        where = ('S.stain COLLATE NOCASE IN (%s)' % 
                 ','.join('?'*len(task['stains'])), 
                 tuple(task['stains']))
    else:
        where = ('S.id IS NOT NULL', ())

    return where


@bp.route('/')
@login_required
def index():
    # Render the entry page
    return render_template('slide/projects_tasks.html')



# The index
@bp.route('/project/<project>')
@project_access_required
def project_detail(project):

    # Render the entry page
    return render_template('slide/projects_tasks.html', project_name=project)



# Project listing for the current user
@bp.route('/api/projects')
@login_required
def project_listing():

    # Get the current user
    user = session['user_id']
    db = get_db()

    # Result array
    listing = []

    # List all projects for the current user
    if session.get('user_is_site_admin', False) is not True:
        rc = db.execute('SELECT P.* FROM project P '
                        'LEFT JOIN project_access PA ON PA.project=P.id '
                        'WHERE PA.user = ?'
                        'ORDER BY P.disp_name', (user,))
    else:
        rc = db.execute('SELECT P.* FROM project P ORDER BY P.disp_name ')

    for row in rc.fetchall():

        # Get the statistics for the project
        stat = db.execute(
            'SELECT COUNT (id) as nslides, '
            '       COUNT(DISTINCT block_id) as nblocks, '
            '       COUNT(DISTINCT specimen_name) as nspecimens '
            'FROM slide_info WHERE project=?', (row['id'],)).fetchone()

        # Create a dictionary
        listing.append({'id':row['id'],'disp_name':row['disp_name'],'desc':row['desc'],
                        'nslides':stat['nslides'], 'nblocks':stat['nblocks'], 'nspecimens':stat['nspecimens']})

    # Generate a bunch of json
    return json.dumps([x for x in listing])


@bp.route('/api/project/<project>/tasks')
@project_access_required
def task_listing(project):
    db=get_db()

    # List the available tasks (TODO: check user access to task)
    rc = db.execute('SELECT * FROM task_info WHERE project=?', (project,))

    listing = []
    for row in rc.fetchall():

        # Parse the json
        task = json.loads(row['json'])

        # Generate the where clause
        where = get_task_slide_where_clause(task)
        print(where)

        # Get the subset of stains to which the task applies
        stat = db.execute(
            'SELECT COUNT(S.id) as nslides, '
            '       COUNT(DISTINCT block_id) as nblocks, '
            '       COUNT(DISTINCT specimen_name) as nspecimens '
            'FROM slide_info S  '
            'WHERE project=? AND %s ' % (where[0],),
            (project,) + where[1]).fetchone()

        # Create a dict
        d = { 'id' : row['id'], 'name': task['name'], 'desc': task['desc'] }
        for key in ('nspecimens', 'nblocks', 'nslides'):
            d[key] = stat[key]

        listing.append(d)

    return json.dumps([x for x in listing])



# Specimen listing for a task
@bp.route('/api/task/<int:task_id>/specimens')
@task_access_required
def task_specimen_listing(task_id):
    db = get_db()

    # Get the current task data
    project,task = get_task_data(task_id)

    # Generate the where clause
    where = get_task_slide_where_clause(task)

    # List all the blocks that meet requirements for the current task
    if task['mode'] == 'annot':

        # Join with the annotations table
        blocks = db.execute(
            'SELECT B.specimen_name, COUNT(DISTINCT B.id) as nblocks, COUNT (S.id) as nslides, '
            '       COUNT(A.slide_id) as nannot '
            'FROM slide S LEFT JOIN block_info B on S.block_id = B.id '
            '             LEFT JOIN annot A on A.slide_id = S.id AND A.task_id = ? '
            '                                  AND A.n_paths+A.n_markers > 0 '
            'WHERE B.project=? AND %s '
            'GROUP BY specimen_name ORDER BY specimen_name' % where[0], 
            (task_id,project) + where[1]).fetchall()

    elif task['mode'] == 'dltrain':

        # Join with the annotations table
        blocks = db.execute(
            'SELECT B.specimen_name, COUNT(DISTINCT B.id) as nblocks, COUNT (DISTINCT S.id) as nslides, '
            '                        COUNT(T.id) as nsamples '
            'FROM slide S LEFT JOIN block_info B on S.block_id = B.id '
            '             LEFT JOIN training_sample T on T.slide = S.id AND T.task = ?'
            'WHERE B.project=? AND %s '
            'GROUP BY specimen_name ORDER BY specimen_name' % where[0], 
            (task_id,project) + where[1]).fetchall()

    elif task['mode'] == 'browse':
        blocks = db.execute(
            'SELECT B.specimen_name, COUNT(DISTINCT B.id) as nblocks, COUNT (S.id) as nslides '
            'FROM slide S LEFT JOIN block_info B on S.block_id = B.id '
            'WHERE B.project=? AND %s '
            'GROUP BY specimen_name ORDER BY specimen_name' % where[0], 
            (project,) + where[1]).fetchall()

    return json.dumps([dict(row) for row in blocks])

# Task detail
@bp.route('/task/<int:task_id>')
@task_access_required
def task_detail(task_id):

    # Get the current task data
    project,task = get_task_data(task_id)
    pr = ProjectRef(project)
    return render_template('slide/task_detail.html',
                           project=project, project_name=pr.disp_name,
                           task=task, task_id=task_id, specimen_name=None, block_name=None)

# Specimen detail (same template as the task detail, but points to a specimen
@bp.route('/task/<int:task_id>/specimen/<specimen_name>')
@task_access_required
def specimen_detail_by_name(task_id, specimen_name):

    # Get the current task data
    project,task = get_task_data(task_id)
    pr = ProjectRef(project)
    return render_template('slide/task_detail.html',
                           project=project, project_name=pr.disp_name, task=task, task_id=task_id,
                           specimen_name=specimen_name, block_name=None)

# Block detail (same template as the task detail, but points to a block
@bp.route('/task/<int:task_id>/specimen/<specimen_name>/block/<block_name>')
@task_access_required
def block_detail_by_name(task_id, specimen_name, block_name):

    # Get the current task data
    project,task = get_task_data(task_id)
    pr = ProjectRef(project)
    return render_template('slide/task_detail.html',
                           project=project, project_name=pr.disp_name, task=task, task_id=task_id,
                           specimen_name=specimen_name, block_name=block_name)

# Task detail
@bp.route('/api/task/<int:task_id>/specimen/<specimen_name>/blocks')
@task_access_required
def specimen_block_listing(task_id, specimen_name):

    db = get_db()

    # Get the current task data
    project,task = get_task_data(task_id)

    # Generate the where clause
    where = get_task_slide_where_clause(task)

    # List all the blocks that meet requirements for the current task
    if task['mode'] == 'annot':

        # Join with the annotations table
        blocks = db.execute(
            'SELECT B.*, COUNT (S.id) as nslides, COUNT(A.slide_id) as nannot '
            'FROM slide S LEFT JOIN block_info B on S.block_id = B.id '
            '             LEFT JOIN annot A on A.slide_id = S.id AND A.task_id = ? '
            '                                  AND A.n_paths+A.n_markers > 0 '
            'WHERE project=? AND specimen_name=? AND %s '
            'GROUP BY B.id ORDER BY block_name' % where[0], 
            (task_id, project, specimen_name) + where[1]).fetchall()

    elif task['mode'] == 'dltrain':

        # Join with the annotations table
        blocks = db.execute(
            'SELECT B.*, COUNT (DISTINCT S.id) as nslides, COUNT(T.id) as nsamples '
            'FROM slide S LEFT JOIN block_info B on S.block_id = B.id '
            '             LEFT JOIN training_sample T on T.slide = S.id AND T.task = ?'
            'WHERE project=? AND specimen_name=? AND %s '
            'GROUP BY B.id ORDER BY block_name' % where[0], 
            (task_id, project, specimen_name) + where[1]).fetchall()

    elif task['mode'] == 'browse':
        blocks = db.execute(
            'SELECT B.*, COUNT (S.id) as nslides '
            'FROM slide S LEFT JOIN block_info B on S.block_id = B.id '
            'WHERE project=? AND specimen_name=? AND %s '
            'GROUP BY B.id ORDER BY block_name' % where[0], 
            (project, specimen_name) + where[1]).fetchall()

    return json.dumps([dict(row) for row in blocks])


# Generate a db view for slides specific to a mode (annot, dltrain)
def make_slide_dbview(task_id, view_name):

    db=get_db()

    # This call guarantees that there is no database spoofing
    project,task = get_task_data(task_id)

    # Create a where clause
    wcl = ''
    if 'stains' in task:
        col = ','.join('"{0}"'.format(x) for x in task['stains'])
        wcl = "AND S.stain COLLATE NOCASE IN (%s)" % col

    if task['mode'] == 'annot':

        db.execute("""CREATE TEMP VIEW %s AS
                      SELECT S.*, 
                      IFNULL(SUM(A.n_paths),0) as n_paths, 
                      IFNULL(SUM(A.n_markers),0) as n_markers
                      FROM slide_info S LEFT JOIN annot A on A.slide_id = S.id AND A.task_id=%d
                      WHERE S.project='%s' %s
                      GROUP BY S.id
                      ORDER BY specimen_name, block_name, section, slide""" % (view_name,task_id,project,wcl))

    elif task['mode'] == 'dltrain':

        db.execute("""CREATE TEMP VIEW %s AS
                      SELECT S.*, COUNT(T.id) as n_samples
                      FROM slide_info S LEFT JOIN training_sample T on T.slide = S.id AND T.task=%d
                      WHERE S.project='%s' %s
                      GROUP BY S.id
                      ORDER BY specimen_name, block_name, section, slide""" % (view_name,task_id,project,wcl))

    elif task['mode'] == 'browse':

        db.execute("""CREATE TEMP VIEW %s AS
                      SELECT S.*
                      FROM slide_info S
                      WHERE S.project='%s' %s
                      ORDER BY specimen_name, block_name, section, slide""" % (view_name,project,wcl))



# The block detail listing
@bp.route('/api/task/<int:task_id>/specimen/<specimen_name>/block/<block_name>/slides', methods=('GET', 'POST'))
@task_access_required
def block_slide_listing(task_id, specimen_name, block_name):
    db = get_db()

    # Get the current task data
    project,task = get_task_data(task_id)

    # Get the block descriptor
    block = db.execute('SELECT * FROM block_info WHERE specimen_name=? AND block_name=? AND project=?',
            (specimen_name,block_name,project)).fetchone()

    if block is None:
        return json.dumps([])

    block_id = block['id']

    # Generate the where clause
    where = get_task_slide_where_clause(task)

    # List all the blocks that meet requirements for the current task
    if task['mode'] == 'annot':

        # Join with the annotations table
        slides = db.execute(
            'SELECT S.*, '
            '       IFNULL(SUM(A.n_paths),0) as n_paths, '
            '       IFNULL(SUM(A.n_markers),0) as n_markers, '
            '       IFNULL(SUM(A.n_paths),0) + IFNULL(SUM(A.n_markers),0) as n_annot '
            'FROM slide S LEFT JOIN annot A on A.slide_id = S.id AND A.task_id = ? '
            'WHERE %s AND S.block_id = ?'
            'GROUP BY S.id ORDER BY section, slide ASC' % where[0], 
            (task_id,) + where[1] + (block_id,)).fetchall()

    elif task['mode'] == 'dltrain':

        # Join with the training samples table
        slides = db.execute(
            'SELECT S.*, COUNT(T.id) as n_samples '
            'FROM slide S LEFT JOIN training_sample T on T.slide = S.id AND T.task = ? '
            'WHERE %s AND S.block_id = ?'
            'GROUP BY S.id ORDER BY section, slide ASC' % where[0], 
            (task_id,) + where[1] + (block_id,)).fetchall()

    elif task['mode'] == 'browse':

        slides = db.execute(
            'SELECT S.* '
            'FROM slide S LEFT JOIN block_info B on S.block_id = B.id '
            'WHERE %s AND S.block_id = ? '
            'GROUP BY S.id ORDER BY section, slide ASC' % where[0],
            where[1] + (block_id,)).fetchall()

    return json.dumps([dict(row) for row in slides])


# Get all the data needed for slide view/annotation/training
def get_slide_info(task_id, slide_id):
    db = get_db()

    # Get the info on the current slide
    slide_info = db.execute('SELECT * from slide_info WHERE id = ?', (slide_id,)).fetchone()

    # Get the slide info
    block_id = slide_info['block_id']
    section = slide_info['section']
    slideno = slide_info['slide']
    stain = slide_info['stain']

    # Get the task-specific where clause
    project,task = get_task_data(task_id)

    # Create a view for this task
    make_slide_dbview(task_id, 'v_full')

    # Get a list of slides/stains for this section (for drop-down menu)
    rc_slide = db.execute(
        'SELECT id, slide, stain FROM v_full '
        'WHERE block_id=? AND section=? '
        'ORDER BY slide', (block_id, section))

    stain_list = [dict(row) for row in rc_slide.fetchall()]

    # Get the corresponding slide in the previous section. A corresponding slide
    # has the same stain and the closest slide to the current section. If the
    # same stain is not available, then just the closest slide.

    # Find the previous section number
    prev_sec = db.execute('SELECT section FROM v_full '
                          'WHERE block_id=? AND section<? '
                          'ORDER BY section DESC limit 1',
                          (block_id, section)).fetchone()

    # Find the closest slide in the section
    if prev_sec is not None:
        prev_slide = db.execute('SELECT *,(stain<>?)*1000+abs(slide-?) AS X '
                                'FROM v_full WHERE block_id=? AND section=? '
                                'ORDER BY X LIMIT 1',
                                (stain, slideno, block_id, prev_sec['section'])).fetchone()
    else:
        prev_slide = None

    # Find the previous section number
    next_sec = db.execute('SELECT section FROM v_full '
                          'WHERE block_id=? AND section>? '
                          'ORDER BY section ASC limit 1',
                          (block_id, section)).fetchone()

    # Find the closest slide in the section
    if next_sec is not None:
        next_slide = db.execute('SELECT *,(stain<>?)*1000+abs(slide-?) AS X '
                                'FROM v_full WHERE block_id=? AND section=? '
                                'ORDER BY X LIMIT 1',
                                (stain, slideno, block_id, next_sec['section'])).fetchone()
    else:
        next_slide = None

    # Load the user preferences for this slide
    rc = db.execute('SELECT json FROM user_task_slide_preferences '
               '            WHERE user=? AND task_id=? AND slide=?',
               (g.user['id'], task_id, slide_id)).fetchone()

    user_prefs = json.loads(rc['json']) if rc is not None else {}

    return slide_info, prev_slide, next_slide, stain_list, user_prefs


# Determine if the deep learning training task uses a fixed size box and
# return the box size if so, None otherwise
def get_dltrain_fixed_box_size(task):

    if task['mode'] != 'dltrain' or 'dltrain' not in task:
        return None

    min_size = task['dltrain'].get('min-size', 0)
    max_size = task['dltrain'].get('max-size', 0)

    if (min_size == 0 and max_size == 0) or min_size != max_size:
        return None

    return min_size


# The slide view
@bp.route('/task/<int:task_id>/slide/<int:slide_id>/view/<resolution>/<affine_mode>', methods=('GET', 'POST'))
@task_access_required
def slide_view(task_id, slide_id, resolution, affine_mode):

    # Get the current task data
    project,task = get_task_data(task_id)

    # Get the next/previous slides for this task
    si, prev_slide, next_slide, stain_list, user_prefs = get_slide_info(task_id, slide_id)

    # Check that the affine mode and resolution requested are available
    pr = ProjectRef(project)
    sr = SlideRef(pr, si['specimen_name'], si['block_name'], si['slide_name'], si['slide_ext'])
    have_affine = sr.resource_exists('affine', True) or sr.resource_exists('affine', False)
    have_x16 = sr.resource_exists('x16', True) or sr.resource_exists('x16', False)

    # If one is missing, we need a redirect
    rd_affine_mode = affine_mode if have_affine else 'raw'
    rd_resolution = resolution if have_x16 else 'raw'

    if (affine_mode == 'affine' and not have_affine) or (resolution == 'x16' and not have_x16):
        return redirect(url_for('slide.slide_view', 
            task_id=task_id, slide_id=slide_id,
            resolution=rd_resolution, affine_mode=rd_affine_mode))

    # Get additional project info
    pr = ProjectRef(project)

    # Form the URL templates for preloading and actual dzi access, so that in JS we
    # can just do a quick substitution
    url_ctx = {
            'project':project,
            'specimen':si['specimen_name'],
            'block':si['block_name'],
            'slide_name':si['slide_name'],
            'slide_ext':si['slide_ext'],
            'mode':affine_mode,
            'resource':'XXXXX'}

    url_tmpl_preload = url_for('dzi.dzi_preload_endpoint', **url_ctx)
    url_tmpl_dzi = url_for('dzi.dzi', **url_ctx)
    url_tmpl_download = url_for('dzi.dzi_download', **url_ctx)

    # Build a dictionary to call
    context = {
            'slide_id': slide_id, 
            'slide_info': si,
            'next_slide':next_slide, 
            'prev_slide':prev_slide,
            'stain_list':stain_list,
            'affine_mode':affine_mode,
            'have_affine':have_affine,
            'have_x16':have_x16,
            'resolution':resolution,
            'seg_mode':task['mode'], 
            'task_id': task_id, 
            'project':si['project'],
            'project_name':pr.disp_name,
            'block_id': si['block_id'],
            'url_tmpl_preload': url_tmpl_preload,
            'url_tmpl_dzi': url_tmpl_dzi,
            'url_tmpl_download': url_tmpl_download,
            'task':task,
            'fixed_box_size' : get_dltrain_fixed_box_size(task),
            'user_prefs': user_prefs}

    # Add optional fields to context
    for field in ('sample_id', 'sample_cx', 'sample_cy'):
        if field in request.form:
            context[field] = request.form[field]

    # Render the template
    return render_template('slide/slide_view.html', **context)


# Get an annotation filename for slide
def get_annot_json_file(task_id, slide_id):
    
    # Get the slide details
    db = get_db()
    si = db.execute('SELECT * FROM slide_info WHERE id = ?', (slide_id,)).fetchone()

    # Generate a file
    json_filename = "annot_%s_%s_%s_%s_%02d_%02d.json" % (
        si['project'], si['specimen_name'], si['block_name'], si['stain'], si['section'], si['slide'])

    # Create a directory locally
    json_dir = os.path.join(current_app.instance_path, 'annot', si['project'], 'task_%03d' % task_id)
    if not os.path.exists(json_dir):
        os.makedirs(json_dir)

    # Get the filename
    return os.path.join(json_dir, json_filename)


# Get the affine matrix for a slide
def get_affine_matrix_by_slideid(slide_id, mode, resolution='raw'):

    sr = get_slide_ref(slide_id)
    return get_affine_matrix(sr, mode, resolution)


# Transform a paper.js project using an affine matrix M. Also returns
# statistics on the number of elements
def transform_annot(data, M):
    # Extract matrix components
    (A,b) = (M[0:2,0:2], M[0:2,2])

    # Count the children
    n_paths = 0
    n_markers = 0
    if 'children' in data[0][1]:
        for x in data[0][1]['children']:

            # Handle paths
            if x[0] == 'Path':
                n_paths = n_paths + 1
                for seg in x[1]['segments']:
                    # Transform the segment (point and handles)
                    for k in range(len(seg)):
                        try:
                            z = np.dot(A,seg[k]) + (b if k==0 else 0)
                            seg[k] = z.tolist()
                        except:
                            print("Error in transform_annot for segment ", seg, " index ", k);

            # Increment the counters
            elif x[0] == 'PointText':
                x[1]['matrix'][4:6] = (np.dot(A,x[1]['matrix'][4:6])+b).tolist()
                n_markers = n_markers + 1

    return (data, {'n_paths' : n_paths, 'n_markers' : n_markers})

# Interpolate curve segment
def curve_interp(seg_1, seg_2, npts):

    x0 = seg_1[0][0]
    x1 = seg_1[0][0] + seg_1[2][0]
    x2 = seg_2[0][0] + seg_2[1][0]
    x3 = seg_2[0][0]

    y0 = seg_1[0][1]
    y1 = seg_1[0][1] + seg_1[2][1]
    y2 = seg_2[0][1] + seg_2[1][1]
    y3 = seg_2[0][1]

    res = [[x0,y0]]

    for p in range(1, npts+1):
        t = (p + 0.5) / (npts+1)
        u = 1.0 - t

        # Code from paper.js
        x4 = u * x0 + t * x1; y4 = u * y0 + t * y1
        x5 = u * x1 + t * x2; y5 = u * y1 + t * y2
        x6 = u * x2 + t * x3; y6 = u * y2 + t * y3
        x7 = u * x4 + t * x5; y7 = u * y4 + t * y5
        x8 = u * x5 + t * x6; y8 = u * y5 + t * y6
        x9 = u * x7 + t * x8; y9 = u * y7 + t * y8

        res.append([x9,y9])

    res.append([x3,y3])

    return res


# Compute curve length
def curve_length(segs):
    L=0
    for p in range(1,len(segs)):
        L = L + ((segs[p][0]-segs[p-1][0])**2 + (segs[p][1]-segs[p-1][1])**2) ** 0.5
    return L


# Regularly sample from path segments with given maximum output segment length
# The return value is a list of 2D numpy arrays
def annot_sample_path_curves(data, max_len):

    # Output data structure
    C=[]

    # Iterate over the paths
    if 'children' in data[0][1]:
        for x in data[0][1]['children']:

            # Handle paths
            if x[0] == 'Path':

                # Create a new output list to store the samples
                ci=[]
                ns = len(x[1]['segments'])
                for i in range(0,ns-1):
                    seg_1 = x[1]['segments'][i]
                    seg_2 = x[1]['segments'][i+1]

                    # Estimate the length of the curve segment
                    pts = curve_interp(seg_1, seg_2, 100)

                    # Determine the actual number of samples we will need
                    nsam = int(curve_length(pts) / max_len)

                    # Get the samples with desired sampling
                    ptsf = curve_interp(seg_1, seg_2, nsam)

                    # No duplicates
                    if i > 0:
                        ptsf = ptsf[1:len(ptsf)]

                    # Append these samples
                    ci = ci + ptsf

                C.append(ci)

    return C



# Get all the paths in an annotation in a flattened dict 
def annot_get_path_segments(data):

    # numpy array to store segments


    if 'children' in data[0][1]:
        for x in data[0][1]['children']:

            # Handle paths
            if x[0] == 'Path':
                for seg in x[1]['segments']:
                    # Transform the segment (point and handles)
                    for k in range(len(seg)):
                        print((k,seg[k]))


# Update annotation for a slide, the annotation assumed to be already transformed
# into raw slide space. Parameter annot is a dict loaded from JSON
def _do_update_annot(task_id, slide_id, annot, stats):

    # See if an annotation already exists
    db = get_db()
    a_row = db.execute('SELECT * FROM annot WHERE slide_id=? AND task_id=?',
                       (slide_id, task_id)).fetchone()

    if a_row is not None:

        # Update the timestamp
        update_edit_meta(a_row['meta_id'])
        
        # Update the row
        db.execute(
            'UPDATE annot SET json=?, n_paths=?, n_markers=? '
            'WHERE slide_id=? AND task_id=?', 
            (json.dumps(annot), stats['n_paths'], stats['n_markers'], slide_id, task_id))

    else:

        # Create a new timestamp
        meta_id = create_edit_meta()

        # Insert a new row
        db.execute(
            'INSERT INTO annot(json, meta_id, n_paths, n_markers, slide_id, task_id) '
            'VALUES (?,?,?,?,?,?)', 
            (json.dumps(annot), meta_id, stats['n_paths'], stats['n_markers'], slide_id, task_id))

    # Commit
    db.commit()

    # Also generate a file, just for backup purposes
    json_filename = get_annot_json_file(task_id, slide_id)

    with open(json_filename, 'w') as outfile:  
        json.dump(annot, outfile)


# Schema for user preferences json
user_preferences_schema = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "rotation": {"type": "number"},
        "flip": {"type": "boolean"}
    }
}


# Receive user preferences for the slide, such as preferred rotation and flip
@bp.route('/api/task/<int:task_id>/slide/<int:slide_id>/user_preferences/set', methods=('POST',))
@task_access_required
def set_slide_user_preferences(task_id, slide_id):

    # Get the json and validate
    data = json.loads(request.get_data())
    try:
        jsonschema.validate(data, user_preferences_schema)
    except jsonschema.ValidationError:
        abort("Invalid JSON")

    # Store the data for the user.
    db = get_db()
    db.execute('REPLACE INTO user_task_slide_preferences (user, task_id, slide, json) VALUES (?,?,?,?)',
               (g.user['id'], task_id, slide_id, json.dumps(data)))
    db.commit()

    # Return success
    return json.dumps({'success':True}), 200, {'ContentType':'application/json'}


# Receive updated json for the slide
@bp.route('/task/<int:task_id>/slide/<mode>/<resolution>/<int:slide_id>/annot/set', methods=('POST',))
@task_access_required
def update_annot_json(task_id, mode, resolution, slide_id):

    # Get the raw json
    data = json.loads(request.get_data())

    # Get the affine transform
    M_inv = get_affine_matrix_by_slideid(slide_id, mode, resolution)

    # Transform the data and count items
    (data, stats) = transform_annot(data, M_inv)

    # Update annotation
    _do_update_annot(task_id, slide_id, data, stats)

    return json.dumps({'success':True}), 200, {'ContentType':'application/json'} 


# Send the json for the slide
@bp.route('/task/<int:task_id>/slide/<mode>/<resolution>/<int:slide_id>/annot/get', methods=('GET',))
@task_access_required
def get_annot_json(task_id, mode, resolution, slide_id):

    # Find the annotation in the database
    db = get_db()
    rc = db.execute('SELECT json FROM annot WHERE slide_id=? AND task_id=?',
                    (slide_id, task_id)).fetchone()

    # Get the affine transform
    M = np.linalg.inv(get_affine_matrix_by_slideid(slide_id, mode, resolution))

    # Return the data
    if rc is not None:
        data = json.loads(rc['json'])
        (data,stats) = transform_annot(data, M)
        return json.dumps(data), 200, {'ContentType':'application/json'} 
    else:
        return "", 200, {'ContentType':'application/json'} 


# API to get the timestamp when an annotation was last modified
# TODO: need API keys!
@bp.route('/api/task/<int:task_id>/slide/<int:slide_id>/annot/timestamp', methods=('GET',))
def api_get_annot_timestamp(task_id, slide_id):
    db = get_db()
    rc = db.execute('SELECT M.t_edit FROM annot A '
                    'LEFT JOIN edit_meta M on A.meta_id = M.id '
                    'WHERE A.task_id = ? AND A.slide_id = ?',
                    (task_id, slide_id)).fetchone()
    if rc is not None:
        return json.dumps({'timestamp' : rc['t_edit']}), 200, {'ContentType':'application/json'}
    else:
        return json.dumps({'timestamp' : None}), 200, {'ContentType':'application/json'}


# API to get the timestamp when an annotation was last modified
@bp.route('/api/task/<int:task_id>/slidename/<slide_name>/annot/timestamp', methods=('GET',))
def api_get_annot_timestamp_by_slidename(task_id, slide_name):
    db = get_db()
    rc = db.execute('SELECT S.id FROM annot A '
                    'LEFT JOIN slide S on A.slide_id = S.id '
                    'WHERE A.task_id = ? and S.slide_name = ?',
                    (task_id, slide_name)).fetchone()
    if rc is not None:
        return api_get_annot_timestamp(task_id, rc['id'])
    else:
        return json.dumps({'timestamp': None}), 200, {'ContentType': 'application/json'}


# API to get an SVG file
# TODO: need API keys!
@bp.route('/api/task/<int:task_id>/slide/<int:slide_id>/annot/svg', methods=('GET','POST'))
def api_get_annot_svg(task_id, slide_id):

    strip_width = request.form.get('strip_width', 0)
    stroke_width = request.form.get('stroke_width', 48)
    font_size = request.form.get('font_size', "2000px")
    font_color = request.form.get('font_color', 'black')

    svg = extract_svg(task_id, slide_id, int(stroke_width), int(strip_width), font_size, font_color)
    txt = svg.tostring()
    resp = make_response(txt)

    resp.mimetype = 'image/svg+xml'
    return resp


# API to get an SVG file
# TODO: need API keys!
@bp.route('/api/task/<int:task_id>/slidename/<slide_name>/annot/svg', methods=('GET','POST'))
def api_get_annot_svg_by_slidename(task_id, slide_name):
    db = get_db()
    rc = db.execute('SELECT S.id FROM annot A '
                    'LEFT JOIN slide S on A.slide_id = S.id '
                    'WHERE A.task_id = ? and S.slide_name = ?',
                    (task_id, slide_name)).fetchone()
    if rc is not None:
        return api_get_annot_svg(task_id, rc['id'])
    else:
        abort(404)


class PILBytesIO(BytesIO):
    def fileno(self):
        '''Classic PIL doesn't understand io.UnsupportedOperation.'''
        raise AttributeError('Not supported')


# Serve up thumbnails
# TODO: need API keys!
@bp.route('/slide/<int:id>/thumb', methods=('GET',))
def thumb(id):
    thumb_dir = os.path.join(current_app.instance_path, 'thumb')
    thumb_fn = "thumb%08d.png" % (id,)
    return send_from_directory(thumb_dir, thumb_fn, as_attachment=False)


# Get a random patch from the slide
@bp.route('/api/task/<int:task_id>/slide/<int:slide_id>/random_patch/<int:width>', methods=('GET','POST'))
@task_access_required
def api_get_slide_random_patch(task_id, slide_id, width):
    db = get_db()

    # Find out which machine the slide is currently being served from
    # Get the tiff image from which to sample the region of interest
    sr = get_slide_ref(slide_id)

    # Get the identifiers for the slide
    # TODO: what to do with project here?
    (project, specimen, block, slide_name, slide_ext) = sr.get_id_tuple()

    # Are we de this slide to a different node?
    del_url = find_delegate_for_slide(slide_id)

    # If local, call the method directly
    rawbytes = None
    if del_url is None:
        rawbytes = get_random_patch(project,specimen,block,'raw',slide_name,slide_ext,0,width,'png').data
    else:
        url = '%s/dzi/random_patch/%s/%s/%s/raw/%s.%s/%d/%d.png' % (
                del_url, project, specimen, block, slide_name, slide_ext, 0, width)
        pr = sr.get_project_ref()
        post_data = urllib.urlencode({'project_data': json.dumps(pr.get_dict())})
        rawbytes = urllib2.urlopen(url, post_data).read()

    # Send the patch
    resp = make_response(rawbytes)
    resp.mimetype = 'image/png'
    return resp


# Preload a slide (using task/id)
@bp.route('/api/task/<int:task_id>/slide/<int:slide_id>/preload/<resource>', methods=('GET','POST'))
@task_access_required
def api_slide_preload(task_id, slide_id, resource):
    db = get_db()
    sr = get_slide_ref(slide_id)
    (project, specimen, block, slide_name, slide_ext) = sr.get_id_tuple()

    # Are we de this slide to a different node?
    del_url = find_delegate_for_slide(slide_id)

    # If local, call the method directly
    if del_url is None:
        return dzi_preload(project, specimen, block, resource, slide_name, slide_ext)
    else:
        url = '%s/dzi/preload/%s/%s/%s/%s/%s.%s.dzi' % (
                del_url, project, specimen, block, resource, slide_name, slide_ext)
        pr = sr.get_project_ref()
        post_data = urllib.urlencode({'project_data': json.dumps(pr.get_dict())})
        return urllib2.urlopen(url, post_data).read()


@bp.route('/api/task/<int:task_id>/slide/<int:slide_id>/job/<jobid>/status', methods=('GET','POST'))
@task_access_required
def api_slide_job_status(task_id, slide_id, jobid):
    db = get_db()
    sr = get_slide_ref(slide_id)
    (project, specimen, block, slide_name, slide_ext) = sr.get_id_tuple()

    # Are we de this slide to a different node?
    del_url = find_delegate_for_slide(slide_id)

    # If local, call the method directly
    if del_url is None:
        return dzi_job_status(project, slide_name, jobid)
    else:
        url = '%s/dzi/job/%s/%s/%s/status' % (
                del_url, project, slide_name, jobid)
        pr = sr.get_project_ref()
        post_data = urllib.urlencode({'project_data': json.dumps(pr.get_dict())})
        return urllib2.urlopen(url, post_data).read()


# CLI commands
@click.command('annot-import')
@click.argument('task', type=click.INT)
@click.argument('slide_id', type=click.INT)
@click.argument('annot_file', type=click.File('rt'))
@click.option('-a','--affine', help="Affine matrix to apply to annotation", type=click.File('rt'))
@click.option('-u','--user', help='User name under which to insert samples', required=True)
@click.option('--raw-stroke-width', help='Set the stroke width of paths', type=click.INT)
@click.option('--font-size', help='Set the font size of markers', type=click.INT)
@with_appcontext
def import_annot_cmd(task, slide_id, annot_file, affine, user, raw_stroke_width, font_size):
    """ Import an annotation file in paper.js JSON format """

    # Read data
    data = json.load(annot_file)

    # Look up the user
    # TODO: need to check user against the task/project
    db = get_db()
    g.user = db.execute('SELECT * FROM user WHERE username=?', (user,)).fetchone()
    if g.user is None:
        print('User %s is not in the system' % user)
        return -1

    # Read matrix
    M = np.eye(3)
    if affine is not None:
        M = np.loadtxt(affine)

    # Transform the data and count items
    (data, stats) = transform_annot(data, M)

    # Apply cosmetic transformations
    if raw_stroke_width is not None:
        if 'children' in data[0][1]:
            for x in data[0][1]['children']:
                if x[0] == 'Path':
                    if 'data' not in x[1]:
                        x[1]['data']={}
                    x[1]['data']['rawStrokeWidth']=raw_stroke_width

    if font_size is not None:
        if 'children' in data[0][1]:
            for x in data[0][1]['children']:
                if x[0] == 'PointText':
                    x[1]['fontSize']=font_size



    # Update annotation
    _do_update_annot(task, slide_id, data, stats)

    # Report
    print('Annotation for slide %d in task %d updated to %d paths, %d markers' %
            (slide_id, task, stats['n_paths'], stats['n_markers']))


# Check a 'child' record in an annotation
def check_annot_child(x):

    try:
        if x[0] == 'Path':
            seg = x[1]['segments']
            if len(seg) < 1:
                return False
            for i in seg:
                for j in i:
                    for k in j:
                        if k is None or not isinstance(k, (float, int)):
                            return False
    except ValueError:
        return False

    return True


# Generate an SVG from an annotation
def extract_svg(task, slide_id, stroke_width, strip_width, font_size, font_color):

    # The return value
    svg = None

    # Find the annotation in the database
    db = get_db()
    rc = db.execute('SELECT json FROM annot WHERE slide_id=? AND task_id=?',
                    (slide_id, task)).fetchone()

    if rc is not None:
        # Get the annotation
        data = json.loads(rc['json'])

        # Get the raw slide dimensions
        sr = get_slide_ref(slide_id)
        dims = get_slide_raw_dims(sr)
        if dims is None:
            raise ValueError("Missing slide dimensions information")

        # Start writing svg
        svg = svgwrite.Drawing(size=(dims[0], dims[1]))

        # Write the paths
        if 'children' in data[0][1]:
            for x in data[0][1]['children']:

                # Handle paths
                if not check_annot_child(x):
                    print('Bad element:', x)
                    continue
                try:
                    if x[0] == 'Path':
                        seg = x[1]['segments']
                        if len(seg) < 1 or seg[0][0][0] is None or seg[0][0][1] is None:
                            continue

                        # Default mode is to draw curves
                        if strip_width is None or strip_width == 0:

                            # List of commands for SVG path
                            cmd = []

                            # Record the initial positioning
                            cmd.append('M%f,%f' % (seg[0][0][0], seg[0][0][1]))

                            # Record the control points
                            for i in range(1,len(seg)):
                                # Get the handles from the control point
                                P1 = seg[i-1][0]
                                P2 = seg[i][0]
                                D = [P2[0]-P1[0], P2[1]-P1[1]]
                                V1 = seg[i-1][2]
                                V2 = seg[i][1]
                                cmd.append('c%f,%f %f,%f %f,%f' %
                                        (V1[0], V1[1], D[0]+V2[0], D[1]+V2[1], D[0], D[1]));

                            # Add the path to the SVG
                            svg.add(svg.path(d=''.join(cmd), stroke="#000",
                                    fill="none", stroke_width=stroke_width))

                        # Alternative mode is to draw parallel strips
                        else:

                            # Record the control points
                            for i in range(1,len(seg)):
                                # Get the handles from the control point
                                P1 = seg[i-1][0]
                                P2 = seg[i][0]

                                D = [P2[0]-P1[0], P2[1]-P1[1]]
                                V1 = seg[i-1][2]
                                V2 = seg[i][1]

                                # Get the tangent vectors
                                nV1 = math.sqrt(V1[0]*V1[0]+V1[1]*V1[1])
                                nV2 = math.sqrt(V2[0]*V2[0]+V2[1]*V2[1])

                                T1=[ x / nV1 for x in V1] if nV1 > 0 else V1
                                T2=[ x / nV2 for x in V2] if nV2 > 0 else V2

                                Q2=[P2[0]+strip_width*T2[1],P2[1]-strip_width*T2[0]]
                                Q1=[P1[0]-strip_width*T1[1],P1[1]+strip_width*T1[0]]
                                DQ=[Q1[0]-Q2[0],Q1[1]-Q2[1]]

                                R2=[P2[0]-strip_width*T2[1],P2[1]+strip_width*T2[0]]
                                R1=[P1[0]+strip_width*T1[1],P1[1]-strip_width*T1[0]]
                                DR=[R1[0]-R2[0],R1[1]-R2[1]]

                                # Draw the path (part curve, part line)
                                svg.add(svg.path(
                                    d="M%f,%f c%f,%f %f,%f %f,%f L%f,%f c%f,%f %f,%f, %f,%f L%f,%f" % (
                                        P1[0],P1[1],
                                        V1[0],V1[1],D[0]+V2[0],D[1]+V2[1],D[0],D[1],
                                        Q2[0], Q2[1],
                                        V2[0], V2[1],DQ[0]+V1[0],DQ[1]+V1[1],DQ[0],DQ[1],
                                        P1[0],P1[1]), stroke="none", fill="#ddd", stroke_width=0))

                                # Draw the path (part curve, part line)
                                svg.add(svg.path(
                                    d="M%f,%f c%f,%f %f,%f %f,%f L%f,%f c%f,%f %f,%f, %f,%f L%f,%f" % (
                                        P1[0],P1[1],
                                        V1[0],V1[1],D[0]+V2[0],D[1]+V2[1],D[0],D[1],
                                        R2[0], R2[1],
                                        V2[0], V2[1],DR[0]+V1[0],DR[1]+V1[1],DR[0],DR[1],
                                        P1[0],P1[1]), stroke="none", fill="#aaa", stroke_width=0))

                                #svg.add(svg.path(
                                #    d="M%f,%f c%f,%f %f,%f %f,%f L%f,%f L%f,%f L%f,%f" % (
                                #        P1[0],P1[1],
                                #        V1[0],V1[1],D[0]+V2[0],D[1]+V2[1],D[0],D[1],
                                #        P2[0]+strip_width*T2[1],P2[1]-strip_width*T2[0],
                                #        P1[0]-strip_width*T1[1],P1[1]+strip_width*T1[0],
                                #        P1[0],P1[1]), stroke="#000", fill="#ddd", stroke_width=48))

                    elif x[0] == 'PointText':

                        tpos = x[1]['matrix'][4:6]
                        text = x[1]['content']
                        svg.add(svg.text(text, insert=tpos, fill=font_color, stroke=font_color,
                            font_size=font_size))

                        # ["PointText",
                        #  {"applyMatrix": false, "matrix": [1, 0, 0, 1, 1416.62777, 2090.96831], "content": "CA2",
                        #  "fontWeight": "bold", "fontSize": 44, "leading": 52.8, "justification": "center"}]

                except TypeError:
                    raise ValueError("Unreadable path %s in slide %d task %d" % (x, slide_id, task))

    return svg


# Export annotation
@click.command('annot-export-svg')
@click.argument('task', type=click.INT)
@click.argument('slide_id', type=click.INT)
@click.argument('out_file', type=click.File('wt'))
@click.option('--stroke-width', '-s', type=click.FLOAT, default=48.0,
              help='Stroke width for exported paths')
@click.option('--strip-width', type=click.FLOAT, default=48.0)
@with_appcontext
def export_annot_svg(task, slide_id, out_file, stroke_width, strip_width):
    """ Export annotations on a slide to SVG file """

    # Find the annotation in the database
    svg = extract_svg(task, slide_id, strip_width, strip_width)

    # Write the completed thing
    out_file.write(svg.tostring())





# Export annotation
@click.command('annot-export-vtk')
@click.argument('task', type=click.INT)
@click.argument('slide_id', type=click.INT)
@click.argument('out_file', type=click.File('wt'))
@with_appcontext
def export_annot_vtk(task, slide_id, out_file):
    """ Export annotations on a slide to VTK file """

    # Find the annotation in the database
    db = get_db()
    rc = db.execute('SELECT json FROM annot WHERE slide_id=? AND task_id=?',
                    (slide_id, task)).fetchone()

    if rc is not None:
        # Get the annotation
        data = json.loads(rc['json'])

        # Get the raw slide dimensions
        sr = get_slide_ref(slide_id)
        dims = get_slide_raw_dims(sr)
        if dims is None:
            sys.exit("Missing slide dimensions information")

        # Get the set of points
        pts = annot_sample_path_curves(data, 5000)

        # Join all the point arrays
        all_pts = [item for sublist in pts for item in sublist]

        # Length of each polyline segment
        plen = [len(sublist) for sublist in pts]

        # Write a VTK file based on points
        out_file.write("# vtk DataFile Version 4.2\n")
        out_file.write("vtk output\n")
        out_file.write("ASCII\n")
        out_file.write("DATASET POLYDATA\n")
        out_file.write("POINTS %d float\n" % len(all_pts))

        # Write all the points
        for p in all_pts:
            out_file.write('%f %f %f\n' % (p[0],p[1],0.0))

        out_file.write("LINES %d %d\n" % (len(pts), len(pts) + sum(plen)))

        idx = 0
        for q in pts:
            out_file.write('%d %s\n' % ( len(q), ' '.join([str(x) for x in range(idx,idx + len(q))])) )
            idx = idx + len(q)



# List slides
@click.command('slides-list')
@click.argument('task', type=click.INT)
@click.option('-s','--specimen',help="List slides for a specimen")
@click.option('-b','--block',help="List slides for a block")
@click.option('--section',help="List slides for a section")
@click.option('--slide',help="List slides for a slide")
@click.option('--stain',help="List slides matching a stain")
@click.option('--min-paths', type=click.INT, help="List slides with path annotations only")
@click.option('--min-markers', type=click.INT, help="List slides with marker annotations only")
@click.option('-C', '--csv', type=click.File('wt'), help="Write results to CSV file")
@with_appcontext
def slides_list_cmd(task, specimen, block, section, slide, stain,
        min_paths, min_markers, csv):
    """List slides in a task"""

    db=get_db()

    # Create a DB view of slide details
    make_slide_dbview(task, 'v_full')

    # Build up a where clause
    w = filter(lambda (a,b): b is not None and b is not False, 
            [('specimen_name LIKE ?', specimen),
             ('block_name LIKE ?', block),
             ('section = ?', section),
             ('slide = ?', slide),
             ('stain = ?', stain),
             ('n_paths >= ?', min_paths), 
             ('n_markers >= ?', min_markers)])

    if len(w) > 0:
        w_sql,w_prm = zip(*w)
        w_clause = 'WHERE %s' % ' AND '.join(w_sql)
    else:
        w_clause = ''
        w_prm = ()

    # Create a Pandas data frame
    df = pandas.read_sql_query( "SELECT * FROM v_full %s" % w_clause, db, params=w_prm)

    # Dump the database entries
    if csv is not None:
        df.to_csv(csv, index=False)
    else:
        with pandas.option_context('display.max_rows', None):  
            print(df)


def init_app(app):
    app.cli.add_command(import_annot_cmd)
    app.cli.add_command(export_annot_svg)
    app.cli.add_command(export_annot_vtk)
    app.cli.add_command(slides_list_cmd)


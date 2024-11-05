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
    current_app, send_from_directory, session, Response, jsonify
)
from werkzeug.exceptions import abort

from .auth import login_required, get_user_id, \
    access_project_read, access_project_admin, \
    access_task_read, access_task_write, access_task_admin
from .db import get_db
from .project_ref import ProjectRef
from .slideref import SlideRef, get_slide_ref
from .project_cli import get_task_data, update_edit_meta, create_edit_meta, update_edit_meta_to_current, refresh_slide_db
from .delegate import find_delegate_for_slide
from .dzi import get_affine_matrix, get_random_patch, get_osl
from io import BytesIO, StringIO
from PIL import Image
from threading import Thread

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
import io


bp = Blueprint('slide', __name__)


@bp.route('/')
@login_required
def index():
    # Render the entry page
    return render_template('slide/projects_tasks.html')


# The index
@bp.route('/project/<project>')
@access_project_read
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
        rc = db.execute('SELECT P.*, PA.access="admin" as admin FROM project P '
                        'LEFT JOIN project_access PA ON PA.project=P.id '
                        'WHERE PA.user = ?'
                        'ORDER BY P.disp_name', (user,))
    else:
        rc = db.execute('SELECT P.*, 1 as admin FROM project P ORDER BY P.disp_name ')

    for row in rc.fetchall():

        # Get the statistics for the project
        stat = db.execute(
            'SELECT COUNT (id) as nslides, '
            '       COUNT(DISTINCT block_id) as nblocks, '
            '       COUNT(DISTINCT specimen) as nspecimens '
            'FROM slide_info WHERE project=?', (row['id'],)).fetchone()

        # Create a dictionary
        listing.append({'id':row['id'],'admin':row['admin'],'disp_name':row['disp_name'],'desc':row['desc'],
                        'nslides':stat['nslides'], 'nblocks':stat['nblocks'], 'nspecimens':stat['nspecimens']})

    # Generate a bunch of json
    return json.dumps([x for x in listing])


@bp.route('/api/project/<project>/tasks')
@access_project_read
def task_listing(project):
    db=get_db()
    user = session['user_id']

    # List the available tasks (TODO: check user access to task)
    rc = db.execute("""
                    SELECT DISTINCT TI.* from task_info TI left join task_access TA on TI.id=TA.task 
                    where project=? and (restrict_access=0 or (user=? and access != "none"))
                    """, (project, user))

    listing = []
    for row in rc.fetchall():

        # Parse the json
        task_id = row['id']

        # Get the subset of stains to which the task applies
        stat = db.execute(
            """SELECT COUNT(S.id) as nslides, COUNT(DISTINCT block_id) as nblocks, COUNT(DISTINCT specimen) as nspecimens
               FROM task_slide_index TSI
               LEFT JOIN slide_info S on S.id == TSI.slide
               WHERE task_id=?""", (task_id,)).fetchone()

        # Create a dict
        task = json.loads(row['json'])
        d = {'id': row['id'], 'name': task['name'], 'desc': task['desc'], 'mode': task['mode']}
        for key in ('nspecimens', 'nblocks', 'nslides'):
            d[key] = stat[key]

        listing.append(d)

    return json.dumps([x for x in listing])


# Get basic information about a task
@bp.route('/api/task/<int:task_id>/info')
@access_task_read
def task_get_info(task_id):
    db = get_db()
    rc = db.execute('SELECT * FROM task_info WHERE id = ?', (task_id,)).fetchone()
    d = { k:rc[k] for k in ('id','project','name','restrict_access','anonymize') }
    detail = json.loads(rc['json'])
    for k in 'desc', 'mode', 'reference_task':
        if k in detail:
            d[k] = detail[k]
    return json.dumps(d)  


# Specimen listing for a task
@bp.route('/api/task/<int:task_id>/specimens')
@access_task_read
def task_specimen_listing(task_id):
    db = get_db()
    print("IN task_specimen_listing!!!")

    # Get the current task data
    project,task = get_task_data(task_id)

    # List all the blocks that meet requirements for the current task
    if task['mode'] == 'annot':

        # Join with the annotations table
        blocks = db.execute(
            """SELECT specimen_display, specimen, COUNT(DISTINCT block_id) as nblocks,
                   COUNT (S.id) as nslides, COUNT(A.slide_id) as nannot
               FROM task_slide_info S
               LEFT JOIN annot A on A.slide_id = S.id AND A.task_id = S.task_id
               WHERE S.task_id = ?
               GROUP BY specimen
               ORDER BY specimen_display""", (task_id,)).fetchall()
        print("ANNOT: ", blocks)

    elif task['mode'] == 'dltrain':

        # Join with the annotations table
        blocks = db.execute(
            """SELECT specimen_display, specimen, COUNT(DISTINCT block_id) as nblocks,
                   COUNT (DISTINCT S.id) as nslides, COUNT(T.slide) as nsamples
               FROM task_slide_info S
               LEFT JOIN training_sample T on T.slide = S.id AND T.task = S.task_id
               WHERE S.task_id = ?
               GROUP BY specimen
               ORDER BY specimen_display""", (task_id,)).fetchall()

    elif task['mode'] == 'sampling':

        blocks = db.execute(
            """SELECT specimen_display, specimen, COUNT(DISTINCT block_id) as nblocks,
                   COUNT (DISTINCT S.id) as nslides, COUNT(SR.slide) as nsamples
               FROM task_slide_info S
               LEFT JOIN sampling_roi SR on SR.slide = S.id AND SR.task = S.task_id
               WHERE S.task_id = ?
               GROUP BY specimen
               ORDER BY specimen_display""", (task_id,)).fetchall()
        
    else:
        blocks = db.execute(
            """SELECT specimen_display, specimen, COUNT(DISTINCT block_id) as nblocks, COUNT(S.id) as nslides
               FROM task_slide_info S
               WHERE S.task_id = ?
               GROUP BY specimen
               ORDER BY specimen_display""", (task_id,)).fetchall()

    return json.dumps([dict(row) for row in blocks])


# Slide listing for a task - simple
@bp.route('/api/task/<int:task_id>/slide_manifest.csv')
@access_task_read
def task_slide_listing_csv(task_id):
    db = get_db()

    # Get the current task data
    project,task = get_task_data(task_id)
    fout = StringIO()

    # Select keys to export
    keys = ('id', 'specimen', 'block', 'stain', 'section', 'slide')

    # List all the blocks that meet requirements for the current task
    rc = db.execute(
            """SELECT S.id, S.stain, S.specimen_display as specimen, S.block_name as block,
                      S.section, S.slide
               FROM task_slide_info S
               WHERE S.task_id = ?
               ORDER BY S.id""", (task_id,))

    fout.write(','.join(keys) + '\n')
    for row in rc.fetchall():
        vals = map(lambda a: str(row[a]), keys)
        fout.write(','.join(vals) + '\n')

    return Response(fout.getvalue(), mimetype='text/csv')


# Command to generate a slide listing in CSV format
def generate_detailed_slide_listing(
    task, specimen, block, section, slide, stain,
    min_paths, min_markers, min_sroi, csv):    

    # Create a DB view of slide details
    db=get_db()
    make_slide_dbview(task, 'v_full')

    # Build up a where clause
    w = list(filter(lambda x: x[1] is not None and x[1] is not False, 
            [('specimen_private LIKE ?', specimen),
             ('block_name LIKE ?', block),
             ('section = ?', section),
             ('slide = ?', slide),
             ('stain = ?', stain),
             ('n_paths >= ?', min_paths), 
             ('n_markers >= ?', min_markers),
             ('n_sampling_rois >= ?', min_sroi)]))

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
            
            
# Get basic information about a task
@bp.route('/api/task/<int:task_id>/slide/<int:slide_id>/info')
@access_task_read
def task_get_slide_info(task_id, slide_id):
    db = get_db()
    rc = db.execute('SELECT * FROM slide_info WHERE id = ?', (slide_id,)).fetchone()
    d = { k:rc[k] for k in ('specimen', 'block_name', 'specimen_private', 'specimen_public', 'section', 'slide', 'stain') }
    return json.dumps(d)  


# Slide listing for a task - simple
@bp.route('/api/task/<int:task_id>/slide_detailed_manifest.csv')
@access_task_read
def get_slide_detailed_manifest(task_id):

    # Parameters to read from the request
    param = {
        'specimen': None, 'block': None, 'section': None, 'slide': None,
        'stain': None, 'min_paths': None, 'min_markers': None, 'min_sroi': None
    }
    
    for k in param.keys():
        if k in request.args:
            param[k] = int(request.args[k]) if k.startswith('min_') else request.args[k]
    
    # Call the main command
    out = StringIO()
    generate_detailed_slide_listing(task_id, csv=out, **param)
    
    # Write the response
    return Response(out.getvalue(), mimetype='text/csv')


def get_task_listing(task_id, specimen, block_name):
    
    # Get the current task data
    project,task = get_task_data(task_id)
    pr = ProjectRef(project)
    
    # Load the view preferences for this task
    d_pref = session.get('slide_view_pref',{}).get(str(task_id), {})
    pref_resolution=d_pref.get('resolution', 'raw')
    pref_affine_mode=d_pref.get('affine_mode', 'raw')
    
    return render_template('slide/task_detail.html',
                           project=project, project_name=pr.disp_name, task=task, task_id=task_id,
                           specimen=specimen, block_name=block_name, 
                           pref_resolution=pref_resolution, 
                           pref_affine_mode=pref_affine_mode)
    

# Block detail (same template as the task detail, but points to a block
@bp.route('/task/<int:task_id>/specimen/<int:specimen>/block/<block_name>')
@access_task_read
def block_detail_by_id(task_id, specimen, block_name):
    return get_task_listing(task_id, specimen, block_name)


# Specimen detail (same template as the task detail, but points to a specimen
@bp.route('/task/<int:task_id>/specimen/<int:specimen>')
@access_task_read
def specimen_detail_by_id(task_id, specimen):
    return get_task_listing(task_id, specimen, None)


# Task detail
@bp.route('/task/<int:task_id>')
@access_task_read
def task_detail(task_id):
    return get_task_listing(task_id, None, None)


# Allow user to set resolution preference for a task
@bp.route('/api/task/<int:task_id>/pref/resolution/set/<resolution>')
@access_task_read
def task_set_resolution_preference(task_id, resolution):
    if resolution not in ('raw', 'x16'):
        raise ValueError(f'Invalid resolution {resolution}')

    task_key = str(task_id)
    if 'slide_view_pref' not in session:
        session['slide_view_pref'] = {}
    if task_key not in session['slide_view_pref']:
        session['slide_view_pref'][task_key] = {}
        
    session['slide_view_pref'][task_key]['resolution'] = resolution
    print(f'\n\nUPDATED PREFERENCES: {session["slide_view_pref"]}\n\n')
    return resolution


# Complete listing of slides in a task
@bp.route('/task/<int:task_id>/slides')
@access_task_read
def task_all_slides(task_id):

    # Get the current task data
    project,task = get_task_data(task_id)
    pr = ProjectRef(project)
    return render_template('slide/task_slide_listing.html',
                           project=project, project_name=pr.disp_name,
                           task=task, task_id=task_id)



# Task detail
@bp.route('/api/task/<int:task_id>/specimen/<specimen>/blocks')
@access_task_read
def specimen_block_listing(task_id, specimen):

    db = get_db()

    # Get the current task data
    project, task = get_task_data(task_id)

    # List all the blocks that meet requirements for the current task
    if task['mode'] == 'annot':

        # Join with the annotations table
        blocks = db.execute(
            """SELECT block_id,block_name,specimen_display,
                   COUNT (S.id) as nslides, COUNT(A.slide_id) as nannot 
               FROM task_slide_info S
               LEFT JOIN annot A on A.slide_id = S.id AND A.task_id = S.task_id
               WHERE S.task_id = ? AND S.specimen = ?
               GROUP BY block_id,block_name,specimen
               ORDER BY block_name""", (task_id, specimen)).fetchall()

    elif task['mode'] == 'dltrain':

        # Join with the annotations table
        blocks = db.execute(
            """SELECT block_id,block_name,specimen_display,
                   COUNT (DISTINCT S.id) as nslides, COUNT(T.slide) as nsamples
               FROM task_slide_info S
               LEFT JOIN training_sample T on T.slide = S.id AND T.task = S.task_id
               WHERE S.task_id = ? AND S.specimen = ?
               GROUP BY block_id,block_name,specimen
               ORDER BY block_name""", (task_id, specimen)).fetchall()

    elif task['mode'] == 'sampling':

        # Join with the annotations table
        blocks = db.execute(
            """SELECT block_id,block_name,specimen_display,
                   COUNT (DISTINCT S.id) as nslides, COUNT(SR.slide) as nsamples
               FROM task_slide_info S
               LEFT JOIN sampling_roi SR on SR.slide = S.id AND SR.task = S.task_id
               WHERE S.task_id = ? AND S.specimen = ?
               GROUP BY block_id,block_name,specimen
               ORDER BY block_name""", (task_id, specimen)).fetchall()

    else:
        # Browse mode
        blocks = db.execute(
            """SELECT block_id,block_name,specimen_display,COUNT(S.id) as nslides
               FROM task_slide_info S
               WHERE S.task_id = ? AND S.specimen = ?
               GROUP BY block_id,block_name,specimen
               ORDER BY block_name""", (task_id, specimen)).fetchall()

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

        db.execute(
            """CREATE TEMP VIEW %s AS
                SELECT S.*,
                   IFNULL(SUM(A.n_paths),0) as n_paths,
                   IFNULL(SUM(A.n_markers),0) as n_markers,
                   IFNULL(SUM(A.n_paths),0) + IFNULL(SUM(A.n_markers),0) as n_annot
                FROM task_slide_index TSI
                    LEFT JOIN slide_info S ON TSI.slide = S.id
                    LEFT JOIN annot A on A.slide_id = S.id AND A.task_id = TSI.task_id
                WHERE TSI.task_id = %d
                GROUP BY S.id, S.section, S.slide, specimen, block_name
                ORDER BY specimen_private, block_name, section, slide""" % (view_name, int(task_id)))

    elif task['mode'] == 'dltrain':

        db.execute(
            """CREATE TEMP VIEW %s AS
                SELECT S.*,
                   COUNT(T.id) as n_samples
                FROM task_slide_index TSI
                    LEFT JOIN slide_info S ON TSI.slide = S.id
                    LEFT JOIN training_sample T on T.slide = S.id AND T.task = TSI.task_id
                WHERE TSI.task_id = %d
                GROUP BY S.id, S.section, S.slide, specimen, block_name
                ORDER BY specimen_private, block_name, section, slide""" % (view_name, int(task_id)))

    elif task['mode'] == 'sampling':

        db.execute(
            """CREATE TEMP VIEW %s AS
                SELECT S.*,
                   COUNT(R.id) as n_sampling_rois
                FROM task_slide_index TSI
                    LEFT JOIN slide_info S ON TSI.slide = S.id
                    LEFT JOIN sampling_roi R on R.slide = S.id AND R.task = TSI.task_id
                WHERE TSI.task_id = %d
                GROUP BY S.id, S.section, S.slide, specimen, block_name
                ORDER BY specimen_private, block_name, section, slide""" % (view_name, int(task_id)))

    else:

        db.execute(
            """CREATE TEMP VIEW %s AS
               SELECT S.* FROM task_slide_index TSI
                   LEFT JOIN slide_info S on S.id == TSI.slide
               WHERE TSI.task_id = %d
               ORDER BY specimen_private, block_name, section, slide""" % (view_name, int(task_id)))


# The block detail listing
@bp.route('/api/task/<int:task_id>/specimen/<int:specimen>/block/<block_name>/slides', methods=('GET', 'POST'))
@access_task_read
def block_slide_listing(task_id, specimen, block_name):
    db = get_db()

    # Get the current task data
    project,task = get_task_data(task_id)

    # Get the block descriptor
    block = db.execute('SELECT * FROM block_info WHERE specimen=? AND block_name=? AND project=?',
            (specimen,block_name,project)).fetchone()

    if block is None:
        return json.dumps([])

    block_id = block['id']

    # List all the blocks that meet requirements for the current task
    if task['mode'] == 'annot':

        # Join with the annotations table
        slides = db.execute(
            """SELECT S.*,
                   IFNULL(SUM(A.n_paths),0) as n_paths,
                   IFNULL(SUM(A.n_markers),0) as n_markers,
                   IFNULL(SUM(A.n_paths),0) + IFNULL(SUM(A.n_markers),0) as n_annot
                FROM task_slide_info S
                    LEFT JOIN annot A on A.slide_id = S.id AND A.task_id = S.task_id
                WHERE S.task_id = ? AND S.block_id = ?
                GROUP BY S.id, S.section, S.slide
                ORDER BY section, slide""", (task_id, block_id)).fetchall()

    elif task['mode'] == 'dltrain':

        # Join with the training samples table
        slides = db.execute(
            """SELECT S.*, COUNT(T.id) as n_samples 
               FROM task_slide_info S
                   LEFT JOIN training_sample T on T.slide = S.id AND T.task = S.task_id
               WHERE S.task_id = ? AND block_id = ?
               GROUP BY S.id, S.section, S.slide
               ORDER BY section, slide""", (task_id, block_id)).fetchall()

    elif task['mode'] == 'sampling':

        # Join with the training samples table
        slides = db.execute(
            """SELECT S.*, COUNT(SR.id) as n_samples 
               FROM task_slide_info S
                   LEFT JOIN sampling_roi SR on SR.slide = S.id AND SR.task = S.task_id
               WHERE S.task_id = ? AND block_id = ?
               GROUP BY S.id, S.section, S.slide
               ORDER BY section, slide""", (task_id, block_id)).fetchall()

    else:

        slides = db.execute(
            """SELECT S.* FROM task_slide_info S
               WHERE S.task_id = ? AND S.block_id = ?
               ORDER BY section, slide""", (task_id, block_id)).fetchall()

    return json.dumps([dict(row) for row in slides])


# Complete task slide listing
@bp.route('/api/task/<int:task_id>/slides', methods=('POST',))
@access_task_read
def task_slide_listing(task_id):
    db = get_db()
    db.set_trace_callback(print)

    # Map the request to json
    r = json.loads(request.get_data().decode('UTF-8'))

    # Get the current task data
    project,task = get_task_data(task_id)

    # Run a query to count the total number of slides to return
    n_total = db.execute(
        """SELECT COUNT(S.id) as n 
           FROM task_slide_info S 
           WHERE S.task_id=?""", (task_id,)).fetchone()['n']

    # Do we have a global search query
    if len(r['search']['value']) > 0:
        # Create search clause for later
        search_clause = 'AND (S.specimen_display LIKE ? OR S.block_name LIKE ? OR S.stain LIKE ?)'
        search_pat = '%' + r['search']['value'] + '%'
        search_items = search_pat,search_pat,search_pat

        # Run search clause to get number of filtered entries
        n_filtered = db.execute(
            """SELECT COUNT(S.id) as n 
               FROM task_slide_info S 
               WHERE S.task_id=? {}""".format(search_clause), (task_id,) + search_items).fetchone()['n']
    else:
        search_clause, search_items = '', ()
        n_filtered = n_total

    # Field to order by
    order_column = int(r['order'][0]['column'])
    order_dir = {'asc':'ASC','desc':'DESC'}[r['order'][0]['dir']]
    paging_start = r['start']
    paging_length = r['length']

    # Run the main query
    slides = db.execute(
        """SELECT S.id, S.specimen_display, S.block_name, S.section, S.slide, S.stain 
           FROM task_slide_info S
           WHERE S.task_id = ? {}
           ORDER BY {:d} {} LIMIT {:d},{:d}""".format(search_clause,order_column+1,order_dir,paging_start,paging_length), 
           (task_id,) + search_items).fetchall()

    # Build return json
    x = {
        'draw' : r['draw'],
        'recordsTotal': n_total,
        'recordsFiltered': n_filtered,
        'data': [dict(row) for row in slides]
    }

    db.set_trace_callback(None)
    return json.dumps(x)

    # List all the blocks that meet requirements for the current task
    if task['mode'] == 'annot':

        # Join with the annotations table
        slides = db.execute(
            """SELECT S.id, S.specimen_display, S.block_name, S.section, S.slide, S.stain,
                   IFNULL(SUM(A.n_paths),0) as n_paths,
                   IFNULL(SUM(A.n_markers),0) as n_markers,
                   IFNULL(SUM(A.n_paths),0) + IFNULL(SUM(A.n_markers),0) as n_annot
                FROM task_slide_info S
                    LEFT JOIN annot A on A.slide_id = S.id AND A.task_id = S.task_id
                WHERE S.task_id = ? 
                GROUP BY S.id, S.section, S.slide
                ORDER BY section, slide""", (task_id,)).fetchall()

    elif task['mode'] == 'dltrain':

        # Join with the training samples table
        slides = db.execute(
            """SELECT S.id, S.specimen_display, S.block_name, S.section, S.slide, S.stain, COUNT(T.id) as n_samples 
               FROM task_slide_info S
                   LEFT JOIN training_sample T on T.slide = S.id AND T.task = S.task_id
               WHERE S.task_id = ? 
               GROUP BY S.id, S.section, S.slide
               ORDER BY section, slide""", (task_id,)).fetchall()

    else:

        slides = db.execute(
            """SELECT S.id, S.specimen_display, S.block_name, S.section, S.slide, S.stain 
               FROM task_slide_info S
               WHERE S.task_id = ? 
               ORDER BY section, slide""", (task_id,)).fetchall()

    return json.dumps([dict(row) for row in slides])


# Get all the data needed for slide view/annotation/training
def get_slide_info(task_id, slide_id):
    db = get_db()

    # Get the info on the current slide
    slide_info = db.execute('SELECT * from task_slide_info WHERE id = ? AND task_id = ?', (slide_id,task_id)).fetchone()

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


# Get all the tasks that are available to the user for a particular slide, returns
# as a dictionary, with task_id as key, dict with task info as value
def get_available_tasks_for_slide(project, slide_id):
    db = get_db()
    user = session['user_id']
    
    # All tasks that the user has access to on this slide
    rc = db.execute(
        "SELECT DISTINCT TI.* FROM task_info TI "
        "       LEFT JOIN task_access TA ON TI.id=TA.task "
        "       LEFT JOIN task_slide_index TSI on TSI.task_id = TI.id "
        "WHERE project=? AND TSI.slide = ? "
        "       AND (restrict_access=0 OR (user=? and access != 'none')) ",
        (project, slide_id, user))
    
    # For each task, designate its mode
    task_mode_dict = {}
    for row in rc.fetchall():
        task = json.loads(row['json'])
        task_mode_dict[row['id']] = { k : task[k] for k in ('mode', 'name', 'desc') }
        
    return task_mode_dict


# Dummy command to get some metadata from openslide, just meant to get the slide header
# loaded in a thread before the user needs it
def load_slide_into_cache(slide_id, sr, resource, socket_addr_list):
    osl = get_osl(slide_id, sr, resource, socket_addr_list)
    print(f'================== Slide {slide_id} has dimensions {osl.dimensions} ===================')
        

# The slide view
@bp.route('/task/<int:task_id>/slide/<int:slide_id>/view/<resolution>/<affine_mode>', methods=('GET', 'POST'))
@access_task_read
def slide_view(task_id, slide_id, resolution, affine_mode):

    # Get the current task data
    project,task = get_task_data(task_id)

    # Get the next/previous slides for this task
    si, prev_slide, next_slide, stain_list, user_prefs = get_slide_info(task_id, slide_id)

    pr = ProjectRef(project)
    sr = get_slide_ref(slide_id, pr)
    
    # Check that the affine mode and resolution requested are available
    have_affine, have_x16 = False, False
    if task['mode'] != 'dltrain':
        have_affine = sr.resource_exists('affine', True) or sr.resource_exists('affine', False)
        have_x16 = sr.resource_exists('x16', True) or sr.resource_exists('x16', False)

    # If one is missing, we need a redirect
    rd_affine_mode = affine_mode if have_affine else 'raw'
    rd_resolution = resolution if have_x16 else 'raw'

    # At this point, we can request the openslide server to read our slide and get some basic 
    # information to reduce the wait time when the page loads
    socket_addr_list=current_app.config['SLIDE_SERVER_ADDR']
    prime_cache_thread = Thread(target=load_slide_into_cache, args=(slide_id, sr, rd_resolution, socket_addr_list))
    prime_cache_thread.start()

    # Get the list of available overlays and jsonify
    overlays = sr.get_available_overlays(local = False)
    
    # Get the list of other tasks available for this slide
    other_tasks = get_available_tasks_for_slide(project, slide_id)
    other_tasks = { k:v for k,v in other_tasks.items() if k != task_id }

    # Remove the URL from the overlay dict - this is not for public consumption
    if overlays:
        for k,v in overlays.items():
            v.pop('url', None)

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
            'slide_id':slide_id,
            'mode':affine_mode,
            'resource':'XXXXX',
            'downsample': 999999,
            'extension': sr.slide_ext
            }

    url_tmpl_dzi = url_for('dzi.dzi', **url_ctx)
    url_tmpl_download_tiff = url_for('dzi.dzi_download_tiff', **url_ctx)
    url_tmpl_download_nii_gz = url_for('dzi.dzi_download_nii_gz', **url_ctx)
    url_tmpl_download_fullres = url_for('dzi.dzi_download_fullres', **url_ctx)
    url_tmpl_download_label = url_for('dzi.dzi_download_label_image', **url_ctx)
    url_tmpl_download_macro = url_for('dzi.dzi_download_macro_image', **url_ctx)
    url_tmpl_download_header = url_for('dzi.dzi_download_header', **url_ctx)

    # Build a dictionary to call
    context = {
        'slide_id': slide_id,
        'slide_info': si,
        'slide_ext': sr.slide_ext,
        'next_slide': next_slide,
        'prev_slide': prev_slide,
        'stain_list': stain_list,
        'affine_mode': affine_mode,
        'have_affine': have_affine,
        'have_x16': have_x16,
        'resolution': resolution,
        'seg_mode': task['mode'],
        'task_id': task_id,
        'project': si['project'],
        'project_name': pr.disp_name,
        'block_id': si['block_id'],
        'url_tmpl_dzi': url_tmpl_dzi,
        'url_tmpl_download_tiff': url_tmpl_download_tiff,
        'url_tmpl_download_nii_gz': url_tmpl_download_nii_gz,
        'url_tmpl_download_fullres': url_tmpl_download_fullres,
        'url_tmpl_download_label': url_tmpl_download_label,
        'url_tmpl_download_macro': url_tmpl_download_macro,
        'url_tmpl_download_header': url_tmpl_download_header,
        'task': task,
        'fixed_box_size': get_dltrain_fixed_box_size(task),
        'user_prefs': user_prefs,
        'overlays': overlays,
        'other_tasks': other_tasks
    }

    # Load the metadata for the slide to get spacing information
    slide_spacing = sr.get_pixel_spacing(resolution)
    if slide_spacing is None:
        context['spacing'] = [0,0]
        context['spacing_str'] = 'Unknown'
    else:
        context['spacing'] = slide_spacing
        sp_mm = tuple(1000. * x for x in slide_spacing)
        context['spacing_str'] = '{:.4f} x {:.4f}'.format(sp_mm[0], sp_mm[1])

    # Get slide dimensions
    dims = sr.get_dims()
    context['dims'] = dims
    context['dims_str'] = f'{dims[0]} x {dims[1]}' if dims else 'Unknown'

    # Add optional fields to context
    sample_data = {}
    if 'slide_view_sample_data' in session:
        sample_data = session.get('slide_view_sample_data')
        session.pop('slide_view_sample_data')

    for field in ('sample_id', 'sample_cx', 'sample_cy'):
        for source in request.args, request.form, sample_data:
            if field in source:
                context[field] = source[field]
                
    # Render the template
    return render_template('slide/slide_view.html', **context)


# Get an annotation filename for slide
def get_annot_json_file(task_id, slide_id):
    
    # Get the slide details
    db = get_db()
    si = db.execute('SELECT * FROM slide_info WHERE id = ?', (slide_id,)).fetchone()

    # Generate a file
    json_filename = "annot_%s_%s_%s_%s_%02d_%02d.json" % (
        si['project'], si['specimen_private'], si['block_name'], si['stain'], si['section'], si['slide'])

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
                    try:
                        pts = curve_interp(seg_1, seg_2, 100)
                    except:
                        print('Error interpolating segment')
                        continue

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
def _do_update_annot(task_id, slide_id, annot, stats, metadata={}):

    # See if an annotation already exists
    db = get_db()
    a_row = db.execute('SELECT * FROM annot WHERE slide_id=? AND task_id=?',
                       (slide_id, task_id)).fetchone()

    if a_row is not None:

        # Update the timestamp
        if metadata:
            update_edit_meta(a_row['meta_id'], **metadata)
        else:
            update_edit_meta_to_current(a_row['meta_id'])
        
        # Update the row
        db.execute(
            'UPDATE annot SET json=?, n_paths=?, n_markers=? '
            'WHERE slide_id=? AND task_id=?', 
            (json.dumps(annot), stats['n_paths'], stats['n_markers'], slide_id, task_id))

    else:

        # Create a new timestamp
        meta_id = create_edit_meta(**metadata)

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
@access_task_read
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
@access_task_write
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
@access_task_read
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


# Serve label image if available
@bp.route('/api/task/<int:task_id>/slide/<int:slide_id>_thumbnail.png', methods=('GET',))
@access_task_read
def get_slide_thumbnail(task_id, slide_id):
    sr = get_slide_ref(slide_id)
    f_local = sr.get_local_copy('thumb')
    if f_local:
        im = Image.open(f_local)
        buf = io.BytesIO()
        im.save(buf, 'PNG')
        resp = make_response(buf.getvalue())
        resp.mimetype = 'image/png'
        return resp
    else:
        abort(404)


# Serve up quick, locally cached thumbnails
# TODO: need API keys!
@bp.route('/slide/<int:id>/thumb', methods=('GET',))
def thumb(id):
    thumb_dir = os.path.join(current_app.instance_path, 'thumb')
    thumb_fn = "thumb%08d.png" % (id,)
    return send_from_directory(thumb_dir, thumb_fn, as_attachment=False)


# Slide metadata
@bp.route('/api/task/<int:task_id>/slide/<int:slide_id>/slide_admin_info', methods=('GET','POST'))
@access_task_admin
def api_get_slide_admin_info(task_id, slide_id):
    sr = get_slide_ref(slide_id)
    return jsonify({
        "id": slide_id,
        "specimen": sr.specimen, 
        "block": sr.block,
        "slide_name": sr.slide_name,
        "slide_ext": sr.slide_ext,
        "raw_url_local": sr.get_resource_url("raw", True),
        "raw_url_remote": sr.get_resource_url("raw", False),
        "metadata": sr.get_metadata()
    })


# Get a random patch from the slide
@bp.route('/api/task/<int:task_id>/slide/<int:slide_id>/random_patch/<int:width>', methods=('GET','POST'))
@access_task_read
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
        rawbytes = get_random_patch(project, slide_id, 'raw', 0, width, 'png')
    else:
        url = '%s/dzi/random_patch/%s/%d/raw/0/%d.png' % (del_url, project, slide_id, width)
        pr = sr.get_project_ref()
        post_data = urllib.urlencode({'project_data': json.dumps(pr.get_dict())})
        rawbytes = urllib.request.urlopen(url, post_data).read()

    # Send the patch
    resp = make_response(rawbytes)
    resp.mimetype = 'image/png'
    return resp


@bp.route('/api/project/<project>/specimen/<specimen>/refresh_slides', methods=('GET','POST'))
@access_project_admin
def api_project_refresh_slides_for_specimen(project, specimen):
    refresh_slide_db(project, None, single_specimen=specimen, check_hash=False)
    return "", 200, {'ContentType':'application/json'} 


@bp.route('/api/task/task_<int:task_id>_slides.csv', methods=('GET','POST'))
@access_task_admin
def api_task_list_all_slides(task_id):
    db=get_db()
    make_slide_dbview(task_id, 'v_full')
    df = pandas.read_sql_query( "SELECT * FROM v_full", db)
    csv = df.to_csv()
    return Response(csv, 200, mimetype="text/csv", headers={"Content-disposition": "attachment"})


# Export all annotations for a task as a single JSON file
@click.command('annot-export-task')
@click.argument('task', type=click.INT)
@click.argument('output', type=click.Path())
@with_appcontext
def export_task_annot_cmd(task, output):
    """Export all annotations from a task to a JSON file"""
    db = get_db()

    # List all annotations for this task
    rc = db.execute('SELECT A.*, EM.*, S.slide_name, UC.username as creator_name, UE.username as editor_name '
                    'FROM annot A '
                    '  LEFT JOIN task_slide_info S ON A.task_id = S.task_id AND A.slide_id = S.id '
                    '  LEFT JOIN edit_meta EM on A.meta_id = EM.id '
                    '  LEFT JOIN user UC on EM.creator = UC.id '
                    '  LEFT JOIN user UE on EM.editor = UE.id '
                    'WHERE A.task_id = ? ORDER BY A.slide_id', (task,)).fetchall()

    # Fields that we want to extract
    fields = [ 'slide_name', 'json', 'creator_name', 't_create', 'editor_name', 't_edit' ]

    # Form a dictionary with this annotation
    data = []
    for row in rc:
        d = dict(zip(fields, [ row[x] for x in fields ] ))
        data.append(d)

    # Save the data as a json file
    with open(output, 'wt') as jfile:
        json.dump(data, jfile)


# Export all annotations for a task as a single JSON file
@click.command('annot-import-task')
@click.argument('task', type=click.INT)
@click.argument('input', type=click.File('rt'))
@with_appcontext
def import_task_annot_cmd(task, input):
    """Import all annotations from a task from a JSON file using annot-export-task format"""
    db = get_db()

    # Read the data
    annot = json.load(input)

    # Import each entry
    for i,a in enumerate(annot):

        # Find slide
        rc = db.execute('SELECT id FROM slide WHERE slide_name=?', (a['slide_name'],)).fetchone()
        if rc is None:
            print('Annot %d, slide %s: Slide does not exist' % (i,a['slide_name'],))
            continue
        slide_id = rc['id']

        # Find creator and editor
        cid, eid = get_user_id(a['creator_name']), get_user_id(a['editor_name'])
        if cid is None:
            print('Annot %d, slide %s: User %s does not exist' % (i,a['slide_name'],a['creator_name'],))
            continue
        if eid is None:
            print('Annot %d, slide %s: User %s does not exist' % (i,a['slide_name'],a['editor_name'],))
            continue

        # Generate the metadata dict
        metadata = {'creator': cid, 'editor': eid, 't_create': a['t_create'], 't_edit': a['t_edit']}

        # Transform the data and count items
        try:
            # Update annotation
            data = json.loads(a['json'])
            (data, stats) = transform_annot(data, np.eye(3))
            _do_update_annot(task, slide_id, data, stats, metadata=metadata)            

            # Report
            print("Annot %d, slide %s: Successfully imported!" % (i, a['slide_name']))

        except:
            print("Annot %d, slide %s: JSON parse error" % (i, a['slide_name']))


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
        dims = sr.get_dims()
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
        dims = sr.get_dims()
        if dims is None:
            raise ValueError("Missing slide dimensions information")

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


# Export annotation
@click.command('annot-copy-to-task')
@click.argument('source_task', type=click.INT)
@click.argument('target_task', type=click.INT)
@click.option('-o', '--overwrite', help='Overwrite existing annotations if they exist', 
              type=click.BOOL, default=False)
@with_appcontext
def annot_copy_to_task_cmd(source_task, target_task, overwrite):
    db=get_db()
    p_src,t_src = get_task_data(source_task)
    p_trg,t_trg = get_task_data(target_task)
    if t_src['mode'] != 'annot' or t_trg['mode'] != 'annot':
        print('Both tasks must be annotation tasks')
        return
    
    # Find a list of all slides that have annotations in the source task and 
    # are also present in the target task
    rc = db.execute('SELECT SA.*, EM.*, SATARG.n_paths + SATARG.n_markers as ntarg '
                    'FROM task_slide_info TT '
                    'LEFT JOIN annot SA on SA.slide_id = TT.id '
                    'LEFT JOIN edit_meta EM on SA.meta_id = EM.id '
                    'LEFT JOIN annot SATARG on SATARG.task_id = TT.task_id and SATARG.slide_id = TT.id '
                    'WHERE TT.task_id=? AND SA.task_id = ?',
                    (target_task, source_task))
    for a in rc.fetchall():
        slide_id = a['slide_id']
        if not overwrite and a['ntarg'] is not None and a['ntarg'] > 0:
            print(f'Annot for slide {slide_id} already exists!')
            continue

        data = json.loads(a['json'])
        (data, stats) = transform_annot(data, np.eye(3))
        metadata = {'creator': a['creator'], 'editor': a['editor'], 
                    't_create': a['t_create'], 't_edit': a['t_edit']}
        _do_update_annot(target_task, slide_id, data, stats, metadata=metadata)            
        print(f'Annot for slide {slide_id} Successfully imported!')


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
@click.option('--min-sroi', type=click.INT, help="List slides with sampling ROIs only")
@click.option('-C', '--csv', type=click.File('wt'), help="Write results to CSV file")
@with_appcontext
def slides_list_cmd(task, specimen, block, section, slide, stain,
        min_paths, min_markers, min_sroi, csv):
    """List slides in a task"""
    generate_detailed_slide_listing(task, specimen, block, section, slide, stain,
                                    min_paths, min_markers, min_sroi, csv)

def init_app(app):
    app.cli.add_command(import_annot_cmd)
    app.cli.add_command(export_annot_svg)
    app.cli.add_command(export_annot_vtk)
    app.cli.add_command(slides_list_cmd)
    app.cli.add_command(export_task_annot_cmd)
    app.cli.add_command(import_task_annot_cmd)
    app.cli.add_command(annot_copy_to_task_cmd)


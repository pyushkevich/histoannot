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
from flask import (
    Blueprint, g, redirect, render_template, request, make_response, current_app, send_file, abort, Response, url_for, session
)
from werkzeug.exceptions import abort
from .auth import \
    access_project_read, access_project_write, access_project_admin, \
    access_task_read, access_task_write
from .db import get_db

# TODO: these should be moved to another module
from .project_cli import get_task_data, create_edit_meta, update_edit_meta_to_current
from .project_ref import ProjectRef
from .delegate import find_delegate_for_slide
from .dzi import get_osl, get_patch, pil_to_nifti_gz
from .slide import make_slide_dbview, annot_sample_path_curves, get_affine_matrix_by_slideid
from .slideref import get_slide_ref

import json
import time
import io
import os
import urllib
import random
import colorsys
from PIL import Image, ImageDraw
import pandas
import sys
import math
import numpy as np

from . import slide

from math import sqrt

import click
from flask.cli import cli, with_appcontext

from jsonschema import validate


bp = Blueprint('dltrain', __name__)

# Index

# TODO: labelsets are not exclusive to dltrain, so this should reside in its own py file


# Get a table of labels in a labelset with counts for the current task/slide
@bp.route('/dltrain/task/<int:task_id>/slide/<int:slide_id>/labelset/table/json', methods=('GET',))
@access_task_read
def get_labelset_labels_table_json(task_id, slide_id):
    db = get_db()

    project,task = get_task_data(task_id)

    if task['mode'] == 'dltrain':
        ll = db.execute('SELECT L.*, COUNT(T.id) as n_samples '
                        'FROM label L LEFT JOIN training_sample T '
                        '             ON T.label = L.id AND T.task=? AND T.slide=? '
                        '             LEFT JOIN labelset_info LS on L.labelset = LS.id '
                        'WHERE LS.name=? AND LS.project=?'
                        'GROUP BY L.id '
                        'ORDER BY L.id', 
                        (task_id, slide_id, task['dltrain']['labelset'], project))
    elif task['mode'] == 'sampling':
        ll = db.execute('SELECT L.*, COUNT(SR.id) as n_samples '
                        'FROM label L LEFT JOIN sampling_roi SR '
                        '             ON SR.label = L.id AND SR.task=? AND SR.slide=? '
                        '             LEFT JOIN labelset_info LS on L.labelset = LS.id '
                        'WHERE LS.name=? AND LS.project=?'
                        'GROUP BY L.id '
                        'ORDER BY L.id', 
                        (task_id, slide_id, task['sampling']['labelset'], project))

    if ll:
        ll_data = [dict(row) for row in ll.fetchall()]    
        return json.dumps(ll_data)
    else:
        abort(Response('Incompatible task mode', 401))


# Get a table of labels in a labelset with counts for the current task/slide
@bp.route('/dltrain/task/<int:task_id>/slide/<int:slide_id>/labelset/table', methods=('GET',))
@access_task_read
def get_labelset_labels_table(task_id, slide_id):
    db = get_db()

    project,task = get_task_data(task_id)

    ll = db.execute('SELECT L.*, COUNT(T.id) as n_samples '
                    'FROM label L LEFT JOIN training_sample T '
                    '             ON T.label = L.id AND T.task=? AND T.slide=? '
                    'GROUP BY L.id '
                    'ORDER BY L.id', 
                    (task_id, slide_id))

    ll_data = [dict(row) for row in ll.fetchall()]
    
    return render_template('dbtrain/label_table.html', labels = ll_data, task_id=task_id, slide_id=slide_id)


# Shared function to perform sample/sampling ROI listing
def server_side_object_listing(task_id, db_view, r):
    db = get_db()
    db.set_trace_callback(print)

    # Get the current task data
    project,task = get_task_data(task_id)

    # Run a query to count the total number of samples to return
    n_total = db.execute(
        """SELECT COUNT(T.id) as n 
           FROM {} T 
           WHERE T.task=?""".format(db_view), (task_id,)).fetchone()['n']

    # Do we have a global search query
    if len(r['search']['value']) > 0:
        # Create search clause for later
        search_fields = ['id','specimen_name','block_name','stain','label_name','creator','editor']
        search_inner = ' OR '.join([f'T.{k} LIKE ?' for k in search_fields])
        search_clause = f'AND ({search_inner})'
        search_pat = '%' + r['search']['value'] + '%'
        search_items = tuple(search_pat for k in search_fields)

        # Run search clause to get number of filtered entries
        n_filtered = db.execute(
            """SELECT COUNT(T.id) as n 
               FROM {} T 
               WHERE T.task=? {}""".format(db_view, search_clause), 
               (task_id,) + search_items).fetchone()['n']
    else:
        search_clause, search_items = '', ()
        n_filtered = n_total

    # Field to order by
    if 'order' in r and len(r['order']) > 0:
        order_column = r['order'][0]['column']
        order_column_name = r['columns'][order_column]['data']
        order_clause = "{} {}".format(
            order_column_name,
            {'asc':'ASC','desc':'DESC'}[r['order'][0]['dir']])
    else:
        order_clause = 'SIN({:d}+id)'.format(r['random_key'])

    # Which page to extract
    paging_start = r.get('start', 0)
    paging_length = r.get('length', 1000)

    # Run the main query
    samples = db.execute(
        """SELECT *, SIN(? + id) as random_index
           FROM {} T
           WHERE T.task = ? {}
           ORDER BY {} LIMIT {:d},{:d}""".format(db_view,search_clause,order_clause,paging_start,paging_length), 
           (int(r['random_key']), task_id) + search_items).fetchall()

    # Build return json
    x = {
        'draw' : r['draw'],
        'recordsTotal': n_total,
        'recordsFiltered': n_filtered,
        'data': [dict(row) for row in samples]
    }

    db.set_trace_callback(None)
    return json.dumps(x)


# Complete sample listing 
@bp.route('/api/task/<int:task_id>/samples', methods=('POST',))
@access_task_read
def task_sample_listing(task_id):

    # Map the request to json
    r = json.loads(request.get_data().decode('UTF-8'))

    # Call helper function
    return server_side_object_listing(task_id, 'training_sample_info', r)



# Complete listing of slides in a task
@bp.route('/task/<int:task_id>/samples')
@access_task_read
def task_all_samples(task_id):

    # Get the current task data
    project,task = get_task_data(task_id)
    pr = ProjectRef(project)
    return render_template('dbtrain/task_sample_listing.html',
                           project=project, project_name=pr.disp_name,
                           task=task, task_id=task_id)


# Get a table of recently created annotations
@bp.route('/dltrain/task/<int:task_id>/slide/<int:slide_id>/label/<int:label_id>/samples/table/json', methods=('POST','GET'))
@access_task_read
def get_samples_for_label(task_id, slide_id, label_id):
    db = get_db()

    # Which order to use
    order = {"newest":"M.t_edit ASC", 
             "oldest":"M.t_edit DESC", 
             "random":"RANDOM()"}[request.form['sort'] if 'sort' in request.form else 'newest']

    # Where clause for selecting slides
    if 'which' not in request.form or request.form['which'] == 'slide':
        where_clause = 'label = ? and T.slide = ? and task = ?'
        where_arg = (label_id, slide_id, task_id)
    elif request.form['which'] == 'block':
        where_clause = 'label = ? and task = ? and S.block_id = (SELECT block_id FROM slide where id=?)'
        where_arg = (label_id, task_id, slide_id)
    elif request.form['which'] == 'specimen':
        where_clause = '''label = ? and task = ? and S.block_id IN (
                              SELECT id FROM block BB where BB.specimen = (
                                  SELECT specimen FROM block B left join slide SS on B.id=SS.block_id
                                  WHERE SS.id=?))'''
        where_arg = (label_id, task_id, slide_id)
    else:
        where_clause = 'label = ? and task = ?'
        where_arg = (label_id, task_id)

    # Run query
    query = '''SELECT T.id,T.have_patch,x0,y0,x1,y1,
                      UC.username as creator, t_create,
                      UE.username as editor, t_edit,
                      datetime(t_create,'unixepoch','localtime') as dt_create,
                      datetime(t_edit,'unixepoch','localtime') as dt_edit,
                      S.specimen_display as specimen_name,S.block_name,S.section,S.slide,S.stain,S.id as slide_id
               FROM training_sample T LEFT JOIN edit_meta M on T.meta_id = M.id 
                                      LEFT JOIN task_slide_info S on S.id = T.slide and S.task_id = T.task
                                      LEFT JOIN user UC on UC.id = M.creator 
                                      LEFT JOIN user UE on UE.id = M.editor 
               WHERE %s 
               ORDER BY %s LIMIT 48''' % (where_clause, order)
    rc = db.execute(query, where_arg)
    return json.dumps([dict(row) for row in rc.fetchall()])


@bp.route('/dltrain/task/<int:task_id>/slide/<int:slide_id>/samples/table', methods=('GET',))
@access_task_read
def get_samples_for_label_table(task_id, slide_id):

    return render_template('dbtrain/sample_table.html', task_id=task_id, slide_id=slide_id, label_id=1)


@bp.route('/dltrain/task/<int:task_id>/sample/<int:sample_id>/nav', methods=('GET',))
@access_task_read
def navigate_to_sample(task_id, sample_id):
    db = get_db()

    # Look up the sample in the database
    rc = db.execute('''select slide, (x0+x1)/2 as cx, (y0+y1)/2 as cy 
                       from training_sample
                       where id==? and task==?''', (sample_id, task_id)).fetchone()

    if rc:
        session['slide_view_sample_data'] = { 'sample_id' : sample_id, 'sample_cx' : rc['cx'], 'sample_cy' : rc['cy'] }
        return redirect(url_for('slide.slide_view', 
            task_id=task_id, slide_id=rc['slide'], resolution='raw', affine_mode=False))


@bp.route('/dltrain/task/<int:task_id>/slide/<int:slide_id>/labelset/picker', methods=('GET',))
@access_task_read
def get_labelset_labels_picker(task_id, slide_id):

    return render_template('dbtrain/label_picker.html', task_id=task_id, slide_id=slide_id)


def get_labelset_id(project, task):
    db=get_db()
    return db.execute('SELECT id FROM labelset_info WHERE name=? AND project=?',
                       (task['dltrain']['labelset'],project)).fetchone()['id']



def get_label_id_in_task(task_id, label_name):

    db=get_db()
    project,task = get_task_data(task_id)
    ls_id = get_labelset_id(project, task)

    # Look up the label
    label_id = db.execute('SELECT id FROM label WHERE name=? AND labelset=?', (label_name, ls_id)).fetchone()['id'];

    return label_id


# Return the name of the labelset in a task
@bp.route('/api/task/<int:task_id>/labelset')
def get_labelset_for_task(task_id):
    db=get_db()
    project,task = get_task_data(task_id)
    labelset, lsid = None, None
    if task['mode'] in ('sampling', 'dltrain'):
        labelset = task[task['mode']]['labelset']
        lsid = db.execute('SELECT id FROM labelset_info WHERE name=? AND project=?',
                          (labelset, project)).fetchone()['id']
    return json.dumps({'name': labelset, 'id': lsid})


@bp.route('/dltrain/<project>/add_labelset', methods=('POST',))
@access_project_write
def add_labelset(project):

    # Read form
    print(request.form)

    ls_name = request.form['name']
    ls_desc = request.form.get('desc', None)
    print(f'Adding labelset {ls_name} with description {ls_desc} to project {project}')

    # Create a new labelset
    db=get_db()
    lsid = db.execute('INSERT INTO labelset(name,description) VALUES (?,?)',
                      (ls_name, ls_desc)).lastrowid

    # Associate with the project
    db.execute('INSERT INTO project_labelset(project,labelset_name,labelset_id) VALUES(?,?,?)',
               (project,ls_name,lsid))

    db.commit()
    return json.dumps({"id": lsid})


@bp.route('/dltrain/task/<int:task_id>/labelset/addlabel', methods=('POST',))
@access_task_write
def add_labelset_label(task_id):

    db = get_db()
    project,task = get_task_data(task_id)
    ls_id = get_labelset_id(project, task)

    label_name = request.form['label_name'];
    color = request.form['label_color'];
    desc = request.form['label_desc'];
    label_id = db.execute(
        'INSERT INTO label (name, labelset, description, color) VALUES (?,?,?,?)',
        (label_name, ls_id, desc, color)).lastrowid
    db.commit()
    return json.dumps({"id":label_id})


@bp.route('/dltrain/api/<project>/label/<int:label_id>/update', methods=('POST',))
@access_project_admin
def project_update_label(project, label_id):
    db = get_db()
    db.execute("UPDATE label SET name=?, description=?, color=? WHERE id=?",
               (request.form['name'], request.form['desc'], request.form['color'], label_id))
    db.commit()
    return json.dumps({"status": "ok"})


@bp.route('/dltrain/api/<project>/label/<int:label_id>/delete', methods=('POST',))
@access_project_admin
def project_delete_label(project, label_id):
    db = get_db()

    # When deleting label, the number of samples must be zero (for now, later maybe relax this)
    nsam = db.execute('SELECT count(id) as n FROM training_sample WHERE label=?',(label_id,)).fetchone()['n']
    if nsam > 0:
        return json.dumps({"status": "failed", "reason": "in_use"})
    else:
        db.execute("DELETE FROM label WHERE id=?",(label_id,))
        db.commit()
        return json.dumps({"status": "ok"})


@bp.route('/dltrain/api/<project>/labelset/<int:labelset_id>/add_label', methods=('POST',))
@access_project_admin
def project_labelset_add_label(project, labelset_id):
    db = get_db()

    # Generate a random color
    r = lambda a,b : random.uniform(a,b)
    clr = [int(x*255) for x in colorsys.hsv_to_rgb(r(0,1), r(0.5,1.0), r(0.5,1.0))]
    clr_html = '#%02x%02x%02x' % tuple(clr)

    label_id = db.execute(
        'INSERT INTO label (name, labelset, description, color) VALUES (?,?,?,?)',
        ('dummy', labelset_id, 'New Label', clr_html)).lastrowid

    db.execute("UPDATE label SET name=? WHERE id=?",
               ("Label %d" % (label_id,), label_id))

    db.commit()
    return json.dumps({"id":label_id})




# Get a listing of labelsets with statistics
@bp.route('/dltrain/api/<project>/labelsets', methods=('GET',))
@access_project_read
def get_project_labelset_listing(project):
    db = get_db()

    # List all the labelsets with counts of labels and samples
    rc = db.execute("""
        select LS.id as id, LS.name as name, LS.description as description,
               count(L.id) as n_labels, 
               sum(STAT.N) as n_samples 
        from labelset_info LS left join label L on L.labelset=LS.id
                              left join (
                                  select L.id,count(TS.id) as N
                                  from label L left join training_sample TS on L.id=TS.label 
                                  group by L.id
                                  ) STAT on L.id=STAT.id
        where project=? group by LS.id order by LS.name""", (project,))

    # Return the json dump of the listing
    return json.dumps([dict(x) for x in rc.fetchall()])


@bp.route('/dltrain/api/<project>/labelset/<int:lset>/labels', methods=('GET',))
@access_project_read
def get_labelset_label_listing(project, lset):
    db = get_db()

    rc = db.execute("""
        select L.id, L.name, L.description, L.color, TSSTAT.N as n_samples, SRSTAT.N as n_sampling_rois
        from label L left join (select L.id,count(TS.id) as N
                                from label L left join training_sample TS on L.id=TS.label 
                                group by L.id) TSSTAT on L.id=TSSTAT.id
                     left join (select L.id,count(SR.id) as N
                                from label L left join sampling_roi SR on L.id=SR.label 
                                group by L.id) SRSTAT on L.id=SRSTAT.id
        where L.labelset=? order by L.id""", (lset,))

    # Return the json dump of the listing
    return json.dumps([dict(x) for x in rc.fetchall()])


# Check sample validity
def check_rect(task_id, rect):

    project,t_data = get_task_data(task_id)
    min_size = t_data['dltrain'].get('min-size')
    max_size = t_data['dltrain'].get('max-size')

    w = round(abs(rect[2] - rect[0]))
    h = round(abs(rect[3] - rect[1]))

    print('Checking %d %d against %d %d' % (w,h,min_size,max_size))

    if min_size is not None and (w < min_size or h < min_size):
        abort(Response('Box is too small', 401))

    if max_size is not None and (w > max_size or h > max_size):
        abort(Response('Box is too large', 401))
    

@bp.route('/task/<int:task_id>/slide/<int:slide_id>/dltrain/sample/create', methods=('POST',))
@access_task_write
def create_sample(task_id, slide_id):

    data = json.loads(request.get_data())
    rect = data['geometry']
    label_id = data['label_id']

    check_rect(task_id, rect)

    return create_sample_base(task_id, slide_id, label_id, rect)


@bp.route('/task/<int:task_id>/slide/<int:slide_id>/dltrain/samples', methods=('GET',))
@access_task_read
def get_samples(task_id, slide_id):
    db = get_db()
    ll = db.execute(
        'SELECT S.*, M.creator, M.editor, M.t_create, M.t_edit, L.color '
        'FROM training_sample S LEFT JOIN label L on S.label = L.id '
        '                       LEFT JOIN edit_meta M on S.meta_id = M.id '
        'WHERE S.slide=? and S.task=? ORDER BY M.t_edit DESC ',
        (slide_id, task_id));
    return json.dumps([dict(row) for row in ll.fetchall()])


@bp.route('/task/<int:task_id>/slide/<int:slide_id>/dltrain/sample/delete', methods=('POST',))
@access_task_write
def delete_sample(task_id, slide_id):
    sample_id = int(request.form['id'])
    db = get_db()

    # Delete the meta record
    db.execute('DELETE FROM edit_meta WHERE id IN '
               '(SELECT meta_id FROM training_sample WHERE id=?)',
               (sample_id,))

    # Delete the sample
    db.execute('DELETE FROM training_sample WHERE id=?', (sample_id,))

    # Commit
    db.commit()

    # Delete the patch image
    try:
        os.remove(get_sample_patch_filename(sample_id))
    except:
        print('Error removing file %s' % (get_sample_patch_filename(sample_id),))

    return "success"


# TODO: login_required is insufficient here
@bp.route('/task/<int:task_id>/slide/<int:slide_id>/dltrain/sample/update', methods=('POST',))
@access_task_write
def update_sample(task_id, slide_id):
    data = json.loads(request.get_data())
    rect = data['geometry']
    sample_id = data['id'];

    db = get_db()

    # Update the metadata
    # Get existing properties
    rc = db.execute('SELECT meta_id,task,x0,x1,y0,y1 FROM training_sample WHERE id=?',
            (sample_id,)).fetchone()

    # If the rectangle size changed, make sure it is within task specification
    sz_old = float(rc['x1']) - float(rc['x0']), float(rc['y1']) - float(rc['y0'])
    sz_new = rect[2]-rect[0], rect[3]-rect[1]
    print('SIZE COMPARISON *********** ', sz_old, sz_new)
    if (sz_old != sz_new):
        check_rect(rc['task'], rect)

    print(f'META-ID: {rc["meta_id"]}')

    update_edit_meta_to_current(rc['meta_id'])

    # Update the main record
    db.execute(
        'UPDATE training_sample '
        'SET x0=?, y0=?, x1=?, y1=?, label=?, have_patch=0 '
        'WHERE id=?',
        (rect[0], rect[1], rect[2], rect[3], data['label_id'], sample_id))

    db.commit()

    # Save an image patch around the sample
    generate_sample_patch(slide_id, sample_id, rect)

    return "success"


@bp.route('/dltrain/api/sample/<int:id>/image.png', methods=('GET',))
def get_sample_png(id):

    # Get the local filename
    fn=get_sample_patch_filename(id)

    # Serve the image
    return send_file(fn)


@bp.route('/dltrain/api/sample/<int:id>/<int:level>/image_<int:w>_<int:h>.png')
def get_sample_custom_png(id, level, w, h):

    # Get the slide reference
    db = get_db()
    sample = db.execute('SELECT * FROM training_sample WHERE id=?', (id,)).fetchone()
    slide_id = sample['slide']
    sr = get_slide_ref(slide_id)

    # Get the identifiers for the slide
    # TODO: what to do with project name here
    (project, specimen, block, slide_name, slide_ext) = sr.get_id_tuple()

    # Are we delegating this slide to a different node?
    del_url = find_delegate_for_slide(slide_id)

    # Compute the parameters
    ctr_x = int((sample['x0'] + sample['x1']) / 2.0 + 0.5)
    ctr_y = int((sample['y0'] + sample['y1']) / 2.0 + 0.5)
    x = ctr_x - ((w - int(w/2.0)) - 1)
    y = ctr_y - ((h - int(h/2.0)) - 1)

    # If local, call the method directly
    rawbytes = None
    if del_url is None:
        rawbytes = get_patch(project,slide_id,'raw',
                             level, ctr_x,ctr_y,w,h,'png').data
    else:
        url = '%s/dzi/patch/%s/raw/%d/%d_%d_%d_%d.png' % (
                del_url, project, slide_id, 
                level, ctr_x, ctr_y, w, h)
        pr = sr.get_project_ref()
        post_data = urllib.urlencode({'project_data': json.dumps(pr.get_dict())})
        rawbytes = urllib.request.urlopen(url, post_data).read()

    resp = make_response(rawbytes)
    resp.mimetype = 'image/%s' % format
    return resp
    

# Get the filename where a sample should be saved
def get_sample_patch_filename(sample_id):

    # Create a directory
    patch_dir = os.path.join(current_app.instance_path, 'dltrain/patches')
    if not os.path.exists(patch_dir):
        os.makedirs(patch_dir)

    # Generate the filename
    patch_fn = "sample_%08d.png" % (sample_id,)

    # Get the full path
    return os.path.join(patch_dir, patch_fn)


# Get the filename where a sample should be saved
def get_sampling_roi_patch_filename(sampling_roi_id):

    # Create a directory
    patch_dir = os.path.join(current_app.instance_path, 'dltrain/patches')
    if not os.path.exists(patch_dir):
        os.makedirs(patch_dir)

    # Generate the filename
    patch_fn = "sampling_roi_%08d.png" % (sampling_roi_id,)

    # Get the full path
    return os.path.join(patch_dir, patch_fn)


# Generate an image patch for a sample
def generate_sample_patch(slide_id, sample_id, rect, dims=(512,512), level=0):

    # Find out which machine the slide is currently being served from
    # Get the tiff image from which to sample the region of interest
    db = get_db()
    sr = get_slide_ref(slide_id)

    # Get the identifiers for the slide
    # TODO: what to do with project here?
    (project, specimen, block, slide_name, slide_ext) = sr.get_id_tuple()

    # Are we de this slide to a different node?
    del_url = find_delegate_for_slide(slide_id)

    # Compute the parameters
    ctr_x = int((rect[0] + rect[2])/2.0 + 0.5)
    ctr_y = int((rect[1] + rect[3])/2.0 + 0.5)
    (w,h) = dims
    x = ctr_x - ((w - int(w/2.0)) - 1)
    y = ctr_y - ((h - int(h/2.0)) - 1)

    # If local, call the method directly
    rawbytes = None
    if del_url is None:
        rawbytes = get_patch(project,slide_id,'raw',
                             level,ctr_x,ctr_y,w,h,'png').data
    else:
        url = '%s/dzi/patch/%s/%d/raw/%d/%d_%d_%d_%d.png' % (
                del_url, project, slide_id,  
                level, ctr_x, ctr_y, w, h)
        pr = sr.get_project_ref()
        post_data = urllib.urlencode({'project_data': json.dumps(pr.get_dict())})
        rawbytes = urllib.request.urlopen(url, post_data).read()

    # Save as PNG
    with open(get_sample_patch_filename(sample_id), 'wb') as f:
        f.write(rawbytes)

    # Record that patch has been written
    db.execute('UPDATE training_sample SET have_patch=1 where id=?', (sample_id,))
    db.commit()


# Callable function for creating a sample
def create_sample_base(task_id, slide_id, label_id, rect, osl_level=0, metadata={}):

    project,t_data = get_task_data(task_id)

    db = get_db()

    # Create a meta record
    meta_id = create_edit_meta(**metadata)

    # Create the main record
    sample_id = db.execute(
        'INSERT INTO training_sample (meta_id,x0,y0,x1,y1,label,slide,task) VALUES (?,?,?,?,?,?,?,?)',
        (meta_id, rect[0], rect[1], rect[2], rect[3], label_id, slide_id, task_id)
    ).lastrowid

    # Get the preferred patch size
    patch_dim = t_data['dltrain'].get('display-patch-size', 512)
    
    # Generate the patch
    generate_sample_patch(slide_id, sample_id, rect, (patch_dim, patch_dim), osl_level)

    # Only commit once this has been saved
    db.commit()

    # Return the sample id and the patch generation job id
    return json.dumps({ 'id' : sample_id })


# For safety, any JSON inserted into the database should be validated
# A schema against which the JSON is validated
sampling_roi_schema = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": [ "trapezoid", "polygon" ]},
        "data": {
            "type": "array",
            "minItems": 2,
            "items": { 
                "type": "array", 
                "minItems": 2,
                "maxItems": 3, 
                "items": {"type": "number"} } } },
    "required": ["type", "data"]
}


# Compute a bounding box for an ROI
def compute_sampling_roi_bounding_box(geom_data):
    if geom_data.get('type') == 'trapezoid':
        # Read the coordinates
        [ [x0, y0, w0], [x1, y1, w1] ] = geom_data['data']
        
        # Compute the corner vertices
        

        # Compute the normal vector
        nx, ny = y1 - y0, x0 - x1
        nlen = sqrt(nx * nx + ny * ny)
        nx, ny = nx/nlen, ny/nlen

        # Compute the corners
        xmin, ymin = x0 - w0 * nx, y0 - w0 * ny 
        xmin, ymin = min(xmin, x0 + w0 * nx), min(ymin, y0 + w0 * ny)
        xmin, ymin = min(xmin, x1 - w1 * nx), min(ymin, y1 - w1 * ny)
        xmin, ymin = min(xmin, x1 + w1 * nx), min(ymin, y1 + w1 * ny)
        xmax, ymax = x0 - w0 * nx, y0 - w0 * ny 
        xmax, ymax = max(xmax, x0 + w0 * nx), max(ymax, y0 + w0 * ny)
        xmax, ymax = max(xmax, x1 - w1 * nx), max(ymax, y1 - w1 * ny)
        xmax, ymax = max(xmax, x1 + w1 * nx), max(ymax, y1 + w1 * ny)

        return [xmin, ymin, xmax, ymax]
    elif geom_data.get('type') == 'polygon':
        # Read the vertex coordinates
        verts = np.array(geom_data['data'])

        # Compute bounding box with numpy/
        (xmin, ymin) = np.min(verts, axis=0)
        (xmax, ymax) = np.max(verts, axis=0)
        return [xmin, ymin, xmax, ymax]

        # Iterate over polygon edges to compute center of mass
        # edges = list(zip(verts,np.roll(verts,-1)))


# Callable function for creating a sampling ROI
def create_sampling_roi_base(task_id, slide_id, label_id, geom_data, osl_level=0, metadata={}):

    # Validate the JSON
    if geom_data.get('type') in ('trapezoid', 'polygon'):
        validate(instance=geom_data, schema=sampling_roi_schema)
    else:
        raise ValueError('Unknown or missing sampling roi type')

    # Put JSON in the database
    _,t_data = get_task_data(task_id)
    db = get_db()

    # Create a meta record
    meta_id = create_edit_meta(**metadata)

    # Compute the bounding box 
    [x0, y0, x1, y1] = compute_sampling_roi_bounding_box(geom_data)

    # Create the main record
    roi_id = db.execute(
        'INSERT INTO sampling_roi (meta_id,x0,y0,x1,y1,json,label,slide,task) VALUES (?,?,?,?,?,?,?,?,?)',
        (meta_id, x0, y0, x1, y1, json.dumps(geom_data), label_id, slide_id, task_id)
    ).lastrowid

    # Commit
    db.commit()

    # Return the sample id and the patch generation job id
    return json.dumps({ 'id' : roi_id })


# Apply arbitrary transform (lambda function) to sampling ROI
def spatial_transform_roi(geom_data, fn_transform):
    
    # Load the JSON
    if geom_data.get('type') == 'trapezoid':
        
        # Read the coordinates
        [ [x1, y1, w1], [x2, y2, w2] ] = geom_data['data']
        
        # Calculate the angle and the distance between the midpoints
        angle = math.atan2(y2 - y1, x2 - x1)

        # Calculate the base direction
        u,v = math.cos(angle + math.pi / 2), math.sin(angle + math.pi / 2)

        # Calculate the four corners of the trapezoid and transform them
        x3, y3 = fn_transform((x1 + w1 * u, y1 + w1 * v))
        x4, y4 = fn_transform((x1 - w1 * u, y1 - w1 * v))
        x5, y5 = fn_transform((x2 + w2 * u, y2 + w2 * v))
        x6, y6 = fn_transform((x2 - w2 * u, y2 - w2 * v))
        
        # Compute the new center points
        x1_t, y1_t = (x4 + x3) / 2., (y4 + y3) / 2. 
        x2_t, y2_t = (x6 + x5) / 2., (y6 + y5) / 2.
        
        # Compute the new base widths
        w1_t = math.sqrt((x4-x3)**2+(y4-y3)**2) / 2.
        w2_t = math.sqrt((x6-x5)**2+(y6-y5)**2) / 2.
        
        # Recompute the widths using the determinant
        geom_data['data'] = [ [ x1_t, y1_t, w1_t ], [ x2_t, y2_t, w2_t ] ]
        
    elif geom_data.get('type') == 'polygon':
        
        # Transform each vertex
        geom_data['data'] = [ fn_transform(v) for v in geom_data['data'] ]
        
    return geom_data


# Apply affine transform to sampling ROI payload
def affine_transform_roi(geom_data, M):
    
    # Extract matrix components
    (A,b) = (M[0:2,0:2], M[0:2,2])
    fn_transform = lambda pos : (np.dot(A, np.array(pos)) + b).tolist()
    return spatial_transform_roi(geom_data, fn_transform)
        

@bp.route('/task/<int:task_id>/slide/<mode>/<resolution>/<int:slide_id>/dltrain/sampling_roi/create', methods=('POST',))
@access_task_write
def create_sampling_roi(task_id, mode, resolution, slide_id):
    # Affine transform to apply 
    M_inv = get_affine_matrix_by_slideid(slide_id, mode, resolution)

    print(request.get_data())
    data = json.loads(request.get_data())
    geom_data = affine_transform_roi(data['geometry'], M_inv)
    if geom_data.get('type') in ('trapezoid', 'polygon'):
        validate(instance=geom_data, schema=sampling_roi_schema)
    else:
        raise ValueError('Unknown or missing sampling roi type')

    label_id = data['label_id']
    return create_sampling_roi_base(task_id, slide_id, label_id, geom_data)


@bp.route('/task/<int:task_id>/slide/<mode>/<resolution>/<int:slide_id>/dltrain/sampling_roi/update', methods=('POST',))
@access_task_write
def update_sampling_roi(task_id, mode, resolution, slide_id):

    # Affine transform to apply 
    M_inv = get_affine_matrix_by_slideid(slide_id, mode, resolution)

    # Validate the JSON
    data = json.loads(request.get_data())
    geom_data = affine_transform_roi(data['geometry'], M_inv)
    if geom_data.get('type') in ('trapezoid', 'polygon'):
        validate(instance=geom_data, schema=sampling_roi_schema)
    else:
        raise ValueError('Unknown or missing sampling roi type')
    
    # Get the roi id and label id
    roi_id, label_id = data['id'], data['label_id']

    # Put JSON in the database
    _,t_data = get_task_data(task_id)
    db = get_db()

    # Update the metadata for this ROI
    rc = db.execute('SELECT meta_id FROM sampling_roi WHERE id=?', (roi_id,)).fetchone()
    update_edit_meta_to_current(rc['meta_id'])

    # Update the geometry
    [x0, y0, x1, y1] = compute_sampling_roi_bounding_box(geom_data)

    # Create the main record
    db.execute(
        'UPDATE sampling_roi '
        'SET x0=?, y0=?, x1=?, y1=?, json=?, label=? ' 
        'WHERE id=?',
        (x0, y0, x1, y1, json.dumps(geom_data), label_id, roi_id))

    # Commit
    db.commit()

    # Return the sample id and the patch generation job id
    return json.dumps({ 'id' : roi_id })


@bp.route('/task/<int:task_id>/slide/<int:slide_id>/dltrain/sampling_roi/delete', methods=('POST',))
@access_task_write
def delete_sampling_roi(task_id, slide_id):

    # Validate the JSON
    data = json.loads(request.get_data())
    roi_id = data['id']

    # Delete the meta record
    db = get_db()
    db.execute('DELETE FROM edit_meta WHERE id IN '
               '(SELECT meta_id FROM sampling_roi WHERE id=?)',
               (roi_id,))

    # Delete the sample
    db.execute('DELETE FROM sampling_roi WHERE id=?', (roi_id,))

    # Commit
    db.commit()

    return "success"


# Common command to delete sampling rois on a slide
def do_delete_sampling_rois_on_slide(task, slide_id):
    # Delete the meta record
    db = get_db()
    db.execute('DELETE FROM edit_meta WHERE id IN '
               '(SELECT meta_id FROM sampling_roi WHERE task=? AND slide=?)',
               (task, slide_id))

    # Delete the sample
    rc = db.execute('DELETE FROM sampling_roi WHERE task=? AND slide=?', (task, slide_id))
    print(f'Deletion successful, {rc.rowcount} sampling ROIs affected')

    # Commit
    db.commit()


# Command to delete sampling ROIs for a single slide in a task
@bp.route('/task/<int:task_id>/slide/<int:slide_id>/dltrain/sampling_roi/delete_all', methods=('GET','POST'))
@access_task_write
def sampling_roi_delete_on_slice(task_id, slide_id):
    """Delete all the sampling ROIs on a selected slide."""
    do_delete_sampling_rois_on_slide(task_id, slide_id)
    return "success"

            

@bp.route('/task/<int:task_id>/slide/<mode>/<resolution>/<int:slide_id>/sampling_roi/get', methods=('GET',))
@access_task_read
def get_sampling_rois(task_id, mode, resolution, slide_id):
    db = get_db()
    ll = db.execute(
        'SELECT SR.*, M.creator, M.editor, M.t_create, M.t_edit, L.color '
        'FROM sampling_roi SR LEFT JOIN label L on SR.label = L.id '
        '                     LEFT JOIN edit_meta M on SR.meta_id = M.id '
        'WHERE SR.slide=? and SR.task=? ORDER BY M.t_edit DESC ',
        (slide_id, task_id))
    
    # Get the affine transform to apply to the sampling ROIs
    M = np.linalg.inv(get_affine_matrix_by_slideid(slide_id, mode, resolution))
    
    # Apply the transformation to the JSON
    result = []
    for row in ll.fetchall():
        d = dict(row)
        d['json'] = json.dumps(affine_transform_roi(json.loads(d['json']), M))
        result.append(d)
    
    return json.dumps(result)


# Define a function to draw a trapezoid on an image
def draw_trapezoid(image, x1, y1, x2, y2, w1, w2, sx, sy, fill=None, outline=None):
    # Calculate the angle and the distance between the midpoints
    angle = math.atan2(y2 - y1, x2 - x1)

    # Calculate the base direction
    u,v = math.cos(angle + math.pi / 2), math.sin(angle + math.pi / 2)

    # Calculate the four corners of the trapezoid
    x3, y3 = x1 + w1 * u, y1 + w1 * v
    x4, y4 = x1 - w1 * u, y1 - w1 * v
    x5, y5 = x2 + w2 * u, y2 + w2 * v
    x6, y6 = x2 - w2 * u, y2 - w2 * v

    # Draw the trapezoid on the image using the label as the color
    draw = ImageDraw.Draw(image)
    draw.polygon([(x3*sx,y3*sy),(x4*sx,y4*sy),(x6*sx,y6*sy),(x5*sx,y5*sy)], fill=fill, outline=outline)


# Draw a polygon on the image
def draw_polygon(image, verts, sx, sy, fill=None, outline=None):
    draw = ImageDraw.Draw(image)
    draw.polygon([(x * sx, y * sy) for (x,y) in verts], fill=fill, outline=outline)


# Draw a sampling ROI
def draw_sampling_roi(image, geom_data, sx, sy, fill=None, outline=None):
    if geom_data.get('type') == 'trapezoid':
        # Read the coordinates
        [ [x1, y1, w1], [x2, y2, w2] ] = geom_data['data']
        draw_trapezoid(image, x1, y1, x2, y2, w1, w2, sx, sy, fill=fill, outline=outline)
    elif geom_data.get('type') == 'polygon':
        draw_polygon(image, geom_data['data'], sx, sy, fill=fill, outline=outline)


@bp.route('/task/<int:task_id>/slide/<int:slide_id>/sampling_roi/make_image_<int:maxdim>.nii.gz', methods=('GET',))
@access_task_read
def make_sampling_roi_image(task_id, slide_id, maxdim):
    # Get all the sampling ROIs on this slide
    db = get_db()
    ll = db.execute(
        'SELECT * FROM sampling_roi '
        'WHERE slide=? and task=? ORDER BY id',
        (slide_id, task_id))

    # Get the slide dimensions from OpenSlide - this is slower than using metadata but
    # closer to the source and ensures that the image generated matches the thumbnail        
    sr = get_slide_ref(slide_id)
    os = get_osl(slide_id, sr)
    thumb = os.get_thumbnail((maxdim, maxdim))

    # Calculate the scale factor to fit the maximum dimension
    W,H = os.dimensions
    sx,sy = thumb.size[0] / W, thumb.size[1] / H

    # Create an empty image with the desired output dimensions and mode L (grayscale)
    image = Image.new('L', (thumb.size[0], thumb.size[1]))
    for row in ll.fetchall():
        geom_data, label = json.loads(row['json']), row['label']
        if geom_data.get('type') == 'trapezoid':
            # Read the coordinates
            [ [x1, y1, w1], [x2, y2, w2] ] = geom_data['data']
            draw_trapezoid(image, x1, y1, x2, y2, w1, w2, sx, sy, fill=label)
        elif geom_data.get('type') == 'polygon':
            draw_polygon(image, geom_data['data'], sx, sy, fill=label)

    # Generate a nifti image
    print('shape: ', np.array(image, dtype=np.uint8).shape)
    mpp = sr.get_pixel_spacing('raw')
    spacing = [ os.dimensions[d] * mpp[d] / image.size[d] for d in (0,1) ]
    data = pil_to_nifti_gz(image, spacing) 
    resp = make_response(data)
    resp.mimetype = 'application/octet-stream'
    return resp


@bp.route('/task/<int:task_id>/sampling_roi/<int:sroi_id>/thumb.png', methods=('GET',))
@access_task_read
def make_thumbnail_for_sampling_roi(task_id, sroi_id):

    # Get this sampling ROI
    db = get_db()
    sroi_row = db.execute('SELECT * FROM sampling_roi WHERE id=? and task=?', (sroi_id, task_id)).fetchone()
    slide_id = sroi_row['slide']

    # Get the cached thumbnail for the slide
    sr = get_slide_ref(slide_id)
    thumb_png = sr.get_local_copy('thumb')

    # Load the thumbnail
    thumb = Image.open(thumb_png)

    # Calculate the scale factor to fit the maximum dimension
    W,H = sr.get_dims()
    sx,sy = thumb.size[0] / W, thumb.size[1] / H

    # Draw the sampling ROI
    geom_data, label = json.loads(sroi_row['json']), sroi_row['label']

    # Get the color for this label
    color = db.execute('SELECT color FROM label WHERE id=?', (label,)).fetchone()['color']

    if geom_data.get('type') == 'trapezoid':
        # Read the coordinates
        [ [x1, y1, w1], [x2, y2, w2] ] = geom_data['data']
        draw_trapezoid(thumb, x1, y1, x2, y2, w1, w2, sx, sy, outline=color)
    elif geom_data.get('type') == 'polygon':
        draw_polygon(image, geom_data['data'], sx, sy, outline=color)

    # Crop the image around the center
    cx, cy = (x1+x2) / 2, (y1+y2) / 2
    thumb = thumb.crop((cx*sx-128,cy*sx-128,cx*sx+127,cy*sx+127))

    # Return the image as a PNG
    buf = io.BytesIO()
    thumb.save(buf, 'PNG')
    resp = make_response(buf.getvalue())
    resp.mimetype = 'image/png'
    return resp


# --------------------------------
# Web pages
# --------------------------------
@bp.route('/dltrain/<project>/labelsets')
@access_project_admin
def labelset_editor(project):

    # Read the project ref
    pr = ProjectRef(project)

    # Render the entry page
    return render_template('dbtrain/labelset_editor.html',
                           project=project,
                           disp_name=pr.disp_name, labelset=None)


# --------------------------------
# Sample import and export
# --------------------------------

# A function to create a temporary view of the samples
def make_dbview_full(view_name):
    db=get_db()
    db.execute("""CREATE TEMP VIEW %s AS
                  SELECT T.*, S.specimen_private, S.block_name, L.name as label_name,
                         UC.username as creator, UE.username as editor,
                         EM.t_create, EM.t_edit, S.slide_name, S.stain
                  FROM training_sample T
                      INNER JOIN edit_meta EM on EM.id = T.meta_id
                      INNER JOIN user UC on UC.id = EM.creator
                      INNER JOIN user UE on UE.id = EM.editor
                      INNER JOIN task_slide_info S on T.slide = S.id and T.task = S.task_id
                      INNER JOIN label L on T.label = L.id""" % (view_name,))
    

# Cache slide metadata for CSV operations
class SlideMetadataCache:

    def __init__(self, project_ref):
        self._dict = {}
        self.pr = project_ref

    def get_meta(self, slide_id):
        slide_meta = self._dict.get(slide_id)
        if slide_meta is None:
            sr = get_slide_ref(slide_id, self.pr)
            slide_meta = {}
            try:
                slide_meta = {
                    'mpp': sr.get_pixel_spacing('raw'),
                    'dims': sr.get_dims()
                }
            except:
                slide_meta = { 'mpp': None, 'dims': None }
            self._dict[slide_id] = slide_meta
        return slide_meta

    def mpp(self, slide_id):
        slide_meta = self.get_meta(slide_id)
        slide_mpp = slide_meta['mpp']
        return slide_mpp if slide_mpp is not None else [None, None]

    def dims(self, slide_id):
        slide_meta = self.get_meta(slide_id)
        slide_mpp = slide_meta['dims']
        return slide_mpp if slide_mpp is not None else [None, None]


def samples_generate_csv(task, fout, list_metadata = False, list_ids = False, list_block = False, list_mpp = False, header = True):

    db = get_db()

    # Get the project reference
    project,_ = get_task_data(task)
    pr = ProjectRef(project)

    # Create the full view
    make_dbview_full('v_full')

    # Select keys to export
    keys = ('slide_name', 'label_name', 'x', 'y', 'w', 'h')
    if list_metadata:
        keys = keys + ('t_create', 'creator', 't_edit', 'editor')
    if list_block:
        keys = keys + ('specimen_private', 'block_name', 'stain')
    if list_ids:
        keys = ('id',) + keys

    # Run query
    rc = db.execute(
        'SELECT *, x0 as x, y0 as y, x1-x0 as w, y1-y0 as h FROM v_full '
        'WHERE task=? ORDER BY id', (task,))

    if header:
        hdr_keys = keys
        if list_mpp:
            hdr_keys = hdr_keys + ('mpp_x', 'mpp_y', 'dim_x', 'dim_y')
        fout.write(','.join(hdr_keys) + '\n')

    # Keep track of slide dimensions info
    mpp_cache = SlideMetadataCache(pr)
    for row in rc.fetchall():
        vals = list(map(lambda a: str(row[a]), keys))
        if list_mpp:
            id = row['slide']
            (mpp, dims) = (mpp_cache.mpp(id), mpp_cache.dims(id))
            vals = vals + [str(x) for x in [ mpp[0], mpp[1], dims[0], dims[1] ] ]
        fout.write(','.join(vals) + '\n')


@bp.route('/dltrain/api/task/<int:task_id>/samples/manifest.csv', methods=('GET',))
@access_task_read
def get_sample_manifest_for_task(task_id):
    fout = io.StringIO()
    samples_generate_csv(task_id, fout, list_metadata=True, list_ids=True, list_block=True, list_mpp=True)
    return Response(fout.getvalue(), mimetype='text/csv')


@click.command('samples-export-csv')
@click.argument('task')
@click.argument('output_file')
@click.option('--header/--no-header', default=False, help='Include header in output CSV file')
@click.option('--metadata/--no-metadata', default=False, help='Include metadata in output CSV file')
@click.option('--specimen/--no-specimen', default=False, help='Include specimen ids in output CSV file')
@click.option('--ids/--no-ids', default=False, help='Include sample database ids in output CSV file')
@click.option('--mpp/--no-mpp', default=False, help='Include microns per pixel info in output CSV file')
@with_appcontext
def samples_export_csv_command(task, output_file, header, metadata, specimen, ids, mpp):
    """Export all training samples in a task to a CSV file"""
    with open(output_file, 'wt') as fout:
        samples_generate_csv(task, fout,
                             list_metadata=metadata, list_ids=ids,
                             list_block=specimen, list_mpp=mpp, header=header)


@click.command('samples-import-csv')
@click.argument('task')
@click.argument('input_file', type=click.File('rt'))
@click.option('-u','--user', help='User name under which to insert samples')
@click.option('-f','--force', is_flag=True, help='Proceed with import even if errors found')
@click.option('-o','--max-overlap', type=click.FLOAT, default=0.99,
              help='Maximum overlap allowed with existing samples')
@with_appcontext
def samples_import_csv_command(task, input_file, user, force, max_overlap):
    """Import training samples from a CSV file"""
    db = get_db()
    df = pandas.read_csv(input_file)

    # If user is specified, get their id and substitute for missing samples
    user_id = None
    if user:
        rc = db.execute('SELECT * FROM user WHERE username=?', (user,)).fetchone()
        if rc:
            user_id = rc['id']
        else:
            print('Specified user ID does not exist')
            sys.exit(-1)

    # Look up the labelset for the current task
    project,tdata = get_task_data(task)
    if not 'dltrain' in tdata:
        print('Task %s is not the right type for importing samples' % tdata['name'])
        return -1

    lsid = get_labelset_id(project, tdata)

    # To speed up validation, check for missing slides, missing labels, missing users
    validation = True

    slide_map = {}
    for slide in df['slide_name'].unique():
        rc = db.execute('SELECT id FROM slide WHERE slide_name=?', (slide,)).fetchone()
        if rc is None:
            print('Slide %s does not exist' % (slide,))
            validation = False
        else:
            slide_map[slide] = rc['id']

    label_map = {}
    for label in df['label_name'].unique():
        rc = db.execute('SELECT id FROM label WHERE name=? AND labelset=?', (label,lsid)).fetchone()
        if rc is None:
            print('Label %s does not exist in task' % (label,))
            validation = False
        else:
            label_map[label] = rc['id']

    user_map = {}
    uc = df['creator'].unique() if 'creator' in df else []
    ue = df['editor'].unique() if 'editor' in df else []
    for uname in list(set(uc) | set(ue)):
        rc = db.execute('SELECT id FROM user WHERE username=? AND disabled=FALSE', (uname,)).fetchone()
        if rc is None:
            print('User %s not in the system' % (uname,))
            validation = False
        else:
            user_map[uname] = rc['id']

    # Stop if not passing validation
    if not validation and not force:
        print('Failed validation')
        sys.exit(1)

    # Add the samples one by one
    for index, row in df.iterrows():

        # Create a data record
        x,y,w,h = row['x'],row['y'],row['w'],row['h']
        rect = (float(x),float(y),float(x)+float(w),float(y)+float(h))

        # Look up the ids
        slide_id = slide_map.get(row['slide_name'], None)
        label_id = label_map.get(row['label_name'], None)
        creator_id = user_map.get(row['creator'], user_id if 'creator' not in df.columns else None)
        editor_id = user_map.get(row['editor'],  user_id if 'editor' not in df.columns else None)

        # If one of the ids is missing, skip this sample
        if not slide_id or not label_id or not creator_id or not editor_id:
            print('Skipping sample %d due to missing data ' % (index,))
            continue

        # Perform a query to measure overlap
        rci = db.execute(
                'SELECT max(min(x1,?)-max(x0,?),0) * max(min(y1,?)-max(y0,?),0) as a_intercept, '
                '       (x1-x0) * (y1-y0) as a_sample, id '
                'FROM training_sample '
                'WHERE task=? and slide=? and a_intercept > 0 ',
                (rect[2], rect[0], rect[3], rect[1], task, slide_id)).fetchall() 

        if len(rci) > 0:
            my_max_overlap, my_max_overlap_sample = 0, 0
            for dbrow in rci:
                dice = 2.0 * dbrow['a_intercept'] / (dbrow['a_sample'] + w * h)
                if dice > my_max_overlap:
                    my_max_overlap, my_max_overlap_sample = dice, dbrow['id']
            if my_max_overlap > max_overlap:
                print('Sample %d overlap with existing sample %d is too high: %f' % 
                        (index, my_max_overlap_sample, my_max_overlap))
                continue

        # Set up the metadata
        metadata = { 'creator': creator_id, 'editor': editor_id }

        # Append the timestamp if available
        if 't_create' in df.columns:
            metadata['t_create'] = row['t_create']
        if 't_edit' in df.columns:
            metadata['t_edit'] = row['t_edit']

        # Create the sample
        result = json.loads(create_sample_base(task, slide_id, label_id, rect, metadata=metadata))

        # Success 
        print('Imported new sample %d from line %d' % (result['id'], index))


# Command to delete samples based on a set of filters
def delete_samples(
        task, creator, editor, specimen, block, slide, 
        label, newer, older, yes, viewname='v_full'):

    # Create a temporary view of the big join table
    db=get_db()
    make_dbview_full(viewname)

    # Build up a where clause
    w = [('creator LIKE ?', creator),
         ('editor LIKE ?', editor),
         ('specimen_private LIKE ?', specimen),
         ('block_name LIKE ?', block),
         ('slide = ?', slide),
         ('label_name = ?', label),
         ('task = ?', task),
         ('t_create > ?', time.mktime(newer.timetuple()) if newer is not None else None),
         ('t_create < ?', time.mktime(older.timetuple()) if older is not None else None)]

    # Filter out the missing entries
    w_sql = [x[0] for x in w if x[1] is not None]
    w_prm = [x[1] for x in w if x[1] is not None]

    # List the ids we would select
    rc=db.execute('SELECT * FROM %s WHERE %s' % (viewname,' AND '.join(w_sql)), w_prm)
    result = rc.fetchall()
    if len(result) == 0:
        print('No entries can be deleted')
        return

    ids = [ x[0] for x in result ]

    # Prompt for confirmation
    if not yes:
        while True:
            reply = str(raw_input('%d samples would be deleted. Are you sure? [Y/n]: ' % len(ids))).strip();
            if reply == 'n':
                return
            elif reply == 'Y':
                break

    # Delete all the meta entries
    rc = db.execute("""DELETE FROM edit_meta WHERE id IN 
                       (SELECT meta_id FROM %s WHERE %s)""" % (viewname,' AND '.join(w_sql)), w_prm)
    print('Removed %d rows from edit_meta' % rc.rowcount)

    # Delete all the sample entries
    rc = db.execute("""DELETE FROM training_sample WHERE id IN
                       (SELECT id FROM %s WHERE %s)""" % (viewname,' AND '.join(w_sql)), w_prm)
    print('Removed %d rows from training_sample' % rc.rowcount)

    # Delete all the image samples
    for sample_id in ids:
        try:
            os.remove(get_sample_patch_filename(sample_id))
        except:
            print('Error removing file %s' % (get_sample_patch_filename(sample_id),))


@click.command('samples-delete')
@click.argument('task')
@click.option('--creator', help='Only delete samples created by user username')
@click.option('--editor', help='Only delete samples edited by user username')
@click.option('--specimen', help='Specify a specimen name whose samples to delete')
@click.option('--block', help='Specify a block name whose samples to delete')
@click.option('--slide', help='Specify a slide id whose samples to delete')
@click.option('--label', help='Specify a label name whose samples to delete')
@click.option('--newer', help='Only delete samples created after date', type=click.DateTime())
@click.option('--older', help='Only delete samples created before date', type=click.DateTime())
@click.option('-y','--yes', help='Do not prompt before deleting', is_flag=True)
@with_appcontext
def samples_delete_cmd(task, creator, editor, specimen, block, slide, label, newer, older, yes):
    """Delete training samples for TASK"""

    delete_samples(task, creator, editor, specimen, block, slide, label, newer, older, yes)
    get_db().commit()
                

# Fix missing pngs for samples
@click.command('samples-fix-missing-patches')
@click.argument('task')
@with_appcontext
def samples_fix_patches_cmd(task):
    """Generate missing patches for the samples in the database"""

    # Get a list of all patches relevant to us, sorted by slide so we don't have to
    # sample slides out of order
    make_dbview_full('v_full')
    db=get_db()
    rc = db.execute(
            'SELECT * FROM v_full '
            'WHERE task=? ORDER BY slide_name', (task,)).fetchall()

    # Get the required patch dimensions
    (project,t_data)=get_task_data(task)
    patch_dim = t_data['dltrain'].get('display-patch-size', 512)

    # For each patch check if it is there
    for row in rc:
        id=row['id']
        fn=get_sample_patch_filename(id)
        if os.path.exists(fn):
            w,h = Image.open(fn).size
            if w == patch_dim and h == patch_dim:
                continue
        print('Missing or corrupt patch for sample %d, %s' % (id,fn))

        rect=(float(row['x0']), float(row['y0']), float(row['x1']), float(row['y1']))

        # Generate the patch
        generate_sample_patch(row['slide'], id, rect, dims=(patch_dim, patch_dim))


# Sample boxes from curves command
@click.command('samples-random-from-annot')
@click.argument('task-annot')
@click.argument('task-dltrain')
@click.argument('label')
@click.argument('box-size', type=click.INT)
@click.option('-c', '--clobber', is_flag=True, default=False, help="Delete existing samples")
@click.option('-n', '--num-samples', type=click.INT, default=10, help="Number of samples per slide")
@click.option('-r', '--random_shift', type=click.FLOAT, default=0.0, 
        help="StDev of random displacment to apply to samples, in pixel units")
@click.option('-u','--user', help='User name under which to insert samples', required=True)
@click.option('-s', '--specimen', help="Restrict to a single specimen")
@with_appcontext
def samples_random_from_annot_cmd(
        task_annot, task_dltrain, label, box_size, 
        clobber, num_samples, random_shift, user, specimen):
    """Randomly generate samples from path annotations. This is used to 
       generate ROIs at random in certain regions, e.g., gray matter."""

    # Find all the slides with path annotations in the source task
    db=get_db()
    make_slide_dbview(int(task_annot), 'v_full')

    if specimen is not None:
        rc = db.execute('SELECT id FROM v_full WHERE n_paths > 0 AND specimen_private=?', (specimen,)).fetchall()
    else:
        rc = db.execute('SELECT id FROM v_full WHERE n_paths > 0').fetchall()

    # Look up the user
    g.user = db.execute('SELECT * FROM user WHERE username=?', (user,)).fetchone()
    if g.user is None:
        print('User %s is not in the system' % user)
        return -1

    # Look up the label
    label_id = get_label_id_in_task(task_dltrain, label)

    # Create another view for the target task
    make_slide_dbview(int(task_dltrain), 'v_dest')

    # Process each of the slices
    for row in rc:
        # Get all the paths from slide
        slide_id = row['id']

        # Check that the slide is in the destination task
        rc_dest = db.execute('SELECT * FROM v_dest WHERE id=?', (slide_id,)).fetchone()
        if rc_dest is None:
            print('Skipping slide %d because it is not in the destination task' % (slide_id,))
            continue

        # Get the JSON for the task
        rc = db.execute('SELECT json FROM annot WHERE slide_id=? AND task_id=?',
                (slide_id, task_annot)).fetchone()
        annot = json.loads(rc['json'])
        sam = annot_sample_path_curves(annot, 100)
        print("Slide %d" % slide_id)

        # Join the curve samples
        allsam = []
        [ allsam.extend(el) for el in sam] 

        # Randomly shuffle the samples
        random.shuffle(allsam)

        # Pick the first n samples
        if num_samples < len(allsam):
            allsam = allsam[0:num_samples]

        # If there are samples already on this slide, delete them
        if clobber:
            delete_samples(task_dltrain,None,None,None,None,slide_id,label,
                    None,None,True,'v_del_%d' % slide_id)

        # Apply random shift to each sample
        for p in allsam:
            p[0]=p[0]+random.gauss(0,1) * random_shift
            p[1]=p[1]+random.gauss(0,1) * random_shift
            rect=(p[0]-box_size//2,p[1]-box_size//2,p[0]+box_size//2-1,p[1]+box_size//2-1)
            s_id = json.loads(create_sample_base(task_dltrain, slide_id, label_id, rect, osl_level=1))['id']
            print('Created sample %d in slide %d at (%f,%f)' % (s_id, slide_id, p[0], p[1]))


# Command to delete sampling ROIs for a single slide in a task
@click.command('sampling-roi-delete-on-slide')
@click.argument('task', type=click.INT)
@click.argument('slide_id', type=click.INT)
@with_appcontext
def sampling_roi_delete_on_slice_cmd(task, slide_id):
    """Delete all the sampling ROIs on a selected slide."""
    do_delete_sampling_rois_on_slide(task, slide_id)


def sampling_roi_generate_csv(task, fout, 
        list_metadata = False, list_ids = False, list_block = False, 
        list_mpp = False, header = True, list_url = False):
    db = get_db()

    # Get the project reference
    project,_ = get_task_data(task)
    pr = ProjectRef(project)

    # Select keys to export
    keys = ('slide_name', 'slide', 'label_name', 'x0', 'x1', 'y0', 'y1')
    if list_metadata:
        keys = keys + ('t_create', 'creator', 't_edit', 'editor')
    if list_block:
        keys = keys + ('specimen_private', 'block_name', 'stain')
    if list_ids:
        keys = ('id',) + keys

    # Run query
    rc = db.execute(
        'SELECT SR.*,SI.*,EM.*,L.name AS label_name FROM sampling_roi SR '
        '   LEFT JOIN slide_info SI ON SR.slide=SI.id '
        '   LEFT JOIN edit_meta EM ON EM.id=SR.meta_id '
        '   LEFT JOIN label L ON L.id = SR.label '
        'WHERE task=? ORDER BY SR.id', (task,))

    if header:
        hdr_keys = keys
        if list_mpp:
            hdr_keys = hdr_keys + ('mpp_x', 'mpp_y', 'dim_x', 'dim_y')
        if list_url:
            hdr_keys = hdr_keys + ('url',)
        fout.write(','.join(hdr_keys) + '\n')

    # Keep track of slide dimensions info
    mpp_cache = SlideMetadataCache(pr)

    for row in rc.fetchall():
        vals = list(map(lambda a: str(row[a]), keys))
        id = row['slide']
        if list_mpp:
            (mpp, dims) = (mpp_cache.mpp(id), mpp_cache.dims(id))
            vals = vals + [str(x) for x in [ mpp[0], mpp[1], dims[0], dims[1] ] ]
        if list_url:
            # This code does not work, not sure why, hacked for an hour
            # url = current_app.url_for('slide.slide_view',
            #         task_id=task, slide_id=row['slide'], resolution='raw', affine_mode='raw')
            server = current_app.config['HISTOANNOT_PUBLIC_URL']
            url = f'{server}/task/{task}/slide/{row["slide"]}/view/raw/raw'
            cx = int((float(row['x0']) + float(row['x1'])) // 2)
            cy = int((float(row['y0']) + float(row['y1'])) // 2)
            url = f'{url}?sample_id={row["id"]}&sample_cx={cx}&sample_cy={cy}'
            vals.append(url)
        fout.write(','.join(vals) + '\n')


@bp.route('/dltrain/api/task/<int:task_id>/sampling_roi/manifest.csv', methods=('GET',))
@access_task_read
def get_sampling_roi_manifest_for_task(task_id):
    fout = io.StringIO()
    current_app.config['SERVER_NAME'] = current_app.config['HISTOANNOT_PUBLIC_URL']
    sampling_roi_generate_csv(task_id, fout, list_metadata=True, list_ids=True, list_block=True, list_mpp=True)
    return Response(fout.getvalue(), mimetype='text/csv')


@click.command('samping-roi-export-csv')
@click.argument('task')
@click.argument('output_file')
@click.option('--header/--no-header', default=False, help='Include header in output CSV file')
@click.option('--metadata/--no-metadata', default=False, help='Include metadata in output CSV file')
@click.option('--specimen/--no-specimen', default=False, help='Include specimen ids in output CSV file')
@click.option('--ids/--no-ids', default=False, help='Include sample database ids in output CSV file')
@click.option('--mpp/--no-mpp', default=False, help='Include microns per pixel info in output CSV file')
@click.option('--url/--no-url', default=False, help='Include box URL in output CSV file')
@with_appcontext
def sampling_roi_export_csv_command(task, output_file, header, metadata, specimen, ids, mpp, url):
    """Export all training samples in a task to a CSV file"""
    with open(output_file, 'wt') as fout:
        sampling_roi_generate_csv(task, fout,
                                  list_metadata=metadata, list_ids=ids,
                                  list_block=specimen, list_mpp=mpp, header=header, list_url=url)




# Complete sample listing 
@bp.route('/api/task/<int:task_id>/sampling-rois', methods=('POST',))
@access_task_read
def task_sampling_roi_listing(task_id):

    # Map the request to json
    r = json.loads(request.get_data().decode('UTF-8'))

    # Call helper function
    return server_side_object_listing(task_id, 'sampling_roi_info', r)


def init_app(app):
    app.cli.add_command(samples_import_csv_command)
    app.cli.add_command(samples_export_csv_command)
    app.cli.add_command(samples_delete_cmd)
    app.cli.add_command(samples_fix_patches_cmd)
    app.cli.add_command(samples_random_from_annot_cmd)
    app.cli.add_command(sampling_roi_delete_on_slice_cmd)
    app.cli.add_command(sampling_roi_export_csv_command)

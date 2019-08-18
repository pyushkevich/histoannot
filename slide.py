from flask import (
    Blueprint, flash, g, redirect, render_template, request, url_for, make_response, current_app, send_from_directory
)
from werkzeug.exceptions import abort

from flaskr.auth import login_required
from flaskr.db import get_db, get_slide_ref, SlideRef, get_task_data, update_edit_meta, create_edit_meta
from io import BytesIO

import os
import json
import time
import numpy as np

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


# The index
@bp.route('/')
@login_required
def index():

    # Output list of dicts
    t = []

    # List the available tasks (TODO: check user access to task)
    db = get_db()
    rc_task = db.execute('SELECT * FROM task ORDER BY id')
    for row in rc_task.fetchall():

        # Parse the json
        task = json.loads(row['json'])

        # Generate the where clause
        where = get_task_slide_where_clause(task)

        # Get the subset of stains to which the task applies
        print(where[1])
        stat = db.execute(
            'SELECT COUNT (S.id) as nslides, '
            '       COUNT(DISTINCT block_id) as nblocks, '
            '       COUNT(DISTINCT specimen_name) as nspecimens '
            'FROM slide S LEFT JOIN block B on S.block_id = B.id '
            'WHERE %s ' % (where[0],), 
            where[1]).fetchone()

        # Create a dict
        d = { 'id' : row['id'], 'name': task['name'], 'desc': task['desc'] }
        for key in ('nspecimens', 'nblocks', 'nslides'):
            d[key] = stat[key]

        t.append(d)

    # Render the template
    return render_template('slide/index.html', tasks=t)

# Task detail
@bp.route('/task/<int:task_id>')
@login_required
def task_detail(task_id):
    db = get_db()

    # Get the current task data
    task = get_task_data(task_id)

    # Generate the where clause
    where = get_task_slide_where_clause(task)

    # List all the blocks that meet requirements for the current task
    if task['mode'] == 'annot':

        # Join with the annotations table
        blocks = db.execute(
            'SELECT B.*, COUNT (S.id) as nslides, COUNT(A.slide_id) as nannot '
            'FROM slide S LEFT JOIN block B on S.block_id = B.id '
            '             LEFT JOIN annot A on A.slide_id = S.id AND A.task_id = ? '
            '                                  AND A.n_paths+A.n_markers > 0 '
            'WHERE %s '
            'GROUP BY B.id ORDER BY specimen_name, block_name' % where[0], 
            (task_id,) + where[1]).fetchall()

    elif task['mode'] == 'dltrain':

        # Join with the annotations table
        blocks = db.execute(
            'SELECT B.*, COUNT (DISTINCT S.id) as nslides, COUNT(T.id) as nsamples '
            'FROM slide S LEFT JOIN block B on S.block_id = B.id '
            '             LEFT JOIN training_sample T on T.slide = S.id AND T.task = ?'
            'WHERE %s '
            'GROUP BY B.id ORDER BY specimen_name, block_name' % where[0], 
            (task_id,) + where[1]).fetchall()

    elif task['mode'] == 'browse':
        blocks = db.execute(
            'SELECT B.*, COUNT (S.id) as nslides '
            'FROM slide S LEFT JOIN block B on S.block_id = B.id '
            '%s '
            'GROUP BY B.id ORDER BY specimen_name, block_name' % where[0], 
            where[1]).fetchall()

    # Mark specimens as odd and even
    last_spec = None
    oddevenmap = {}
    for b in blocks:
        if last_spec is None or b['specimen_name'] != last_spec:
            oddevenmap[b['specimen_name']] = len(oddevenmap) % 2
            last_spec = b['specimen_name']

    # For each block, count the number of annotations
    print(blocks)
    print(oddevenmap)
    return render_template('slide/task_detail.html', 
                           blocks=blocks, task=task, task_id=task_id, clr = oddevenmap)




# The block detail listing
@bp.route('/task/<int:task_id>/block/<int:block_id>/detail', methods=('GET', 'POST'))
@login_required
def block_detail(task_id, block_id):
    db = get_db()

    # Get the block descriptor
    block = db.execute('SELECT * FROM block WHERE id=?', (block_id,)).fetchone()

    # Get the current task data
    task = get_task_data(task_id)

    # Generate the where clause
    where = get_task_slide_where_clause(task)

    # List all the blocks that meet requirements for the current task
    if task['mode'] == 'annot':

        # Join with the annotations table
        slides = db.execute(
            'SELECT S.*, SUM(A.n_paths) as n_paths, SUM(A.n_markers) as n_markers '
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

        print(slides)

    return render_template('slide/block_detail.html', 
                           block=block, slides=slides, task_id = task_id, task=task)

# Get all the data needed for slide view/annotation/training
def get_slide_info(task_id, slide_id):
    db = get_db()

    # Get the info on the current slide
    slide_info = db.execute(
        'SELECT S.*, B.specimen_name, B.block_name'
        ' FROM block B join slide S on B.id = S.block_id'
        ' WHERE S.id = ?', (slide_id,)).fetchone()

    # Get the slide info
    block_id = slide_info['block_id']
    section = slide_info['section']
    slideno = slide_info['slide']

    # Get the task-specific where clause
    task = get_task_data(task_id)
    where = get_task_slide_where_clause(task)

    # Get the previous and next slides
    prev_slide = db.execute(
        'SELECT id FROM slide S'
        ' WHERE %s AND block_id=? AND section * 1000 + slide <= ? AND id != ?'
        ' ORDER BY section DESC, slide DESC limit 1' % where[0], 
        where[1] + (block_id, section * 1000 + slideno, slide_id)).fetchone()

    next_slide = db.execute(
        'SELECT id FROM slide S'
        ' WHERE %s AND block_id=? AND section * 1000 + slide >= ? AND id != ?'
        ' ORDER BY section ASC, slide ASC limit 1' % where[0], 
        where[1] + (block_id, section * 1000 + slideno, slide_id)).fetchone()

    return (slide_info, prev_slide, next_slide)



# The slide view
@bp.route('/task/<int:task_id>/slide/<int:slide_id>/view/<affine_mode>', methods=('GET', 'POST'))
@login_required
def slide_view(task_id, slide_id, affine_mode):
    # Get the current task data
    task = get_task_data(task_id)

    # Get the next/previous slides for this task
    (slide_info, prev_slide, next_slide) = get_slide_info(task_id, slide_id)

    # Build a dictionary to call
    context = {
            'slide_id': slide_id, 
            'slide_info': slide_info, 
            'next_slide':next_slide, 
            'prev_slide':prev_slide, 
            'affine_mode':affine_mode, 
            'seg_mode':task['mode'], 
            'task_id': task_id, 
            'task':task }

    # Add optional fields to context
    print(json.dumps(request.form, indent=4))
    for field in ('sample_id', 'sample_cx', 'sample_cy'):
        if field in request.form:
            context[field] = request.form[field]

    # Render the template
    return render_template('slide/slide_view.html', **context)


# Get the DZI for a slide
@bp.route('/slide/<mode>/<int:id>.dzi', methods=('GET', 'POST'))
@login_required
def dzi(mode, id):
    format = 'jpeg'

    # Get the raw SVS/tiff file for the slide (the resource should exist, 
    # or else we will spend a minute here waiting with no response to user)
    sr = get_slide_ref(id)
    tiff_file = sr.get_local_copy('raw', check_hash=True)

    # Get an affine transform if that is an option
    affine_file = sr.get_local_copy('affine', check_hash=True) if mode=='affine' else None
    
    try:
        slide = bp.cache.get(tiff_file, affine_file)
        slide.filename = os.path.basename(tiff_file)
        resp = make_response(slide.get_dzi('jpeg'))
        resp.mimetype = 'application/xml'
        return resp
    except (KeyError, ValueError):
        # Unknown slug
        abort(404)

# What percentage of the slide is available locally (in the cache)
@bp.route('/slide/api/<int:id>/cache_progress', methods=('GET', ))
@login_required
def get_cache_progress(id):

    sr = get_slide_ref(id)
    progress = sr.get_download_progress('raw');
    print("Progress: ", progress)
    return json.dumps({'progress':progress}), 200, {'ContentType':'application/json'} 




# Get an annotation filename for slide
def get_annot_json_file(task_id, slide_id):
    
    # Get the slide details
    db = get_db()
    si = db.execute(
        'SELECT s.*, b.specimen_name, b.block_name'
        ' FROM block b join slide s on b.id = s.block_id'
        ' WHERE s.id = ?', (slide_id,)).fetchone()

    # Generate a file
    json_filename = "annot_%s_%s_%s_%02d_%02d.json" % (si['specimen_name'], si['block_name'], si['stain'], si['section'], si['slide'])

    # Create a directory locally
    json_dir = os.path.join(current_app.instance_path, 'annot', 'task_%03d' % task_id)
    if not os.path.exists(json_dir):
        os.makedirs(json_dir)

    # Get the filename
    return os.path.join(json_dir, json_filename)

import numpy

# Get the affine matrix for a slide
def get_affine_matrix(slide_id, mode):

    if mode == 'affine':

        sr = get_slide_ref(slide_id)
        affine_fn = sr.get_local_copy('affine')
        if affine_fn is not None:
            M = np.loadtxt(affine_fn)
            return M

    return np.eye(3)


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
                        seg[k] = list(np.dot(A,seg[k]) + (b if k==0 else 0))

            # Increment the counters
            elif x[0] == 'PointText':
                x[1]['matrix'][4:6] = list(np.dot(A,x[1]['matrix'][4:6])+b)
                n_markers = n_markers + 1

    return (data, {'n_paths' : n_paths, 'n_markers' : n_markers})


# Receive updated json for the slide
@bp.route('/task/<int:task_id>/slide/<mode>/<int:slide_id>/annot/set', methods=('POST',))
@login_required
def update_annot_json(task_id, mode, slide_id):

    # Get the raw json
    data = json.loads(request.get_data())

    # Get the affine transform
    M_inv = get_affine_matrix(slide_id, mode)

    # Transform the data and count items
    (data, stats) = transform_annot(data, M_inv)

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
            (json.dumps(data), stats['n_paths'], stats['n_markers'], slide_id, task_id))

    else:

        # Create a new timestamp
        meta_id = create_edit_meta()

        # Insert a new row
        db.execute(
            'INSERT INTO annot(json, meta_id, n_paths, n_markers, slide_id, task_id) '
            'VALUES (?,?,?,?,?,?)', 
            (json.dumps(data), meta_id, stats['n_paths'], stats['n_markers'], slide_id, task_id))

    # Commit
    db.commit()

    # Also generate a file, just for backup purposes
    json_filename = get_annot_json_file(task_id, slide_id)

    with open(json_filename, 'w') as outfile:  
        json.dump(data, outfile)

    return json.dumps({'success':True}), 200, {'ContentType':'application/json'} 


# Send the json for the slide
@bp.route('/task/<int:task_id>/slide/<mode>/<int:slide_id>/annot/get', methods=('GET',))
@login_required
def get_annot_json(task_id, mode, slide_id):

    # Find the annotation in the database
    db = get_db()
    rc = db.execute('SELECT json FROM annot WHERE slide_id=? AND task_id=?',
                    (slide_id, task_id)).fetchone()

    # Get the affine transform
    M = np.linalg.inv(get_affine_matrix(slide_id, mode))

    # Return the data
    if rc is not None:
        data = json.loads(rc['json'])
        (data,stats) = transform_annot(data, M)
        return json.dumps(data), 200, {'ContentType':'application/json'} 
    else:
        return "", 200, {'ContentType':'application/json'} 

    

class PILBytesIO(BytesIO):
    def fileno(self):
        '''Classic PIL doesn't understand io.UnsupportedOperation.'''
        raise AttributeError('Not supported')

# Get the tiles for a slide
@bp.route('/slide/<mode>/<int:id>_files/<int:level>/<int:col>_<int:row>.<format>',  methods=('GET', 'POST'))
@login_required
def tile(mode, id, level, col, row, format):
    format = format.lower()
    if format != 'jpeg' and format != 'png':
        # Not supported by Deep Zoom
        return 'bad format'
        abort(404)

    # Get the raw SVS/tiff file for the slide (the resource should exist, 
    # or else we will spend a minute here waiting with no response to user)
    sr = get_slide_ref(id)
    tiff_file = sr.get_local_copy('raw')
    affine_file = sr.get_local_copy('affine') if mode == 'affine' else None
    tile = bp.cache.get(tiff_file, affine_file).get_tile(level, (col, row))

    buf = PILBytesIO()
    tile.save(buf, format, quality=75)
    resp = make_response(buf.getvalue())
    resp.mimetype = 'image/%s' % format
    return resp


# Serve up thumbnails
@bp.route('/slide/<int:id>/thumb', methods=('GET',))
def thumb(id):
    thumb_dir = os.path.join(current_app.instance_path, 'thumb')
    thumb_fn = "thumb%08d.png" % (id,)
    return send_from_directory(thumb_dir, thumb_fn, as_attachment=False)

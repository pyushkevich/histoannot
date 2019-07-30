from flask import (
    Blueprint, flash, g, redirect, render_template, request, url_for, make_response, current_app, send_from_directory, abort
)
from werkzeug.exceptions import abort
from flaskr.auth import login_required
from flaskr.db import get_db


import sqlite3
import json
import time
import os

from . import slide

bp = Blueprint('dltrain', __name__)

# Index
@bp.route('/dltrain/api/labelset', methods=('GET',))
@login_required
def list_labelsets():
    db = get_db()
    ls = db.execute('SELECT * from labelset')
    return json.dumps([dict(row) for row in ls.fetchall()])

@bp.route('/dltrain/api/labelset/<name>/labels', methods=('GET',))
@login_required
def list_labelset_labels(name):
    db = get_db()
    try:
        ll = db.execute('SELECT L.* FROM label L LEFT JOIN labelset LS ON L.labelset = LS.id WHERE LS.name=?', (name,))
        return json.dumps([dict(row) for row in ll.fetchall()])
    except:
        abort(404)

@bp.route('/dltrain/labelset/<name>/table', methods=('GET',))
@login_required
def get_labelset_labels_table(name):
    db = get_db()
    ll = db.execute('SELECT L.* FROM label L LEFT JOIN labelset LS ON L.labelset = LS.id WHERE LS.name=?', (name,))
    ll_data = [dict(row) for row in ll.fetchall()]
    return render_template('dbtrain/label_table.html', labels = ll_data, labelset_name = name)

@bp.route('/dltrain/labelset/<name>/picker', methods=('GET',))
@login_required
def get_labelset_labels_picker(name):
    return render_template('dbtrain/label_picker.html', labelset_name = name)

@bp.route('/dltrain/api/labelset/<name>/addlabel', methods=('POST',))
@login_required
def add_labelset_label(name):
    db = get_db()
    label_name = request.form['label_name'];
    color = request.form['label_color'];
    desc = request.form['label_desc'];
    set_id = db.execute('SELECT * FROM labelset WHERE name=?',(name,)).fetchone()['id']
    label_id = db.execute(
        'INSERT INTO label (name, labelset, description, color) VALUES (?,?,?,?)',
        (label_name, set_id, desc, color)).lastrowid
    db.commit()
    return json.dumps({"id":label_id})



# Get an annotation filename for slide
def get_annot_json_file(id):
    
    # Get the slide details
    db = get_db()
    si = db.execute(
        'SELECT s.*, b.specimen_name, b.block_name'
        ' FROM block b join slide s on b.id = s.block_id'
        ' WHERE s.id = ?', (id,)).fetchone()

    # Generate a file
    json_filename = "annot_%s_%s_%s_%02d_%02d.json" % (si['specimen_name'], si['block_name'], si['stain'], si['section'], si['slide'])

    # Create a directory locally
    json_dir = os.path.join(current_app.instance_path, 'annot')
    if not os.path.exists(json_dir):
        os.makedirs(json_dir)

    # Get the filename
    return os.path.join(json_dir, json_filename)

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


# Generate an image patch for a sample
def generate_sample_patch(slide_id, sample_id, rect):

    # Get the tiff image from which to sample the region of interest
    db = get_db()
    tiff_file = db.execute(
        'SELECT tiff_file FROM slide WHERE id=?', (slide_id,)).fetchone()['tiff_file']

    # Get the openslide object corresponding to it
    osr = slide.bp.cache.get(tiff_file, False)._osr;

    # Read the region centered on the box of size 512x512
    ctr_x = int((rect[0] + rect[2])/2.0 + 0.5)
    ctr_y = int((rect[1] + rect[3])/2.0 + 0.5)
    tile = osr.read_region((ctr_x - 255, ctr_y-255), 0, (512, 512));

    # Convert to PNG
    tile.save(get_sample_patch_filename(sample_id), 'png')



@bp.route('/dltrain/api/slide/<int:id>/sample/create', methods=('POST',))
@login_required
def create_sample(id):

    data = json.loads(request.get_data())
    rect = data['geometry']
    db = get_db()
    sample_id = db.execute(
        'INSERT INTO training_sample (tstamp,x0,y0,x1,y1,label,slide) VALUES (?,?,?,?,?,?,?)',
        (time.time(), rect[0], rect[1], rect[2], rect[3], data['label_id'], id)
    ).lastrowid
    db.commit();

    # Save an image patch around the sample
    generate_sample_patch(id, sample_id, rect)

    return json.dumps({"id":sample_id})

@bp.route('/dltrain/api/slide/<int:slide_id>/samples/labelset/<ls_name>', methods=('GET',))
@login_required
def get_samples(slide_id, ls_name):
    db = get_db()
    set_id = db.execute('SELECT * FROM labelset WHERE name=?',(ls_name,)).fetchone()['id']
    ll = db.execute(
        'SELECT S.*, L.color FROM training_sample S left join label L on S.label = L.id '
        'WHERE S.slide=? and L.labelset=? ORDER BY S.tstamp DESC ',
        (slide_id, set_id));
    return json.dumps([dict(row) for row in ll.fetchall()])


@bp.route('/dltrain/api/sample/delete', methods=('POST',))
@login_required
def delete_sample():
    sample_id = int(request.form['id'])
    db = get_db()
    db.execute('DELETE FROM training_sample WHERE id=?', (sample_id,))
    db.commit()

    # Delete the patch image
    try:
        os.remove(get_sample_patch_filename(sample_id))
    except:
        print('Error removing file %s' % (get_sample_patch_filename(sample_id),))

    return "success"

@bp.route('/dltrain/api/sample/update', methods=('POST',))
@login_required
def update_sample():
    data = json.loads(request.get_data())
    rect = data['geometry']
    sample_id = data['id'];
    print('UPDATE REQUEST', data)
    db = get_db()
    db.execute(
        'UPDATE training_sample SET tstamp=?, x0=?, y0=?, x1=?, y1=?, label=? WHERE id=?',
        (time.time(), rect[0], rect[1], rect[2], rect[3], data['label_id'], sample_id)
    )
    db.commit()

    # Save an image patch around the sample
    slide_id = db.execute('SELECT slide FROM training_sample WHERE id=?', 
                          (sample_id,)).fetchone()['slide']
    generate_sample_patch(slide_id, sample_id, rect)

    return "success"



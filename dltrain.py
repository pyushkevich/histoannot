from flask import (
    Blueprint, flash, g, redirect, render_template, request, url_for, make_response, current_app, send_file, abort
)
from werkzeug.exceptions import abort
from flaskr.auth import login_required
from flaskr.db import get_db, get_slide_ref, SlideRef, get_task_data, create_edit_meta, update_edit_meta, create_sample_base, generate_sample_patch, get_sample_patch_filename


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

# Get a table of labels in a labelset with counts for the current task/slide
@bp.route('/dltrain/task/<int:task_id>/slide/<int:slide_id>/labelset/table/json', methods=('GET',))
@login_required
def get_labelset_labels_table_json(task_id, slide_id):
    db = get_db()

    task = get_task_data(task_id)

    ll = db.execute('SELECT L.*, COUNT(T.id) as n_samples '
                    'FROM label L LEFT JOIN training_sample T '
                    '             ON T.label = L.id AND T.task=? AND T.slide=? '
                    'GROUP BY L.id '
                    'ORDER BY L.id', 
                    (task_id, slide_id))

    ll_data = [dict(row) for row in ll.fetchall()]
    
    return json.dumps(ll_data)

# Get a table of labels in a labelset with counts for the current task/slide
@bp.route('/dltrain/task/<int:task_id>/slide/<int:slide_id>/labelset/table', methods=('GET',))
@login_required
def get_labelset_labels_table(task_id, slide_id):
    db = get_db()

    task = get_task_data(task_id)

    ll = db.execute('SELECT L.*, COUNT(T.id) as n_samples '
                    'FROM label L LEFT JOIN training_sample T '
                    '             ON T.label = L.id AND T.task=? AND T.slide=? '
                    'GROUP BY L.id '
                    'ORDER BY L.id', 
                    (task_id, slide_id))

    ll_data = [dict(row) for row in ll.fetchall()]
    
    return render_template('dbtrain/label_table.html', labels = ll_data, task_id=task_id, slide_id=slide_id)

# Get a table of recently created annotations
@bp.route('/dltrain/task/<int:task_id>/slide/<int:slide_id>/label/<int:label_id>/samples/table/json', methods=('POST','GET'))
@login_required
def get_samples_for_label(task_id, slide_id, label_id):
    db = get_db()

    # list the samples
    rc = db.execute('SELECT T.id,x0,y0,x1,y1,creator,editor,t_create,t_edit '
                    'FROM training_sample T LEFT JOIN edit_meta M on T.meta_id = M.id '
                    'WHERE label = ? and slide = ? and task = ? '
                    'ORDER BY M.t_edit DESC LIMIT 48',
                    (label_id, slide_id, task_id))

    return json.dumps([dict(row) for row in rc.fetchall()])


@bp.route('/dltrain/task/<int:task_id>/slide/<int:slide_id>/samples/table', methods=('GET',))
@login_required
def get_samples_for_label_table(task_id, slide_id):

    return render_template('dbtrain/sample_table.html', task_id=task_id, slide_id=slide_id, label_id=1)



@bp.route('/dltrain/task/<int:task_id>/slide/<int:slide_id>/labelset/picker', methods=('GET',))
@login_required
def get_labelset_labels_picker(task_id, slide_id):

    return render_template('dbtrain/label_picker.html', task_id=task_id, slide_id=slide_id)

@bp.route('/dltrain/task/<int:task_id>/labelset/addlabel', methods=('POST',))
@login_required
def add_labelset_label(task_id):
    db = get_db()

    task = get_task_data(task_id)
    ls_id = db.execute('SELECT id FROM labelset WHERE name=?', (task['dltrain']['labelset'],)).fetchone()['id']

    label_name = request.form['label_name'];
    color = request.form['label_color'];
    desc = request.form['label_desc'];
    label_id = db.execute(
        'INSERT INTO label (name, labelset, description, color) VALUES (?,?,?,?)',
        (label_name, ls_id, desc, color)).lastrowid
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


@bp.route('/task/<int:task_id>/slide/<int:slide_id>/dltrain/sample/create', methods=('POST',))
@login_required
def create_sample(task_id, slide_id):

    data = json.loads(request.get_data())
    rect = data['geometry']
    label_id = data['label_id']
    sample_id = create_sample_base(task_id, slide_id, label_id, rect)

    return json.dumps({"id":sample_id})

@bp.route('/task/<int:task_id>/slide/<int:slide_id>/dltrain/samples', methods=('GET',))
@login_required
def get_samples(task_id, slide_id):
    db = get_db()
    ll = db.execute(
        'SELECT S.*, M.creator, M.editor, M.t_create, M.t_edit, L.color '
        'FROM training_sample S LEFT JOIN label L on S.label = L.id '
        '                       LEFT JOIN edit_meta M on S.meta_id = M.id '
        'WHERE S.slide=? and S.task=? ORDER BY M.t_edit DESC ',
        (slide_id, task_id));
    return json.dumps([dict(row) for row in ll.fetchall()])


@bp.route('/dltrain/api/sample/delete', methods=('POST',))
@login_required
def delete_sample():
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

@bp.route('/dltrain/api/sample/update', methods=('POST',))
@login_required
def update_sample():
    data = json.loads(request.get_data())
    rect = data['geometry']
    sample_id = data['id'];
    print('UPDATE REQUEST', data)
    db = get_db()

    # Update the metadata
    meta_id = db.execute('SELECT meta_id FROM training_sample WHERE id=?', 
                         (sample_id,)).fetchone()['meta_id']
    update_edit_meta(meta_id)

    # Update the main record
    db.execute(
        'UPDATE training_sample SET x0=?, y0=?, x1=?, y1=?, label=? WHERE id=?',
        (rect[0], rect[1], rect[2], rect[3], data['label_id'], sample_id))

    db.commit()

    # Save an image patch around the sample
    slide_id = db.execute('SELECT slide FROM training_sample WHERE id=?', 
                          (sample_id,)).fetchone()['slide']
    generate_sample_patch(slide_id, sample_id, rect)

    return "success"


@bp.route('/dltrain/api/sample/<int:id>/image.png', methods=('GET',))
@login_required
def get_sample_png(id):

    # Get the local filename
    fn=get_sample_patch_filename(id)

    # Serve the image
    return send_file(fn)
    




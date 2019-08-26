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
    Blueprint, flash, g, redirect, render_template, request, url_for, make_response, current_app, send_file, abort, Response
)
from werkzeug.exceptions import abort
from histoannot.auth import login_required
from histoannot.db import get_db, get_slide_ref, SlideRef, get_task_data, create_edit_meta, update_edit_meta
from histoannot.delegate import find_delegate_for_slide
from histoannot.dzi import get_patch

import sqlite3
import json
import time
import datetime
import os
import urllib2

from . import slide

from rq import Queue, Connection, Worker
from rq.job import Job, JobStatus
from redis import Redis

import click
from flask.cli import with_appcontext

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
                              SELECT id FROM block BB where BB.specimen_name = (
                                  SELECT specimen_name FROM block B left join slide SS on B.id=SS.block_id
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
                      BBB.specimen_name,BBB.block_name,S.section,S.slide,S.stain,S.id as slide_id
               FROM training_sample T LEFT JOIN edit_meta M on T.meta_id = M.id 
                                      LEFT JOIN slide S on S.id = T.slide 
                                      LEFT JOIN block BBB on BBB.id = S.block_id
                                      LEFT JOIN user UC on UC.id = M.creator 
                                      LEFT JOIN user UE on UE.id = M.editor 
               WHERE %s 
               ORDER BY %s LIMIT 48''' % (where_clause, order)
    rc = db.execute(query, where_arg)
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


# Check sample validity
def check_rect(task_id, rect):

    t_data = get_task_data(task_id)
    min_size = t_data['dltrain'].get('min-size')
    max_size = t_data['dltrain'].get('max-size')

    w = abs(rect[2] - rect[0])
    h = abs(rect[3] - rect[1])

    print('Checking %d %d against %d %d' % (w,h,min_size,max_size))

    if min_size is not None and (w < min_size or h < min_size):
        abort(Response('Box is too small', 401))

    if max_size is not None and (w > max_size or h > max_size):
        abort(Response('Box is too large', 401))


    

@bp.route('/task/<int:task_id>/slide/<int:slide_id>/dltrain/sample/create', methods=('POST',))
@login_required
def create_sample(task_id, slide_id):

    data = json.loads(request.get_data())
    rect = data['geometry']
    label_id = data['label_id']

    check_rect(task_id, rect)

    return create_sample_base(task_id, slide_id, label_id, rect)


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

    db = get_db()

    # Update the metadata
    # Get existing properties
    rc = db.execute('SELECT meta_id,task FROM training_sample WHERE id=?',
            (sample_id,)).fetchone()

    check_rect(rc['task'], rect)
    update_edit_meta(rc['meta_id'])

    # Update the main record
    db.execute(
        'UPDATE training_sample '
        'SET x0=?, y0=?, x1=?, y1=?, label=?, have_patch=0 '
        'WHERE id=?',
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
def generate_sample_patch(slide_id, sample_id, rect, dims=(512,512)):

    # Find out which machine the slide is currently being served from
    # Get the tiff image from which to sample the region of interest
    db = get_db()
    sr = get_slide_ref(slide_id)

    # Get the identifiers for the slide
    (specimen, block, slide_name, slide_ext) = sr.get_id_tuple()

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
        rawbytes = get_patch(specimen,block,slide_name,slide_ext,x,y,w,h,'png').data
    else:
        subs = (del_url, specimen, block, slide_name, slide_ext, x, y, w, h)
        url = '%s/dzi/patch/%s/%s/%s.%s/%d_%d_%d_%d.png' % subs
        print(url)
        rawbytes = urllib2.urlopen(url).read()

    # Save as PNG
    with open(get_sample_patch_filename(sample_id), 'wb') as f:
        f.write(rawbytes)

    # Record that patch has been written
    db.execute('UPDATE training_sample SET have_patch=1 where id=?', (sample_id,))
    db.commit()


# Callable function for creating a sample
def create_sample_base(task_id, slide_id, label_id, rect):

    db = get_db()

    # Create a meta record
    meta_id = create_edit_meta()

    # Create the main record
    sample_id = db.execute(
        'INSERT INTO training_sample (meta_id,x0,y0,x1,y1,label,slide,task) VALUES (?,?,?,?,?,?,?,?)',
        (meta_id, rect[0], rect[1], rect[2], rect[3], label_id, slide_id, task_id)
    ).lastrowid

    # Create a job that will sample the patch from the image. The reason we do this in a queue
    # is that a server hosting the slide might have gone down and the slide would need to be
    # downloaded again, and we don't want to hold up returning to the user for so long
    q = Queue("preload", connection=Redis())
    job = q.enqueue(generate_sample_patch, slide_id, sample_id, rect, job_timeout="120s", result_ttl="60s")

    # Stick the properties into the job
    job.meta['args']=(slide_id, sample_id, rect)
    job.save_meta()

    # Only commit once this has been saved
    db.commit()

    # Return the sample id and the patch generation job id
    return json.dumps({ 'id' : sample_id, 'patch_job_id' : job.id })




# --------------------------------
# Sample import and export
# --------------------------------
@click.command('samples-export-csv')
@click.argument('task')
@click.argument('output_file')
@click.option('--header/--no-header', default=False, help='Include header in output CSV file')
@click.option('--metadata/--no-metadata', default=False, help='Include metadata in output CSV file')
@with_appcontext
def samples_export_csv_command(task, output_file, header, metadata):
    """Export all training samples in a task to a CSV file"""
    db = get_db()

    if metadata:
        rc = db.execute(
                'SELECT L.name as label_name, S.slide_name, '
                '       x0 as x, y0 as y, x1-x0 as w, y1-y0 as h, '
                        'M.t_create, M.t_edit, '
                '       UC.username as creator, UE.username as editor '
                'FROM training_sample TS '
                '  LEFT JOIN label L on L.id = TS.label '
                '  LEFT JOIN slide S on S.id = TS.slide '
                '  LEFT JOIN edit_meta M on M.id = TS.meta_id '
                '  LEFT JOIN user UC on M.creator = UC.id '
                '  LEFT JOIN user UE on M.editor = UE.id '
                'WHERE TS.task = ?', (task,))
        keys=('slide_name','label_name','x','y','w','h','t_create','creator','t_edit','editor')

    else:
        rc = db.execute(
                'SELECT L.name as label_name, S.slide_name, '
                '       x0 as x, y0 as y, x1-x0 as w, y1-y0 as h '
                'FROM training_sample TS '
                '  LEFT JOIN label L on L.id = TS.label '
                '  LEFT JOIN slide S on S.id = TS.slide '
                'WHERE TS.task = ?', (task,))
        keys=('slide_name','label_name','x','y','w','h')

    with open(output_file, 'wt') as fout:
        if header:
            fout.write(','.join(keys) + '\n')

        for row in rc.fetchall():
            vals = map(lambda a : str(row[a]), keys)
            fout.write(','.join(vals) + '\n')


@click.command('samples-import-csv')
@click.argument('task')
@click.argument('input_file', type=click.File('rt'))
@click.option('-u','--user', help='User name under which to insert samples')
@with_appcontext
def samples_import_csv_command(task, input_file, user):
    """Import training samples from a CSV file"""
    db = get_db()

    # Look up the labelset for the current task
    tdata = get_task_data(task)
    if not 'dltrain' in tdata:
        print('Task %s is not the right type for importing samples' % tdata['name'])
        return -1

    lsid = db.execute('SELECT id FROM labelset WHERE name = ?',
            (tdata['dltrain']['labelset'],)).fetchone()['id']

    # Look up the user
    g.user = db.execute('SELECT * FROM user WHERE username=?', (user,)).fetchone()
    if g.user is None:
        print('User %s is not in the system' % user)
        return -1

    lines = input_file.read().splitlines()
    for line in lines:
        fields = line.split(',')
        if len(fields) < 6:
            print('skipping ill-formatted line "%s"' % (line,))
            continue

        (slide_name,label_name,x,y,w,h) = fields[0:6]

        # Look up the slide
        rcs = db.execute('SELECT id FROM slide WHERE slide_name=?', (slide_name,)).fetchone()
        if rcs is None:
            print('Slide %s does not exist, skipping line %s' % (slide_name, line))
            continue

        # Look up the label
        rcl = db.execute('SELECT id FROM label WHERE name=? AND labelset=?',
                (label_name, lsid)).fetchone()
        if rcl is None:
            print('Label %s does not exist, skipping line %s' % (label_name, line))
            continue

        # Create a data record
        rect = (float(x),float(y),float(x)+float(w),float(y)+float(h))

        # Check for overlapping samples
        rc_intercept = db.execute(
                'SELECT max(x0,?) as p0, min(x1,?) as p1, '
                '       max(y0,?) as q0, min(y1,?) as q1, * '
                'FROM training_sample '
                'WHERE p0 < p1 AND q0 < q1 AND task=? and slide=?', 
                (rect[0], rect[2], rect[1], rect[3], task, rcs['id'])).fetchall() 

        if len(rc_intercept) > 0:
            # for row in rc_intercept:
            #    print(row)
            print('There are %d overlapping samples for sample "%s"' %
                    (len(rc_intercept), line))
            continue

        # Create the sample
        result = json.loads(create_sample_base(task, rcs['id'], rcl['id'], rect))

        # Success 
        print('Imported new sample %d from line "%s"' % (result['id'], line))
        

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

    # Create a temporary view of the big join table
    db=get_db()
    db.execute("""CREATE TEMP VIEW v_del AS
                  SELECT T.*, B.specimen_name, B.block_name, L.name as label_name,
                         UC.username as creator_name, UE.username as editor_name,
                         EM.t_create
                  FROM training_sample T
                      LEFT JOIN edit_meta EM on EM.id = T.meta_id
                      LEFT JOIN user UC on UC.id = EM.creator
                      LEFT JOIN user UE on UE.id = EM.editor
                      LEFT JOIN slide S on T.slide = S.id
                      LEFT JOIN block B on S.block_id = B.id
                      LEFT JOIN label L on T.label = L.id""")

    # Build up a where clause
    w = [('creator_name LIKE ?', creator),
         ('editor_name LIKE ?', editor),
         ('specimen_name LIKE ?', specimen),
         ('block_name LIKE ?', block),
         ('slide = ?', slide),
         ('label_name = ?', label),
         ('task = ?', task),
         ('t_create > ?', time.mktime(newer.timetuple()) if newer is not None else None),
         ('t_create < ?', time.mktime(older.timetuple()) if older is not None else None)]

    # Filter out the missing entries
    w = filter(lambda (a,b): b is not None, w)

    # Get the pieces 
    (w_sql,w_prm) = zip(*w)

    # List the ids we would select
    rc=db.execute('SELECT * FROM v_del WHERE %s' % ' AND '.join(w_sql), w_prm)
    result = rc.fetchall()
    if len(result) == 0:
        print('No entries can be deleted')
        return

    ids = zip(*result)[0]

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
                       (SELECT meta_id FROM v_del WHERE %s)""" % ' AND '.join(w_sql), w_prm)
    print('Removed %d rows from edit_meta' % rc.rowcount)

    # Delete all the sample entries
    rc = db.execute("""DELETE FROM training_sample WHERE id IN
                       (SELECT id FROM v_del WHERE %s)""" % ' AND '.join(w_sql), w_prm)
    print('Removed %d rows from training_sample' % rc.rowcount)

    # Delete all the image samples
    for sample_id in ids:
        try:
            os.remove(get_sample_patch_filename(sample_id))
        except:
            print('Error removing file %s' % (get_sample_patch_filename(sample_id),))

    # Commit
    db.commit()
                



        
def init_app(app):
    app.cli.add_command(samples_import_csv_command)
    app.cli.add_command(samples_export_csv_command)
    app.cli.add_command(samples_delete_cmd)

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
    Blueprint, flash, g, redirect, render_template, request, make_response, current_app, send_file, abort, Response
)
from werkzeug.exceptions import abort
from histoannot.auth import login_required, task_access_required, project_access_required, project_admin_access_required
from histoannot.db import get_db

# TODO: these should be moved to another module
from histoannot.project_cli import get_task_data, create_edit_meta, update_edit_meta
from histoannot.project_ref import ProjectRef
from histoannot.delegate import find_delegate_for_slide
from histoannot.dzi import get_patch
from histoannot.slide import make_slide_dbview, annot_sample_path_curves
from histoannot.slideref import get_slide_ref

import json
import time
import io
import os
import urllib
import random
import colorsys
from PIL import Image

from . import slide

from rq import Queue, Connection, Worker
from rq.job import Job, JobStatus
from redis import Redis

import click
from flask.cli import with_appcontext

bp = Blueprint('dltrain', __name__)

# Index

# Get a table of labels in a labelset with counts for the current task/slide
@bp.route('/dltrain/task/<int:task_id>/slide/<int:slide_id>/labelset/table/json', methods=('GET',))
@task_access_required
def get_labelset_labels_table_json(task_id, slide_id):
    db = get_db()

    project,task = get_task_data(task_id)

    ll = db.execute('SELECT L.*, COUNT(T.id) as n_samples '
                    'FROM label L LEFT JOIN training_sample T '
                    '             ON T.label = L.id AND T.task=? AND T.slide=? '
                    '             LEFT JOIN labelset_info LS on L.labelset = LS.id '
                    'WHERE LS.name=? AND LS.project=?'
                    'GROUP BY L.id '
                    'ORDER BY L.id', 
                    (task_id, slide_id, task['dltrain']['labelset'], project))

    ll_data = [dict(row) for row in ll.fetchall()]
    
    return json.dumps(ll_data)


# Get a table of labels in a labelset with counts for the current task/slide
@bp.route('/dltrain/task/<int:task_id>/slide/<int:slide_id>/labelset/table', methods=('GET',))
@task_access_required
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


# Get a table of recently created annotations
@bp.route('/dltrain/task/<int:task_id>/slide/<int:slide_id>/label/<int:label_id>/samples/table/json', methods=('POST','GET'))
@task_access_required
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
@task_access_required
def get_samples_for_label_table(task_id, slide_id):

    return render_template('dbtrain/sample_table.html', task_id=task_id, slide_id=slide_id, label_id=1)



@bp.route('/dltrain/task/<int:task_id>/slide/<int:slide_id>/labelset/picker', methods=('GET',))
@task_access_required
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



@bp.route('/dltrain/task/<int:task_id>/labelset/addlabel', methods=('POST',))
@task_access_required
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
@project_admin_access_required
def project_update_label(project, label_id):
    db = get_db()
    db.execute("UPDATE label SET name=?, description=?, color=? WHERE id=?",
               (request.form['name'], request.form['desc'], request.form['color'], label_id))
    db.commit()
    return json.dumps({"status": "ok"})


@bp.route('/dltrain/api/<project>/label/<int:label_id>/delete', methods=('POST',))
@project_admin_access_required
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
@project_admin_access_required
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
@project_access_required
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
@project_access_required
def get_labelset_label_listing(project, lset):
    db = get_db()

    rc = db.execute("""
        select L.id, L.name, L.description, L.color, STAT.N as n_samples
        from label L left join (select L.id,count(TS.id) as N
                                from label L left join training_sample TS on L.id=TS.label 
                                group by L.id) STAT on L.id=STAT.id
        where L.labelset=? order by L.id""", (lset,))

    # Return the json dump of the listing
    return json.dumps([dict(x) for x in rc.fetchall()])






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
@task_access_required
def create_sample(task_id, slide_id):

    data = json.loads(request.get_data())
    rect = data['geometry']
    label_id = data['label_id']

    check_rect(task_id, rect)

    return create_sample_base(task_id, slide_id, label_id, rect)


@bp.route('/task/<int:task_id>/slide/<int:slide_id>/dltrain/samples', methods=('GET',))
@task_access_required
def get_samples(task_id, slide_id):
    db = get_db()
    ll = db.execute(
        'SELECT S.*, M.creator, M.editor, M.t_create, M.t_edit, L.color '
        'FROM training_sample S LEFT JOIN label L on S.label = L.id '
        '                       LEFT JOIN edit_meta M on S.meta_id = M.id '
        'WHERE S.slide=? and S.task=? ORDER BY M.t_edit DESC ',
        (slide_id, task_id));
    return json.dumps([dict(row) for row in ll.fetchall()])

# TODO: login_required is insufficient here
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


# TODO: login_required is insufficient here
@bp.route('/dltrain/api/sample/update', methods=('POST',))
@login_required
def update_sample():
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
        rawbytes = get_patch(project,specimen,block,'raw',
                             slide_name,slide_ext,level,
                             ctr_x,ctr_y,w,h,'png').data
    else:
        url = '%s/dzi/patch/%s/%s/%s/raw/%s.%s/%d/%d_%d_%d_%d.png' % (
                del_url, project, specimen, block, slide_name, slide_ext, 
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
        rawbytes = get_patch(project,specimen,block,'raw',
                             slide_name,slide_ext,level,
                             ctr_x,ctr_y,w,h,'png').data
    else:
        url = '%s/dzi/patch/%s/%s/%s/raw/%s.%s/%d/%d_%d_%d_%d.png' % (
                del_url, project, specimen, block, slide_name, slide_ext, 
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
def create_sample_base(task_id, slide_id, label_id, rect, osl_level=0):

    project,t_data = get_task_data(task_id)

    db = get_db()

    # Create a meta record
    meta_id = create_edit_meta()

    # Create the main record
    sample_id = db.execute(
        'INSERT INTO training_sample (meta_id,x0,y0,x1,y1,label,slide,task) VALUES (?,?,?,?,?,?,?,?)',
        (meta_id, rect[0], rect[1], rect[2], rect[3], label_id, slide_id, task_id)
    ).lastrowid

    # Get the preferred patch size
    patch_dim = t_data['dltrain'].get('display-patch-size', 512)

    # Create a job that will sample the patch from the image. The reason we do this in a queue
    # is that a server hosting the slide might have gone down and the slide would need to be
    # downloaded again, and we don't want to hold up returning to the user for so long
    q = Queue(current_app.config['PRELOAD_QUEUE'], connection=Redis())
    job = q.enqueue(generate_sample_patch, slide_id, sample_id, rect, 
                    (patch_dim, patch_dim), osl_level, job_timeout="120s", result_ttl="60s")

    # Stick the properties into the job
    job.meta['args']=(slide_id, sample_id, rect)
    job.save_meta()

    # Only commit once this has been saved
    db.commit()

    # Return the sample id and the patch generation job id
    return json.dumps({ 'id' : sample_id, 'patch_job_id' : job.id })


# --------------------------------
# Web pages
# --------------------------------
@bp.route('/dltrain/<project>/labelsets')
@project_admin_access_required
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
                  SELECT T.*, B.specimen_name, B.block_name, L.name as label_name,
                         UC.username as creator, UE.username as editor,
                         EM.t_create, EM.t_edit, S.slide_name, S.stain
                  FROM training_sample T
                      INNER JOIN edit_meta EM on EM.id = T.meta_id
                      INNER JOIN user UC on UC.id = EM.creator
                      INNER JOIN user UE on UE.id = EM.editor
                      INNER JOIN slide S on T.slide = S.id
                      INNER JOIN block B on S.block_id = B.id
                      INNER JOIN label L on T.label = L.id""" % (view_name,))


def samples_generate_csv(task, fout, list_metadata = False, list_ids = False, list_block = False, header = True):

    db = get_db()

    # Create the full view
    make_dbview_full('v_full')

    # Select keys to export
    keys = ('slide_name', 'label_name', 'x', 'y', 'w', 'h')
    if list_metadata:
        keys = keys + ('t_create', 'creator', 't_edit', 'editor')
    if list_block:
        keys = keys + ('specimen_name', 'block_name', 'stain')
    if list_ids:
        keys = ('id',) + keys

    # Run query
    rc = db.execute(
        'SELECT *, x0 as x, y0 as y, x1-x0 as w, y1-y0 as h FROM v_full '
        'WHERE task=? ORDER BY id', (task,))

    if header:
        fout.write(','.join(keys) + '\n')

    for row in rc.fetchall():
        vals = map(lambda a: str(row[a]), keys)
        fout.write(','.join(vals) + '\n')


@bp.route('/dltrain/api/task/<int:task_id>/samples/manifest.csv', methods=('GET',))
@task_access_required
def get_sample_manifest_for_task(task_id):
    fout = io.StringIO.StringIO()
    samples_generate_csv(task_id, fout, list_metadata=True, list_ids=True, list_block=True)
    return Response(fout.getvalue(), mimetype='text/csv')


@click.command('samples-export-csv')
@click.argument('task')
@click.argument('output_file')
@click.option('--header/--no-header', default=False, help='Include header in output CSV file')
@click.option('--metadata/--no-metadata', default=False, help='Include metadata in output CSV file')
@click.option('--specimen/--no-specimen', default=False, help='Include specimen ids in output CSV file')
@click.option('--ids/--no-ids', default=False, help='Include sample database ids in output CSV file')
@with_appcontext
def samples_export_csv_command(task, output_file, header, metadata, specimen, ids):
    """Export all training samples in a task to a CSV file"""
    with open(output_file, 'wt') as fout:
        samples_generate_csv(task, fout,
                             list_metadata=metadata, list_ids=ids,
                             list_block=specimen, header=header)


@click.command('samples-import-csv')
@click.argument('task')
@click.argument('input_file', type=click.File('rt'))
@click.option('-u','--user', help='User name under which to insert samples', required=True)
@with_appcontext
def samples_import_csv_command(task, input_file, user):
    """Import training samples from a CSV file"""
    db = get_db()

    # Look up the labelset for the current task
    project,tdata = get_task_data(task)
    if not 'dltrain' in tdata:
        print('Task %s is not the right type for importing samples' % tdata['name'])
        return -1

    lsid = get_labelset_id(project, tdata)

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
         ('specimen_name LIKE ?', specimen),
         ('block_name LIKE ?', block),
         ('slide = ?', slide),
         ('label_name = ?', label),
         ('task = ?', task),
         ('t_create > ?', time.mktime(newer.timetuple()) if newer is not None else None),
         ('t_create < ?', time.mktime(older.timetuple()) if older is not None else None)]

    # Filter out the missing entries
    w = filter(lambda a,b: b is not None, w)

    # Get the pieces 
    (w_sql,w_prm) = zip(*w)

    # List the ids we would select
    rc=db.execute('SELECT * FROM %s WHERE %s' % (viewname,' AND '.join(w_sql)), w_prm)
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
        rc = db.execute('SELECT id FROM v_full WHERE n_paths > 0 AND specimen_name=?', (specimen,)).fetchall()
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


        
def init_app(app):
    app.cli.add_command(samples_import_csv_command)
    app.cli.add_command(samples_export_csv_command)
    app.cli.add_command(samples_delete_cmd)
    app.cli.add_command(samples_fix_patches_cmd)
    app.cli.add_command(samples_random_from_annot_cmd)

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
import sqlite3
import os
import re
import csv
import sys
import traceback

import click
from flask import current_app, g, url_for
from flask.cli import with_appcontext

import openslide
import time
import urllib2

from PIL import Image

from histoannot.slideref import SlideRef, get_slideref_by_info
from histoannot.cache import get_slide_cache
from histoannot.dzi import get_patch

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(
            current_app.config['DATABASE'],
            detect_types=sqlite3.PARSE_DECLTYPES
        )
        g.db.row_factory = sqlite3.Row

    return g.db


def close_db(e=None):
    db = g.pop('db', None)

    if db is not None:
        db.close()

def init_db():
    db = get_db()
    with current_app.open_resource('schema.sql') as f:
        db.executescript(f.read().decode('utf8'))
    init_db_dltrain()

def init_db_dltrain():
    db = get_db()
    with current_app.open_resource('schema_dltrain.sql') as f:
        db.executescript(f.read().decode('utf8'))


def add_block(specimen, block):
    db=get_db()
    if db.execute('SELECT id FROM block'
                  ' WHERE specimen_name=? and block_name=?', 
                 (specimen, block)).fetchone() is not None:
        return


# Check if the file refers to a valid slide
def is_valid_slide(src_dir, matfile):
    if os.path.isfile(os.path.join(src_dir, matfile)) and matfile.endswith('.mat'):
        p = re.compile('.mat$').sub('.tiff', matfile)
        return os.path.exists(os.path.join(src_dir, p))
    else:
        return False

# Get a dictionary from a filename
def parse_slide_filename(matfile, src_dir):

    # Drop the .mat extension
    basename = re.compile('_x16_aff.mat$').sub('', matfile)

    # Split the filename on '_'
    s=basename.split('_')

    # Take the last bits
    n = len(s)
    if n < 4:
        raise ValueError('Invalid filename')

    # Get the full filename
    matfile_full = os.path.abspath(os.path.join(src_dir, matfile))
    tiff_file = re.compile('.mat$').sub('.tiff', matfile_full)

    return {'slide':s[n-2], 'section':s[n-3], 'stain':s[n-1], 'block':s[n-4],
            'tiff_file': tiff_file, 'mat_file': matfile_full}

# Generate a screenshot for a slice
def make_thumbnail(slide_id, tiff_file, lazy):

    thumb_dir = os.path.join(current_app.instance_path, 'thumb')
    thumb_fn = os.path.join(thumb_dir, "thumb%08d.png" % (slide_id,))

    if lazy is True and os.path.exists(thumb_fn):
        return
    
    if not os.path.exists(thumb_dir):
        os.makedirs(thumb_dir)

    osr = openslide.OpenSlide(tiff_file)
    thumb = osr.get_thumbnail((256,192))
    thumb.save(thumb_fn)


# A better way to import tiff files, actually synchronize the file system with
# the database. That is go through all files in the directory and update or 
# create their records. 
def update_slide_db(src_dir):

    db = get_db()

    # Traverse over specimens and blocks
    specimens = filter(lambda d: os.path.isdir(os.path.join(src_dir, d)), os.listdir(src_dir))
    for s in specimens:
        spec_dir = os.path.join(src_dir, s)
        blocks = filter(lambda d: os.path.isdir(os.path.join(spec_dir, d)), os.listdir(spec_dir))
        for b in blocks:
            block_dir = os.path.join(spec_dir, b)

            # Collect a list of valid slides with .mat files
            mats = filter(lambda d: is_valid_slide(block_dir, d), os.listdir(block_dir))
            print("%s: %d files" % (block_dir, len(mats)))

            # Keep track of number of inserted and updated slides
            (n_ins, n_upd) = (0, 0)

            # There must be at least one slide
            if len(mats) > 0:

                # Make sure the block exists
                brec = db.execute('SELECT * FROM block WHERE specimen_name=? AND block_name=?', (s,b)).fetchone()
                if brec is None:
                    db.execute('INSERT INTO block (specimen_name, block_name) VALUES (?,?)', (s,b))
                    brec = db.execute('SELECT * FROM block WHERE specimen_name=? AND block_name=?', (s,b)).fetchone()

                # Get the block id
                block_id = brec['id']

                # Loop over the slides
                for m in mats:

                    # Get the slide info from the filename
                    si=parse_slide_filename(m, block_dir)

                    # Search for the slide in the database
                    srec = db.execute('SELECT * FROM slide'
                                      ' WHERE block_id=? AND section=? AND stain=? AND slide=?',
                                      (block_id, si['section'], si['stain'], si['slide'])).fetchone()

                    # If slide was not found, insert one
                    lazy = True
                    if srec is None:
                        db.execute('INSERT INTO slide (block_id, section, slide, stain, tiff_file, mat_file)'
                                   ' VALUES (?,?,?,?,?,?)', 
                                   (block_id, si['section'], si['slide'], 
                                       si['stain'], si['tiff_file'], si['mat_file']))

                        srec = db.execute('SELECT * FROM slide'
                                          ' WHERE block_id=? AND section=? AND stain=? AND slide=?',
                                          (block_id, si['section'], si['stain'], si['slide'])).fetchone()
                        lazy = False
                        n_ins+=1

                    # If slide was found, check if it requires an updateA
                    elif srec['tiff_file'] != si['tiff_file'] or srec['mat_file'] != si['mat_file']:
                        db.execute('UPDATE slide SET tiff_file=?, mat_file=? WHERE id=?',
                                (si['tiff_file'], si['mat_file'], srec['id']))
                        lazy = False
                        n_upd+=1

                    # Generate thumbnail
                    make_thumbnail(srec['id'], srec['tiff_file'], lazy)

                # Commit
                db.commit()

                # Print results
                print("  Inserted: %d, Updated %d" % (n_ins, n_upd))





# The administrator is expected to curate the image collection. The system simply
# takes stock of the available data and presents it for segmentation. Each image 
# in the curated collection is associated with one or more segmentations. The SVG
# files are updated as the segmentation progresses.
def rebuild_slide_db(src_dir):

    # Clear the block and slide tables
    db = get_db()
    db.execute('DELETE FROM block WHERE id >= 0')
    db.execute('DELETE FROM slide WHERE id >= 0')
    db.commit()
    
    # Traverse over specimens and blocks
    specimens = filter(lambda d: os.path.isdir(os.path.join(src_dir, d)), os.listdir(src_dir))
    for s in specimens:
        spec_dir = os.path.join(src_dir, s)
        blocks = filter(lambda d: os.path.isdir(os.path.join(spec_dir, d)), os.listdir(spec_dir))
        for b in blocks:
            block_dir = os.path.join(spec_dir, b)

            # Collect a list of valid slides with .mat files
            mats = filter(lambda d: is_valid_slide(block_dir, d), os.listdir(block_dir))
            print("%s: %d files" % (block_dir, len(mats)))

            # There must be at least one slide
            if len(mats) > 0:

                # Add a block reference
                db.execute('INSERT INTO block (specimen_name, block_name) VALUES (?,?)', (s,b))

                # Get the inserted id
                block_id = db.execute('SELECT id FROM block'
                                      ' WHERE specimen_name=? AND block_name=?', (s,b)
                                     ).fetchone()['id']


                # Add each of the slides
                for m in mats:

                    si=parse_slide_filename(m, block_dir)

                    slide_id = db.execute('INSERT INTO slide '
                                          '    (block_id, section, slide, stain, tiff_file, mat_file)'
                                          ' VALUES (?,?,?,?,?,?)', 
                                          (block_id, si['section'], si['slide'], 
                                           si['stain'], si['tiff_file'], si['mat_file'])).fetchone()

                # Commit the database
                db.commit()


# The administrator is expected to curate the image collection. The system simply
# takes stock of the available data and presents it for segmentation. Each image 
# in the curated collection is associated with one or more segmentations. The SVG
# files are updated as the segmentation progresses.
def rebuild_slide_db_block(src_dir, specimen, block, match_csv):

    # Clear the block and slide tables
    db = get_db()
    brec = db.execute('SELECT * FROM block WHERE specimen_name=? AND block_name=?', (specimen, block)).fetchone()
    if brec is not None:
        db.execute('DELETE FROM slide WHERE block_id = ?', (brec['id'],))
        db.execute('DELETE FROM block WHERE id = ?', (brec['id'],))
        db.commit()

    # Create a list of dicts from CSV
    si=[]

    # Read CSV
    with open(match_csv) as csv_file:
        csv_reader = csv.reader(csv_file, delimiter=',')
        for row in csv_reader:
            # Check correctness
            if row[2] != block:
                raise ValueError('block name mismatch')

            # Check for existence of file
            fn=None
            for ext in ('.svs', '.tiff', '.tif'):
                fn_test=os.path.join(src_dir, row[0]+ext)
                print('testing file %s' % fn_test)
                if os.path.exists(fn_test):
                    fn=fn_test
                    print('found file %s' % fn)
                    break

            # If file exists, add it to the database
            if fn is not None:
                si.append({'tiff_file':fn, 'stain':row[1], 'section':row[3], 'slide':row[4], 'mat_file':'blah'})

    # Make sure we read some records
    if len(si) > 0:

        # Add a block reference
        db.execute('INSERT INTO block (specimen_name, block_name) VALUES (?,?)', (specimen,block))

        # Get the inserted id
        block_id = db.execute('SELECT id FROM block'
                              ' WHERE specimen_name=? AND block_name=?', (specimen,block)).fetchone()['id']

        # Add each slide
        for s in si:

            db.execute('INSERT INTO slide '
                       '    (block_id, section, slide, stain, tiff_file, mat_file)'
                       ' VALUES (?,?,?,?,?,?)', 
                       (block_id, s['section'], s['slide'], 
                        s['stain'], s['tiff_file'], s['mat_file']))

        # Commit the database
        db.commit()


@click.command('init-db')
@with_appcontext
def init_db_command():
    """Clear the existing data and create new tables."""
    init_db()
    click.echo('Initialized the database.')

@click.command('init-db-dltrain')
@with_appcontext
def init_db_dltrain_command():
    """Clear the existing data related to DL training and create new tables."""
    init_db_dltrain()
    click.echo('Initialized the DL training database.')


@click.command('scan-slides')
@click.option('--src', prompt='Source directory', 
              help='Directory to import data from')
@with_appcontext
def scan_slides_command(src):
    """Scan the local directory for slides and create block and slide database"""
    update_slide_db(src)
    click.echo('Scanned for slides')

@click.command('add-block')
@click.option('--src', prompt='Source directory', help='Directory to import data from')
@click.option('--specimen', prompt='Specimen ID', help='Specimen ID')
@click.option('--block', prompt='Block ID', help='Block ID')
@click.option('--match', prompt='Match CSV file', help='Match CSV file')
@with_appcontext
def add_block_command(src, specimen, block, match):
    """Scan the local directory for slides and create block and slide database"""
    rebuild_slide_db_block(src, specimen, block, match)
    click.echo('Scanned for slides')


# Generic function to insert a slide, creating a block descriptor of needed
def db_create_slide(specimen, block, section, slide, stain, slice_name, slice_ext):

    db = get_db()

    # Find the block
    brec = db.execute('SELECT * FROM block WHERE specimen_name=? AND block_name=?', 
                      (specimen,block)).fetchone()
    if brec is None:
        bid = db.execute('INSERT INTO block (specimen_name, block_name) VALUES (?,?)', 
                         (specimen,block)).lastrowid
    else:
        bid = brec['id']

    # Create a slide
    sid = db.execute('INSERT INTO slide (block_id, section, slide, stain, slide_name, slide_ext) '
                     'VALUES (?,?,?,?,?,?)', (bid, section, slide, stain, slice_name, slice_ext)).lastrowid

    # Commit to the database
    db.commit()

    # Return the ID
    return sid

# Function to update slide derived data (affine transform, thumbnail, etc.)
def update_slide_derived_data(slide_id):

    # Get the slide reference
    sr = get_slide_ref(slide_id)

    # Get the remote thumbnail file
    f_thumb = sr.get_local_copy("thumb", check_hash=True)

    # Get the local thumbnail 
    thumb_dir = os.path.join(current_app.instance_path, 'thumb')
    thumb_fn = os.path.join(thumb_dir, "thumb%08d.png" % (slide_id,))

    # If the thumb has been downloaded successfully, derive a HTML-usable thumb
    if f_thumb is not None:
        x = Image.open(f_thumb)
        m=max(x.size[0]/256.,x.size[1]/192.)
        x = x.resize(map(int, (x.size[0]/m,x.size[1]/m)))

        if not os.path.exists(thumb_dir):
            os.makedirs(thumb_dir)
        x.save(thumb_fn)


# Builds up a slide database. Scans a manifest file that contains names of specimens
# and URLs to Google Sheet spreadsheets in which individual slides are matched to the
# block/section/slice/stain information. Checks if the corresponding files exist in 
# the Google cloud and creates slide identifiers as needed
def refresh_slide_db(manifest, bucket, single_specimen = None):

    # Database cursor
    db = get_db()

    # Read from the manifest file
    with open(manifest) as f_manifest:
        for line in f_manifest:

            # Split into words
            words = line.split()
            if len(words) != 2:
                continue

            # Check for single specimen selector
            specimen = line.split()[0]
            if single_specimen is not None and single_specimen != specimen:
                continue

            url = line.split()[1]
            print('Parsing specimen "%s" with URL "%s"' % (specimen,url))

            try:
                # Get the lines from the URL
                resp=urllib2.urlopen(url)
                r=csv.reader(resp.read().splitlines()[1:])

                # For each line in the URL consider it as a new slide
                for line in r:

                    try:

                        # Read the elements from the string
                        (slide_name, stain, block, section, slide_no, cert) = line[0:6]

                        # Remap slide_name to string
                        slide_name = str(slide_name)

                        # Check if the slide has already been imported into the database
                        t = db.execute('SELECT * FROM slide WHERE slide_name=?', (slide_name,)).fetchone()
                        if t is not None:
                            print('Slide %s already in the database with id=%s' % (slide_name, t['id']));
                            update_slide_derived_data(t['id'])
                            continue

                        # Create a slideref for this object. The way we have set all of this up, the extension is not
                        # coded anywhere in the manifests, so we dynamically check for multiple extensions
                        for slide_ext in ('svs', 'tif', 'tiff'):

                            sr = get_slideref_by_info(specimen, block, slide_name, slide_ext)

                            if sr.resource_exists('raw', False):

                                # The raw slide has been found, so the slide will be entered into the database. 
                                sid = db_create_slide(specimen, block, section, slide_no, stain, slide_name, slide_ext)

                                print('Slide %s located with url %s and assigned new id %d' % 
                                      (slide_name, sr.get_resource_url('raw', False), sid))

                                # Update thumbnail and such
                                update_slide_derived_data(sid)

                                break

                        # We are here because no URL was found
                        print('Raw image was not found for slide %s' % slide_name)

                    except:

                        print('Failed to import slide for ', line)
                        (exc_type, exc_value, exc_traceback) = sys.exc_info()
                        print(repr(traceback.format_exception(exc_type, exc_value, exc_traceback)))
                        raise ValueError('crash')

            except:

                print('Failed to import specimen ', specimen)
                (exc_type, exc_value, exc_traceback) = sys.exc_info()
                print(repr(traceback.format_exception(exc_type, exc_value, exc_traceback)))
                raise ValueError('crash')


def get_slide_ref(slice_id):

    db = get_db()  

    # Load slide from database
    row = db.execute('SELECT S.*, B.specimen_name, B.block_name '
                     'FROM slide S LEFT JOIN block B ON S.block_id = B.id WHERE S.id=?', (slice_id,)).fetchone()

    # Handle missing data
    if row is None:
        return None

    # Create the sliceref
    return get_slideref_by_info(
            row['specimen_name'], row['block_name'], row['slide_name'], row['slide_ext'])



def load_raw_slide_to_cache(slide_id, resource):

    # Get the data for this slide
    sr = get_slide_ref(slide_id)
    if sr is not None:
        sr.get_local_copy(resource)
    else:
        print('Slide %s not found' % (slide_id,))



@click.command('refresh-slides')
@click.argument('manifest', type=click.Path(exists=True))
@click.option('-s', '--specimen', default=None, 
        help='Only refresh slides for a single specimen')
@with_appcontext
def refresh_slides_command(manifest, specimen):
    """Refresh the slide database using manifest file MANIFEST that lists specimens and GDrive links"""
    refresh_slide_db(manifest, "gs://mtl_histology", specimen);
    click.echo('Scanning complete')


@click.command('cache-load-raw-slide')
@click.option('--slideid', prompt='Slide ID')
@with_appcontext
def cache_load_raw_slide_command(slideid):
    """Download a raw slide into the cache"""
    load_raw_slide_to_cache(slideid, 'raw')


# --------------------------------
# TASKS
# --------------------------------
from jsonschema import validate
import json

# A schema against which the JSON is validated
task_schema = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "name" : { "type" : "string", "minLength": 2, "maxLength": 80 },
        "desc" : { "type" : "string", "maxLength": 1024 },
        "mode" : { "type" : "string", "enum" : ["annot", "dltrain", "browse" ] },
        "dltrain" : { 
            "type" : "object",
            "properties" : {
                "labelset" : { "type" : "string" },
            },
            "required" : ["labelset"]
        },
        "restrict-access" : { "type" : "boolean" },
        "stains" : {
            "type" : "array",
            "items" : {
                "type" : "string"
            }
        }
    },
    "required" : ["name", "mode", "restrict-access"]
}


# Add a new task (or update existing task) based on a JSON specifier. We
# will check the JSON for completeness here and then configure the task
def add_task(json_file, update_existing_task_id = None):

    with open(json_file) as fdesc:

        # Validate the JSON against the schema
        data=json.load(fdesc)
        validate(instance=data, schema=task_schema)

        db = get_db()

        # Update or insert?
        if update_existing_task_id is None:

            # Insert the JSON
            rc = db.execute('INSERT INTO task(name,json,restrict_access) VALUES (?,?,?)',
                            (data['name'], json.dumps(data), data['restrict-access']))
            if rc.rowcount == 1:
                print("Successfully inserted task %s with id %d" % (data['name'], rc.lastrowid))

        else:

            # Update
            rc = db.execute('UPDATE task SET name=?, json=?, restrict_access=? WHERE id=?',
                            (data['name'], json.dumps(data), data['restrict-access'], update_existing_task_id))
            if rc.rowcount == 1:
                print("Successfully updated task %d" % int(update_existing_task_id))
            else:
                raise ValueError("Task %d not found" % int(update_existing_task_id))

        db.commit()

# Get json for a task
def get_task_data(task_id):
    db = get_db()
    rc = db.execute('SELECT * FROM task WHERE id = ?', (task_id,)).fetchone()
    return json.loads(rc['json'])



@click.command('tasks-add')
@click.option('--json', prompt='JSON descriptor for the task')
@with_appcontext
def add_task_command(json):
    """Configure a new task"""
    add_task(json)

@click.command('tasks-update')
@click.option('--json', prompt='JSON descriptor for the task')
@click.option('--task', prompt='ID of the task to update')
@with_appcontext
def update_task_command(json, task):
    """Update an existing task"""
    add_task(json, task)

@click.command('tasks-list')
@with_appcontext
def list_tasks_command():
    """List available tasks"""
    print('%08s %s' % ('Task ID', 'Task Name'))

    db = get_db()
    rc = db.execute('SELECT id, name FROM task ORDER BY id')
    for row in rc.fetchall():
        print('%08s %s' % (row['id'], row['name']))

@click.command('tasks-print')
@click.option('--task', prompt='ID of the task to print')
@with_appcontext
def print_tasks_command(task):
    """Print the JSON for a task"""

    db = get_db()
    rc = db.execute('SELECT json FROM task WHERE id=?', (task,)).fetchone()
    if rc is not None:
        print(json.dumps(json.loads(rc['json']), indent=4))
    else:
        print('Task %d does not exist' % (int(task),))


# --------------------------------
# LABELSETS
# --------------------------------
@click.command('labelset-list')
@with_appcontext
def list_labelsets_command():
    """Print label sets"""
    print('%-8s %-32s %-8s %-8s' % ('Id', 'Name', '#Labels', '#Samples'))

    db = get_db()
    rc = db.execute('SELECT S.id, S.name, count(distinct L.id) as n_labels, count(D.id) as n_samples '
                    'FROM labelset S left join label L on S.id=L.labelset '
                    '                left join training_sample D on L.id = D.label '
                    'GROUP BY S.id ORDER BY S.id')
    for row in rc.fetchall():
        print('%-8s %-32s %-8s %-8s' % (row['id'], row['name'], row['n_labels'], row['n_samples']))

@click.command('labelset-status')
@click.argument('id')
@with_appcontext
def print_labelset_labels_command(id):
    """Print the labels in a labelset"""
    print('%-8s %-32s %-8s %-8s' % ('Id', 'Name', 'Color', '#Samples'))

    db = get_db()
    rc = db.execute('SELECT L.id, L.name, L.color, count(D.id) as n_samples '
                    'FROM label L left join training_sample D on L.id = D.label '
                    'WHERE L.labelset=? '
                    'GROUP BY L.id ORDER BY L.id', (id,))
    for row in rc.fetchall():
        print('%-8s %-32s %-8s %-8s' % (row['id'], row['name'], row['color'], row['n_samples']))

@click.command('labelset-dump')
@click.argument('id')
@with_appcontext
def dump_labelset_command(id):
    """Dump a labelset in JSON format"""
    db = get_db()
    rc = db.execute('SELECT name, description, color FROM label WHERE labelset=? ORDER BY id', (id,))

    d=[]
    for row in rc.fetchall():
        d.append({'name': row['name'], 'color': row['color'], 'description' : row['description']})

    print(json.dumps(d))


# A schema for importing labels
label_schema = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "array",
    "items" : {
        "type" : "object",
        "properties" : {
            "name" : { "type" : "string", "minLength": 2, "maxLength": 80 },
            "description" : { "type" : "string", "maxLength": 1024 },
            "color" : { "type" : "string", "minLength": 2, "maxLength": 80 }
        },
        "required" : [ "name", "color" ]
    }
}

@click.command('labelset-update')
@click.argument('id')
@click.argument('json_file')
@with_appcontext
def update_labelset_from_json_command(id, json_file):

    db = get_db()
    with open(json_file) as fdesc:

        # Validate the JSON against the schema
        data=json.load(fdesc)
        validate(instance=data, schema=label_schema)

        # Go through the labels
        for row in data:

            # Check if a label with this name already exists
            rc = db.execute("SELECT * FROM label "
                            "WHERE labelset=? and name COLLATE NOCASE = ?",
                            (id, row['name'])).fetchone()

            # Get the description, which is optional
            desc = row['description'] if 'description' in row else None

            # If so, we will be updating this label
            if rc is not None:
                db.execute("UPDATE label SET color=?, description=? "
                           "WHERE labelset=? and name COLLATE NOCASE = ?",
                           (row['color'], desc, id, row['name']))
            else:
                db.execute("INSERT INTO label (name, color, description, labelset) VALUES (?,?,?,?)",
                           (row['name'], row['color'], desc, id))

        # Commit
        db.commit()


# --------------------------------
# Editing Metadata
# --------------------------------
def update_edit_meta(meta_id):
    db = get_db()
    db.execute('UPDATE edit_meta SET editor=?, t_edit=? WHERE id=?', 
               (g.user['id'], time.time(), meta_id))


def create_edit_meta():
    t_stamp = time.time()
    db = get_db()
    return db.execute("INSERT INTO edit_meta (creator, editor, t_create, t_edit) VALUES (?,?,?,?)",
                      (g.user['id'], g.user['id'], t_stamp, t_stamp)).lastrowid



def init_app(app):
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)
    app.cli.add_command(init_db_dltrain_command)
    app.cli.add_command(refresh_slides_command)
    app.cli.add_command(cache_load_raw_slide_command)
    app.cli.add_command(add_task_command)
    app.cli.add_command(update_task_command)
    app.cli.add_command(list_tasks_command)
    app.cli.add_command(print_tasks_command)
    app.cli.add_command(list_labelsets_command)
    app.cli.add_command(print_labelset_labels_command)
    app.cli.add_command(dump_labelset_command)
    app.cli.add_command(update_labelset_from_json_command)
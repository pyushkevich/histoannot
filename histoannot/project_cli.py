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
import os
import re
import csv
import sys
import traceback
import pandas
import glob
import pathlib

import click
from flask import current_app, g
from flask.cli import with_appcontext

import openslide
import time
import urllib.request
import logging
import json
from jsonschema import validate


from PIL import Image

from histoannot.project_ref import ProjectRef
from histoannot.slideref import SlideRef,get_slide_ref
from histoannot.db import get_db
from histoannot.gcs_handler import GCSHandler

def init_db_dltrain():
    db = get_db()
    with current_app.open_resource('schema_dltrain.sql') as f:
        db.executescript(f.read().decode('utf8'))


def init_db_views():
    db = get_db()
    with current_app.open_resource('schema_views.sql') as f:
        db.executescript(f.read().decode('utf8'))


def init_db():
    db = get_db()
    with current_app.open_resource('schema.sql') as f:
        db.executescript(f.read().decode('utf8'))
    init_db_dltrain()
    init_db_views()


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
    s = basename.split('_')

    # Take the last bits
    n = len(s)
    if n < 4:
        raise ValueError('Invalid filename')

    # Get the full filename
    matfile_full = os.path.abspath(os.path.join(src_dir, matfile))
    tiff_file = re.compile('.mat$').sub('.tiff', matfile_full)

    return {'slide': s[n - 2], 'section': s[n - 3], 'stain': s[n - 1], 'block': s[n - 4],
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
    thumb = osr.get_thumbnail((256, 192))
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
                brec = db.execute('SELECT * FROM block WHERE specimen_name=? AND block_name=?', (s, b)).fetchone()
                if brec is None:
                    db.execute('INSERT INTO block (specimen_name, block_name) VALUES (?,?)', (s, b))
                    brec = db.execute('SELECT * FROM block WHERE specimen_name=? AND block_name=?', (s, b)).fetchone()

                # Get the block id
                block_id = brec['id']

                # Loop over the slides
                for m in mats:

                    # Get the slide info from the filename
                    si = parse_slide_filename(m, block_dir)

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
                        n_ins += 1

                    # If slide was found, check if it requires an updateA
                    elif srec['tiff_file'] != si['tiff_file'] or srec['mat_file'] != si['mat_file']:
                        db.execute('UPDATE slide SET tiff_file=?, mat_file=? WHERE id=?',
                                   (si['tiff_file'], si['mat_file'], srec['id']))
                        lazy = False
                        n_upd += 1

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
                db.execute('INSERT INTO block (specimen_name, block_name) VALUES (?,?)', (s, b))

                # Get the inserted id
                block_id = db.execute('SELECT id FROM block'
                                      ' WHERE specimen_name=? AND block_name=?', (s, b)
                                      ).fetchone()['id']

                # Add each of the slides
                for m in mats:
                    si = parse_slide_filename(m, block_dir)

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
    si = []

    # Read CSV
    with open(match_csv) as csv_file:
        csv_reader = csv.reader(csv_file, delimiter=',')
        for row in csv_reader:
            # Check correctness
            if row[2] != block:
                raise ValueError('block name mismatch')

            # Check for existence of file
            fn = None
            for ext in ('.svs', '.tiff', '.tif'):
                fn_test = os.path.join(src_dir, row[0] + ext)
                print('testing file %s' % fn_test)
                if os.path.exists(fn_test):
                    fn = fn_test
                    print('found file %s' % fn)
                    break

            # If file exists, add it to the database
            if fn is not None:
                si.append({'tiff_file': fn, 'stain': row[1], 'section': row[3], 'slide': row[4], 'mat_file': 'blah'})

    # Make sure we read some records
    if len(si) > 0:

        # Add a block reference
        db.execute('INSERT INTO block (specimen_name, block_name) VALUES (?,?)', (specimen, block))

        # Get the inserted id
        block_id = db.execute('SELECT id FROM block'
                              ' WHERE specimen_name=? AND block_name=?', (specimen, block)).fetchone()['id']

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


@click.command('init-db-views')
@with_appcontext
def init_db_views_command():
    """Clear the existing data related to DL training and create new tables."""
    init_db_views()
    click.echo('Initialized the database views.')


@click.command('scan-slides')
@click.option('--src', prompt='Source directory',
              help='Directory to import data from')
@with_appcontext
def scan_slides_command(src):
    """Scan the local directory for slides and create block and slide database"""
    update_slide_db(src)
    click.echo('Scanned for slides')


# A schema against which the JSON is validated
project_schema = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "disp_name": {"type": "string", "minLength": 2, "maxLength": 80},
        "desc": {"type": "string", "maxLength": 1024},
        "base_url": {"type": "string"},
        "url_schema": {"type": "object"}},
    "required": ["disp_name", "desc", "base_url"]
}


def add_project(name, json_file, update_existing=False):

    # Read the JSON
    data = json.load(json_file)

    # Validate the JSON
    validate(instance=data, schema=project_schema)

    # Update the database
    db = get_db()
    if update_existing:
        rc = db.execute('UPDATE project SET disp_name=?, desc=?, base_url=?, json=? WHERE id=?',
                        (data['disp_name'], data['desc'], data['base_url'], json.dumps(data), name))

        if rc.rowcount == 1:
            db.commit()
            print('Updated project %s' % (rc.lastrowid,))

    else:
        rc = db.execute('INSERT INTO project (id, disp_name, desc, base_url, json) VALUES (?,?,?,?,?)',
                        (name, data['disp_name'], data['desc'], data['base_url'], json.dumps(data)))

        if rc.rowcount == 1:
            db.commit()
            print('Created project %s' % (rc.lastrowid,))


@click.command('project-add')
@click.argument('name')
@click.argument('json', type=click.File('rt'))
@with_appcontext
def create_project_command(name, json):
    """Create a project with a given short name using json specification"""
    add_project(name, json, False)


@click.command('project-update')
@click.argument('name')
@click.argument('json', type=click.File('rt'))
@with_appcontext
def update_project_command(name, json):
    """Update an existing project with new json"""
    add_project(name, json, True)


@click.command('projects-assign-unclaimed-entities')
@click.argument('project')
@with_appcontext
def project_assign_unclaimed_command(project):
    """
    Associates with a project all entities (blocks, tasks, users, labelsets) in the database that
    have not been previously associated with a project. Used when migrating a database that did
    not have projects one that does."""

    # The table may not exist
    db = get_db()

    # Assign all unassociated blocks to the new project
    rc = db.execute('SELECT * FROM block WHERE id NOT IN (SELECT DISTINCT(block) FROM project_block)')
    for row in rc.fetchall():
        db.execute('INSERT INTO project_block (project,block) VALUES (?,?)', (project, row['id']))

    # Assign all the tasks to the new project
    rc = db.execute('SELECT id,name FROM task '
                    'WHERE id NOT IN (SELECT DISTINCT(task_id) FROM project_task)')
    for row in rc.fetchall():
        db.execute('INSERT INTO project_task (project,task_id,task_name) VALUES (?,?,?)',
                   (project, row['id'],row['name']))

    # Assign all the users to the new project
    rc = db.execute('SELECT id FROM user WHERE id NOT IN (SELECT DISTINCT(user) FROM project_access)')
    for row in rc.fetchall():
        db.execute('INSERT INTO project_access (project,user) VALUES (?,?)', (project, row['id']))

    # Assign all the labelsets to the new project
    rc = db.execute('SELECT id,name FROM labelset '
                    'WHERE id NOT IN (SELECT DISTINCT(labelset_id) FROM project_labelset)')
    for row in rc.fetchall():
        db.execute('INSERT INTO project_labelset (project,labelset_id,labelset_name) VALUES (?,?,?)',
                   (project, row['id'], row['name']))

    db.commit()


@click.command('projects-list')
@with_appcontext
def list_projects_command():
    # Parse existing projects read from JSON files
    df = pandas.read_sql_query("SELECT id,disp_name,desc FROM project", get_db())
    with pandas.option_context('display.max_rows', None, 'display.max_colwidth', 20):
        print(df)


@click.command('project-get-json')
@click.argument('project')
@with_appcontext
def project_get_json_command(project):
    db = get_db()
    row = db.execute('SELECT * FROM project WHERE id=?', (project,)).fetchone()
    if row is not None:
        print(json.dumps(row['json'], indent=2))
    else:
        print('Project %s not found' % (project,))


# Find existing block or create if it does not exist
def db_get_or_create_block(project, specimen, block):
    db = get_db()

    # Find the block within the current project.
    brec = db.execute('SELECT * FROM block_info '
                      'WHERE specimen_name=? AND block_name=? AND project=?',
                      (specimen, block, project)).fetchone()
    if brec is not None:
        return brec['id']

    # Block must be created
    bid = db.execute('INSERT INTO block (specimen_name, block_name) VALUES (?,?)',
                     (specimen, block)).lastrowid

    rc = db.execute('INSERT INTO project_block (project,block) VALUES (?,?)',
                    (project, bid))

    db.commit()

    return bid


# Generic function to insert a slide, creating a block descriptor of needed
def db_create_slide(project, specimen, block, section, slide, stain, slice_name, slice_ext, tags):
    db = get_db()

    # Find the block within the current project.
    bid = db_get_or_create_block(project, specimen, block)

    # Create a slide
    sid = db.execute('INSERT INTO slide (block_id, section, slide, stain, slide_name, slide_ext) '
                     'VALUES (?,?,?,?,?,?)', (bid, section, slide, stain, slice_name, slice_ext)).lastrowid

    # Create tags for the slide
    for t in tags:
        db.execute('INSERT INTO slide_tags(slide, tag, external) VALUES (?,?,1)', (sid, t))

    # Commit to the database
    db.commit()

    # Return the ID
    return sid


# Function to update slide derived data (affine transform, thumbnail, etc.)
def update_slide_derived_data(slide_id, check_hash=True):
    # Get the slide reference
    sr = get_slide_ref(slide_id)

    # Get the remote thumbnail file
    f_thumb = sr.get_local_copy("thumb", check_hash=check_hash)

    # Get the local thumbnail
    thumb_dir = os.path.join(current_app.instance_path, 'thumb')
    thumb_fn = os.path.join(thumb_dir, "thumb%08d.png" % (slide_id,))

    # If the thumb has been downloaded successfully, derive a HTML-usable thumb
    if f_thumb is not None:
        x = Image.open(f_thumb)
        m = max(x.size[0] / 256., x.size[1] / 192.)
        x = x.resize(map(int, (x.size[0] / m, x.size[1] / m)))

        if not os.path.exists(thumb_dir):
            os.makedirs(thumb_dir)
        x.save(thumb_fn)


# Generic function to load a URL or local file into a string
def load_url(url, parent_dir=None):
    for attempt in range(5):
        try:
            if url.startswith('http://') or url.startswith('https://'):
                resp = urllib.request.urlopen(url)
                return resp.read()
            elif url.startswith('gs://'):
                gcsh = GCSHandler()
                return gcsh.download_text_file(url)
            else:
                if not os.path.isabs(url) and parent_dir is not None:
                    url = os.path.join(parent_dir, url)
                with open(url) as f:
                    return f.read()
        except urllib.request.URLError:
            print('Attempt %d to open URL %s failed' % (attempt+1,url))

    return None


# Rebuild the index of slide/task membership for a specific task
def rebuild_task_slide_index(task_id):

    db = get_db()

    # Get the task information
    (project,task) = get_task_data(task_id)

    # Get the list of stains that are included, in lower case
    stains = set()
    if 'stains' in task:
        for s in task['stains']:
            stains.add(s.strip().lower())

    # Get the list of all, any, and not tags
    tags = {}
    for kind in ('all', 'any', 'not'):
        tags[kind] = set()
        if 'tags' in task and kind in task['tags']:
            for t in task['tags'][kind]:
                tags[kind].add(t.strip().lower())

    # Purge current task from the database
    db.execute('DELETE FROM task_slide_index WHERE task_id=?', (task_id,))

    # Get all the slides in this project along with their tags
    rc = db.execute("select s.*, group_concat(st.tag,';') as tags "
                    "from slide_info s left join slide_tags st on s.id = st.slide "
                    "where s.project==? "
                    "group by s.id", (project,))

    # Generate all the new insert commands
    index_size = 0
    for row in rc.fetchall():

        # Check the stain selector
        if len(stains) and row['stain'].lower() not in stains:
            continue

        # Get the tags for the slide
        slide_tags = set() if row['tags'] is None else set(row['tags'].strip().lower().split(';'))

        # Check against the tag specifiers
        if len(tags['all']) and len(tags['all'] - slide_tags):
            continue

        if len(tags['any']) and len(tags['any'] & slide_tags) == 0:
            continue

        if len(tags['not']) and len(tags['not'] & slide_tags):
            continue

        # The slide has survived all challenges
        db.execute('INSERT INTO task_slide_index(slide, task_id) VALUES (?,?)', (row['id'], task_id))
        index_size = index_size+1

    # Commit to the database
    db.commit()

    # Return number of entries in the index
    return index_size


# Rebuild the index of slide/task membership for all tasks in a project
def rebuild_project_slice_indices(project, specific_task_id=None):
    db = get_db()
    rc = db.execute('SELECT id, name FROM task_info WHERE project=? ORDER BY id', (project,))
    for row in rc.fetchall():
        if specific_task_id is None or specific_task_id == row['id']:
            n = rebuild_task_slide_index(row['id'])
            print('Index for task %d rebuilt with %d slides' % (row['id'], n))


# A schema against which to validate per-slide JSON files
# A schema against which the JSON is validated
slide_json_schema = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "specimen": {"type": "string" },
        "block": {"type": "string"},
        "stain": {"type": "string"},
        "section": {"type": "integer"},
        "slide": {"type": "integer"},
        "certainty": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": [ "specimen", "block", "stain" ]
}


# Imports a single slide into the database
def refresh_slide(pr, sr, slide_name, specimen, stain, block,
                  section=0, slide_no=0, cert="", tags=[], check_hash = True):

    # Database cursor
    db = get_db()

    # Check if the slide has already been imported into the database
    slide_id = pr.get_slide_by_name(slide_name)

    # If the slide is marked as a duplicate, we may need to delete it but regardless
    # we do not proceed further
    if cert == 'duplicate' or cert == 'exclude':

        # If the slide is a duplicate, we should make it disappear
        # but the problem is that we might have already done some annotation
        # for that slide. I guess it still makes sense to delete the slide
        if slide_id is not None:
            print('DELETING slide %s as DUPLICATE' % (slide_name,))
            db.execute('DELETE FROM slide WHERE id=?', (slide_id,))
            db.commit()

    # If non-duplicate slide exists, we need to check its metadata against the database
    elif slide_id is not None:
        print('Slide %s already in the database with id=%s' % (slide_name, slide_id))

        # Check if the metadata matches
        t0 = db.execute('SELECT * FROM slide '
                        'WHERE section=? AND slide=? AND stain=? AND id=?',
                        (section, slide_no, stain, slide_id)).fetchone()

        # We may need to update the properties
        if t0 is None:
            print('UPDATING metadata for slide %s' % (slide_name,))
            db.execute('UPDATE slide SET section=?, slide=?, stain=? '
                       'WHERE id=?', (section, slide_no, stain, slide_id))
            db.commit()

        # We may also need to update the specimen/block id
        t1 = db.execute('SELECT * FROM slide S '
                        '         LEFT JOIN block B on S.block_id = B.id '
                        'WHERE B.specimen_name=? AND B.block_name=? AND S.id=?',
                        (specimen, block, slide_id)).fetchone()

        # Update the specimen/block for this slide
        if t1 is None:
            print('UPDATING specimen/block for slide %s to %s/%s' % (slide_name,specimen,block))
            bid = db_get_or_create_block(pr.name, specimen, block)
            db.execute('UPDATE slide SET block_id=? WHERE id=?',
                       (bid, slide_id))
            db.commit()

        # Finally, we may need to update the tags
        current_tags = set()
        rc = db.execute('SELECT tag FROM slide_tags WHERE slide=? AND external=1', (slide_id,))
        for row in rc.fetchall():
            current_tags.add(row['tag'])

        # If tags have changed, update the tags
        if tags != current_tags:
            print('UPDATING tags for slide %s to %s' % (slide_name, str(tags)))
            db.execute('DELETE FROM slide_tags WHERE slide=? AND external=1', (slide_id,))
            for t in tags:
                db.execute('INSERT INTO slide_tags(slide, tag, external) VALUES (?, ?, 1)',
                           (slide_id, t))
            db.commit()

        # Update the slide thumbnail, etc., optionally checking against source filesystem
        update_slide_derived_data(slide_id, check_hash)

    else:
        # Create a slideref for this object. The way we have set all of this up,
        # the extension is not coded anywhere in the manifests, so we dynamically
        # check for multiple extensions
        if sr is not None:

            # Get the filename/URL of the slide
            url = sr.get_resource_url('raw', False)
            slide_ext = pathlib.Path(url).suffix[1:]

            # The raw slide has been found, so the slide will be entered into the database.
            sid = db_create_slide(pr.name, specimen, block, section, slide_no, stain,
                                  slide_name, slide_ext, tags)

            print('Slide %s located with url %s and assigned new id %d' %
                  (slide_name, sr.get_resource_url('raw', False), sid))

            # Update thumbnail and such
            update_slide_derived_data(sid, True)


# Builds up a slide database. Scans a manifest file that contains names of specimens
# and URLs to Google Sheet spreadsheets in which individual slides are matched to the
# block/section/slice/stain information. Checks if the corresponding files exist in
# the Google cloud and creates slide identifiers as needed
def refresh_slide_db(project, manifest, single_specimen=None, check_hash=True):
    # Database cursor
    db = get_db()

    # Get the project object
    pr = ProjectRef(project)

    # Get the manifest mode for this project
    mm = pr.get_dict().get('manifest_mode', 'specimen_csv')

    # Get the list of extensions for this project
    ext_list = pr.get_dict().get("raw_slide_ext", ["tif", "tiff", "mrxs", "svs"])

    # If the manifest mode is individual JSON, we scan the filesystem
    if mm == 'individual_json':
        if pr.get_url_handler() is not None:
            raise Exception('individual_json mode not supported for remote databases')

        # Build a search pattern
        raw_fmt = pr.get_url_schema()["pattern"]["raw"]

        # Create dictionary
        fmt_dict = { 
                'specimen' : single_specimen if single_specimen is not None else '*',
                'slide_name': '*', 'slide_ext': '*' }

        # Create a glob
        globstr = os.path.join(pr.url_base, raw_fmt.format(**fmt_dict))
        print('Using glob string %s' % (globstr,))

        # Iterate over globbed files
        for fn in glob.iglob(globstr):

            # Skip files that don't match extension
            p = pathlib.Path(fn)
            if p.suffix[1:] not in ext_list:
                continue

            # Check if a json exists for the file
            pj = p.with_suffix('.json')
            if pj.is_file():

                # Read the relevant keys from the JSON file
                with open(pj) as fj:

                    # Validate the JSON against the schema
                    data = json.load(fj)
                    validate(instance=data, schema=slide_json_schema)

                    # Create a slideref for this filename
                    sr = SlideRef(pr, data['specimen'], data['block'], p.stem, p.suffix[1:])
                    if not sr.resource_exists('raw', False):
                        print("Raw file does not exist for JSON: %s" % pj)
                        continue

                    # Tags should be a set
                    data['tags'] = set(data.get('tags', []))

                    # Load the slide
                    refresh_slide(pr, sr, slide_name=p.stem, check_hash=check_hash, **data)

    else:

        # Load the manifest file
        manifest_contents = load_url(manifest)
        if manifest_contents is None:
            logging.error("Cannot read URL or file: %s" % (manifest,))
            sys.exit(-1)

        # Read from the manifest file
        for line in manifest_contents.splitlines():

            # Split into words
            words = line.split()
            if len(words) != 2:
                continue

            # Check for single specimen selector
            specimen = line.split()[0]
            if single_specimen is not None and single_specimen != specimen:
                continue

            url = line.split()[1]
            print('Parsing specimen "%s" with URL "%s"' % (specimen, url))

            # Get the lines from the URL
            specimen_manifest_contents = load_url(url, os.path.dirname(manifest))
            if specimen_manifest_contents is None:
                logging.warning("Cannot read URL or file: %s" % (url,))
                continue

            # For each line in the URL consider it as a new slide
            r = csv.reader(specimen_manifest_contents.splitlines()[1:])
            for sl in r:

                # Read the elements from the string into a dict
                data = dict(zip(
                    ['slide_name', 'stain', 'block', 'section', 'slide_no', 'cert'],
                    [str(sl[0]), str(sl[1]), str(sl[2]), int(sl[3]), int(sl[4]), str(sl[5])]))

                # If the line supports tags, read them
                tagline = sl[6].lower().strip() if len(sl) > 6 else ""
                data['tags'] = set(tagline.split(';')) if len(tagline) > 0 else set()

                # Try to find a raw slide
                sr = None
                for slide_ext in ext_list:
                    sr_test = SlideRef(pr, specimen, block, slide_name, slide_ext)
                    if sr_test.resource_exists('raw', False):
                        sr = sr_test
                        break

                # Show warining if a slide has not been found
                if sr is None:
                    print('Raw image was not found for slide {slide_name}'.format(**data))

                # Update this slide
                refresh_slide(pr, sr, specimen, check_hash=check_hash, **data)

    # Refresh slice index for all tasks in this project
    rebuild_project_slice_indices(project)


def load_raw_slide_to_cache(slide_id, resource):
    # Get the data for this slide
    sr = get_slide_ref(slide_id)
    if sr is not None:
        sr.get_local_copy(resource)
    else:
        print('Slide %s not found' % (slide_id,))


@click.command('refresh-slides')
@click.argument('project')
@click.option('-m', '--manifest', default=None, type=click.Path(exists=True),
              help='CSV manifest file for projects that require them')
@click.option('-s', '--specimen', default=None,
              help='Only refresh slides for a single specimen')
@click.option('-f', '--fast', is_flag=True,
              help='Skip md5 checks for locally cached files')
@with_appcontext
def refresh_slides_command(project, manifest, specimen, fast):
    """Refresh the slide database for project PROJECT using manifest
       file MANIFEST that lists specimens and CSV files or GDrive links"""

    refresh_slide_db(project, manifest, specimen, not fast)
    click.echo('Scanning complete')


@click.command('cache-load-raw-slide')
@click.option('--slideid', prompt='Slide ID')
@with_appcontext
def cache_load_raw_slide_command(slideid):
    """Download a raw slide into the cache"""
    load_raw_slide_to_cache(slideid, 'raw')


@click.command('rebuild-task-slide-index')
@click.argument('project')
@click.option('-t', '--task', default=None,
              help='Only rebuild index for a single task for a single task')
@with_appcontext
def rebuild_task_slide_index_command(project, task):
    """Rebuild the index that links slides to tasks. This is done automatically
       when slides are imported and tasks are updated, so normally you should not
       have to run this command directly"""
    rebuild_project_slice_indices(project, task)


# --------------------------------
# TASKS
# --------------------------------

# A schema against which the JSON is validated
task_schema = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "name": {"type": "string", "minLength": 2, "maxLength": 80},
        "desc": {"type": "string", "maxLength": 1024},
        "mode": {"type": "string", "enum": ["annot", "dltrain", "browse"]},
        "dltrain": {
            "type": "object",
            "properties": {
                "labelset": {"type": "string"},
                "min-size": {"type": "integer"},
                "max-size": {"type": "integer"},
                "display-patch-size": {"type": "integer"}
            },
            "required": ["labelset"]
        },
        "restrict-access": {"type": "boolean"},
        "stains": {
            "type": "array",
            "items": {
                "type": "string"
            }
        },
        "tags": {
            "type": "object",
            "properties": {
                "any": {"type": "array", "items": {"type": "string"}},
                "all": {"type": "array", "items": {"type": "string"}},
                "not": {"type": "array", "items": {"type": "string"}}
            }
        }
    },
    "required": ["name", "mode", "restrict-access"]
}


# Add a new task (or update existing task) based on a JSON specifier. We
# will check the JSON for completeness here and then configure the task
def add_task(project, json_file, update_existing_task_id=None):

    # Get a project reference
    pr = ProjectRef(project)

    with open(json_file) as fdesc:

        # Validate the JSON against the schema
        data = json.load(fdesc)
        validate(instance=data, schema=task_schema)

        db = get_db()

        # Update or insert?
        if update_existing_task_id is None:

            # Insert the JSON
            rc = db.execute('INSERT INTO task(name,json,restrict_access) VALUES (?,?,?)',
                            (data['name'], json.dumps(data), data['restrict-access']))

            # Associate the task with the project
            rc = db.execute('INSERT INTO project_task (project, task_id, task_name) VALUES (?,?,?)',
                            (project, rc.lastrowid, data['name']))

            task_id = rc.lastrowid
            print("Successfully inserted task %s with id %d" % (data['name'], task_id))

        else:

            # Update
            task_id = int(update_existing_task_id)
            rc = db.execute('UPDATE task SET name=?, json=?, restrict_access=? WHERE id=?',
                            (data['name'], json.dumps(data), data['restrict-access'], task_id))

            if rc.rowcount != 1:
                raise ValueError("Task %d not found" % task_id)

            # If the task is currently not associated with the same project, update
            db.execute('UPDATE project_task SET project=? WHERE task_id=?', (project, task_id))

            print("Successfully updated task %d" % task_id)

        db.commit()

        # Update the slide index
        rebuild_task_slide_index(task_id)


# Get json for a task
def get_task_data(task_id):
    db = get_db()
    rc = db.execute('SELECT * FROM task_info WHERE id = ?', (task_id,)).fetchone()
    return rc['project'], json.loads(rc['json'])


@click.command('tasks-add')
@click.argument('project')
@click.argument('json')
@with_appcontext
def add_task_command(project, json):
    """Create a new task in a project using a json descriptor"""
    add_task(project, json)


@click.command('tasks-update')
@click.argument('task', type=click.INT)
@click.argument('json', type=click.Path(exists=True))
@with_appcontext
def update_task_command(task, json):
    """Update an existing task"""
    (project,t_data) = get_task_data(task)
    add_task(project, json, update_existing_task_id=task)


@click.command('tasks-list')
@click.argument('project')
@with_appcontext
def list_tasks_command(project):
    """List available tasks in a project"""
    print('%08s %s' % ('Task ID', 'Task Name'))

    db = get_db()
    rc = db.execute('SELECT id, name FROM task_info WHERE project=? ORDER BY id',
                    (project,))
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
@click.argument('project')
@with_appcontext
def list_labelsets_command(project):
    """Print label sets in a project"""
    print('%-8s %-32s %-8s %-8s' % ('Id', 'Name', '#Labels', '#Samples'))

    db = get_db()
    rc = db.execute('SELECT S.id as id, S.name as name, '
                    '       count(distinct L.id) as n_labels, count(D.id) as n_samples '
                    'FROM labelset_info S left join label L on S.id=L.labelset '
                    '                     left join training_sample D on L.id = D.label '
                    'WHERE S.project=? '
                    'GROUP BY S.id ORDER BY S.id', (project,))
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

    d = []
    for row in rc.fetchall():
        d.append({'name': row['name'], 'color': row['color'], 'description': row['description']})

    print(json.dumps(d))


# A schema for importing labels
label_schema = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "minLength": 2, "maxLength": 80},
            "description": {"type": "string", "maxLength": 1024},
            "color": {"type": "string", "minLength": 2, "maxLength": 80}
        },
        "required": ["name", "color"]
    }
}


# Shared code for updating labelset
def update_labelset_from_json(id, json_file):
    db = get_db()

    # Validate the JSON against the schem
    data = json.load(json_file)
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


@click.command('labelset-update')
@click.argument('id')
@click.argument('json_file', type=click.File('rb'))
@with_appcontext
def update_labelset_from_json_command(id, json_file):
    """Update an existing labelset"""
    update_labelset_from_json(id, json_file)
    get_db().commit()


@click.command('labelset-add')
@click.argument('project')
@click.argument('name')
@click.argument('json_file', type=click.File('rb'))
@click.option('--description', '-d', help="Labelset description")
@with_appcontext
def labelset_add_new_command(project, name, json_file, description):
    db = get_db()

    # Create a new labelset
    lsid = db.execute('INSERT INTO labelset(name,description) VALUES (?,?)',
                      (name, description)).lastrowid

    # Associate with the project
    db.execute('INSERT INTO project_labelset(project,labelset_name,labelset_id) VALUES(?,?,?)',
               (project,name,lsid))

    # Call the update command for this labelset (which also calls commit)
    update_labelset_from_json(lsid, json_file)
    db.commit()

    print('Labelset %s added with id %d' % (name, lsid))


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


# --------------------------------
# User permissions
# --------------------------------
@click.command('users-grant-permission')
@click.argument('username', type=click.STRING)
@click.option('--project','-p', help="Grant permission to specified project")
@click.option('--project-admin',help="Grant permission to specified project as admin")
@click.option('--site-admin', type=click.BOOL, help="Grant side-wide administrative permission")
@with_appcontext
def users_grant_permission_command(username, project, project_admin, site_admin):
    """Grant permissions to users on projects and globally"""
    db=get_db()

    # Check that the user is in the system
    rc = db.execute('SELECT id FROM user WHERE username=?', (username,)).fetchone()
    if rc is None:
        current_app.logger.info('User %s does not exist' % (username,))
        sys.exit(1)

    user_id = rc['id']

    if site_admin is True:
        db.execute('UPDATE user SET site_admin=1 WHERE id=?', (user_id,))
        current_app.logger.info('User %s added as site administrator' % (username,))

    if project_admin is not None:
        pr = ProjectRef(project_admin)
        db.execute('REPLACE INTO project_access(project,user,admin) VALUES (?,?,1)',
                   (project_admin, user_id))
        current_app.logger.info('User %s added as administrator for project %s' % (username,project))

    if project is not None:
        pr = ProjectRef(project)
        db.execute('REPLACE INTO project_access(project,user,admin) VALUES (?,?,0)',
                   (project, user_id))
        current_app.logger.info('User %s added as user for project %s' % (username,project))

    db.commit()



@click.command('users-list')
@with_appcontext
def users_list_command():
    """List users"""
    db = get_db()

    # Create a Pandas data frame
    df = pandas.read_sql_query('SELECT id,username,site_admin FROM user ORDER BY id', get_db())

    # Dump the database entries
    with pandas.option_context('display.max_rows', None):
        print(df)




def init_app(app):
    app.cli.add_command(init_db_command)
    app.cli.add_command(init_db_dltrain_command)
    app.cli.add_command(init_db_views_command)
    app.cli.add_command(list_projects_command)
    app.cli.add_command(project_get_json_command)
    app.cli.add_command(create_project_command)
    app.cli.add_command(update_project_command)
    app.cli.add_command(project_assign_unclaimed_command)
    app.cli.add_command(refresh_slides_command)
    app.cli.add_command(rebuild_task_slide_index_command)
    app.cli.add_command(cache_load_raw_slide_command)
    app.cli.add_command(add_task_command)
    app.cli.add_command(update_task_command)
    app.cli.add_command(list_tasks_command)
    app.cli.add_command(print_tasks_command)
    app.cli.add_command(list_labelsets_command)
    app.cli.add_command(print_labelset_labels_command)
    app.cli.add_command(dump_labelset_command)
    app.cli.add_command(update_labelset_from_json_command)
    app.cli.add_command(labelset_add_new_command)
    app.cli.add_command(users_list_command)
    app.cli.add_command(users_grant_permission_command)

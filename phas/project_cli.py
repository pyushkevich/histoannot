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

import time
import urllib.request
import logging
import json
from jsonschema import validate

from PIL import Image

from .project_ref import ProjectRef
from .slideref import SlideRef,get_slide_ref
from .db import get_db
from .gcs_handler import GCSHandler
from .auth import get_user_id
from .common import AccessLevel

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

    # Create the specimen record if not already present
    db.execute('INSERT OR IGNORE INTO specimen(private_name,project) VALUES (?,?)',
               (specimen, project))

    # Create the block record if not already present
    db.execute('INSERT OR IGNORE INTO block(specimen, block_name) '
               'SELECT id,? FROM specimen WHERE private_name=? AND project=?',
               (block, specimen, project))

    # Commit the transaction
    db.commit()

    # Retrieve the block id.
    brec = db.execute('SELECT * FROM block_info '
                      'WHERE specimen_private=? AND block_name=? AND project=?',
                      (specimen, block, project)).fetchone()
    
    return brec['id'] if brec is not None else None


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
        x = x.resize([int(x.size[0] / m), int(x.size[1] / m)])

        if not os.path.exists(thumb_dir):
            os.makedirs(thumb_dir)
        x.save(thumb_fn)


# Generic function to load a URL or local file into a string
def load_url(url, parent_dir=None):
    for attempt in range(5):
        try:
            if url.startswith('http://') or url.startswith('https://'):
                resp = urllib.request.urlopen(url)
                return resp.read().decode('utf-8')
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

    # Get the list of all specimens that are included, don't touch case
    specimens = set()
    if 'specimens' in task:
        for s in task['specimens']:
            specimens.add(s.strip())

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

        # Check the specimen selector
        if len(specimens) and row['specimen_private'] not in specimens:
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
        "slide_number": {"type": "integer"},
        "cert": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": [ "specimen", "block", "stain" ]
}


# Imports a single slide into the database
def refresh_slide(pr, sr, slide_name, specimen, stain, block,
                  section=0, slide_number=0, cert="", tags=[], check_hash = True,
                  **kwargs):

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
                        'WHERE section=? AND slide=? AND stain=? AND id=? AND slide_ext=?',
                        (section, slide_number, stain, slide_id, sr.slide_ext)).fetchone()

        # We may need to update the properties
        if t0 is None:
            print('UPDATING metadata for slide %s' % (slide_name,))
            db.execute('UPDATE slide SET section=?, slide=?, stain=?, slide_ext=? '
                       'WHERE id=?', (section, slide_number, stain, sr.slide_ext, slide_id))
            db.commit()

        # We may also need to update the specimen/block id
        t1 = db.execute('SELECT * FROM slide_info '
                        'WHERE specimen_private=? AND block_name=? AND id=?',
                        (specimen, block, slide_id)).fetchone()

        # Update the specimen/block for this slide
        if t1 is None:
            print('UPDATING specimen/block for slide %s to %s/%s' % (slide_name,specimen,block))
            bid = db_get_or_create_block(pr.name, specimen, block)
            db.execute('UPDATE slide SET block_id=? WHERE id=?', (bid, slide_id))
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
            sid = db_create_slide(pr.name, specimen, block, section, slide_number, stain,
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

                    try:
                        # Validate the JSON against the schema
                        data = json.load(fj)
                        validate(instance=data, schema=slide_json_schema)

                        # Create a slideref for this filename
                        sr = SlideRef(pr, data['specimen'], data['block'], p.stem, p.suffix[1:])
                        print(data['specimen'], data['block'], p.stem, p.suffix[1:])
                        if not sr.resource_exists('raw', False):
                            print("Raw file does not exist for JSON: %s" % pj)
                            continue

                        # Tags should be a set
                        data['tags'] = set(data.get('tags', []))

                        # Load the slide
                        refresh_slide(pr, sr, slide_name=p.stem, check_hash=check_hash, **data)
                    except:
                        print('Exception importing slide JSON: {}'.format(pj))
                        print(traceback.format_exc())
                        pass

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
            print(specimen_manifest_contents)
            r = csv.reader(specimen_manifest_contents.splitlines()[1:])
            for sl in r:
                # Read the elements from the string into a dict, ignoring blank lines or other
                # lines that cannot be parsed
                try:
                    data = dict(zip(
                        ['slide_name', 'stain', 'block', 'section', 'slide_number', 'cert'],
                        [str(sl[0]), str(sl[1]), str(sl[2]), int(sl[3]), int(sl[4]), str(sl[5])]))
                except ValueError:
                    continue

                # If the line supports tags, read them
                tagline = sl[6].lower().strip() if len(sl) > 6 else ""
                data['tags'] = set(tagline.split(';')) if len(tagline) > 0 else set()

                # Try to find a raw slide
                sr = None
                for slide_ext in ext_list:
                    sr_test = SlideRef(pr, specimen, data['block'], data['slide_name'], slide_ext)
                    if sr_test.resource_exists('raw', False):
                        sr = sr_test
                        break

                # Show warining if a slide has not been found
                if sr is None:
                    print('Raw image was not found for slide {slide_name}'.format(**data))

                # Update this slide
                refresh_slide(pr, sr, specimen=specimen, check_hash=check_hash, **data)

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
@click.option('-s', '--specimen', default=None, multiple=True,
              help='Only refresh slides for a single specimen')
@click.option('-f', '--fast', is_flag=True,
              help='Skip md5 checks for locally cached files')
@with_appcontext
def refresh_slides_command(project, manifest, specimen, fast):
    """Refresh the slide database for project PROJECT using manifest
       file MANIFEST that lists specimens and CSV files or GDrive links"""

    if len(specimen) > 0:
        for s in specimen:
            refresh_slide_db(project, manifest, s, not fast)
    else:
        refresh_slide_db(project, manifest, None, not fast)
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
        "mode": {"type": "string", "enum": ["annot", "dltrain", "browse", "sampling"]},
        "reference_task": {"type": "string"},
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
        "sampling": {
            "type": "object",
            "properties": {
                "labelset": {"type": "string"}
            },
            "required": ["labelset"]
        },
        "restrict-access": {"type": "boolean"},
        "anonymize": {"type": "boolean"},
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
        },
        "specimens": {
            "type": "array",
            "items": {
                "type": "string"
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

        # Update the anonymization, but only if specified in the task json
        if 'anonymize' in data:
            db.execute('UPDATE task SET anonymize=? WHERE id=?', (data['anonymize'], task_id))

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
    
    # Create a Pandas data frame
    df = pandas.read_sql_query('SELECT id, name, restrict_access FROM task_info WHERE project=? ORDER BY id', 
                               get_db(), params=(project,))

    # Dump the database entries
    with pandas.option_context('display.max_rows', None):
        print(df)


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
def update_edit_meta_to_current(meta_id):
    db = get_db()
    db.execute('UPDATE edit_meta SET editor=?, t_edit=? WHERE id=?',
               (g.user['id'], time.time(), meta_id))
    

def update_edit_meta(meta_id, creator=None, editor=None, t_create=None, t_edit=None):
    db = get_db()
    db.execute('UPDATE edit_meta SET creator=?, editor=?, t_create=?, t_edit=? WHERE id=?',
               (creator, editor, t_create, t_edit, meta_id))



def create_edit_meta(creator=None, editor=None, t_create=None, t_edit=None):
    t_stamp = time.time()
    db = get_db()

    return db.execute("INSERT INTO edit_meta (creator, editor, t_create, t_edit) "
                      "VALUES (?,?,?,?) RETURNING id",
                      (creator if creator is not None else g.user['id'], 
                       editor if editor is not None else g.user['id'], 
                       t_create if t_create is not None else t_stamp, 
                       t_edit if t_edit is not None else t_stamp)).fetchone()['id']



# --------------------------------
# User access level
# --------------------------------
@click.command('users-set-site-admin')
@click.argument('username', type=click.STRING)
@click.option('--revoke','-R', is_flag=True, help="Revoke sysadmin status instead of granting it")
@with_appcontext
def users_set_site_admin_command(username, revoke):
    """Make the specified user a site administrator"""
    db=get_db()

    # Check that the user is in the system
    user_id = get_user_id(username)
    if user_id is None:
        raise ValueError('User %s does not exist' % (username,))

    # Update the user level
    if not revoke:
        db.execute('UPDATE user SET site_admin=1 WHERE id=?', (user_id,))
        current_app.logger.info('User %s added as site administrator' % (username,))
    else:
        db.execute('UPDATE user SET site_admin=0 WHERE id=?', (user_id,))
        current_app.logger.info('User %s site administrator status revoked' % (username,))

    db.commit()


def user_set_access_level(username, project, project_and_tasks, task):
    db=get_db()

    # Check that the user is in the system
    user_id = get_user_id(username)
    if user_id is None:
        raise ValueError('User %s does not exist' % (username,))

    # Provide access to all requested projects
    for i, project_set in enumerate([project, project_and_tasks]):
        for (p_k, abbrv) in project_set:
            pr = ProjectRef(p_k)
            access_level = AccessLevel.from_abbrv(abbrv)
            pr.user_set_access_level(user_id, access_level)
            if i > 0:
                pr.user_set_all_tasks_access_level(user_id, access_level)

    # Provide access to all requested tasks
    for (t_k, abbrv) in task:
        access_level = AccessLevel.from_abbrv(abbrv)
        rc = db.execute('SELECT * FROM task_info WHERE id=?', (t_k,)).fetchone()
        pr = ProjectRef(rc['project'])
        pr.user_set_task_access_level(t_k, user_id, access_level)


@click.command('users-set-access-level')
@click.argument('username', type=click.STRING)
@click.option('--project','-p', help="Set access level for specified project", nargs=2, multiple=True)
@click.option('--project-and-tasks','-P', help="Set access level for specified project and subtasks", nargs=2, multiple=True)
@click.option('--task','-t', help="Set access level for specified task", nargs=2, multiple=True)
@click.option('--csv', is_flag=True, help="Read usernames from CSV with column 'username'")
@with_appcontext
def users_set_access_level_command(username, project, project_and_tasks, task, csv):
    """
    Set project/task access permissions for a user or list of users.

    Typical use of this function is to provide a list of project and/or tasks with access levels that
    the user will be given access to. Access levels are 'none', 'read', 'write', 'admin', abbreviated
    by 'N', 'R', 'W' and 'A'.

    Example usage:

    flask users-set-access-level -p proj1 R -t 22 R -P proj2 W username
    flask users-set-access-level -p proj1 R -t 22 R -P proj2 W --csv users.csv
    """
    db=get_db()

    # Check if CSV specified
    if csv is True:
        df = pandas.read_csv(username)
        if 'username' not in df.columns:
            print('CSV file missing column "username"')
            sys.exit(-1)
        users = df['username'].unique()
    else:
        users = [username]

    # Loop over the users
    for user in users:
        user_set_access_level(user, project, project_and_tasks, task)


@click.command('users-set-access-level-from-metadata')
@click.option('--project','-p', help="Set access level for specified project", multiple=True)
@with_appcontext
def users_set_access_level_from_metadata_command(project):
    """
    Set project/task access permissions based on edit metadata in the database.

    Users will be given write access to restricted tasks if they have contributed to those
    tasks in the past, otherwise their access will not be changed. This command is mainly
    intended for database upgrades from older version of PHAS when permissions were just
    binary access flags
    """
    db=get_db()

    # Get a list of users
    users = [ {'id': x['id'], 'username': x['username']} for x in db.execute('SELECT * FROM user').fetchall() ]

    # This counts the number of annotations that each user contributed to each task
    annot_counts = {}
    rc = db.execute('select U.id as user, A.task_id as task, count(M.id) as n FROM user U '
                    '  left join edit_meta M on (U.id=M.creator or U.id=M.editor) '
                    '  left join annot A on A.meta_id = M.id where A.task_id is not null '
                    '  group by U.id, A.task_id')
    for row in rc.fetchall():
        annot_counts[(row['user'], row['task'])] = int(row['n'])

    # This counts the number of dltrain markings that each user contributed to each task
    dltrain_counts = {}
    rc = db.execute('select U.id as user, T.task as task, count(M.id) as n FROM user U '
                    '  left join edit_meta M on (U.id=M.creator or U.id=M.editor) '
                    '  left join training_sample T on T.meta_id = M.id where T.task is not null '
                    '  group by U.id, T.task')
    for row in rc.fetchall():
        dltrain_counts[(row['user'], row['task'])] = int(row['n'])

    print(annot_counts, dltrain_counts)

    # Iterate over projects
    all_proj = db.execute('SELECT id FROM project').fetchall();
    for row_p in all_proj:
        p_id = row_p['id']
        if len(project)==0 or p_id in project:
            # Get the project reference
            pr = ProjectRef(p_id)

            # Iterate over the tasks in the project
            for t_id in pr.get_tasks():
                print('Project {} Task {}: '.format(p_id, t_id))

                # Iterate over users
                for u in users:
                    count = annot_counts.get((u['id'], t_id), 0) + dltrain_counts.get((u['id'], t_id), 0)
                    if count > 0:
                        rc = pr.user_set_task_access_level(t_id, u['id'], "write", increase_only = True)
                        if rc:
                            print('User {} access increased to "write"'.format(u['username']))
                        else:
                            print('User {} already has write access'.format(u['username']))



@click.command('users-list')
@click.option('--project','-p', help="List users with access to given project", multiple=True)
@click.option('--task','-t', help="List users with access to given task", multiple=True, type=click.INT)
@click.option('--csv', '-c', type=click.Path(), help="Export to a CSV file")
@with_appcontext
def users_list_command(project, task, csv):
    """List users"""
    db = get_db()

    # Subset of users to include
    user_set = set({})

    # For each project, get a list of user IDs that have access
    for p in project:
        rc = db.execute('SELECT id FROM user U '
                        'LEFT JOIN project_access PA ON U.id = PA.user '
                        'WHERE project=? AND PA.access != "none"', (p,))
        for row in rc.fetchall():
            user_set.add(row['id'])

    # For each task, get a list of user IDs that have access
    for t in task:
        rc = db.execute('SELECT id FROM user U '
                        'LEFT JOIN task_access TA ON U.id = TA.user '
                        'WHERE task=? and TA.access != "none"', (t,))
        for row in rc.fetchall():
            user_set.add(row['id'])

    # Initialize dataframe
    df = pandas.read_sql_query('SELECT id,username,email,site_admin FROM user ORDER BY id', get_db())

    # Get subset of selected rows
    if len(user_set):
        df = df[df['id'].isin(user_set)]

    # Dump the database entries
    if csv:
        df.to_csv(csv)
    else:
        with pandas.option_context('display.max_rows', None):
            print(df)


# --------------------------------
# User permissions
# --------------------------------
@click.command('users-list-permissions')
@click.argument('username', type=click.STRING)
@with_appcontext
def users_list_permissions(username):
    """List permissions assigned to a user"""
    db=get_db()

    # Check that the user is in the system
    rc = db.execute('SELECT id, site_admin FROM user WHERE username=?', (username,)).fetchone()
    if rc is None:
        current_app.logger.info('User %s does not exist' % (username,))
        sys.exit(1)

    user_id = rc['id']
    site_admin = rc['site_admin']

    # Describe the user
    print('User:    %20s   Access level: %s' % (username, 'Site Admin' if site_admin > 0 else 'Regular'))

    # List all projects that the user has access to
    rc = db.execute('SELECT project, access FROM project_access WHERE user=? AND access != "none"', (user_id,))
    for row in rc.fetchall():
        prj = row['project']
        print('Project: %20s   Access level: %s ' % (prj, row['access']))
        rc2 = db.execute('SELECT TI.name, TI.id, TA.access '
                         'FROM task_info TI left join task_access TA on TI.id = TA.task '
                         'WHERE TI.project = ? '
                         '  AND ((TI.restrict_access IS FALSE) OR (TA.user=? AND TA.access != "none"))',
                         (prj, user_id))
        for row_t in rc2.fetchall():
            access = row_t['access'] if row_t['access'] is not None else row['access']
            print('  Task: %03d [%40s]   Access level: %s' % (row_t['id'], row_t['name'], access))


# ---------------------
# Anonymization aliases
# ---------------------
@click.command('anon-list-specimen-aliases')
@click.argument('project', type=click.STRING)
@with_appcontext
def anon_list_specimen_aliases(project):
    """List anonymization aliases for a project"""
    df = pandas.read_sql_query('SELECT private_name, public_name FROM specimen WHERE project=?', 
                               get_db(), params=(project,))
    with pandas.option_context('display.max_rows', None, 'display.max_colwidth', 20):
        print(df)


@click.command('anon-set-specimen-aliases')
@click.argument('project', type=click.STRING)
@click.argument('csv', type=click.Path(exists=True))
@with_appcontext
def anon_set_specimen_aliases_csv(project, csv):
    """Set anonymization aliases for project PROJECT from file CSV, overriding existing aliases
       The file CSV should have two columns, first is specimen name, second is alias"""
    db=get_db()
    try:
        df = pandas.read_csv(csv, names=('private', 'public'))
        
        # Drop existing anonymization mapping
        for index, row in df.iterrows():
            db.execute('UPDATE specimen SET public_name=? WHERE project=? AND private_name=?',
                       (row['public'], project, row['private']))  
        db.commit()
    
    except:
        print("Error reading/parsing CSV file")


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
    app.cli.add_command(users_set_access_level_command)
    app.cli.add_command(users_set_access_level_from_metadata_command)
    app.cli.add_command(users_list_permissions)
    app.cli.add_command(users_set_site_admin_command)
    app.cli.add_command(anon_list_specimen_aliases)
    app.cli.add_command(anon_set_specimen_aliases_csv)

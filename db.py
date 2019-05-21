import sqlite3
import os
import re
import csv

import click
from flask import current_app, g
from flask.cli import with_appcontext

import openslide

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
            for ext in ('.svs', '.tiff'):
                fn_test=os.path.join(src_dir, row[0]+ext)
                if os.path.exists(fn_test):
                    fn=fn_test
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
@click.option('--specimen', prompt='Specimen ID')
@click.option('--block', prompt='Block ID')
@click.option('--match', prompt='Match CSV file')
@with_appcontext
def add_block_command(src, specimen, block, match):
    """Scan the local directory for slides and create block and slide database"""
    rebuild_slide_db_block(src, specimen, block, match)
    click.echo('Scanned for slides')



def init_app(app):
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)
    app.cli.add_command(scan_slides_command)
    app.cli.add_command(add_block_command)

from flask import (
    Blueprint, flash, g, redirect, render_template, request, url_for, make_response
)
from werkzeug.exceptions import abort

from flaskr.auth import login_required
from flaskr.db import get_db
from io import BytesIO

import os

bp = Blueprint('slide', __name__)

# The index
@bp.route('/')
def index():
    db = get_db()
    blocks = db.execute(
        'SELECT b.*, count(s.id) as nslides'
        ' FROM block b join slide s on b.id = s.block_id'
        ' ORDER BY specimen_name, block_name ASC').fetchall()
    return render_template('slide/index.html', blocks=blocks)

# The block detail listing
@bp.route('/block/<int:id>/detail', methods=('GET', 'POST'))
def block_detail(id):
    db = get_db()
    block = db.execute(
        'SELECT * FROM block WHERE id=?', (id,)).fetchone()
    slides = db.execute(
        'SELECT * FROM slide WHERE block_id=? ORDER BY section, slide ASC', (id,)).fetchall()
    return render_template('slide/block_detail.html', block=block, slides=slides)

# The slide view
@bp.route('/slide/<int:id>/view', methods=('GET', 'POST'))
def slide_view(id):
    db = get_db()
    slide_info = db.execute(
        'SELECT s.*, b.specimen_name, b.block_name'
        ' FROM block b join slide s on b.id = s.block_id'
        ' WHERE s.id = ?', (id,)).fetchone()

    return render_template('slide/slide_view.html', slide_id = id, slide_info = slide_info)

# Get the DZI for a slide
@bp.route('/slide/<int:id>.dzi', methods=('GET', 'POST'))
def dzi(id):
    format = 'jpeg'
    
    # Get the tiff filename for slide
    db = get_db()
    tiff_file = db.execute(
        'SELECT tiff_file FROM slide WHERE id=?', (id,)).fetchone()['tiff_file']

    try:
        slide = bp.cache.get(tiff_file)
        slide.filename = os.path.basename(tiff_file)
        resp = make_response(slide.get_dzi('jpeg'))
        resp.mimetype = 'application/xml'
        return resp
    except KeyError:
        # Unknown slug
        abort(404)
    

class PILBytesIO(BytesIO):
    def fileno(self):
        '''Classic PIL doesn't understand io.UnsupportedOperation.'''
        raise AttributeError('Not supported')


# Get the tiles for a slide
@bp.route('/slide/<int:id>_files/<int:level>/<int:col>_<int:row>.<format>',  methods=('GET', 'POST'))
def tile(id, level, col, row, format):
    format = format.lower()
    if format != 'jpeg' and format != 'png':
        # Not supported by Deep Zoom
        return 'bad format'
        abort(404)

    # Get the tiff filename for slide
    db = get_db()
    tiff_file = db.execute(
        'SELECT tiff_file FROM slide WHERE id=?', (id,)).fetchone()['tiff_file']

    tile = bp.cache.get(tiff_file).get_tile(level, (col, row))

    buf = PILBytesIO()
    tile.save(buf, format, quality=75)
    resp = make_response(buf.getvalue())
    resp.mimetype = 'image/%s' % format
    return resp


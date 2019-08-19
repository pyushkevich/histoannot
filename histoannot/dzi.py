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
    Blueprint, flash, g, redirect, render_template, request, url_for, make_response, current_app, send_from_directory
)
from werkzeug.exceptions import abort

from io import BytesIO
import os
import json
import time
import numpy as np

from histoannot.slideref import SlideRef, get_slideref_by_info
from histoannot.cache import get_slide_cache

bp = Blueprint('dzi', __name__)

# Get the DZI for a slide
@bp.route('/dzi/<mode>/<specimen>/<block>/<slide_name>.<slide_ext>.dzi', methods=('GET', 'POST'))
def dzi(mode, specimen, block, slide_name, slide_ext):
    format = 'jpeg'

    # Get the raw SVS/tiff file for the slide (the resource should exist, 
    # or else we will spend a minute here waiting with no response to user)
    sr = get_slideref_by_info(specimen, block, slide_name, slide_ext)

    tiff_file = sr.get_local_copy('raw', check_hash=True)

    # Get an affine transform if that is an option
    affine_file = sr.get_local_copy('affine', check_hash=True) if mode=='affine' else None
    
    try:
        slide = get_slide_cache().get(tiff_file, affine_file)
        slide.filename = os.path.basename(tiff_file)
        resp = make_response(slide.get_dzi('jpeg'))
        resp.mimetype = 'application/xml'
        return resp
    except (KeyError, ValueError):
        # Unknown slug
        abort(404)

# What percentage of the slide is available locally (in the cache)
@bp.route('/dzi/api/<int:id>/cache_progress', methods=('GET', ))
def get_cache_progress(id):

    sr = get_slide_ref(id)
    progress = sr.get_download_progress('raw');
    print("Progress: ", progress)
    return json.dumps({'progress':progress}), 200, {'ContentType':'application/json'} 


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


class PILBytesIO(BytesIO):
    def fileno(self):
        '''Classic PIL doesn't understand io.UnsupportedOperation.'''
        raise AttributeError('Not supported')

# Get the tiles for a slide
@bp.route('/dzi/<mode>/<specimen>/<block>/<slide_name>.<slide_ext>_files/<int:level>/<int:col>_<int:row>.<format>',
        methods=('GET', 'POST'))
def tile(mode, specimen, block, slide_name, slide_ext, level, col, row, format):
    format = format.lower()
    if format != 'jpeg' and format != 'png':
        # Not supported by Deep Zoom
        return 'bad format'
        abort(404)

    # Get the raw SVS/tiff file for the slide (the resource should exist, 
    # or else we will spend a minute here waiting with no response to user)
    sr = get_slideref_by_info(specimen, block, slide_name, slide_ext)
    tiff_file = sr.get_local_copy('raw')
    affine_file = sr.get_local_copy('affine') if mode == 'affine' else None

    cache = get_slide_cache()
    tile = cache.get(tiff_file, affine_file).get_tile(level, (col, row))

    buf = PILBytesIO()
    tile.save(buf, format, quality=75)
    resp = make_response(buf.getvalue())
    resp.mimetype = 'image/%s' % format
    return resp

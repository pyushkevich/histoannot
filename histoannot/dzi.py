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
import urllib
import urllib2
import psutil

from rq import Queue, Connection, Worker
from rq.job import Job, JobStatus
from redis import Redis

from openslide import OpenSlide, OpenSlideError
from openslide.deepzoom import DeepZoomGenerator
from histoannot.slideref import SlideRef, get_slideref_by_info
from histoannot.cache import AffineTransformedOpenSlide

bp = Blueprint('dzi', __name__)

import click
import numpy
from flask.cli import with_appcontext


# The function that is enqueued in RQ
def do_preload_file(specimen, block, slide_name, slide_ext):

    sr = get_slideref_by_info(specimen, block, slide_name, slide_ext)
    print('Fetching %s %s %s.%s' % (specimen, block, slide_name, slide_ext))
    tiff_file = sr.get_local_copy('raw', check_hash=True)
    print('Fetched to %s' % tiff_file)
    return tiff_file


# Prepare the DZI for a slide. Must be called first
@bp.route('/dzi/preload/<specimen>/<block>/<slide_name>.<slide_ext>.dzi', methods=('GET', 'POST'))
def dzi_preload(specimen, block, slide_name, slide_ext):

    # Check if the file exists locally. If so, there is no need to queue a worker
    sr = get_slideref_by_info(specimen, block, slide_name, slide_ext)
    tiff_file = sr.get_local_copy('raw', check_hash=True, dry_run=True)
    if tiff_file is not None:
        return json.dumps({ "status" : JobStatus.FINISHED })

    # Get a redis queue
    q = Queue("preload", connection=Redis())
    job = q.enqueue(do_preload_file, specimen, block, slide_name, slide_ext,
            job_timeout="120s", result_ttl="60s")

    # Stick the properties into the job
    job.meta['args']=(specimen, block, slide_name, slide_ext, 'raw')
    job.save_meta()

    # Return the job id
    return json.dumps({ "job_id" : job.id, "status" : JobStatus.QUEUED })


# Check the status of a job
@bp.route('/dzi/job/<job_id>/status', methods=('GET', 'POST'))
def dzi_job_status(job_id):
    q = Queue("preload", connection=Redis())
    j = q.fetch_job(job_id)

    if j is None:
        return 'bad job'
        abort(404)

    res = { 'status' : j.get_status() }

    if j.get_status() == JobStatus.STARTED:
        (specimen, block, slide_name, slide_ext, resource) = j.meta['args']
        sr = get_slideref_by_info(specimen, block, slide_name, slide_ext)
        res['progress'] = sr.get_download_progress(resource)

    return json.dumps(res)



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
        osa = AffineTransformedOpenSlide(tiff_file, affine_file)
        dz = DeepZoomGenerator(osa)
        dz.filename = os.path.basename(tiff_file)
        resp = make_response(dz.get_dzi('jpeg'))
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

    os = None
    if mode == 'affine':
        affine_file = sr.get_local_copy('affine')
        os = AffineTransformedOpenSlide(tiff_file, affine_file)
    else:
        os = OpenSlide(tiff_file)

    dz = DeepZoomGenerator(os)
    tile = dz.get_tile(level, (col, row))

    buf = PILBytesIO()
    tile.save(buf, format, quality=75)
    resp = make_response(buf.getvalue())
    resp.mimetype = 'image/%s' % format
    return resp


# Get an image patch at level 0 from the raw image
@bp.route('/dzi/patch/<specimen>/<block>/<slide_name>.<slide_ext>/<int:x>_<int:y>_<int:w>_<int:h>.<format>',
        methods=('GET','POST'))
def get_patch(specimen, block, slide_name, slide_ext, x, y, w, h, format):

    format = format.lower()
    if format != 'jpeg' and format != 'png':
        # Not supported by Deep Zoom
        return 'bad format'
        abort(404)

    # Get the raw SVS/tiff file for the slide (the resource should exist, 
    # or else we will spend a minute here waiting with no response to user)
    sr = get_slideref_by_info(specimen, block, slide_name, slide_ext)
    tiff_file = sr.get_local_copy('raw')
    
    # Read the region centered on the box of size 512x512
    os = OpenSlide(tiff_file)
    tile = os.read_region((x, y), 0, (512, 512));

    # Convert to PNG
    buf = PILBytesIO()
    tile.save(buf, format)
    resp = make_response(buf.getvalue())
    resp.mimetype = 'image/%s' % format
    return resp


# Command to run preload worker
@click.command('preload-worker-run')
@with_appcontext
def run_preload_worker_cmd():
    with Connection():
        w = Worker("preload")
        w.work()

# Command to ping master
@click.command('dzi-node-ping-master')
@with_appcontext
def delegate_dzi_ping_command():
    """Ping the master continuously to let it know we exist"""

    if current_app.config['HISTOANNOT_SERVER_MODE'] != 'dzi_node':
        print('Ping process terminating, not in dzi_mode')
        return 0

    if 'HISTOANNOT_MASTER_URL' not in current_app.config:
        print('Missing HISTOANNOT_MASTER_URL in config')
        return 2

    # Get the external IP address (GCP-specific)
    opener = urllib2.build_opener()
    opener.addheaders = [('Metadata-Flavor','Google')]
    external_ip = opener.open(
            'http://metadata/computeMetadata/v1/instance'
            '/network-interfaces/0/access-configs/0/external-ip').read()

    # Store our URL (TODO: read port from config)
    node_url = 'http://%s:5000' % (external_ip,)
    master_url = current_app.config['HISTOANNOT_MASTER_URL'] + '/delegate/ping'

    while True:
        try:
            cpu_percent=psutil.cpu_percent()
            urllib2.urlopen(master_url, 
                    urllib.urlencode([('url', node_url), ('cpu_percent',str(cpu_percent))]), timeout=10)
        except:
            pass
        time.sleep(30)


# CLI stuff
def init_app(app):
    app.cli.add_command(run_preload_worker_cmd)
    app.cli.add_command(delegate_dzi_ping_command)

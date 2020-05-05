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
    Blueprint, flash, g, redirect, render_template, request, url_for, make_response, current_app, send_file
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
import re
import functools

from rq import Queue, Connection, Worker
from rq.job import Job, JobStatus
from redis import Redis
from random import randint

from openslide import OpenSlide, OpenSlideError
from openslide.deepzoom import DeepZoomGenerator
from histoannot.slideref import SlideRef,get_slide_ref
from histoannot.project_ref import ProjectRef
from histoannot.cache import AffineTransformedOpenSlide
from histoannot.delegate import find_delegate_for_slide
from histoannot.auth import project_access_required

bp = Blueprint('dzi', __name__)

import click
import numpy
from flask.cli import with_appcontext


# The function that is enqueued in RQ
def do_preload_file(project, proj_dict, specimen, block, resource, slide_name, slide_ext):

    sr = SlideRef(ProjectRef(project, proj_dict), specimen, block, slide_name, slide_ext)
    print('Fetching %s %s %s %s.%s' % (project, specimen, block, slide_name, slide_ext))
    tiff_file = sr.get_local_copy(resource, check_hash=True)
    print('Fetched to %s' % tiff_file)
    return tiff_file


# Forward to a worker if possible
def forward_to_worker(view):
    @functools.wraps(view)
    def wrapped_view(**kwargs):

        # Find or allocate an assigned worker for this slide
        worker_url = find_delegate_for_slide(project=kwargs['project'], slide_name=kwargs['slide_name'])

        # If no worker, just call the method
        if worker_url is None:
            return view(**kwargs)

        # Call the worker's method
        full_url = '%s/%s' % (worker_url, request.full_path)

        # Take the project information and embed it in the call as a POST parameter
        pr = ProjectRef(kwargs['project'])
        post_data = urllib.urlencode({'project_data': json.dumps(pr.get_dict())})
        return urllib2.urlopen(full_url, post_data).read()

    return wrapped_view


# Get the project data if present in the request
def dzi_get_project_ref(project):

    if current_app.config['HISTOANNOT_SERVER_MODE'] != 'dzi_node':
        # TODO: Check permission!
        return ProjectRef(project)

    else:
        pdata = json.loads(request.form.get('project_data'))
        return ProjectRef(project, pdata)


# Prepare the DZI for a slide. Must be called first
@bp.route('/dzi/preload/<project>/<specimen>/<block>/<resource>/<slide_name>.<slide_ext>.dzi', methods=('GET', 'POST'))
@project_access_required
@forward_to_worker
def dzi_preload(project, specimen, block, resource, slide_name, slide_ext):

    # Get a project reference, using either local database or remotely supplied dict
    pr = dzi_get_project_ref(project)

    # Check if the file exists locally. If so, there is no need to queue a worker
    sr = SlideRef(pr, specimen, block, slide_name, slide_ext)
    tiff_file = sr.get_local_copy(resource, check_hash=True, dry_run=True)
    if tiff_file is not None:
        return json.dumps({ "status" : JobStatus.FINISHED })

    # Get a redis queue
    q = Queue(current_app.config['PRELOAD_QUEUE'], connection=Redis())
    job = q.enqueue(do_preload_file, project, pr.get_dict(),
            specimen, block, resource, slide_name, slide_ext,
            job_timeout="300s", result_ttl="60s")

    # Stick the properties into the job
    job.meta['args']=(project, specimen, block, resource, slide_name, slide_ext)
    job.save_meta()

    # Return the job id
    return json.dumps({ "job_id" : job.id, "status" : JobStatus.QUEUED })


# Check the status of a job
@bp.route('/dzi/job/<project>/<slide_name>/<job_id>/status', methods=('GET', 'POST'))
@forward_to_worker
def dzi_job_status(project, slide_name, job_id):
    pr = dzi_get_project_ref(project)
    q = Queue(current_app.config['PRELOAD_QUEUE'], connection=Redis())
    j = q.fetch_job(job_id)

    if j is None:
        return 'bad job'
        abort(404)

    res = { 'status' : j.get_status() }

    if j.get_status() == JobStatus.STARTED:
        (project, specimen, block, resource, slide_name, slide_ext) = j.meta['args']
        sr = SlideRef(pr, specimen, block, slide_name, slide_ext)
        res['progress'] = sr.get_download_progress(resource)

    return json.dumps(res)


# Get affine matrix corresponding to affine mode and resolution
def get_affine_matrix(slide_ref, mode, resolution='raw', target='annot'):

    M_aff=np.eye(3)
    M_res=np.eye(3)
    if mode == 'affine':

        affine_fn = slide_ref.get_local_copy('affine')
        if affine_fn is not None:
            M_aff = np.loadtxt(affine_fn)

    if resolution == 'x16':

        M_res[0,0] = 16.0
        M_res[1,1] = 16.0

    if target == 'annot':
        M = np.dot(M_aff, M_res)
    elif target == 'image':
        M_res_inv = np.linalg.inv(M_res)
        M = np.dot(np.dot(M_res_inv,M_aff),M_res)
        #M = np.dot(np.dot(M_res,M_aff),np.linalg.inv(M_res))

    return M


# Read the dimensions of the slide at a given level. Uses information from the
# resolution.txt file
def get_slide_raw_dims(slide_ref):

    # Resolution files have level information. We convert them to json to read easily
    resfile = slide_ref.get_local_copy('dims')
    if resfile is not None:
        with open(resfile) as f:

            # Expression to search for
            cre = re.compile("Level dimensions: *(.*)")

            for line in f:
                m = cre.match(line)
                if m is not None:
                    json_line = m.group(1).replace('(','[').replace(')',']')
                    return json.loads(json_line)[0]

    return None



# Get the DZI for a slide
@bp.route('/dzi/<mode>/<project>/<specimen>/<block>/<resource>/<slide_name>.<slide_ext>.dzi',
          methods=('GET', 'POST'))
@project_access_required
@forward_to_worker
def dzi(mode, project, specimen, block, resource, slide_name, slide_ext):

    # Get a project reference, using either local database or remotely supplied dict
    pr = dzi_get_project_ref(project)

    # Get the raw SVS/tiff file for the slide (the resource should exist, 
    # or else we will spend a minute here waiting with no response to user)
    sr = SlideRef(pr, specimen, block, slide_name, slide_ext)

    tiff_file = sr.get_local_copy(resource, check_hash=True)

    # Get an affine transform if that is an option
    A = get_affine_matrix(sr, mode, resource, 'image')

    # Load the slide
    osa = AffineTransformedOpenSlide(tiff_file, A)
    dz = DeepZoomGenerator(osa)
    dz.filename = os.path.basename(tiff_file)
    resp = make_response(dz.get_dzi('png'))
    resp.mimetype = 'application/xml'
    return resp


# Download the raw data for the slide
@bp.route('/dzi/download/<mode>/<project>/<specimen>/<block>/<resource>/<slide_name>.<slide_ext>',
          methods=('GET', 'POST'))
@project_access_required
@forward_to_worker
def dzi_download(mode, project, specimen, block, resource, slide_name, slide_ext):

    # Get a project reference, using either local database or remotely supplied dict
    pr = dzi_get_project_ref(project)

    # Get the raw SVS/tiff file for the slide (the resource should exist,
    # or else we will spend a minute here waiting with no response to user)
    sr = SlideRef(pr, specimen, block, slide_name, slide_ext)

    # Get the resource
    tiff_file = sr.get_local_copy(resource, check_hash=True)
    return send_file(tiff_file)


class PILBytesIO(BytesIO):
    def fileno(self):
        '''Classic PIL doesn't understand io.UnsupportedOperation.'''
        raise AttributeError('Not supported')


# Get the tiles for a slide
@bp.route('/dzi/<mode>/<project>/<specimen>/<block>/<resource>/<slide_name>.<slide_ext>_files/<int:level>/<int:col>_<int:row>.<format>',
        methods=('GET', 'POST'))
@project_access_required
@forward_to_worker
def tile(mode, project, specimen, block, resource, slide_name, slide_ext, level, col, row, format):
    format = format.lower()
    if format != 'jpeg' and format != 'png':
        # Not supported by Deep Zoom
        return 'bad format'
        abort(404)

    # Get a project reference, using either local database or remotely supplied dict
    pr = dzi_get_project_ref(project)

    # Get the raw SVS/tiff file for the slide (the resource should exist, 
    # or else we will spend a minute here waiting with no response to user)
    sr = SlideRef(pr, specimen, block, slide_name, slide_ext)
    tiff_file = sr.get_local_copy(resource)

    os = None
    if mode == 'affine':
        os = AffineTransformedOpenSlide(tiff_file, get_affine_matrix(sr, 'affine', resource, 'image'))
    else:
        os = OpenSlide(tiff_file)

    dz = DeepZoomGenerator(os)
    tile = dz.get_tile(level, (col, row))

    buf = PILBytesIO()
    tile.save(buf, format, quality=75)
    resp = make_response(buf.getvalue())
    resp.mimetype = 'image/%s' % format
    print('PNG size: %d' % (len(buf.getvalue(),)))
    return resp


# Method to actually get a patch
def get_patch(project, specimen, block, resource, slide_name, slide_ext, level, ctrx, ctry, w, h, format):
    format = format.lower()
    if format != 'jpeg' and format != 'png':
        # Not supported by Deep Zoom
        return 'bad format'

    # Get a project reference, using either local database or remotely supplied dict
    pr = dzi_get_project_ref(project)

    # Get the raw SVS/tiff file for the slide (the resource should exist,
    # or else we will spend a minute here waiting with no response to user)
    sr = SlideRef(pr, specimen, block, slide_name, slide_ext)
    tiff_file = sr.get_local_copy(resource)

    # Read the region centered on the box of size 512x512
    os = OpenSlide(tiff_file)

    # Work out the offset
    x = ctrx - int(w * 0.5 * os.level_downsamples[level])
    y = ctry - int(h * 0.5 * os.level_downsamples[level])

    tile = os.read_region((x, y), level, (w, h));

    # Convert to PNG
    buf = PILBytesIO()
    tile.save(buf, format)
    resp = make_response(buf.getvalue())
    resp.mimetype = 'image/%s' % format
    return resp


# Method to get a patch sampled at random
def get_random_patch(project, specimen, block, resource, slide_name, slide_ext, level, w, format):
    format = format.lower()
    if format != 'jpeg' and format != 'png':
        # Not supported by Deep Zoom
        return 'bad format'

    # Get a project reference, using either local database or remotely supplied dict
    pr = dzi_get_project_ref(project)

    # Get the raw SVS/tiff file for the slide (the resource should exist,
    # or else we will spend a minute here waiting with no response to user)
    sr = SlideRef(pr, specimen, block, slide_name, slide_ext)
    tiff_file = sr.get_local_copy(resource)

    # Read the region centered on the box of size 512x512
    os = OpenSlide(tiff_file)

    # Work out the offset
    cx = randint(0, os.level_dimensions[level][0] - w * os.level_downsamples[level])
    cy = randint(0, os.level_dimensions[level][1] - w * os.level_downsamples[level])
    tile = os.read_region((x, y), level, (w, w))

    # Convert to PNG
    buf = PILBytesIO()
    tile.save(buf, format)
    resp = make_response(buf.getvalue())
    resp.mimetype = 'image/%s' % format
    return resp


# Get an image patch at level 0 from the raw image
@bp.route('/dzi/patch/<project>/<specimen>/<block>/<resource>/<slide_name>.<slide_ext>/<int:level>/<int:ctrx>_<int:ctry>_<int:w>_<int:h>.<format>',
        methods=('GET','POST'))
@project_access_required
@forward_to_worker
def get_patch_endpoint(project, specimen, block, resource, slide_name, slide_ext, level, ctrx, ctry, w, h, format):
    return get_patch(project, specimen, block, resource, slide_name, slide_ext, level, ctrx, ctry, w, h, format)


# Get an image patch at level 0 from the raw image
@bp.route('/dzi/random_patch/<project>/<specimen>/<block>/<resource>/<slide_name>.<slide_ext>/<int:level>/<int:width>.<format>',
        methods=('GET','POST'))
@project_access_required
@forward_to_worker
def get_patch_endpoint(project, specimen, block, resource, slide_name, slide_ext, level, width, format):
    return get_random_patch(project, specimen, block, resource, slide_name, slide_ext, level, width, format)


# Command to run preload worker
@click.command('preload-worker-run')
@with_appcontext
def run_preload_worker_cmd():
    with Connection():
        w = Worker(current_app.config['PRELOAD_QUEUE'])
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

    # Store our URL (TODO: read port from config)
    node_url = url_for('hello')
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

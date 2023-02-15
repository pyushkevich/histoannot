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
    Blueprint, flash, g, redirect, render_template, request, url_for, make_response, current_app, send_file, jsonify
)
from werkzeug.exceptions import abort

from io import BytesIO, StringIO
import os
import json
import time
import numpy as np
import urllib
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
from histoannot.auth import access_project_read

bp = Blueprint('dzi', __name__)

import click
import numpy
from flask.cli import with_appcontext
import nibabel as nib
import gzip


# The function that is enqueued in RQ
def do_preload_file(project, proj_info, slide_info, resource):

    pr = ProjectRef(project, proj_info)
    sr = SlideRef(pr, **slide_info)
    _, specimen, block, slide_name, slide_ext = sr.get_id_tuple()
    print('Fetching %s %s %s %s.%s' % (project, specimen, block, slide_name, slide_ext))
    tiff_file = sr.get_local_copy(resource, check_hash=True)
    print('Fetched to %s' % tiff_file)
    return tiff_file


# Forward to a worker if possible
def forward_to_worker(view):
    @functools.wraps(view)
    def wrapped_view(**kwargs):

        # Get the slide id and project variables
        project, slide_id = kwargs['project'], kwargs['slide_id']

        # Find or allocate an assigned worker for this slide
        worker_url = find_delegate_for_slide(slide_id)

        # If no worker, just call the method
        if worker_url is None:
            return view(**kwargs)

        # Call the worker's method
        full_url = '%s/%s' % (worker_url, request.full_path)

        # Take the project information and embed it in the call as a POST parameter
        pr = ProjectRef(kwargs['project'])
        sr = get_slide_ref(slide_id, pr)
        post_data = urllib.parse.urlencode({
            'project_data': json.dumps(pr.get_dict()),
            'slide_data': json.dumps(sr.get_dict())
            })

        # Pass the information to the delegate
        return urllib.request.urlopen(full_url, post_data.encode('ascii')).read()

    return wrapped_view


# Get the project data if present in the request
def dzi_get_project_ref(project):

    if current_app.config['HISTOANNOT_SERVER_MODE'] != 'dzi_node':
        # TODO: Check permission!
        return ProjectRef(project)

    else:
        pdata = json.loads(request.form.get('project_data'))
        return ProjectRef(project, pdata)


# Get the project data if present in the request
def dzi_get_project_and_slide_ref(project, slide_id):

    if current_app.config['HISTOANNOT_SERVER_MODE'] != 'dzi_node':
        # TODO: Check permission!
        pr = ProjectRef(project)
        sr = get_slide_ref(slide_id, pr)
        return pr, sr

    else:
        pdata = json.loads(request.form.get('project_data'))
        sdata = json.loads(request.form.get('slide_data'))
        ProjectRef(project, pdata), SlideRef(project, **sdata)


# Preload the slide using complete information (no access to database needed)
@bp.route('/dzi/preload/<project>/<int:slide_id>/<resource>.dzi', methods=('GET', 'POST'))
@access_project_read
@forward_to_worker
def dzi_preload(project, slide_id, resource):

    # Get a project reference, using either local database or remotely supplied dict
    pr, sr = dzi_get_project_and_slide_ref(project, slide_id)

    # Check if the file exists locally. If so, there is no need to queue a worker
    tiff_file = sr.get_local_copy(resource, check_hash=True, dry_run=True)
    print("Preload for %s,%s,%s returned local file %s" % (project, slide_id, resource, tiff_file))
    if tiff_file is not None:
        return json.dumps({ "status" : JobStatus.FINISHED })

    # Get a redis queue
    q = Queue(current_app.config['PRELOAD_QUEUE'], connection=Redis())
    job = q.enqueue(do_preload_file, project, pr.get_dict(), sr.get_dict(), resource, job_timeout="300s", result_ttl="60s")

    # Stick the properties into the job
    job.meta['args'] = (project, pr.get_dict(), sr.get_dict(), resource)
    job.save_meta()

    # Return the job id
    return json.dumps({ "job_id" : job.id, "status" : JobStatus.QUEUED })


# Check the status of a job - version that does not require database but uses private ids
@bp.route('/dzi/job/<project>/<int:slide_id>/<job_id>/status', methods=('GET', 'POST'))
@access_project_read
@forward_to_worker
def dzi_job_status(project, slide_id, job_id):
    pr, sr = dzi_get_project_and_slide_ref(project, slide_id)
    q = Queue(current_app.config['PRELOAD_QUEUE'], connection=Redis())
    j = q.fetch_job(job_id)

    if j is None:
        return 'bad job'
        abort(404)

    res = { 'status' : j.get_status() }
    print('JOB status: ', j)

    if j.get_status() == JobStatus.STARTED:
        (project, proj_data, slide_data, resource) = j.meta['args']
        pr = ProjectRef(project, proj_data)
        sr = SlideRef(pr, **slide_data)
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


# Get the dimensions for a slide in JSON format
@bp.route('/dzi/<project>/<int:slide_id>/header', methods=('GET', 'POST'))
@access_project_read
@forward_to_worker
def dzi_slide_dimensions(project, slide_id):
    pr, sr = dzi_get_project_and_slide_ref(project, slide_id)
    return jsonify(
        {"dimensions": sr.get_dims(),
         "spacing": sr.get_pixel_spacing('raw') })


# Get the DZI for a slide
@bp.route('/dzi/<mode>/<project>/<int:slide_id>/<resource>.dzi', methods=('GET', 'POST'))
@access_project_read
@forward_to_worker
def dzi(mode, project, slide_id, resource):

    # Get a project reference, using either local database or remotely supplied dict
    pr, sr = dzi_get_project_and_slide_ref(project, slide_id)
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


# Download image data
def dzi_download(project, slide_id, resource, downsample, extension):

    # Get a project reference, using either local database or remotely supplied dict
    pr, sr = dzi_get_project_and_slide_ref(project, slide_id)

    # Get the resource
    tiff_file = sr.get_local_copy(resource, check_hash=True)

    # If no downsample, send raw file
    if downsample == 0:
        if extension != sr.slide_ext:
            return "Wrong extension requested", 400

        # MRXS files will require special handling
        if extension.lower() == ".mrxs":
            return "Cannot download .mrxs files", 400

        return send_file(tiff_file)

    else:
        os = OpenSlide(tiff_file)
        thumb = os.get_thumbnail((downsample, downsample))
        mpp = sr.get_pixel_spacing(resource)

        if extension == 'tiff':
            buf = PILBytesIO()

            # Get the spacing
            if mpp:
                dpcm = [10. * thumb.size[0] / (mpp[0] * os.dimensions[0]),
                        10. * thumb.size[1] / (mpp[1] * os.dimensions[1])]
                thumb.save(buf, 'TIFF', resolution_unit=3, resolution=dpcm)
            else:
                thumb.save(buf, 'TIFF')

            resp = make_response(buf.getvalue())
            resp.mimetype = 'application/octet-stream'
            return resp

        elif extension == 'nii.gz':
            bio = BytesIO()

            # This is the code needed to put RGB into NIB
            pix = numpy.array(thumb, dtype=np.uint8).transpose(1,0,2)
            pix = numpy.expand_dims(pix, 2)
            pix = pix.copy().view(dtype=np.dtype([('R', 'u1'), ('G', 'u1'), ('B', 'u1')])).reshape(pix.shape[0:3])
            spacing = [ os.dimensions[d] * mpp[d] / thumb.size[d] for d in (0,1) ]
            
            # Start with a diagonal affine matrix and then swap AP and SI axes
            # because sections are typically coronal
            affine = np.diag([-spacing[0], -spacing[1], 1.0, 1.0])
            print("RAS code:", nib.aff2axcodes(affine))
            nii = nib.Nifti1Image(pix, affine)
            file_map = nii.make_file_map({'image': bio, 'header': bio})
            nii.to_file_map(file_map)
            data = gzip.compress(bio.getvalue())
            resp = make_response(data)
            resp.mimetype = 'application/octet-stream'
            return resp
            


# Download a thumbnail for the slide
@bp.route('/dzi/download/<project>/slide_<int:slide_id>_<resource>_<int:downsample>.tiff', methods=('GET', 'POST'))
@access_project_read
@forward_to_worker
def dzi_download_tiff(project, slide_id, resource, downsample):
    return dzi_download(project, slide_id, resource, downsample, 'tiff')


# Download a thumbnail for the slide, in NIFTI format
@bp.route('/dzi/download/<project>/slide_<int:slide_id>_<resource>_<int:downsample>.nii.gz', methods=('GET', 'POST'))
@access_project_read
@forward_to_worker
def dzi_download_nii_gz(project, slide_id, resource, downsample):
    return dzi_download(project, slide_id, resource, downsample, 'nii.gz')


# Download a full-resolution slide, in whatever format the slide is in
@bp.route('/dzi/download/<project>/slide_<int:slide_id>_<resource>_fullres.<extension>', methods=('GET', 'POST'))
@access_project_read
@forward_to_worker
def dzi_download_fullres(project, slide_id, resource, extension):
    return dzi_download(project, slide_id, resource, 0, extension)




class PILBytesIO(BytesIO):
    def fileno(self):
        '''Classic PIL doesn't understand io.UnsupportedOperation.'''
        raise AttributeError('Not supported')


# Get the tiles for a slide
@bp.route('/dzi/<mode>/<project>/<int:slide_id>/<resource>_files/<int:level>/<int:col>_<int:row>.<format>',
        methods=('GET', 'POST'))
@access_project_read
@forward_to_worker
def tile_db(mode, project, slide_id, resource, level, col, row, format):
    format = format.lower()
    if format != 'jpeg' and format != 'png':
        # Not supported by Deep Zoom
        return 'bad format'
        abort(404)

    # Get a project reference, using either local database or remotely supplied dict
    pr, sr = dzi_get_project_and_slide_ref(project, slide_id)

    # Get the raw SVS/tiff file for the slide (the resource should exist, 
    # or else we will spend a minute here waiting with no response to user)
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
def get_patch(project, slide_id, resource, level, ctrx, ctry, w, h, format):
    format = format.lower()
    if format != 'jpeg' and format != 'png':
        # Not supported by Deep Zoom
        return 'bad format'

    # Get a project reference, using either local database or remotely supplied dict
    pr, sr = dzi_get_project_and_slide_ref(project, slide_id)

    # Get the raw SVS/tiff file for the slide (the resource should exist,
    # or else we will spend a minute here waiting with no response to user)
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
def get_random_patch(project, slide_id, resource, level, w, format):
    format = format.lower()
    if format != 'jpeg' and format != 'png':
        # Not supported by Deep Zoom
        return 'bad format'

    # Get the raw SVS/tiff file for the slide (the resource should exist,
    # or else we will spend a minute here waiting with no response to user)
    pr, sr = dzi_get_project_and_slide_ref(project, slide_id)
    tiff_file = sr.get_local_copy(resource)

    # Read the region centered on the box of size 512x512
    os = OpenSlide(tiff_file)

    # Work out the offset
    cx = randint(0, int(os.level_dimensions[level][0] - w * os.level_downsamples[level]))
    cy = randint(0, int(os.level_dimensions[level][1] - w * os.level_downsamples[level]))
    tile = os.read_region((cx, cy), level, (w, w))

    # Convert to PNG
    buf = PILBytesIO()
    tile.save(buf, format)
    resp = make_response(buf.getvalue())
    resp.mimetype = 'image/%s' % format
    return resp


# Queue patch generation at a worker
@bp.route('/dzi/preload/<project>/<int:slide_id>/<resource>.dzi', methods=('GET', 'POST'))
@access_project_read
@forward_to_worker
def patch_preload(project, slide_id, resource):


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

    # Get a project reference, using either local database or remotely supplied dict
    pr, sr = dzi_get_project_and_slide_ref(project, slide_id)

    # Check if the file exists locally. If so, there is no need to queue a worker
    tiff_file = sr.get_local_copy(resource, check_hash=True, dry_run=True)
    print("Preload for %s,%s,%s returned local file %s" % (project, slide_id, resource, tiff_file))
    if tiff_file is not None:
        return json.dumps({ "status" : JobStatus.FINISHED })

    # Get a redis queue
    q = Queue(current_app.config['PRELOAD_QUEUE'], connection=Redis())
    job = q.enqueue(do_preload_file, project, pr.get_dict(), sr.get_dict(), resource, job_timeout="300s", result_ttl="60s")

    # Stick the properties into the job
    job.meta['args'] = (project, pr.get_dict(), sr.get_dict(), resource)
    job.save_meta()

    # Return the job id
    return json.dumps({ "job_id" : job.id, "status" : JobStatus.QUEUED })




# Get an image patch at level 0 from the raw image
@bp.route('/dzi/patch/<project>/<int:slide_id>/<resource>/<int:level>/<int:ctrx>_<int:ctry>_<int:w>_<int:h>.<format>',
        methods=('GET','POST'))
@access_project_read
@forward_to_worker
def get_patch_endpoint(project, slide_id, resource, level, ctrx, ctry, w, h, format):
    return get_patch(project, slide_id, resource, level, ctrx, ctry, w, h, format)


# Get an image patch at level 0 from the raw image
@bp.route('/dzi/random_patch/<project>/<int:slide_id>/<resource>/<int:level>/<int:width>.<format>',
        methods=('GET','POST'))
@access_project_read
@forward_to_worker
def get_random_patch_endpoint(project, slide_id, resource, level, width, format):
    return get_random_patch(project, slide_id, resource, level, width, format)


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
            coded = urllib.parse.urlencode([('url', node_url), ('cpu_percent',str(cpu_percent))])
            urllib.request.urlopen(master_url, coded.encode('ascii'), timeout=10)
        except urllib.error.URLError as e:
            print(e)
        time.sleep(30)

# CLI stuff
def init_app(app):
    app.cli.add_command(run_preload_worker_cmd)
    app.cli.add_command(delegate_dzi_ping_command)

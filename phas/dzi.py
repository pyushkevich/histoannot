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
    Blueprint, request, url_for, make_response, current_app, send_file, jsonify
)
from werkzeug.exceptions import abort

from io import BytesIO
import os
import json
import time
import numpy as np
import urllib
import psutil

from random import randint

# from openslide import OpenSlide
# from openslide.deepzoom import DeepZoomGenerator
from .slideref import SlideRef,get_slide_ref
from .project_ref import ProjectRef
from .auth import access_project_read, access_project_admin
from google.cloud import storage

bp = Blueprint('dzi', __name__)

import click
import numpy
from flask.cli import with_appcontext
import nibabel as nib
import gzip
from PIL import Image
import socket
import pickle
from datetime import datetime

import socketserver
import multiprocessing
import ctypes
from .gcs_handler import GoogleCloudOpenSlideWrapper, MultiprocessManagedMultiFilePageCache
from sortedcontainers import SortedKeyList


# The function that is enqueued in RQ
def do_preload_file(project, proj_info, slide_info, resource):

    pr = ProjectRef(project, proj_info)
    sr = SlideRef(pr, **slide_info)
    _, specimen, block, slide_name, slide_ext = sr.get_id_tuple()
    print('Fetching %s %s %s %s.%s' % (project, specimen, block, slide_name, slide_ext))
    tiff_file = sr.get_local_copy(resource, check_hash=True)
    print('Fetched to %s' % tiff_file)
    return tiff_file


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
def dzi_slide_dimensions(project, slide_id):
    pr, sr = dzi_get_project_and_slide_ref(project, slide_id)
    return jsonify(
        {"dimensions": sr.get_dims(),
         "spacing": sr.get_pixel_spacing('raw') })


# Get the file path of the slide - this requires admin access
@bp.route('/dzi/<project>/<int:slide_id>/filepath', methods=('GET', 'POST'))
@access_project_admin
def dzi_slide_filepath(project, slide_id):
    pr, sr = dzi_get_project_and_slide_ref(project, slide_id)
    return jsonify(
        {"remote": sr.get_resource_url('raw', False),
         "local": sr.get_resource_url('raw', True) })


# Get the DZI for a slide
@bp.route('/dzi/<mode>/<project>/<int:slide_id>/<resource>.dzi', methods=('GET', 'POST'))
@access_project_read
def dzi(mode, project, slide_id, resource):
    from openslide.deepzoom import DeepZoomGenerator

    # Get a project reference, using either local database or remotely supplied dict
    pr, sr = dzi_get_project_and_slide_ref(project, slide_id)
    # tiff_file = sr.get_local_copy(resource, check_hash=True)
    tiff_file = sr.get_resource_url(resource, False)

    # Get an affine transform if that is an option
    A = get_affine_matrix(sr, mode, resource, 'image')

    # Load the slide
    osa = get_osl(slide_id, sr, resource)
    dz = DeepZoomGenerator(osa)
    dz.filename = os.path.basename(tiff_file)
    resp = make_response(dz.get_dzi('png'))
    resp.mimetype = 'application/xml'
    return resp


# Generate a 2D NIFTI.GZ image from a PIL image
def pil_to_nifti_gz(image, spacing):
    bio = BytesIO()

    # This is the code needed to put RGB into NIB
    pix = numpy.array(image, dtype=np.uint8)
    if len(pix.shape) == 3:
        pix = pix.transpose(1,0,2)
        pix = numpy.expand_dims(pix, 2)
        pix = pix.copy().view(dtype=np.dtype([('R', 'u1'), ('G', 'u1'), ('B', 'u1')])).reshape(pix.shape[0:3])
    elif len(pix.shape) == 2:
        pix = pix.transpose(1,0)
        pix = numpy.expand_dims(pix, (2,3))
    else:
        raise ValueError('Wrong dimension of input image to pil_to_nifti_gz')
    
    # Start with a diagonal affine matrix and then swap AP and SI axes
    # because sections are typically coronal
    affine = np.diag([-spacing[0], -spacing[1], 1.0, 1.0])
    print("RAS code:", nib.aff2axcodes(affine))
    nii = nib.Nifti1Image(pix, affine)
    file_map = nii.make_file_map({'image': bio, 'header': bio})
    nii.to_file_map(file_map)
    return gzip.compress(bio.getvalue())


# Download image data
def dzi_download(project, slide_id, resource, downsample, extension):

    # Get a project reference, using either local database or remotely supplied dict
    pr, sr = dzi_get_project_and_slide_ref(project, slide_id)

    # If no downsample, send raw file
    if downsample == 0:
        # TODO: downloads are broken now
        tiff_file = sr.get_local_copy(resource, check_hash=True)
        if tiff_file is None:
            return f'Resource {resource} not available for slide {slide_id}', 400
        if not tiff_file.lower().endswith(extension.lower()):
            return "Wrong extension requested", 400

        # MRXS files will require special handling
        if extension.lower() == ".mrxs":
            return "Cannot download .mrxs files", 400

        return send_file(tiff_file)

    else:
        os = get_osl(slide_id, sr, resource)
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
            spacing = [ os.dimensions[d] * mpp[d] / thumb.size[d] for d in (0,1) ]
            data = pil_to_nifti_gz(thumb, spacing) 
            resp = make_response(data)
            resp.mimetype = 'application/octet-stream'
            return resp
            


# Download a thumbnail for the slide
@bp.route('/dzi/download/<project>/slide_<int:slide_id>_<resource>_<int:downsample>.tiff', methods=('GET', 'POST'))
@access_project_read
def dzi_download_tiff(project, slide_id, resource, downsample):
    return dzi_download(project, slide_id, resource, downsample, 'tiff')


# Download a thumbnail for the slide, in NIFTI format
@bp.route('/dzi/download/<project>/slide_<int:slide_id>_<resource>_<int:downsample>.nii.gz', methods=('GET', 'POST'))
@access_project_read
def dzi_download_nii_gz(project, slide_id, resource, downsample):
    return dzi_download(project, slide_id, resource, downsample, 'nii.gz')


# Download a full-resolution slide, in whatever format the slide is in
@bp.route('/dzi/download/<project>/slide_<int:slide_id>_<resource>_fullres.<extension>', methods=('GET', 'POST'))
@access_project_read
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
    # tiff_file = sr.get_local_copy(resource)
    os = get_osl(slide_id, sr, resource)

    # TODO: bring back affine!
    #os = None
    #if mode == 'affine':
    #    os = AffineTransformedOpenSlide(tiff_file, get_affine_matrix(sr, 'affine', resource, 'image'))
    #else:
    #    os = OpenSlide(tiff_file)

    from openslide.deepzoom import DeepZoomGenerator
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
    os = get_osl(slide_id, sr, resource)

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
    os = get_osl(slide_id, sr, resource)

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


# Get an image patch at level 0 from the raw image
@bp.route('/dzi/patch/<project>/<int:slide_id>/<resource>/<int:level>/<int:ctrx>_<int:ctry>_<int:w>_<int:h>.<format>',
        methods=('GET','POST'))
@access_project_read
def get_patch_endpoint(project, slide_id, resource, level, ctrx, ctry, w, h, format):
    return get_patch(project, slide_id, resource, level, ctrx, ctry, w, h, format)


# Get an image patch at level 0 from the raw image
@bp.route('/dzi/random_patch/<project>/<int:slide_id>/<resource>/<int:level>/<int:width>.<format>',
        methods=('GET','POST'))
@access_project_read
def get_random_patch_endpoint(project, slide_id, resource, level, width, format):
    return get_random_patch(project, slide_id, resource, level, width, format)


def dzi_download_thumblike_image(project, slide_id, resource, extension):

    # Get a project reference, using either local database or remotely supplied dict
    pr, sr = dzi_get_project_and_slide_ref(project, slide_id)
    osl = get_osl(slide_id, sr)
    assoc_image = osl.associated_images.get(resource.lower(), None)

    # Get the resource
    if not assoc_image:    
        return f'Resource {resource} not available for slide {slide_id}', 400
    
    # Save in requested format to a buffer
    buf = PILBytesIO()
    assoc_image.save(buf, extension)
    resp = make_response(buf.getvalue())
    resp.mimetype = 'image/%s' % format
    return resp


# Download a label image for a slide 
@bp.route('/dzi/download/<project>/slide_<int:slide_id>_label.png', methods=('GET', 'POST'))
@access_project_read
def dzi_download_label_image(project, slide_id):
    return dzi_download_thumblike_image(project, slide_id, 'label', 'png')


# Download a label image for a slide 
@bp.route('/dzi/download/<project>/slide_<int:slide_id>_macro.png', methods=('GET', 'POST'))
@access_project_read
def dzi_download_macro_image(project, slide_id):
    return dzi_download_thumblike_image(project, slide_id, 'macro', 'png')


# Download a label image for a slide 
@bp.route('/dzi/download/<project>/slide_<int:slide_id>_<resource>_header.json', methods=('GET', 'POST'))
@access_project_read
def dzi_download_header(project, slide_id, resource):

    # Get a project reference, using either local database or remotely supplied dict
    _, sr = dzi_get_project_and_slide_ref(project, slide_id)
    os = get_osl(slide_id, sr, resource)
    
    # Collect relevant properties
    prop_dict = {
        'properties': { k:v for k,v in os.properties.items() },
        'level_dimensions': os.level_dimensions, 
        'level_downsamples': os.level_downsamples 
    }
    
    return json.dumps(prop_dict), 200, {'ContentType':'application/json'}


class OpenSlideThinInterface:
    """
    This class is a thin interface to an OpenSlide object that is running in a 
    different process and is connected to via a socket. 
    """
    
    def __init__(self, url, socket_addr_list):
        # Hash the URL to determine which socket to connect to
        self.socket_addr = socket_addr_list[hash(url) % len(socket_addr_list)]
        self.url = url
        
    def _exec_remote(self, method, **kwargs):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            
            # Request a region from an image 
            request = {
                'url': self.url,
                'command': method,
                'args': kwargs
            }
            
            # Connect to server and send data
            sock.connect(self.socket_addr)
            sock.sendall(pickle.dumps(request))

            # Receive data from the server and shut down
            header = sock.recv(8)
            n_bytes = int.from_bytes(header, byteorder='big')
            payload = bytearray(n_bytes)
            pos = 0
            while pos < n_bytes:
                chunk = sock.recv(n_bytes-pos)
                payload[pos:pos+len(chunk)] = chunk
                pos += len(chunk)
            result = pickle.loads(payload)
            return result
        
    @property
    def level_count(self):
        return self._exec_remote('level_count')

    @property
    def dimensions(self):
        return self._exec_remote('dimensions')
        
    @property
    def level_dimensions(self):
        return self._exec_remote('level_dimensions')
    
    @property
    def level_downsamples(self):
        return self._exec_remote('level_downsamples')
    
    @property
    def properties(self):
        return self._exec_remote('properties')
    
    @property
    def associated_images(self):
        return self._exec_remote('associated_images')
    
    def get_best_level_for_downsample(self, downsample):
        return self._exec_remote('get_best_level_for_downsample', downsample=downsample)
                
    def read_region(self, location, level, size):
        return self._exec_remote('read_region', location=location, level=level, size=size)
    
    def get_thumbnail(self, size):
        return self._exec_remote('get_thumbnail', size=size)
        


def get_osl(slide_id, sr:SlideRef, resource='raw', socket_addr_list=None):
    tiff_file = sr.get_resource_url(resource, local=False)
    # if tiff_file.startswith('gs://'):
    if socket_addr_list is None:
        socket_addr_list = current_app.config['SLIDE_SERVER_ADDR']
    return OpenSlideThinInterface(tiff_file, socket_addr_list=socket_addr_list)
    #else:
    #    return OpenSlide(tiff_file)
    
    
class SlideCacheEntry:
    """
    Representation of a slide that is kept by a slide server process. Stores a
    handle to the slide wrapper and a timestamp from last use, which is
    updated whenever the wrapper is requested
    """    
    def __init__(self, osl_wrapper):
        self._osl = osl_wrapper
        self.t_access = time.time_ns()
        
    @property
    def osl_wrapper(self):
        self.t_access = time.time_ns()
        return self._osl
    

class OpenSlideRequestHandler(socketserver.BaseRequestHandler):
    """
    The request handler class for our server.

    It is instantiated once per connection to the server, and must
    override the handle() method to implement communication to the
    client.
    """
    def handle(self):

        from openslide import OpenSlide
        class OpenSlidePickleableWrapper(OpenSlide):
            
            def __init__(self, filename):
                OpenSlide.__init__(self, filename)
                
            @property
            def properties(self):
                return dict({k:v for (k,v) in OpenSlide.properties.fget(self).items()})

            @property
            def associated_images(self):
                return dict({k:v for (k,v) in OpenSlide.associated_images.fget(self).items()})

        # Block to receive request
        self.data = pickle.loads(self.request.recv(4096))
        
        # Get the slide URL
        url = self.data['url']
        
        # Get a cached TIFF object for the URL or create and cache one
        # TODO: purge tiff cache once in a while
        slide_cache_entry = self.server.osl_cache.get(url, None)
        if not slide_cache_entry:
            if url.startswith('gs://'):
                if self.server.gcs_client is None:
                    self.server.gcs_client = storage.Client()                
                tiff = GoogleCloudOpenSlideWrapper(self.server.gcs_client, url, self.server.gcs_cache)
            else:
                tiff = OpenSlidePickleableWrapper(url)
            self.server.osl_cache[url] = slide_cache_entry = SlideCacheEntry(tiff)
            
        # Process the request
        attr = getattr(slide_cache_entry.osl_wrapper, self.data['command'])
        result = attr(**self.data['args']) if callable(attr) else attr

        payload = pickle.dumps(result)
        self.request.send(len(payload).to_bytes(8, byteorder='big'))
        self.request.sendall(payload)


# Worker process that runs the openslide server. This thread operates a single 
# unix socket based on its id and handles files that correspond to this ID 
# based on its hash. 
def slide_server_process_run(index, socket_addr_list, page_size_mb, cache_queue, purge_value):
    
    # Get the socket address
    socket_addr = socket_addr_list[index]
    
    # Make sure the directory exists and delete the socket file if present
    os.makedirs(os.path.dirname(socket_addr), exist_ok=True)
    if os.path.exists(socket_addr):
        os.remove(socket_addr)
    
    # Run the socket server forever or until interrupted
    try:
        with socketserver.UnixStreamServer(socket_addr, OpenSlideRequestHandler) as server:
            server.osl_cache = {}
            server.gcs_cache = MultiprocessManagedMultiFilePageCache(index, cache_queue, page_size_mb=page_size_mb)
            server.gcs_client = None
            server.timeout = 30
            while(True):
                # Handle the request
                server.handle_request()
                
                # Purge the memory cache of pages newer than specified value
                server.gcs_cache.purge(purge_value.value)
                
                # Purge the slide wrapper cache of slides that have not been accessed in 30 minutes
                t_cutoff = time.time_ns() - 1800 * 1000**3
                server.osl_cache = dict({k:v for k,v in server.osl_cache.items() if v.t_access >= t_cutoff})
                
    except KeyboardInterrupt:
        print(f'Worker {index} interrupted by keyboard')
    finally:
        # Clean up the socket
        os.remove(socket_addr)


# Manager process that keeps an eye on the cache size and forces a purge when the
# cache size is exceeded
def slide_server_cache_manager_process_run(cache_queue, purge_value, max_pages=4096, purge_size_pct=0.25):
    cache_metadata = dict()
    t_last_print = time.time_ns()
    try:
        while(True):
            (id,url,index,t_access) = cache_queue.get()
            cache_metadata[(id,url,index)] = t_access
            if time.time_ns() - t_last_print > 5 * 1e9:
                print(f'Cache Manager: {len(cache_metadata)} of {max_pages} allocated')
                t_last_print = time.time_ns()
            if len(cache_metadata) > max_pages:
                l_purge = SortedKeyList(key=lambda x:x[1])
                for key, t_access in cache_metadata.items():
                    l_purge.add((key, t_access))

                n_purge = min(max(1,int(purge_size_pct * max_pages)), len(l_purge)-1)
                t_purge = l_purge[n_purge][1]
                print(f'Cache manager: Purging cache with cutoff t={t_purge}')
                cache_metadata = dict({
                    key:t_access for key, t_access in cache_metadata.items() if t_access >= t_purge })

                # This communicates to the workers that their caches must be freed
                purge_value.value = t_purge
    except KeyboardInterrupt:
        print('Cache manager processinterrupted by keyboard')


# Command to run openslide server
@click.command('slide-server-run')
@with_appcontext
def run_slide_server():
    # Create the queue and value to share between processes
    cache_queue = multiprocessing.Queue()
    purge_value = multiprocessing.Value(ctypes.c_longlong, int(time.time_ns()))
    socket_addr_list = current_app.config['SLIDE_SERVER_ADDR']
    
    # Load cache properties
    page_size_mb = current_app.config['SLIDE_SERVER_CACHE_PAGE_SIZE_MB']
    cache_size_pg = current_app.config['SLIDE_SERVER_CACHE_SIZE_IN_PAGES']
    
    # Start the manager process
    p_man = multiprocessing.Process(
        target = slide_server_cache_manager_process_run,
        args = (cache_queue, purge_value, cache_size_pg))
    p_man.start()

    # Start the worker processes
    p_workers = []
    for k in range(len(current_app.config['SLIDE_SERVER_ADDR'])):
        p = multiprocessing.Process(
            target = slide_server_process_run,
            args = (k, socket_addr_list, page_size_mb, cache_queue, purge_value))
        p.start()
        p_workers.append(p)

    # Print startup message
    print(f'PHAS slide server started with {len(p_workers)} workers, '
          f'total cache size {cache_size_pg*page_size_mb}MB')

    # Wait for the workers to finish (never)
    for p in p_workers:
        p.join()


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
    app.cli.add_command(delegate_dzi_ping_command)
    app.cli.add_command(run_slide_server)

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
from urlparse import urlparse
from google.cloud import storage
import glob
import stat
import heapq
import urllib2
import csv
import sys, traceback
import threading
import os
from flask import g, current_app



# This class handles remote URLs for Google cloud. The remote URLs must have format
# "gs://bucket/path/to/blob.ext" 

class GCSHandler:

    # Constructor
    def __init__(self):
        self._bucket_cache={}
        self._blob_cache={}
        self._client = storage.Client()

    # Process a URL
    def _get_blob(self, uri):

        # Check the cache
        if uri in self._blob_cache:
            return self._blob_cache[uri]

        # Unpack the URL
        o = urlparse(uri)

        # Make sure that it includes gs
        if o.scheme != "gs":
            raise ValueError('URL should have schema "gs"')

        # Find the bucket, if not found add it to the cache
        if o.netloc in self._bucket_cache:
            bucket = self._bucket_cache[o.netloc]
        else:
            bucket = self._client.get_bucket(o.netloc)
            self._bucket_cache[o.netloc] = bucket

        # Place the blob in the cache
        blob = bucket.get_blob(o.path.strip('/'))
        self._blob_cache[uri] = blob

        # Get the blob in the bucket
        return blob

    # Check if a URL refers to an existing file
    def exists(self, uri):
        return self._get_blob(uri) is not None

    # Get the MD5 hash
    def get_md5hash(self, uri):
        return self._get_blob(uri).md5_hash

    # Download a remote resource locally
    def download(self, uri, local_file):
        
        # Make sure the path containing local_file exists
        dir_path = os.path.dirname(local_file)
        if not os.path.exists(dir_path):
            os.makedirs(dir_path)

        # Perform the download
        blob = self._get_blob(uri)
        with open(local_file, "wb") as file_obj:
            worker = threading.Thread(target = self._client.download_blob_to_file, args=(blob, file_obj))
            worker.start()
            while worker.isAlive():
                worker.join(1.0)
                print('GCS: downloaded: %d of %s' % (os.stat(local_file).st_size, uri))

    # Get the remote download size
    def get_size(self, uri):
        return self._get_blob(uri).size


# Get a global GCP handler
def get_gstor():
    if 'gstor' not in g:
        g.gstor = GCSHandler()
    return g.gstor



# This class is used to describe a histology slide and associated data that resides in
# a remote (cloud-based) location and may be cached locally
class SlideRef:

    # A mapping from resources to files

    # Initialize a slide reference with a remote URL.
    # slide_info is a dict with fields specimen, block, slide_name, slide_ext
    def __init__(self, schema, remote_baseurl, remote_handler, slide_info):

        # Store the remote url
        self._remote_baseurl = remote_baseurl
        self._url_handler = remote_handler
        self._specimen = slide_info["specimen"]
        self._block = slide_info["block"]
        self._slide_name = slide_info["slide_name"]
        self._slide_ext = slide_info["slide_ext"]

        # Store the schema
        self._schema = schema

        # Create a local directory for caching the content from the remote URL
        self._local_baseurl = os.path.join(current_app.instance_path, 'slidecache')
        if not os.path.exists(self._local_baseurl):
            os.makedirs(self._local_baseurl)

    # Generate the filename for the resource (local or remote)
    def get_resource_url(self, resource, local = True):

        # Use a dictionary for the substitution
        d = {"baseurl" : self._local_baseurl if local else self._remote_baseurl,
             "specimen" : self._specimen, "block" : self._block,
             "slide_name": self._slide_name, "slide_ext" : self._slide_ext }

        # Apply the dictionary to retrieve the filename vie schema
        return self._schema["pattern"][resource].format(**d)

    # Check whether a resource exists (locally or remotely)
    def resource_exists(self, resource, local = True):
        f = self.get_resource_url(resource, local)
        if local is True:
            return os.path.exists(f) and os.path.isfile(f)
        else:
            return self._url_handler.exists(f)

    # Get a local copy of the resource, copying it if necessary
    def get_local_copy(self, resource, check_hash=False, dry_run=False):

        # Get the local URL
        f_local = self.get_resource_url(resource, True)
        f_local_md5 = f_local + '.md5'
        f_remote = self.get_resource_url(resource, False)

        # If it already exists, make sure it matches the remote file
        have_local = os.path.exists(f_local) and os.path.isfile(f_local)

        # If we are not checking hashes, and local file exists, we can return it
        if have_local and not check_hash:
            return f_local

        # If we are here, we will need to access the remote url. Let's check if
        # it actually exists. If not, we must return None. But we should also 
        # delete the local resource to avoid stale files
        if not self._url_handler.exists(f_remote):
            if have_local:
                print("Remote has disappeared for local %s" % f_local)
                os.remove(f_local)
                if os.path.exists(f_local_md5):
                    os.remove(f_local_md5)
            return None

        # At this point, remote exists. If local exists, check its hash against
        # the remote
        if have_local:

            # Get the local hash if it exists
            if os.path.exists(f_local_md5):
                with open(f_local_md5) as x:
                    hash_local = x.readline()
                    if hash_local == self._url_handler.get_md5hash(f_remote):
                        print('File %s has NOT changed relative to remote %s' % (f_local, f_remote))
                        return f_local
                    else:
                        print('File %s HAS changed relative to remote %s' % (f_local, f_remote))

        # If dry-run, don't actually download the thing
        if dry_run:
            return None

        # Clean up the cache
        if resource == 'raw':

            # Generate a wildcard pattern to list all 'raw' format files 
            d = {"baseurl" : self._local_baseurl,
                 "specimen" : "*", "block" : "*", "slide_name": "*", "slide_ext" : "*" }
            wildcard_str =  self._schema["pattern"]['raw'].format(**d)

            # For each of the files, use os.stat to get information
            f_heap = []
            total_bytes = 0
            for fn in glob.glob(wildcard_str):
                s = os.stat(fn)
                total_bytes += s.st_size
                heapq.heappush(f_heap, (s.st_atime, s.st_size, fn))

            # If the total number of bytes exceeds the cache size
            while total_bytes > self._schema["cache_capacity"] and len(f_heap) > 0:

                # Get the oldest file
                (atime, size, fn) = heapq.heappop(f_heap)

                # Remove the file
                total_bytes -= size
                os.remove(fn)


        # Make a copy of the resource locally
        self._url_handler.download(f_remote, f_local)

        # Create an md5 stamp
        with open(f_local_md5, 'wt') as f:
            f.write(self._url_handler.get_md5hash(f_remote))

        return f_local


    # Get the download progress (fraction of local file size to remote)
    def get_download_progress(self, resource):

        # Get the local file and remote blob
        f_local = self.get_resource_url(resource, True)
        f_remote = self.get_resource_url(resource, False)

        # Get remote size
        sz_local = os.stat(f_local).st_size if os.path.exists(f_local) else 0
        sz_remote = self._url_handler.get_size(f_remote)

        # Get the ratio
        return sz_local * 1.0 / sz_remote


# Get a slide reference for slide specified by detailed information
def get_slideref_by_info(specimen, block, slide_name, slide_ext):

    # Get the current schema
    schema=current_app.config['HISTOANNOT_URL_SCHEMA']

    # Get the current URL base
    url_base=current_app.config['HISTOANNOT_URL_BASE']

    # Check the url_base
    if url_base.startswith('gs://'):
        handler = get_gstor()
    else:
        handler = None

    slide_info = {
        "specimen" : specimen, "block" : block, 
        "slide_name": slide_name, "slide_ext" : slide_ext }

    sr = SlideRef(schema, url_base, handler, slide_info)
    return sr

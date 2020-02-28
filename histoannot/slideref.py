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

import glob
import stat
import heapq

import csv
import sys, traceback
import threading
import os
from flask import g, current_app




# This class is used to describe a histology slide and associated data that resides in
# a remote (cloud-based) location and may be cached locally
class SlideRef:

    # A mapping from resources to files

    # Initialize a slide reference with a remote URL.
    # slide_info is a dict with fields specimen, block, slide_name, slide_ext
    def __init__(self, project, specimen, block, name, ext):
        """
        Slide reference constructor

        Args:
            project(str): name of the project
            specimen(str): ID of the specimen
            block(str): ID of the block
            name(str): name/ID of the slide (must be unique)
            ext(str): extension of the slide
        """

        # Find the project configuration
        self._proj = current_app.config['PROJECTS'][project]

        # Store the slice properties
        self._specimen = specimen
        self._block = block
        self._slide_name = name
        self._slide_ext = ext


    # Get a tuple identifying the slide
    def get_id_tuple(self):
        return (self._proj.get_name(), self._specimen, self._block, 
                self._slide_name, self._slide_ext)

    # Generate the filename for the resource (local or remote)
    def get_resource_url(self, resource, local = True):

        # Use a dictionary for the substitution
        d = {"baseurl" : self._proj.get_baseurl(local),
             "specimen" : self._specimen, "block" : self._block,
             "slide_name": self._slide_name, "slide_ext" : self._slide_ext }

        # Apply the dictionary to retrieve the filename vie schema
        return self._proj.get_urlschema()["pattern"][resource].format(**d)

    # Check whether a resource exists (locally or remotely)
    def resource_exists(self, resource, local = True):
        f = self.get_resource_url(resource, local)
        return self._proj.file_exists(f, local)

    # Get a local copy of the resource, copying it if necessary
    def get_local_copy(self, resource, check_hash=False, dry_run=False):

        # Get the local URL
        f_local = self.get_resource_url(resource, True)

        # If it already exists, make sure it matches the remote file
        have_local = os.path.exists(f_local) and os.path.isfile(f_local)

        # If no remote, then we are done
        if self._url_handler is None:
            return f_local if have_local else None

        # If we are not checking hashes, and local file exists, we can return it
        if have_local and not check_hash:
            return f_local

        # Get ready to check the remote
        f_local_md5 = f_local + '.md5'
        f_remote = self.get_resource_url(resource, False)

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
                        return f_local

        # If dry-run, don't actually download the thing
        if dry_run:
            return None

        # Clean up the cache
        if resource == 'raw' or resource == 'x16':

            # Generate a wildcard pattern to list all 'raw' format files 
            d = {"baseurl" : self._local_baseurl,
                 "specimen" : "*", "block" : "*", "slide_name": "*", "slide_ext" : "*" }
            wildcard_str =  self._schema["pattern"][resource].format(**d)

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

        if self._url_handler is None:
            return 1.0

        # Get the local file and remote blob
        f_local = self.get_resource_url(resource, True)
        f_remote = self.get_resource_url(resource, False)

        # Get remote size
        sz_local = os.stat(f_local).st_size if os.path.exists(f_local) else 0
        sz_remote = self._url_handler.get_size(f_remote)

        # Get the ratio
        return sz_local * 1.0 / sz_remote


# Get a slide reference for slide specified by detailed information
def get_slideref_by_info(project, specimen, block, slide_name, slide_ext):

    # Get the project reference
    proj_src_ref = current_app.config['PROJECTS'][project]

    # Get the current schema
    schema=proj_src_ref.get_urlschema()

    # Get the current URL base
    url_base=proj_src_ref.get_baseurl()

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

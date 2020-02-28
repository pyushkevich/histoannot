import os
import json
from flask import current_app, g, url_for
from gcs_handler import get_gstor

# This class represents a project configuration. It stores data associated
# with the project that is not kept in a database, but rather stored in 
# a json file in the instance/projects directory. 
#
# Not having to depend on a database means that this class can live on the
# DZI slave nodes that serve image pieces in a distributed environment

# The default naming schema for slide objects
_default_histo_url_schema = {
    # The filename patterns
    "pattern": {
        "raw": "{baseurl}/{specimen}/histo_raw/{slide_name}.{slide_ext}",
        "x16": "{baseurl}/{specimen}/histo_proc/{slide_name}/preproc/{slide_name}_x16_pyramid.tiff",
        "affine": "{baseurl}/{specimen}/histo_proc/{slide_name}/recon/{slide_name}_recon_iter10_affine.mat",
        "thumb": "{baseurl}/{specimen}/histo_proc/{slide_name}/preproc/{slide_name}_thumbnail.tiff",
        "dims": "{baseurl}/{specimen}/histo_proc/{slide_name}/preproc/{slide_name}_resolution.txt",
        "d_tangles": "{baseurl}/{specimen}/histo_proc/{slide_name}/density/{slide_name}_Tau_tangles_densitymap.tiff"
    },

    # The maximum number of bytes that may be cached locally for 'raw' data
    "cache_capacity": 32 * 1024 ** 3
}


class RemoteFileCache:
    """This class handles local caching of remote files, such as Google Cloud Storage bucket
       files. The cache has a fixed capacity and when it is full, files are deleted to bring
       it to X% capacity."""

    def __init__(self, path, source, max_size = 20*2^30, free_fraction = 0.1):
        """Create new cache with given max size in bytes and given fraction of size that
           gets freed up when cache hits capacity"""
        self._max_size = max_size
        self._free_fraction = free_fraction
        self._path = path
        self._source = source

    def get_file(self, fname):



class ProjectSourceRef:

    # Constructor reads and checks the json file for the project. The
    # name of the json file must match the name of the project in the
    # database
    def __init__(self, json_file):

        # Read the json file into a dict
        with open(json_file, 'rt') as jf:
            self._dict = json.load(jf)

        # Store the name
        self._name = os.path.splitext(os.path.basename(jfile))[0]

        # Initialize the URL handler
        if self.get_remote_urlbase().startswith('gs://'):
            self._url_handler = get_gstor()
        else:
            self._url_handler = None

        # Set up the local directory
        if self._url_handler is not None:
            # Create a local directory for caching the content from the remote URL
            self._local_baseurl = os.path.join(
                current_app.instance_path, 'slidecache', self._name)
            if not os.path.exists(self._local_baseurl):
                os.makedirs(self._local_baseurl)
        else:
            self._local_baseurl = self.get_remote_urlbase()

    # Get the name of the project
    def get_name(self):
        return self._name

    # Get the remote base URL of the project
    def get_remote_urlbase(self):
        return self._dict.get('base_url', None)

    # Get the local base URL of the project
    def get_local_urlbase(self):
        return self._local_baseurl

    # Get either local or remote URL base
    def get_urlbase(self, local):
        return self._local_baseurl if local else self.get_remote_urlbase()

    # Get the URL schema for this project
    def get_urlschema(self):
        return self._dict.get('url_schema', _default_histo_url_schema)

    # Get the URL handler
    def get_urlhandler(self):
        return self._url_handler

    # Check if a file exists locally or remotely
    def file_exists(self, f, local):
        if local is True or self._url_handler is None:
            return os.path.exists(f) and os.path.isfile(f)
        else:
            return self._url_handler.exists(f)

    # Get a local copy of the resource, copying it if necessary
    def get_local_copy(self, f, check_hash=False, dry_run=False):

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
            d = {"baseurl": self._local_baseurl,
                 "specimen": "*", "block": "*", "slide_name": "*", "slide_ext": "*"}
            wildcard_str = self._schema["pattern"][resource].format(**d)

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

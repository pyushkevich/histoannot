import os
import json
import heapq
from flask import current_app, g
from .gcs_handler import GCSHandler
from .db import get_db
from .common import AccessLevel

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
        "raw": "{specimen}/histo_raw/{slide_name}.{slide_ext}",
        "x16": "{specimen}/histo_proc/{slide_name}/preproc/{slide_name}_x16_pyramid.tiff",
        "affine": "{specimen}/histo_proc/{slide_name}/recon/{slide_name}_recon_iter10_affine.mat",
        "thumb": "{specimen}/histo_proc/{slide_name}/preproc/{slide_name}_thumbnail.tiff",
        "label": "{specimen}/histo_proc/{slide_name}/preproc/{slide_name}_label.tiff",
        "macro": "{specimen}/histo_proc/{slide_name}/preproc/{slide_name}_macro.tiff",
        "dims": "{specimen}/histo_proc/{slide_name}/preproc/{slide_name}_resolution.txt",
        "metadata": "{specimen}/histo_proc/{slide_name}/preproc/{slide_name}_metadata.json",
        "d_tangles": "{specimen}/histo_proc/{slide_name}/density/{slide_name}_Tau_tangles_densitymap.tiff"
    },

    # The overlays
    "overlays": {
        "d_tangles": {
            "name": "Tau tangle density",
            "pattern": "d_tangles",
            "data_type": "scalar",
            "mapping": [-128, 127],
            "level": {"min":-10., "max":10., "default": 0.},
            "window": {"min":0.5, "max":20., "default": 10., "step": 0.5},
        }
    },

    # The maximum number of bytes that may be cached locally for 'raw' data
    "cache_capacity": 32 * 1024 ** 3
}


class RemoteResourceCache:
    """This class handles local caching of remote files, such as Google Cloud Storage bucket
       files. The cache has a fixed capacity and when it is full, files are deleted to bring
       it to X% capacity."""

    def __init__(self, path):
        """Create new cache in given directory"""
        self._path = path
        self._limits = {}

    def set_limit(self, resource, max_bytes):
        self._limits[resource] = max_bytes

    def get_cache_dir(self, resource):
        return os.path.join(self._path, resource)

    def cleanup(self, resource):
        """
        Clean up the cache corresponding to a given resource. This will delete files
        until the total storage is brought under the cache limit
        :param resource: Resource name (e.g., "raw")
        """

        # If the resource is not managed, quit
        if resource not in self._limits:
            return

        # For each of the files, use os.stat to get information
        f_heap = []
        total_bytes = 0

        # Find all files cached for this resource type
        cache_dir=self.get_cache_dir(resource)
        for root, d_names, f_names in os.walk(cache_dir):
            for fn in f_names:
                fn_full = os.path.join(root, fn)
                s = os.stat(fn_full)
                total_bytes += s.st_size
                heapq.heappush(f_heap, (s.st_atime, s.st_size, fn_full))

        # If the total number of bytes exceeds the cache size
        while total_bytes > self._limits[resource] and len(f_heap) > 0:
            print('Clearing local cache. Used: %d, Limit: %d' % (total_bytes, self._limits[resource]))

            # Get the oldest file
            (atime, size, fn) = heapq.heappop(f_heap)

            # Remove the file
            total_bytes -= size
            os.remove(fn)


# Configure a cache in g if one does not exist
def get_remote_resource_cache():
    if 'rrc' not in g:
        g.rrc = RemoteResourceCache(os.path.join(current_app.instance_path, "resource_cache"))
        g.rrc.set_limit("raw", current_app.config.get('MAX_CACHE_SIZE_RAW', 16 * 2 ** 30))
        g.rrc.set_limit("x16", current_app.config.get('MAX_CACHE_SIZE_X16', 4 * 2 ** 30))

    return g.rrc


class ProjectRef:
    """
    This class is a wrapper around a project. It is responsible for mapping resource keys
    like "raw" or "affine" to local or remote files, and for caching of remote files
    locally.
    """

    # Constructor reads and checks the json file for the project. The
    # name of the json file must match the name of the project in the
    # database. An alternative invocation is to pass None for the name
    # and provide all the necessary fields directly in the dict
    def __init__(self, name, dict=None):

        self.name = name

        if dict is None:
            # When JSON is not provided, we read the entry from the database
            db = get_db()
            rc = db.execute('SELECT * FROM project WHERE id=?', (name,)).fetchone()
            if rc is None:
                raise ValueError('Project %s is not in the database' % (name,))

            # Read the database entries into local vars
            self.disp_name = rc['disp_name']
            self.desc = rc['desc']
            self.url_base = rc['base_url']
            self._dict = json.loads(rc['json'])

        else:
            # Use dict
            self.disp_name = dict['disp_name']
            self.desc = dict['desc']
            self.url_base = dict['base_url']
            self._dict = dict

        # Initialize the URL handler
        if self.url_base.startswith('gs://'):
            self._url_handler = GCSHandler()
        else:
            self._url_handler = None

    def __str__(self):
        return "Project {}".format(self.name)

    def get_url_schema(self):
        return self._dict.get('url_schema', _default_histo_url_schema)

    def get_url_handler(self):
        return self._url_handler

    def get_dict(self):
        return self._dict

    def get_resource_relative_path(self, resource, d):
        """
        Get the relative path of a resource relative to the base URL for the project.
        :param resource: Type of resource (e.g., "raw" for raw slide images)
        :param d: Dictionary used to check the resource against the schema
        :return: Relative path of the file corresponding to the resource
        """
        pattern = self.get_url_schema()["pattern"].get(resource, None)
        return pattern.format(**d) if pattern is not None else None

    def get_resource_url(self, resource, d, local=True):
        """
        Get a full path to a resource (either local file or remote URL)
        """
        rel_path = self.get_resource_relative_path(resource, d)
        if rel_path is None:
            return None

        if local and self._url_handler is not None:
            rrc = get_remote_resource_cache()
            return os.path.join(rrc.get_cache_dir(resource), rel_path)
        else:
            return os.path.join(self.url_base, rel_path)

    def path_exists(self, full_path, local=True):
        # If the project does not use remote URLs, then simply check that the file exists
        if full_path is None:
            return None
        elif self._url_handler is None or local is True:
            return os.path.exists(full_path)
        else:
            return self._url_handler.exists(full_path)

    def resource_exists(self, resource, d, local=True, full_path=None):
        """
        Look up a resource.
        :param resource: Type of resource (e.g., "raw" for raw slide images)
        :param d: Dictionary used to check the resource against the schema
        :param local: Flag indicating to check that the resource is locally available
        """
        full_path = self.get_resource_url(resource, d, local)
        return self.path_exists(full_path, local)


    # Return a list of overlays for a slide
    def get_available_overlays(self, d, local=True):
        """
        Get a listing of available overlays for a slide
        :param d: Dictionary used to check the overlays against the schema
        :param local: Flag indicating to check that the overlays are locally available
        """
        
        # Are there any overlays defined in the schema?
        ovl_dict = self.get_url_schema().get("overlays")
        if ovl_dict is None:
            return None
        
        # For each overlay in the schema, check it against the available resources
        ovl_dict_matched = {}
        for name, o in ovl_dict.items():
            o_resource = o.get("pattern")
            o_path = self.get_resource_url(o_resource, d, local) if o_resource is not None else None
            if o_path is not None and self.path_exists(o_path, local):
                o["url"] = o_path
                ovl_dict_matched[name] = o

        return ovl_dict_matched
        
    # Get a local copy of the resource, copying it if necessary
    def get_local_copy(self, resource, d, check_hash=False, dry_run=False):

        # Get the local URL
        f_local = self.get_resource_url(resource, d, True)
        if f_local is None:
            return None

        # If it already exists, make sure it matches the remote file
        have_local = os.path.exists(f_local) and os.path.isfile(f_local)

        # If no remote handler, then f_local is all there is, so return it
        if self._url_handler is None:
            return f_local if have_local else None

        # If we are not checking hashes, and local file exists, we can return it
        if have_local and not check_hash:
            return f_local

        # Get ready to check the remote
        f_local_md5 = f_local + '.md5'
        f_remote = self.get_resource_url(resource, d, False)

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

        # Prepare cache for downloading a file
        rrc = get_remote_resource_cache()
        rrc.cleanup(resource)

        # Make a copy of the resource locally
        self._url_handler.download(f_remote, f_local)

        # Create an md5 stamp
        with open(f_local_md5, 'wt') as f:
            f.write(self._url_handler.get_md5hash(f_remote))

        return f_local

    def get_download_progress(self, resource, d):

        if self._url_handler is None:
            return 1.0

        # Get the local file and remote blob
        f_local = self.get_resource_url(resource, d, True)
        f_remote = self.get_resource_url(resource, d, False)

        # Get remote size
        sz_local = os.stat(f_local).st_size if os.path.exists(f_local) else 0
        sz_remote = self._url_handler.get_size(f_remote)

        # Get the ratio
        return sz_local * 1.0 / sz_remote

    def get_slide_by_name(self, slide_name):
        """Search for a slide with a given name in the project. All slides imported
        into a project must have unique names"""
        db=get_db()
        rc = db.execute('SELECT * FROM slide_info WHERE slide_name=? AND project=?',
                        (slide_name, self.name)).fetchone()

        return rc['id'] if rc is not None else None

    def user_set_access_level(self, user, access_level):
        """Provide access to a user with level (none,read,write,admin) """
        db=get_db()
        if not AccessLevel.is_valid(access_level):
            raise ValueError("Invalid access level %s" % access_level)

        # Get the list of tasks in this project with access for this user
        rc1 = db.execute('SELECT TA.* FROM task_info TI '
                         '  LEFT JOIN task_access TA on TI.id = TA.task '
                         'WHERE TI.project=? and TA.user=?', (self.name, user))

        # Set the access level for the project
        rc2 = db.execute('INSERT OR REPLACE INTO project_access (user,project,access) VALUES (?,?,?)',
                        (user, self.name, access_level))

        # For each task, make sure its access level is <= that of the project access level
        for row in rc1.fetchall():
            print(row['task'], row['access'], access_level)
            if AccessLevel.to_int(row['access']) > AccessLevel.to_int(access_level):
                print("Lowering access ", access_level, row['task'], user)
                db.execute('UPDATE task_access SET access=? WHERE task=? and user=?',
                           (access_level, row['task'], user))

        db.commit()

    def get_tasks(self):
        """List tasks in this project by id"""
        db=get_db()
        rc = db.execute('SELECT id FROM task_info WHERE project=?', (self.name,))
        return [ int(x['id']) for x in rc.fetchall() ]

    def user_set_task_access_level(self, task, user, access_level, increase_only=False):
        """Provide access to a user with level (none,read,write,admin) """
        if not AccessLevel.is_valid(access_level):
            raise ValueError("Invalid access level %s" % access_level)

        if int(task) not in self.get_tasks():
            raise ValueError('Task %d not in project %s', (task, self.name))

        db=get_db()

        # Check the current access level
        row = db.execute('SELECT access FROM task_access WHERE user=? AND task=?', (user, task)).fetchone()
        val_current = AccessLevel.to_int(row['access'] if row is not None else "none")
        val_new = AccessLevel.to_int(access_level)

        # Don't change access if not needed
        if (increase_only is True and val_current >= val_new) or val_current == val_new:
            return False

        # Set the task access level
        db.execute('INSERT OR REPLACE INTO task_access (user,task,access) VALUES (?,?,?)',
                   (user, task, access_level))

        # Make sure the project access is at least as high as the
        row = db.execute('SELECT * FROM project_access WHERE project=? AND user=?',
                         (self.name, user)).fetchone()
        if row is None or AccessLevel.to_int(row['access']) < AccessLevel.to_int(access_level):
            db.execute('INSERT OR REPLACE INTO project_access (user,project,access) VALUES (?,?,?)',
                       (user, self.name, access_level))

        # Commit
        db.commit()
        return True

    def user_set_all_tasks_access_level(self, user, access_level):
        for t in self.get_tasks():
            self.user_set_task_access_level(t, user, access_level)











from ..slide import *
from .. import create_app
import requests
import json
import pandas as pd
from urllib.parse import urlparse
from io import StringIO

from ..slide import project_listing, task_listing, get_slide_detailed_manifest, task_get_info
from ..dltrain import get_sampling_rois, make_sampling_roi_image
from ..dzi import dzi_download_nii_gz

class Client:
    """A connection to a remote PHAS server. 
    
    The connection is established by supplying a server address and an API key. The API key, 
    which is a JSON file that can be generated through the web interface can also be passed 
    in by setting the environment variable `PHAS_AUTH_KEY`.
    
    Args:
        url (str): URL of the remote server, e.g., `http://histo.itksnap.org:8888`. The URL must include the scheme (`http:` or `https:`) while the port number is optional.
        api_key (str,optional): path to the JSON file storing the user's API key. The API key can be downloaded by connecting to the PHAS server via the web interface.
    """
    
    def __init__(self, url, api_key=None):
        # Create a Flask application, which will allow us to use url_for
        self.flask = create_app()
        
        # Split URL into components and assign them to parts of the flask config
        try:
            parsed = urlparse(url)
            self.flask.config['SERVER_NAME'] = parsed.netloc
            self.flask.config['PREFERRED_URL_SCHEME'] = parsed.scheme
            self.flask.config['APPLICATION_ROOT'] = parsed.path
        except:
            print(f'Failed to parse URL {url}')
            raise
        
        # Load the API key
        try:
            api_key = api_key if api_key is not None else os.environ.get('PHAS_AUTH_KEY', None)
            with open(api_key, 'rt') as fk:
                d = json.load(fk)
                self.api_key_token = d['api_key']
        except:
            print(f'Failed to load API key from {api_key}' if api_key is not None else f'API key was not provided')
            raise
        
        # Compute the authentication URL
        auth = self.flask.url_for('auth.login_with_api_key')
        
        # Connect to the server with the API key
        r = requests.post(auth, {'api_key': self.api_key_token})
        r.raise_for_status()
        self.jar = r.cookies
        
    def __str__(self) -> str:
        o = StringIO()
        server,scheme,root = ( self.flask.config[x] for x in ('SERVER_NAME', 'PREFERRED_URL_SCHEME', 'APPLICATION_ROOT') )
        print(f'PHAS API Client:', file=o)
        print(f'  Server URL:        {scheme}://{server}/{root}', file=o)
        return o.getvalue()
    
    def _get(self, blueprint, endpoint, params=None, **kwargs):
        url = self.flask.url_for(f'{blueprint}.{endpoint.__name__}', **kwargs)
        r = requests.get(url, cookies=self.jar, params=params)
        r.raise_for_status()
        return r
               
    def project_listing(self):
        """Listing of projects available on the server.
        
        Returns:
            A ``dict`` with project details that can be passed to a pandas DataFrame constructor. 
            Only the tasks to which the user has access are returned. 
        """
        r = self._get('slide', project_listing)
        return r.json()
    
    def task_listing(self, project):
        """Listing of tasks available for a project.
        
        Args:
            project(str): Id of the project
        
        Returns:
            A ``dict`` with task details that can be passed to a pandas DataFrame constructor. 
            Only the tasks to which the user has access are returned. 
        """
        r = self._get('slide', task_listing, project=project)
        return r.json()


class Task:
    """A representation of a task on the remote server.
    
    This class represents a task and provides access to methods that are task-specific.
    
    Args:
        client (Client): Connection to the PHAS server
        task_id (int): Numerical id of the task    
    """
    def __init__(self, client:Client, task_id:int):
        self.client = client
        self.task_id = task_id
        r = self.client._get('slide', task_get_info, task_id = self.task_id)
        self.detail = r.json()
        
    def __str__(self):
        o = StringIO()
        print(f'Task {self.task_id} [{self.detail["name"]}]', file=o)
        print(f'  Project: {self.detail["project"]}', file=o)
        print(f'  Description: {self.detail["desc"]}', file=o)
        print(f'  Mode: {self.detail["mode"]}', file=o)
        
    def slide_manifest(self, specimen=None, block=None, 
                       section=None, slide=None, stain=None, 
                       min_paths=None, min_markers=None, min_sroi=None):
        """A detailed listing of the slides in the task.
        
        Args:
            specimen (str,optional): Only list slides for the given specimen
            block (str,optional): Only list slides for the given block
            section (str,optional): Only list slides for the given section
            slide (str,optional): Only list slides for the given slide number
            stain (str,optional): Only list slides with the given stain
            min_paths (int, optional): Only list slides with at least so many annotation paths
            min_markers (int, optional): Only list slides with at least so many annotation markers
            min_sroi (int, optional): Only list slides with at least so many sampling ROIs
        Returns:
            A ``dict`` with slide details that can be passed to a pandas DataFrame constructor
        """
        r = self.client._get('slide', get_slide_detailed_manifest, task_id=self.task_id, params=locals())
        io = StringIO(r.text)
        
        # This is a bit lame to convert to pandas and back to dict, but best for API consistency
        return pd.read_csv(io).to_dict()
    
    def slide_thumbnail_nifti_image(self, slide_id:int, filename:str=None, max_dim:int=1000):
        """Generate a NIFTI image of the sampling ROIs on a slide.
        
        Args:
            slide_id (int): Slide ID
            filename (str, optional): File where to save the image, if not specified ``bytes``
                containing the image are returned
            max_dim (int, optional): Maximum image dimension, defaults to 1000.
        
        Returns:
            raw image data as ``bytes`` or None if filename provided
        """
        r = self.client._get('dzi', dzi_download_nii_gz, 
                             task_id=self.task_id, slide_id=slide_id, resource='raw', downsample=max_dim)
        if filename:
            with open(filename, 'wb') as f:
                f.write(r.content)
        else:
            return r.content

        
class SamplingROITask(Task):
    """A representation of a sampling ROI placement task on the remote server.
    
    Args:
        client (Client): Connection to the PHAS server
        task_id (int): Numerical id of the task    
    """
    
    def __init__(self, client:Client, task_id:int):
        Task.__init__(self, client, task_id)
        

    def get_sampling_rois(self, slide_id:int):
        """Get all the sampling ROIs on a slide.

        Args:
            slide_id(int): Slide ID
        
        Returns:
            A ``dict`` containing the sampling ROIs
        """
        r = self.client._get('dltrain', get_sampling_rois, 
                             task_id=self.task_id, slide_id=slide_id, 
                             mode='raw', resolution='raw')
        return r.json()
    
    def slide_sampling_roi_nifti_image(self, slide_id:int, filename:str=None, max_dim:int=1000):
        """Generate a NIFTI image of the sampling ROIs on a slide.
        
        Args:
            slide_id (int): Slide ID
            filename (str, optional): File where to save the image, if not specified ``bytes``
                containing the image are returned
            max_dim (int, optional): Maximum image dimension, defaults to 1000.
        
        Returns:
            raw image data as ``bytes`` or None if filename provided
        """
        r = self.client._get('dltrain', make_sampling_roi_image, 
                             task_id=self.task_id, slide_id=slide_id, maxdim=max_dim)
        if filename:
            with open(filename, 'wb') as f:
                f.write(r.content)
        else:
            return r.content
        
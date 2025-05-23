from ..slide import *
from .. import create_app
import requests
import json
import pandas as pd
from urllib.parse import urlparse
from io import StringIO, BytesIO
from PIL import Image

from ..auth import login_with_api_key
from ..slide import project_listing, task_listing, get_slide_detailed_manifest, task_get_info
from ..dltrain import get_sampling_rois, make_sampling_roi_image, get_labelset_for_task, get_labelset_label_listing
from ..dltrain import create_sampling_roi, sampling_roi_delete_on_slice, compute_sampling_roi_bounding_box, draw_sampling_roi
from ..dltrain import spatial_transform_roi, get_samples, get_sample_png
from ..dzi import dzi_download_nii_gz, dzi_slide_dimensions, dzi_slide_filepath, get_patch_endpoint, dzi_download_header

from warnings import simplefilter
from urllib3.exceptions import InsecureRequestWarning

class Client:
    """A connection to a remote PHAS server. 
    
    The connection is established by supplying a server address and an API key. The API key, 
    which is a JSON file that can be generated through the web interface can also be passed 
    in by setting the environment variable `PHAS_AUTH_KEY`.
    
    Args:
        url (str): URL of the remote server, e.g., `http://histo.itksnap.org:8888`. The URL must include the scheme (`http:` or `https:`) while the port number is optional.
        api_key (str,optional): path to the JSON file storing the user's API key. The API key can be downloaded by connecting to the PHAS server via the web interface.
        verify (bool, optional): Whether to perform SSL verification (see ``requests`` package)
    """
    
    def __init__(self, url, api_key=None, verify=True):
        # Create a Flask application, which will allow us to use url_for
        self.flask = create_app()
        self.verify = verify
        
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
                api_key_token = d['api_key']
        except:
            print(f'Failed to load API key from {api_key}' if api_key is not None else f'API key was not provided')
            raise
        
        # Connect to the server with the API key
        self.jar = None
        r = self._post('auth', login_with_api_key, {'api_key': api_key_token})
        self.jar = r.cookies
        
    def __str__(self) -> str:
        o = StringIO()
        server,scheme,root = ( self.flask.config[x] for x in ('SERVER_NAME', 'PREFERRED_URL_SCHEME', 'APPLICATION_ROOT') )
        print(f'PHAS API Client:', file=o)
        print(f'  Server URL:        {scheme}://{server}/{root}', file=o)
        return o.getvalue()
    
    def _get(self, blueprint, endpoint, params=None, **kwargs):
        url = self.flask.url_for(f'{blueprint}.{endpoint.__name__}', **kwargs)
        simplefilter('ignore', InsecureRequestWarning)
        r = requests.get(url, cookies=self.jar, params=params, verify=self.verify)
        simplefilter('default', InsecureRequestWarning)
        r.raise_for_status()
        return r
    
    def _post(self, blueprint, endpoint, data=None, json=None, **kwargs):
        url = self.flask.url_for(f'{blueprint}.{endpoint.__name__}', **kwargs)
        simplefilter('ignore', InsecureRequestWarning)
        r = requests.post(url, cookies=self.jar, data=data, json=json, verify=self.verify)
        simplefilter('default', InsecureRequestWarning)
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


class Labelset:
    """A representation of a labelset in PHAS.
    
    Args:
        client (Client): Connection to the PHAS server
        project (str): Project associated with the labelset
        labelset_id (int): Numeric id of the labelset
    """
    
    def __init__(self, client: Client, project:str, labelset_id:int):
        self.client = client
        self.project = project
        self.labelset_id = labelset_id
        
    def label_listing(self):
        r = self.client._get('dltrain', get_labelset_label_listing, 
                             project = self.project, lset=self.labelset_id)
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
        self.project = self.detail['project']
        self._labelset = None
        
    def __str__(self):
        o = StringIO()
        print(f'Task {self.task_id} [{self.detail["name"]}]', file=o)
        print(f'  Project: {self.detail["project"]}', file=o)
        print(f'  Description: {self.detail["desc"]}', file=o)
        print(f'  Mode: {self.detail["mode"]}', file=o)
        return o.getvalue()
        
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
    
    @property
    def labelset(self):
        """`Labelset` associated with this task (or None if task has no labelset)"""
        if self._labelset is None:
            r = self.client._get('dltrain', get_labelset_for_task, task_id=self.task_id)
            lsid = r.json().get('id', None)
            if lsid:
                self._labelset = Labelset(self.client, self.project, r.json()['id'])
            
        return self._labelset


class SamplingROITask(Task):
    """A representation of a sampling ROI placement task on the remote server.
    
    Args:
        client (Client): Connection to the PHAS server
        task_id (int): Numerical id of the task    
    """
    
    def __init__(self, client:Client, task_id:int):
        Task.__init__(self, client, task_id)
        if self.detail["mode"] != 'sampling':
            raise ValueError(f'SamplingROITask cannot be used for task of mode {self.detail["mode"]}')
        

    def slide_sampling_rois(self, slide_id:int):
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
    
    def create_sampling_roi(self, slide_id:int, label_id:int, geom_data: dict):
        """Create a new sampling ROI on a slide.
        
        Args:
            slide_id(int): Slide ID
            label_id(int): Label to assign to the new sampling ROI
            geom_data(dict): A dict describing the sampling ROI geometry, see `dltrain.sampling_roi_schema`
            
        Returns:
            id of the newly created ROI
        """
        r = self.client._post('dltrain', create_sampling_roi, 
                              task_id=self.task_id, slide_id=slide_id,
                              mode='raw', resolution='raw',
                              json={'label_id': label_id, 'geometry': geom_data})
        return r.json()['id']
    
    def delete_sampling_rois_on_slide(self, slide_id:int):
        """Delete all the sampling ROIs on a slide.

        Args:
            slide_id(int): Slide ID
        
        Returns:
            True if request was successful
        """
        r = self.client._get('dltrain', sampling_roi_delete_on_slice, 
                              task_id=self.task_id, slide_id=slide_id)
        return r.text == "success"
                                  
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
                
        
class DLTrainingTask(Task):
    """A representation of a classifier training task on the remote server.
    
    Args:
        client (Client): Connection to the PHAS server
        task_id (int): Numerical id of the task    
    """
    
    def __init__(self, client:Client, task_id:int):
        Task.__init__(self, client, task_id)
        if self.detail["mode"] != 'dltrain':
            raise ValueError(f'DLTrainingTask cannot be used for task of mode {self.detail["mode"]}')
        
    def slide_training_samples(self, slide_id:int):
        """Get all the training samples available on a slide.

        Args:
            slide_id(int): Slide ID
        
        Returns:
            A ``dict`` containing the training samples
        """
        r = self.client._get('dltrain', get_samples, 
                             task_id=self.task_id, slide_id=slide_id)
        return r.json()
    
    def get_sample_image(self, sample_id:int):
        """Download a PNG for a sample.
        
        Args:
            sample_id(id): ID of the sample
        Returns:
            PIL Image containing the requested region
        """
        r = self.client._get('dltrain', get_sample_png, id=sample_id)
        return Image.open(BytesIO(r.content))
        
                
class Slide:
    """A representation of a slide on the remote server.
    
    This class represents a slide and provides access to methods that are slide-specific.
    
    Args:
        task (Task): Task under which to access the slide. The task is used to check access priviliges.
        slide_id (int): Numerical id of the slide    
    """
    def __init__(self, task:Task, slide_id:int):
        self.task = task
        self.task_id = task.task_id
        self.project = task.project
        self.client = task.client
        self.slide_id = slide_id
        r = self.client._get('slide', task_get_slide_info, task_id = self.task_id, slide_id=self.slide_id)
        self.detail = r.json()
        self._header = None
        self._osl_header = None
        self._fullpath = None
        
    def __str__(self):
        o = StringIO()
        print(f'Slide {self.slide_id} in task {self.task_id}', file=o)
        print(f'  Specimen (public name): {self.detail["specimen_public"]}', file=o)
        print(f'  Specimen (private name): {self.detail["specimen_private"]}', file=o)
        print(f'  Block name: {self.detail["block_name"]}', file=o)
        print(f'  Section Number: {self.detail["section"]}', file=o)
        print(f'  Slide Number: {self.detail["slide"]}', file=o)
        print(f'  Stain: {self.detail["stain"]}', file=o)
        return o.getvalue()
    
    def thumbnail_nifti_image(self, filename:str=None, max_dim:int=1000):
        """Generate a NIFTI image of the sampling ROIs on a slide.
        
        Args:
            filename (str, optional): File where to save the image, if not specified ``bytes``
                containing the image are returned
            max_dim (int, optional): Maximum image dimension, defaults to 1000.
        
        Returns:
            raw image data as ``bytes`` or None if filename provided
        """
        r = self.client._get('dzi', dzi_download_nii_gz, 
                             project=self.project, slide_id=self.slide_id, resource='raw', downsample=max_dim)
        if filename:
            with open(filename, 'wb') as f:
                f.write(r.content)
        else:
            return r.content
        
    def get_patch(self, center, level, size, tile_size=1024):
        """Read a region from the slide.
        
        Args:
            center: Tuple of int indicating the center of the patch in full-resolution pixel units
            level: Downsample level from which to retrieve the region.
            size: Size of the image to retrieve.
        Returns:
            PIL Image containing the requested region
        """
        # Old method, should be removed
        if tile_size == 0:
            r = self.client._get('dzi', get_patch_endpoint, 
                                 project=self.project, slide_id=self.slide_id, resource='raw',
                                 level=level, ctrx=center[0], ctry=center[1], w=size[0], h=size[1], format='png')
            return Image.open(BytesIO(r.content))
        
        else:            
            # Break the image region into tiles
            full_image = None
            tiles_x, tiles_y = np.arange(0, size[0], tile_size), np.arange(0, size[1], tile_size)
            for x in tiles_x:
                w = np.minimum(size[0] - x, tile_size)
                cx = center[0] + x - 0.5 * (size[0] - w)
                for y in tiles_y:
                    h = np.minimum(size[1] - y, tile_size)
                    cy = center[1] + y - 0.5 * (size[1] - h)
                    r = self.client._get('dzi', get_patch_endpoint, 
                                 project=self.project, slide_id=self.slide_id, resource='raw',
                                 level=level, ctrx=cx, ctry=cy, w=w, h=h, format='png')
                    tile = Image.open(BytesIO(r.content))
                    print(f'Requesting region {cx-w/2,cy-w/2,w,h} from image, tile {tile} pasting at {x,y}')
                    if full_image is None:
                        full_image = Image.new(tile.mode, (size[0], size[1]))
                    full_image.paste(tile, (x, y))
            return full_image
        
    def _get_header(self):        
        if self._header is None:
            self._header = self.client._get('dzi', dzi_slide_dimensions, project=self.project, slide_id=self.slide_id).json()
        return self._header
        
    def _get_openslide_header(self):        
        if self._osl_header is None:
            self._osl_header = self.client._get('dzi', dzi_download_header, project=self.project, slide_id=self.slide_id, resource='raw').json()
        return self._osl_header
        
    @property
    def dimensions(self):
        """Slide dimensions in pixels (tuple of int)."""
        return self._get_header()['dimensions']
    
    @property
    def spacing(self):
        """Slide pixel spacing in millimeters."""
        return self._get_header()['spacing']
    
    @property
    def level_dimensions(self):
        """Slide pixel spacing in millimeters."""
        return self._get_openslide_header()['level_dimensions']
    
    @property
    def level_downsamples(self):
        """Slide pixel spacing in millimeters."""
        return self._get_openslide_header()['level_downsamples']
    
    @property
    def properties(self):
        """Slide pixel spacing in millimeters."""
        return self._get_openslide_header()['properties']
    
    # TODO: need to implement fine-grained permissions
    @property
    def fullpath(self):
        """Full path or URL of the slide on the server."""
        if self._fullpath is None:
            self._fullpath = self.client._get('dzi', dzi_slide_filepath, project=self.project, slide_id=self.slide_id).json()['remote'] 
        return self._fullpath        

    @property
    def stain(self):
        """Name of the slide's stain (str)"""
        return self.detail['stain']
    
    @property
    def block_name(self):
        """Name of the slide's block (str)"""
        return self.detail['block_name']
    
    @property
    def specimen_private(self):
        """Name of the slide's specimen, not anonymized (str)"""
        return self.detail['specimen_private']
    
    @property
    def specimen_public(self):
        """Name of the slide's specimen, anonymized (str)"""
        return self.detail['specimen_public']
    
    @property
    def section(self):
        """Number of the slide's section (int)"""
        return self.detail['section']

    @property
    def slide_number(self):
        """Number of the slide within the section (int)"""
        return self.detail['slide']


class SamplingROIPatchExtractor:
    """
    SamplingROIPatchExtractor is a helper class that makes it easy to download patches that overlap 
    a sampling ROI. This is particularly useful for sampling ROIs that are large where we do not
    want to process the whole ROI using quantitative tools, but rather extract a few random measurements
    from the ROI.

    The area containing the sampling ROI is divided into equal size tiles (default size 1024x1024) and
    the list of tiles that overlap the sampling ROI can be obtained using `tiles()` method. Then for each
    tile, a patch containing that tile, plus some `padding` can be extracted, along with a mask that shows
    the portion of the tile that overlaps the sampling ROI, using `get_tile_patch_and_mask`.

    Args:
        slide (Slide): The slide from which the patches will be sampled
        geom (dict): The geometry of the sampling ROI that will be used to guide the sampling.
        tile_size (int, optional): The size of the tile, default is 1024 pixels, should be divisible
                by `sub_tiles_per_tile`
        padding (int, optional): Padding added to the tiles. Default: 32 pixels.
        sub_tiles_per_tile (int, optional): Subtiling is used to render the polygon before figuring out
                what tiles are overlapping the ROI. The default 16 should be good for most applications.    
    """

    def __init__(self, slide, geom, tile_size=1024, padding = 32, sub_tiles_per_tile = 16):
        self.tile_size = tile_size
        self.sub_tiles_per_tile = sub_tiles_per_tile
        self.padding = padding
        self.slide = slide
        self.geom = geom

        # Compute the number of tiles to cover the ROI
        x0,y0,x1,y1 = compute_sampling_roi_bounding_box(geom)
        w, h = x1-x0, y1-y0
        tw, th = (int(np.ceil(u / tile_size)) for u in (w, h))
        
        # Compute the upper left corner of the tiling that leaves equal margins on all sides
        self.tx, self.ty = int((x1+x0)//2 - (tw * tile_size)//2), int((y1+y0)//2 - (th * tile_size)//2)

        # Create a canvas where each pixel is a sub-tile and draw the polygon onto this canvas
        subtile_canvas = Image.new('L', (tw*sub_tiles_per_tile, th*sub_tiles_per_tile))
        draw_sampling_roi(subtile_canvas, self.translate_geom(geom, -self.tx, -self.ty), 
                          sub_tiles_per_tile/tile_size, sub_tiles_per_tile/tile_size, 
                          fill='white', outline='white')
        self.arr_subtiles = np.array(subtile_canvas)

        # Find the tiles that overlap the polygon
        n = sub_tiles_per_tile
        self.arr_tiles = self.arr_subtiles.reshape(-1, n, self.arr_subtiles.shape[1]//n, n).mean((-1,-3)) / 255.

        # Extract the indices of all the tiles that overlap the shape
        self.tile_nz = np.flip(np.vstack(np.nonzero(self.arr_tiles)).T, 1)
        self.tile_overlap = self.arr_tiles[self.arr_tiles > 0]

    def tiles(self):
        """
        Return the indices of the tiles that overlap the sampling ROI

        Returns:
            N x 2 array of tile indices, each tile corresponds to a patch that can be downloaded
            that overlaps the sampling ROI.
        """
        return self.tile_nz, self.tile_overlap

    def get_tile_patch_and_mask(self, tile_index):
        """
        Download a padded tile and ROI mask for the tile. 

        Args:
            tile_index (tuple): Index of the tile, must be one of the rows of the array returned by `tiles()`

        Returns:
            Tuple `image`,`mask`, where `image` is a PIL image containig the padded tile, and `mask` is a PIL
            image in which all the pixels in the **non-padded** part of the tile that overlap the sampling ROI
            are labeled 255.
        """
        # Extract the patch
        tile_size_pad = self.tile_size + 2 * self.padding
        patch_xy = np.array((self.tx, self.ty)) + np.array(tile_index) * self.tile_size 
        patch_ctr = patch_xy + self.tile_size//2
        patch = self.slide.get_patch(patch_ctr.tolist(), 0, (tile_size_pad, tile_size_pad), tile_size=0).convert("RGB")

        # Draw the mask over the patch
        mask_canvas_padded = Image.new('L', (tile_size_pad, tile_size_pad))
        mask_canvas_inner = Image.new('L', (self.tile_size, self.tile_size))
        draw_sampling_roi(mask_canvas_inner, self.translate_geom(self.geom, -patch_xy[0], -patch_xy[1]), 1, 1, fill='white')
        mask_canvas_padded.paste(mask_canvas_inner, (self.padding, self.padding))

        # Return the two images
        return patch, mask_canvas_padded
    
    def tile_patch_origin(self, tile_index):
        """
        Compute the origin (per ITK) of the patch returned by `get_tile_patch_and_mask`
        
        Args:
            tile_index (tuple): Index of the tile, must be one of the rows of the array returned by `tiles()`

        Returns:
            `np.array` of size 2 containing the coordinate of the center of the (0,0) pixel in the patch returned
            by `get_tile_patch_and_mask`. This can be used, together with `Slide.spacing` to set the header of
            the patch image relative to the overall slide image.    
        """
        spacing = np.array(self.slide.spacing)
        patch_xy = np.array((self.tx, self.ty)) + np.array(tile_index) * self.tile_size - self.padding
        origin = spacing * (patch_xy + 0.5)
        return origin
        

    def tile_mask_density(self):
        """
        Returns: a 2D array where each element represents a tile and the value of each element is the percentage
        of the area of that tile that overlaps the sampling ROI. For visualization/debugging use.
        """
        return self.arr_tiles
        
    def subtile_mask_density(self):
        """
        Returns: a 2D array where each element represents a subtile and the value of each element is the percentage
        of the area of that tile that overlaps the sampling ROI. For visualization/debugging use.
        """
        return self.arr_subtiles
        
    # Shift geometry by amount
    def translate_geom(self, geom, dx, dy):
        gnew = geom.copy()
        tran = lambda xy : (xy[0] + dx, xy[1] + dy)
        spatial_transform_roi(gnew, tran)
        return gnew
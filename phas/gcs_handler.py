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
import urllib.parse as urlparse
import os
import threading
from google.cloud import storage
import io
import tifffile
import numpy as np
from PIL import Image
from sortedcontainers import SortedKeyList
import time

# This class handles remote URLs for Google cloud. The remote URLs must have format
# "gs://bucket/path/to/blob.ext"

class GCSHandler:

    _client = None  # type: storage.Client

    # Constructor
    def __init__(self):
        self._bucket_cache = {}
        self._blob_cache = {}

    # Get client (on demand)
    def get_client(self):
        if self._client is None:
            self._client = storage.Client()
        return self._client

    # Process a URL
    def _get_blob(self, uri):

        # Check the cache
        if uri in self._blob_cache:
            return self._blob_cache[uri]

        # Unpack the URL
        o = urlparse.urlparse(uri)

        # Make sure that it includes gs
        if o.scheme != "gs":
            raise ValueError('URL should have schema "gs"')

        # Find the bucket, if not found add it to the cache
        if o.netloc in self._bucket_cache:
            bucket = self._bucket_cache[o.netloc]
        else:
            bucket = self.get_client().get_bucket(o.netloc)
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
        os.makedirs(dir_path, exist_ok=True)

        # Perform the download
        blob = self._get_blob(uri)
        with open(local_file, "wb") as file_obj:
            worker = threading.Thread(target=self.get_client().download_blob_to_file, args=(blob, file_obj))
            worker.start()
            while worker.is_alive():
                worker.join(1.0)
                print('GCS: downloaded: %d of %s' % (os.stat(local_file).st_size, uri))

    # Download a text file directory to memory
    def download_text_file(self, uri):
        blob = self._get_blob(uri)
        sfile = io.BytesIO()
        self.get_client().download_blob_to_file(blob, sfile)
        print(sfile.getvalue())
        return sfile.getvalue().decode('UTF-8')

    # Get the remote download size
    def get_size(self, uri):
        return self._get_blob(uri).size


class AbstractMultiFilePageCache:
    """
    Abstract parent class for in-memory cache for large files. A file is divided into 
    discrete pages of equal size. Each page has a timestamp that is updated when the 
    page is read or written.
    """

    class CachePage:
        def __init__(self, index, t_access, data=None):
            self.index = index
            self.t_access = t_access
            self.data = data

    def __init__(self, page_size_mb=1):
        self.cache = {}
        self.page_size = page_size_mb * 1024**2
        self.t_last_purge = time.time_ns()

    def get_page(self, url, pageno):        
        pages = self.cache.get(url)
        if not pages:
            self.cache[url] = pages = dict()
        page = pages.get(pageno)
        if page:
            page.t_access = time.time_ns()
            return page
    
    def set_page(self, url, pageno, data):
        pages = self.cache.get(url)
        if not pages:
            self.cache[url] = pages = dict()
        page = pages.get(pageno)
        if not page:
            pages[pageno] = page = self.CachePage(pageno, time.time_ns(), data)
            return page

    def purge(self, t_purge):
        if t_purge > self.t_last_purge:
            for url, pages in self.cache.items():
                self.cache[url] = dict({ i: page for (i, page) in pages.items() if page.t_access >= t_purge })
            self.t_last_purge = t_purge


class SelfManagedMultiFilePageCache(AbstractMultiFilePageCache):
    """
    An in-memory cache for large files. A file is divided into discrete pages of equal
    size. Each page has a timestamp that is updated when the page is read or written.

    This cache does simple memory management based on maximum allowed size. When the size
    is exceeded, the specified fraction of the cache is purged.
    """

    def __init__(self, page_size_mb=1, cache_max_size_mb=1024, purge_size_pct=0.25):
        AbstractMultiFilePageCache.__init__(self, page_size_mb)
        self.cache_max_size = cache_max_size_mb * 1024**2
        self.purge_size_pct = purge_size_pct
        self.total_size = 0

    def set_page(self, url, pageno, data):
        page = AbstractMultiFilePageCache.set_page(self, url, pageno, data)
        if page:
            self.total_size += self.page_size
        
        if self.total_size >= self.cache_max_size:
            # Time has come to purge the cache. For this we need to sort
            # all the pages by access time
            l_purge = SortedKeyList()
            for _, pp in self.cache.items():
                for _, page in pp.items():
                    l_purge.add(page.t_access)
                    
            # Purge pages to free up space
            n_purge = min(max(1, int(len(l_purge) * self.purge_size_pct)), len(l_purge)-1)
            if n_purge > 0:
                self.purge(l_purge[n_purge-1])
                
        return page
        

class MultiprocessManagedMultiFilePageCache(AbstractMultiFilePageCache):
    """
    An in-memory cache for large files. A file is divided into discrete pages of equal
    size. Each page has a timestamp that is updated when the page is read or written.

    A special feature of this cache is that it is designed to be used in a multiprocess
    setting. Each process has its own cache, but the size of the cache across all the 
    processes is monitored and constrained. The cache is purged whenever the total size
    of all caches exceeds a threshhold. To facilitate this, the cache writes to a queue
    each time that a page is read or written, allowing the manager process to keep track
    of pages and timestamps across all pages. 
    """

    def __init__(self, unique_id, report_queue, page_size_mb=1):
        AbstractMultiFilePageCache.__init__(self, page_size_mb)
        self.cache = {}
        self.unique_id = unique_id
        self.report_queue = report_queue
        self.page_size = page_size_mb * 1024**2

    def get_page(self, url, pageno):        
        page = AbstractMultiFilePageCache.get_page(self, url, pageno)
        if page:
            self.report_queue.put((self.unique_id, url, pageno, page.t_access))
        return page

    def set_page(self, url, pageno, data):
        page = AbstractMultiFilePageCache.set_page(self, url, pageno, data)
        if page:
            self.report_queue.put((self.unique_id, url, pageno, page.t_access))
        return page


class CachedFileRepresentation:
    """
    This class works together with a cache to provide fast access to files that may
    be on a remote filesystem. The main method is readinto which reads data either 
    from the source using the callback supplied by the user, or from the cache if
    available.
    """
        
    def __init__(self, cache):
        self.cache = cache
        self.page_size = cache.page_size
        
    def fullfill(self, offset, size, dest, page):
        page_start = page.index * self.page_size        
        off_dest = max(0, page_start - offset)
        off_page = max(0, offset - page_start)
        len_page = len(page.data)
        size_adj = min(offset + size - (page_start + off_page), len_page - off_page)
        dest[off_dest:(off_dest+size_adj)] = page.data[off_page:(off_page+size_adj)]
        return size_adj
        
    def split_range(self, p0, p1, p_excl):
        ranges = []
        current_start = p0
        for p in p_excl:
            if p0 <= p <= p1:
                if current_start < p:
                    ranges.append((current_start, p))
                current_start = p + 1
        if current_start < p1:
            ranges.append((current_start, p1))
        return ranges    
        
    def readinto(self, url, offset, size, dest, fn_readinto):
        
        # This is the range of pages that is spanned by the data
        ps = self.page_size
        p0, p1 = offset // ps, (offset+size-1) // ps
        
        # Iterate over the needed pages
        size_fullfilled = 0
        not_in_cache = []
        p = p0
        while True:
            page = self.cache.get_page(url, p) if p <= p1 else None
            if p <= p1 and page is None:
                not_in_cache.append(p)
            else:
                if len(not_in_cache) > 0:

                    # Read the missing pages
                    j0, j1 = not_in_cache[0], not_in_cache[-1]
                    chunk_size = (1 + j1 - j0) * ps
                    chunk = bytearray(chunk_size)
                    fn_readinto(j0 * ps, chunk_size, chunk)
                    
                    # Place the missing pages into the cache
                    for j in range(j0, j1+1):
                        pnew = self.cache.set_page(url, j, chunk[(j-j0)*ps:(j+1-j0)*ps])
                        size_fullfilled += self.fullfill(offset, size, dest, pnew)

                    not_in_cache = []
                if p <= p1:
                    size_fullfilled += self.fullfill(offset, size, dest, page)
                else:
                    break
            p += 1
                
        return size_fullfilled

import concurrent.futures
class GoogleCloudTiffHandle(io.RawIOBase):
    def __init__(self, client: storage.Client, gs_url: str, cache):
        self.gs_url = gs_url
        url_parts = urlparse.urlparse(gs_url)
        self._bucket = client.get_bucket(url_parts.netloc)
        self._blob = self._bucket.get_blob(url_parts.path.strip('/'))        
        self.fsize = self._blob.size  
        self.pos = 0
        self.cache = CachedFileRepresentation(cache)
        self.total_read = 0
        self.total_served = 0
        self.total_gcpops = 0
        self.total_gcpns = 0
        print(f'Created handle for GCS-hosted Tiff file {gs_url} of size {self.fsize}')  

    def __del__(self):
        self.print_report()
        
    def print_report(self):        
        print(f'Cache Performance for GoogleCloudTiffHandle[{self.gs_url}]:')
        print(f'  Total Read (MB)        : {self.total_read / 1024**2:6.2f}')
        print(f'  Total Served (MB)      : {self.total_served / 1024**2:6.2f}MB') 
        print(f'  Efficiency             : {self.total_served / self.total_read}')
        print(f'  REST API calls         : {self.total_gcpops}')
        print(f'  MB per call            : {self.total_read / (1024**2 * self.total_gcpops):6.2f}')
        print(f'  Time per call (ms)     : {self.total_gcpns / (1000**2 * self.total_gcpops):6.2f}')
    
    def _readinto_internal(self, offset, size, buffer):
        size = min(size, self.fsize - offset)
        
        # Concurrent download with 8 threads (does not seem to provide meaningful speedup)
        '''
        def dl_piece(start, end):
            # print(f'  chunk {start}:{end}')
            data = self._blob.download_as_bytes(start=start, end=end, checksum=None)
            return start, data
        n_threads = 8
        piece_bnd = np.lib.stride_tricks.sliding_window_view(np.linspace(offset,offset+size-1,n_threads+1).astype(int),2).tolist()

        t_start = time.time_ns()
        chunk = bytearray(size)
        with concurrent.futures.ThreadPoolExecutor(max_workers=n_threads) as executor:
            futures = [ executor.submit(dl_piece, p[0], p[1]) for p in piece_bnd ]
            for future in concurrent.futures.as_completed(futures):
                start, data = future.result()
                chunk[start-offset:start+len(data)-offset] = data
        '''
        
        # Non-concurrent download
        t_start = time.time_ns()
        chunk = self._blob.download_as_bytes(start=offset, end=offset+size-1, checksum=None)
        t_used = time.time_ns() - t_start
        
        n_read = len(chunk)
        print(f'GCS DL {n_read//1024**2}MB @{offset//1024**2}MB from {self.gs_url} in {t_used//1000**2}ms')
        buffer[:n_read] = chunk
        self.total_read += n_read
        self.total_gcpops += 1
        self.total_gcpns += t_used
        return n_read
    
    def readinto(self, buffer):
        if isinstance(buffer, np.ndarray):
            buffer = buffer.view(np.uint8)
            print(buffer, len(buffer), self.fsize, self.pos, self.fsize - self.pos)
        elif not isinstance(buffer, (bytearray, memoryview)):
            raise TypeError("Buffer object expected, got: {}".format(type(buffer)))
        size = min(len(buffer), self.fsize - self.pos)
        if self.cache:
            n_read = self.cache.readinto(self.gs_url, self.pos, size, buffer, self._readinto_internal)
        else:
            n_read = self._readinto_internal(self.pos, size, buffer)            
        self.pos += n_read
        self.total_served += n_read
        return n_read

    def read(self, size=-1):
        if size == -1:
            size = self.fsize - self.pos
        data = bytearray(size)
        nread = self.readinto(data)
        return bytes(data) if size == nread else bytes(data[:nread])

    def seek(self, offset, whence=io.SEEK_SET):
        """Move to a new file position."""
        if whence == io.SEEK_SET:
            self.pos = offset
        elif whence == io.SEEK_CUR:
            self.pos += offset
        elif whence == io.SEEK_END:
            self.pos = self.fsize + offset
        else:
            raise ValueError("Invalid value for `whence`.")
        return self.pos

    def tell(self):
        """Return the current file position."""
        return self.pos

    
# OpenSlide wrapper for GCS
class GoogleCloudOpenSlideWrapper:
    
    def __init__(self, client, gs_url, cache=None):
        # Load the tiff file
        self.h = GoogleCloudTiffHandle(client, gs_url, cache)
        self.tf = tifffile.TiffFile(self.h)
        
        # Collect the tiled pages
        self.tiled_pages = [ p for p in self.tf.pages if p.is_tiled ]
        
        # Place to store associated images
        self.assoc = {}
        
    def read_region(self, location, level, size):
        page = self.tiled_pages[level]
        
        # The pos coordinates are in the coordinate frame of level 0 and
        # have to be converted to the correct coordinate frame
        ds = self.level_downsamples[level]
        location = [ int(0.5 + x // ds) for x in location ]

        if not page.is_tiled:
            arr = page.asarray()
            icrop = arr[location[1]:(location[1]+size[1]),location[0]:(location[0]+size[0]),:]

        else:
            fh = self.tf.filehandle
            tw, th = page.tilewidth, page.tilelength
            
            # How many tiles in x and y
            ntx = 1 + (page.imagewidth-1) // page.tilewidth
            nty = 1 + (page.imagelength-1) // page.tilelength
            
            tx0 = location[0] // tw
            ty0 = location[1] // th
            tx1 = (location[0] + size[0]) // tw
            ty1 = (location[1] + size[1]) // th
            stx, sty = 1+tx1-tx0, 1+ty1-ty0
            
            image = np.zeros((sty * th, stx * tw, page.samplesperpixel), dtype=page.dtype)
            for ty in range(ty0, np.minimum(ty1 + 1, nty)):
                for tx in range(tx0, np.minimum(tx1 + 1, ntx)):
                    i_tile = ty * ntx + tx
                    fh.seek(page.dataoffsets[i_tile])
                    data = fh.read(page.databytecounts[i_tile])
                    tile, indices, shape = page.decode(data, i_tile, jpegtables=page.jpegtables)
                    offx, offy = (tx - tx0) * tw, (ty - ty0) * th
                    image[offy:(offy+th),offx:(offx+th),:] = np.array(tile, dtype=page.dtype)
                    
            # Crop out the exact image requested
            crop_x, crop_y = location[0] - tx0 * tw, location[1] - ty0 * th
            icrop = image[crop_y:(crop_y+size[1]),crop_x:(crop_x+size[0]),:]
        
        # Return the image as PIL
        return Image.fromarray(icrop).convert("RGBA")

    def get_thumbnail(self, size):
        # Taken from openslide
        downsample = max(dim / thumb for dim, thumb in zip(self.dimensions, size))
        level = self.get_best_level_for_downsample(downsample)
        tile = self.read_region((0, 0), level, self.level_dimensions[level])
        # Apply on solid background
        bg_color = '#' + self.properties.get('openslide.background-color', 'ffffff')
        thumb = Image.new('RGB', tile.size, bg_color)
        thumb.paste(tile, None, tile)
        # Image.Resampling added in Pillow 9.1.0
        # Image.LANCZOS removed in Pillow 10
        thumb.thumbnail(size, getattr(Image, 'Resampling', Image).LANCZOS)
        # if self._profile is not None:
        #    thumb.info['icc_profile'] = self._profile
        return thumb

    
    @property
    def level_count(self):
        return len(self.tiled_pages)
    
    @property
    def dimensions(self):
        print(self.tiled_pages[0])
        return self.tiled_pages[0].imagewidth, self.tiled_pages[0].imagelength
    
    @property
    def level_dimensions(self):
        return list([(p.imagewidth,p.imagelength) for p in self.tiled_pages])
    
    @property
    def level_downsamples(self):
        w = self.tiled_pages[0].imagewidth
        return list([w / p.imagewidth for p in self.tiled_pages])
    
    @property
    def associated_images(self):
        if not self.assoc:
            for page in self.tf.pages:
                if page.is_tiled is False and 'ImageDescription' in page.tags:
                    for tag in 'macro', 'label', 'thumbnail':
                        if tag in page.tags['ImageDescription'].value.lower():
                            self.assoc[tag] = Image.fromarray(page.asarray()).convert("RGBA")
        return self.assoc
    
    # TODO: add all the TIFF properties 
    @property
    def properties(self):
        # Read MPP. For Aperio files, this information is not stored in the Tiff tags but can be
        # read from the image header. For more regular TIFF we can use the get_resolution method
        page = self.tiled_pages[0]
        d = page.description
        is_svs = d is not None and d.startswith('Aperio Image Library')
        has_res = all([t in page.tags for t in ('XResolution','YResolution','ResolutionUnit')])
        
        # If this is an Aperio image, first try reading MPP from there
        mpp_x, mpp_y = None, None
        if is_svs:
            svsmeta = tifffile.tifffile.svs_description_metadata(d)
            mpp_x = svsmeta.get('MPP', None)
            mpp_y = mpp_x
        if (mpp_x is None or mpp_y is None) and has_res:
            ppm_x, ppm_y = page.get_resolution(unit=tifffile.RESUNIT.MICROMETER)
            mpp_x, mpp_y = 1.0 / ppm_x, 1.0 / ppm_y
        
        return dict({
            'openslide.mpp-x': mpp_x,
            'openslide.mpp-y': mpp_y            
        })
    
    def get_best_level_for_downsample(self, downsample):
        ds = self.level_downsamples
        for (i, ds_i) in enumerate(ds):
            if downsample < ds_i:
                return max(i-1, 0)
        return len(ds)-1        

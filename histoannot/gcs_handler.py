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
from bisect import bisect, insort
from collections import namedtuple
import numpy as np
from PIL import Image
from sortedcontainers import SortedKeyList

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
        if uri.endswith('.tif') or uri.endswith('.tiff'):
            raise Exception('No TIFF DL')
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


class PageCache:

    Page = namedtuple('Page', ('offset', 'size', 'data', 'order'))
    _counter = 0
    
    def __init__(self, chunk_size, cache_max_size=200, purge_size=50):
        self.cache = []
        self.chunk_size = chunk_size
        self.cache_max_size = cache_max_size
        self.purge_size = purge_size
        
    def read_cache(self, offset, size):
        page = self.Page(offset=offset, size=self.chunk_size+1, data=None, order=0)
        pos = bisect(self.cache, page) - 1
        
        if pos >= 0 and pos < len(self.cache):
            z_offset, z_size = self.cache[pos].offset, self.cache[pos].size
            if z_offset <= offset and z_offset + z_size >= offset + size:
                c_offset = offset - self.cache[pos].offset
                self.cache[pos]._replace(order=self._counter)
                self._counter+=1
                return self.cache[pos].data[c_offset: c_offset+size]
        return None
    
    def write_cache(self, offset, data):
        page = self.Page(offset, len(data), data, self._counter)
        self._counter += 1
        insort(self.cache, page)
        

class MultiFilePageCache:
    """
    This class caches pieces of remote files. Each file is divided into pages of a fixed
    size and pages are stored in the cache until memory is exhausted. When a range of data
    is needed from the remote file, calling readinto() on the cache will provide the part 
    of the range that is already available and will fullfill the rest from the remote 
    source. 
    """
    
    class CachePage:
        def __init__(self, index, t_access, data=None):
            self.index = index
            self.t_access = t_access
            self.data = data

    # Counter - used to implement time for cache clearing purposes
    _counter = 0
    
    def __init__(self, page_size_mb=1, cache_max_size_mb=1024, purge_size_pct=0.25):
        self.cache = {}
        self.page_size = int(page_size_mb * 1024**2)
        self.cache_max_size = int(cache_max_size_mb * 1024**2)
        self.purge_size = int(self.cache_max_size * purge_size_pct)
        self.total_size = 0
        
    @property
    def timestamp(self):
        self._counter += 1
        return self._counter
    
    def insert(self, entry, page):
        if self.total_size + self.page_size > self.cache_max_size:
            # Time has come to purge the cache. For this we need to sort
            # all the pages by access time
            l_purge = SortedKeyList(key=lambda x:x[2].t_access)
            for url,pp in self.cache:
                for index,page in pp:
                    l_purge.add((url, index, page))
                    
            # Purge pages to free up space
            n_purge = min(max(1,self.purge_size // self.page_size), len(l_purge))
            for k in range(n_purge):
                url,index,_ = l_purge[k]
                self.cache[url].remove(index)
                
        # Finally add the new page to the cache entry
        entry[page.index] = page        
    
    def fullfill(self, offset, size, dest, page):
        page_start = page.index * self.page_size        
        off_dest = max(0, page_start - offset)
        off_page = max(0, offset - page_start)
        size_adj = min(offset + size - (page_start + off_page), self.page_size)
        dest[off_dest:(off_dest+size_adj)] = page.data[off_page:(off_page+size_adj)]
        page.t_access = self.timestamp
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
        
        # Retrieve the cache entry for this URL
        pages = self.cache.get(url)
        if not pages:
            self.cache[url] = pages = dict()
            
        # This is the range of pages that is spanned by the data
        ps = self.page_size
        p0, p1 = offset // ps, (offset+size-1) // ps
        
        # This is a list of pages that are present in the cache
        #p_cached = SortedKeyList(pages.irange_key(p0, p1), key=lambda x: x.index)
        #print(f'Need pages {p0} through {p1}, cache has pages {[x.index for x in p_cached]}')
        
        # Get the list of ranges that need to be retrieved from the cache
        #r_missing = self.split_range(p0, p1+1, [p.index for p in p_cached])
        
        # Iterate over the needed pages
        #i_miss = iter(r_missing)
        size_fullfilled = 0
        not_in_cache = []
        p = p0
        while True:
            if p <= p1 and p not in pages:
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
                        page = self.CachePage(j, self.timestamp, chunk[(j-j0)*ps:(j+1-j0)*ps])
                        size_fullfilled += self.fullfill(offset, size, dest, page)
                        self.insert(pages, page)

                    not_in_cache = []
                if p <= p1:
                    size_fullfilled += self.fullfill(offset, size, dest, pages[p])
                else:
                    break
            p += 1
                
        return size_fullfilled

        
class GoogleCloudTiffHandle(io.RawIOBase):
    def __init__(self, client: storage.Client, gs_url: str, cache: None):
        self.gs_url = gs_url
        url_parts = urlparse.urlparse(gs_url)
        self._bucket = client.get_bucket(url_parts.netloc)
        self._blob = self._bucket.get_blob(url_parts.path.strip('/'))        
        self.fsize = self._blob.size  
        self.pos = 0
        self.cache = cache
        self.total_read = 0
        self.total_served = 0
        print(f'Created handle for GCS-hosted Tiff file {gs_url} of size {self.fsize}')  
        
    def __del__(self):
        print(f'GoogleCloudTiffHandle[{self.gs_url}]  Total Read: {self.total_read}   Total Served: {self.total_served}   Efficiency: {self.total_served / self.total_read}')
    
    def _readinto_internal(self, offset, size, buffer):
        size = min(size, self.fsize - offset)
        print(f'GCS DL {offset}:{offset+size-1} FROM {self.gs_url}')
        chunk = self._blob.download_as_bytes(start=offset, end=offset+size-1, checksum=None)
        n_read = len(chunk)
        buffer[:n_read] = chunk
        return n_read
    
    def readinto(self, buffer):
        if isinstance(buffer, np.ndarray):
            buffer = memoryview(buffer)
        elif not isinstance(buffer, (bytearray, memoryview)):
            raise TypeError("Buffer object expected, got: {}".format(type(buffer)))
        size = min(len(buffer), self.fsize - self.pos)
        print(f'Read {self.pos}:{self.pos+size} FROM {self.gs_url}')
        if self.cache:
            n_read = self.cache.readinto(self.gs_url, self.pos, size, buffer, self._readinto_internal)
        else:
            n_read = self._readinto_internal(self.pos, size, buffer)            
        self.pos += n_read
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

'''

class GoogleCloudTiffHandle(io.RawIOBase):
    def __init__(self, client: storage.Client, cache, gs_url: str, page_size=1024**2):
        self.gs_url = gs_url
        url_parts = urlparse.urlparse(gs_url)
        self._bucket = client.get_bucket(url_parts.netloc)
        self._blob = self._bucket.get_blob(url_parts.path.strip('/'))        
        self.fsize = self._blob.size  
        self.pos = 0
        self.total_read = 0
        self.total_served = 0
        self.cache = cache
        self.page_size = page_size
        print(f'Read GSC resource {gs_url} of size {self.fsize}')  
        
    def __del__(self):
        print(f'GoogleCloudTiffHandle[{self.gs_url}]  Total Read: {self.total_read}   Total Served: {self.total_served}   Efficiency: {self.total_served / self.total_read}')
    
    def read(self, size=-1):
        """Fetch `size` bytes from the current position in the file."""
        if size == -1:
            size = self.fsize - self.pos
            
        # Compute the first page from which this data might be read
        p0 = self.pos // self.page_size
        
        # Compute the last page from which the data might be read
        p1 = (self.pos + size - 1) // self.page_size
        
        # Read pages from the cache (TODO: set a limit on how many pages)
        data = bytearray(size)
        d_pos = 0
        for p in range(p0,p1+1):
            
            # Get the page from cache or GCS
            key = (self.gs_url, p)
            d_cached = self.cache.get(key)
            if d_cached is None:
                p_start = p0*self.page_size
                p_end = min(p_start + self.page_size, self.fsize)
                d_cached = self._blob.download_as_bytes(start=p_start, end=p_end-1, checksum=None)
                self.cache.set(key, d_cached)
            else:
                print(f'Read page {p} from cache {self.gs_url}')
            
            # Where does the chunk we want from this page start?
            ch_start = self.pos - p0 * self.page_size if p == p0 else 0
            
            # How long is the chunk we want from this page
            ch_len = self.pos + size - p1 * self.page_size if p == p1 else len(d_cached)
            
            # Copy the data to the chunk
            data[d_pos:(d_pos+ch_len)] = d_cached[ch_start:(ch_start+ch_len)]

            # Advance the current position
            d_pos +=ch_len
        
        # Update the current file position
        self.pos += len(data)
        self.total_served += len(data)
        return data
    
    def readinto(self, buffer):
        if isinstance(buffer, np.ndarray):
            buffer = memoryview(buffer)
        if not isinstance(buffer, (bytearray, memoryview)):
            raise TypeError("Buffer object expected, got: {}".format(type(buffer)))
        data = self.read(len(buffer))
        print(f'readinto read {len(data)} bytes')
        buffer[:len(data)] = data
        return len(data)

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
'''
    
    
# OpenSlide wrapper for GCS
class GoogleCloudOpenSlideWrapper:
    
    def __init__(self, client, gs_url, cache=None):
        # Load the tiff file
        self.h = GoogleCloudTiffHandle(client, gs_url, cache)
        self.tf = tifffile.TiffFile(self.h)
        
        # Collect the tiled pages
        self.tiled_pages = [ p for p in self.tf.pages if p.is_tiled ]
        
    def read_region(self, pos, level, size):
        print(f'Read region {pos}, {level}, {size}')
        page = self.tiled_pages[level]
        
        # The pos coordinates are in the coordinate frame of level 0 and
        # have to be converted to the correct coordinate frame
        ds = self.level_downsamples[level]
        pos = [ int(0.5 + x // ds) for x in pos ]

        if not page.is_tiled:
            print(f'Page {level} is not tiled, size {page.imagewidth, page.imagelength}')
            arr = page.asarray()
            icrop = arr[pos[1]:(pos[1]+size[1]),pos[0]:(pos[0]+size[0]),:]
            print('icrop', icrop.shape)

        else:
            fh = self.tf.filehandle
            tw, th = page.tilewidth, page.tilelength
            
            # How many tiles in x and y
            ntx = 1 + (page.imagewidth-1) // page.tilewidth
            nty = 1 + (page.imagelength-1) // page.tilelength
            
            tx0 = pos[0] // tw
            ty0 = pos[1] // th
            tx1 = (pos[0] + size[0]) // tw
            ty1 = (pos[1] + size[1]) // th
            stx, sty = 1+tx1-tx0, 1+ty1-ty0
            
            print(tw, th, ntx, nty, tx0, tx1, ty0, ty1, stx, sty)

                    
            image = np.zeros((sty * th, stx * tw, page.samplesperpixel), dtype=page.dtype)
            print(f'Reading tiles {tx0}:{tx1+1} in x, {ty0}:{ty1+1} in y')
            for ty in range(ty0, ty1 + 1):
                for tx in range(tx0, tx1 + 1):
                    i_tile = ty * ntx + tx
                    fh.seek(page.dataoffsets[i_tile])
                    data = fh.read(page.databytecounts[i_tile])
                    tile, indices, shape = page.decode(data, i_tile, jpegtables=page.jpegtables)
                    offx, offy = (tx - tx0) * tw, (ty - ty0) * th
                    image[offy:(offy+th),offx:(offx+th),:] = np.array(tile, dtype=page.dtype)
                    
            # Crop out the exact image requested
            crop_x, crop_y = pos[0] - tx0 * tw, pos[1] - ty0 * th
            icrop = image[crop_y:(crop_y+size[1]),crop_x:(crop_x+size[0]),:]
        
        # Return the image as PIL
        return Image.fromarray(icrop).convert("RGBA")
    
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
    def properties(self):
        ppm_x, ppm_y = self.tiled_pages[0].get_resolution(unit=tifffile.RESUNIT.MICROMETER)
        return dict({
            'openslide.mpp-x': 1.0 / ppm_x,
            'openslide.mpp-y': 1.0 / ppm_y            
        })
    
    def get_best_level_for_downsample(self, downsample):
        ds = self.level_downsamples
        for (i, ds_i) in enumerate(ds):
            if downsample < ds_i:
                return max(i-1, 0)
        return len(ds)-1        

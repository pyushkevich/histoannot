import openslide
from openslide import OpenSlide, OpenSlideError
from openslide.deepzoom import DeepZoomGenerator
from threading import Lock
from collections import OrderedDict
from PIL import Image

import mytest as m

class AffineTransformedOpenSlide(object):
    def __init__(self, c_tile_cache, slide_path):
        # TODO: get this number
        self._osr = m.init_osr(c_tile_cache, slide_path, (0,0))
        n_levels = m.get_nlevels(self._osr)
        self.level_downsamples = ()
        self.level_dimensions = ()
        for i in range(n_levels):
            ds = m.get_downsample_level(self._osr, i); 
            self.level_downsamples += (ds, )
            dims = m.get_level_dimensions(self._osr, i);
            self.level_dimensions += (dims, )

        print("LEVEL DIMS")
        print(self.level_downsamples)
        print(self.level_dimensions)

        # TODO:
        self.properties = {}

    def get_best_level_for_downsample(self, d):
        return m.get_best_level_for_downsample(self._osr, d)

    def read_region(self, location, level, size):
        b=bytearray(4 * size[0] * size[1]);
        # Amat = ((1.0, 0.1, 2000.0), (-0.1, 0.9, 0.0), (0.0, 0.0, 1.0))
        Amat = ((1.0, 0.1, 0.0), (-0.1, 1.0, 0.0), (0.0, 0.0, 1.0))
        m.read_region(self._osr, location, level, size, Amat, b)
        # img=Image.frombuffer('RGBA',size,str(b),'raw','RGBA',0,1)
        img=Image.frombuffer('RGBA',size,str(b),'raw','BGRA',0,1)
        return img

        

class DeepZoomSource(object):
    def __init__(self, max_tiles, max_dzi, dz_opts):
        self.max_dzi = max_dzi
        self.dz_opts = dz_opts
        self._lock = Lock()
        self._cache = OrderedDict()
        self.c_tile_cache = m.init_cache(max_tiles)

    def get(self, path):
        with self._lock:
            if path in self._cache:
                # Move to end of LRU
                slide = self._cache.pop(path)
                self._cache[path] = slide
                return slide

        osr = AffineTransformedOpenSlide(self.c_tile_cache, path)
        slide = DeepZoomGenerator(osr)
        try:
            mpp_x = osr.properties[openslide.PROPERTY_NAME_MPP_X]
            mpp_y = osr.properties[openslide.PROPERTY_NAME_MPP_Y]
            slide.mpp = (float(mpp_x) + float(mpp_y)) / 2
        except (KeyError, ValueError):
            slide.mpp = 8

        with self._lock:
            if path not in self._cache:
                if len(self._cache) == self.max_dzi:
                    self._cache.popitem(last=False)
                self._cache[path] = slide
        return slide


### class SlideCache(object):
###     def __init__(self, cache_size, dz_opts):
###         self.cache_size = cache_size
###         self.dz_opts = dz_opts
###         self._lock = Lock()
###         self._cache = OrderedDict()
### 
###     def get(self, path):
###         with self._lock:
###             if path in self._cache:
###                 # Move to end of LRU
###                 slide = self._cache.pop(path)
###                 self._cache[path] = slide
###                 return slide
### 
###         osr = OpenSlide(path)
###         # slide = DeepZoomGenerator(osr, **self.dz_opts)
###         slide = DeepZoomGenerator(osr)
###         try:
###             mpp_x = osr.properties[openslide.PROPERTY_NAME_MPP_X]
###             mpp_y = osr.properties[openslide.PROPERTY_NAME_MPP_Y]
###             slide.mpp = (float(mpp_x) + float(mpp_y)) / 2
###         except (KeyError, ValueError):
###             slide.mpp = 8
### 
###         with self._lock:
###             if path not in self._cache:
###                 if len(self._cache) == self.cache_size:
###                     self._cache.popitem(last=False)
###                 self._cache[path] = slide
###         return slide

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
import openslide
from openslide import OpenSlide, OpenSlideError
from openslide.deepzoom import DeepZoomGenerator
from threading import Lock
from collections import OrderedDict
from PIL import Image

import mytest as m

class AffineTransformedOpenSlide(object):
    def __init__(self, c_tile_cache, slide_path, affine_path):

        # Read the affine matrix into a tuple
        self._affine = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
        if affine_path is not None:
            with open(affine_path) as f:
                self._affine = tuple(tuple(map(float, i.split(' '))) for i in f)

        # TODO: get this number
        self._osr = m.init_osr(c_tile_cache, slide_path, (0,0))
        n_levels = m.get_nlevels(self._osr)

        # If n_levels is 1 throw up
        if(n_levels == 1):
            raise ValueError('Image is not a pyramid')

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
        m.read_region(self._osr, location, level, size, self._affine, b)
        img=Image.frombuffer('RGBA',size,str(b),'raw','BGRA',0,1)
        return img

class DeepZoomSource(object):
    def __init__(self, max_tiles, max_dzi, dz_opts):
        self.max_dzi = max_dzi
        self.dz_opts = dz_opts
        self._lock = Lock()
        self._cache = OrderedDict()
        self.c_tile_cache = m.init_cache(max_tiles)

    def get(self, path, affine_file):
        # Combine path and affine into a single hash
        hashstr=path
        if affine_file is not None:
            hashstr=hashstr + ':' + affine_file

        print('get', path, affine_file)

        with self._lock:
            if hashstr in self._cache:
                # Move to end of LRU
                slide = self._cache.pop(hashstr)
                self._cache[hashstr] = slide
                return slide

        osr = AffineTransformedOpenSlide(self.c_tile_cache, path, affine_file)
        slide = DeepZoomGenerator(osr)
        try:
            mpp_x = osr.properties[openslide.PROPERTY_NAME_MPP_X]
            mpp_y = osr.properties[openslide.PROPERTY_NAME_MPP_Y]
            slide.mpp = (float(mpp_x) + float(mpp_y)) / 2
        except (KeyError, ValueError):
            slide.mpp = 8

        with self._lock:
            if hashstr not in self._cache:
                if len(self._cache) == self.max_dzi:
                    self._cache.popitem(last=False)
                self._cache[hashstr] = slide
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

/*
    PICSL Histology Annotator
    Copyright (C) 2019 Paul A. Yushkevich
    
    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.
    
    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.
    
    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <https://www.gnu.org/licenses/>.
*/
#ifndef __os_affine_api_
#define __os_affine_api_

#include <openslide.h>
#include <itkVectorImage.h>
#include <itkImage.h>
#include <itkImageFileWriter.h>
#include <vnl/vnl_matrix_fixed.h>
#include "FastLinearInterpolator.h"

const long TILESIZE=512;
const long OVERHANG=2;

class OpenSlideWrapper;
 
class TileCache
{
public:
  typedef itk::VectorImage<unsigned char, 2> ImageType;
  typedef FastLinearInterpolator<ImageType, float, 2> InterpType;

  struct TileRef {
    OpenSlideWrapper *slide;
    int level, ti_x, ti_y;
    TileRef(OpenSlideWrapper *s = NULL, int l = 0, int tx = 0, int ty = 0) 
      : slide(s), level(l), ti_x(tx), ti_y(ty) {}
  };

  TileCache(unsigned int max_tiles) 
    {
    this->max_tiles = max_tiles;
    this->counter = 0l;
    }

protected:
  std::vector<TileRef> tiles;
  unsigned long counter;
  unsigned long max_tiles;

  friend class OpenSlideWrapper;
};

// Global tile cache
class OpenSlideWrapper
{
public:
  typedef vnl_matrix_fixed<double, 3,3> Mat;
  typedef vnl_vector_fixed<double, 3> Vec;

  // Constructor
  OpenSlideWrapper(TileCache *tile_cache, const char *path, unsigned long canvas_x, unsigned long canvas_y)
    {
    // Store the cache
    this->tile_cache = tile_cache;

    // Open the slide
    osr = openslide_open(path);

    // Save the canvas dimensions
    canvas[0] = canvas_x;
    canvas[1] = canvas_y;

    // If canvas dimensions are zero use the current slide itself
    if(canvas[0] == 0 || canvas[1] == 0)
      {
      int64_t cw, ch;
      openslide_get_level0_dimensions(osr, &cw, &ch);
      canvas[0] = cw; canvas[1] = ch;
      }

    // Get the dimensions info
    unsigned int n_lev = openslide_get_level_count(osr);
    dim_x.resize(n_lev);
    dim_y.resize(n_lev);
    tile_array.resize(n_lev);
    for(unsigned int i = 0; i < dim_x.size(); i++)
      {
      openslide_get_level_dimensions(osr, i, &dim_x[i], &dim_y[i]);
      
      // how many tiles?
      unsigned int nx = ceil(dim_x[i] * 1.0 / TILESIZE);
      unsigned int ny = ceil(dim_y[i] * 1.0 / TILESIZE);
      tile_array[i].resize(nx, std::vector<Tile>(ny));
      }
    }



  int GetNumberOfLevels() const 
    {
    return dim_x.size();
    }

  double GetLevelDownsample(int level) const
    {
    return openslide_get_level_downsample(osr, level);
    }

  void GetLevelDimensions(int level, long *w, long *h) const
    {
    int64_t ww, hh;
    openslide_get_level_dimensions(osr, level, &ww, &hh);
    *w = ww; *h = hh;
    }

  int GetBestLevelForDownsample(double ds) const
    {
    return openslide_get_best_level_for_downsample(osr, ds);
    }

  // Locate a tile corresponding to a pixel coordinate in target space
  // and return data associated with that index
  void FindTile(int level, double ds, double ts, double x, double y, const Mat &A,
    TileCache::InterpType **interp, long *tx, long *ty, float *cix, int *nskip)
    {
    double t_start_method = 0.0;

    // Apply affine transform to the coordinates
    double Sx = A(0,0) * x + A(0,1) * y + A(0, 2);
    double Sy = A(1,0) * x + A(1,1) * y + A(1, 2);

    // Determine which tile we need at this level
    double ti_x = floor(Sx / ts), ti_y = floor(Sy / ts);

    // Get the starting point of the tile. Note the additional offset
    // which accounts for a single-voxel padding factor
    *tx = (long) floor(ti_x * ts - OVERHANG * ds);
    *ty = (long) floor(ti_y * ts - OVERHANG * ds);

    // Compute the corresponding sampling index into the tile
    cix[0] = (Sx - *tx) / ds;
    cix[1] = (Sy - *ty) / ds;

    // Are we inside the image?
    if(ti_x < 0 || ti_y < 0 
      || ti_x >= tile_array[level].size()
      || ti_y >= tile_array[level][0].size())
      {
      // Interp will be set to NULL. There is nothing to interpolate
      *interp = NULL;

      // We also need to calculate how many voxels to skip until we hit the next
      // tile as we walk along this line. Since cix is in tile units, we just need
      // to walk until we run off the tile. Let's be lazy and walk
      double wx = cix[0], wy = cix[1];
      *nskip = 0;
      while(wx >= 0 && wy >= 0 && wx < TILESIZE && wy <= TILESIZE)
        {
        (*nskip)++; wx += A(0,0); wy += A(1,0);
        }
      if(*nskip == 0)
        *nskip = 1;

      return;
      }
      

    // Does the tile exist?
    Tile &tile_info = tile_array[level][ti_x][ti_y];
    if(tile_info.interp)
      {
      tile_info.TimeStamp = ++tile_cache->counter;
      *interp = tile_info.interp;
      }
    else
      {
      // Tile has to be loaded. First make sure that the cache is not full
      while(tile_cache->tiles.size() >= tile_cache->max_tiles)
        {
        // Linear search through all the cached tiles to find the oldest
        unsigned long oldest_counter = tile_cache->counter;
        unsigned int oldest_index = 0;
        TileCache::TileRef oldest_tr;
        Tile *oldest_tile = NULL;
        for(unsigned int i = 0; i < tile_cache->tiles.size(); i++)
          {
          TileCache::TileRef &tr = tile_cache->tiles[i];
          Tile *tile = &tr.slide->tile_array[tr.level][tr.ti_x][tr.ti_y];
          if(tile->TimeStamp < oldest_counter)
            {
            oldest_counter = tile->TimeStamp;
            oldest_index = i;
            oldest_tile = tile;
            oldest_tr = tr;
            }
          }

        // Reset the tile
        delete(oldest_tile->interp);
        *oldest_tile = Tile();

        // Remove from list
        tile_cache->tiles.erase(tile_cache->tiles.begin() + oldest_index);
        printf("Erasing tile %d (%d %d)\n", oldest_tr.level, oldest_tr.ti_x, oldest_tr.ti_y);
        }

      // Now that there is room for a new tile, we can load it
      // Load the needed tile (plus a little padding)
      long actual_size = TILESIZE + 2 * OVERHANG;
      tile_info.image = TileCache::ImageType::New();
      TileCache::ImageType::RegionType rgn; rgn.SetSize(0, actual_size); rgn.SetSize(1, actual_size);
      tile_info.image->SetRegions(rgn);
      tile_info.image->SetNumberOfComponentsPerPixel(4);
      tile_info.image->Allocate();

      unsigned char *q = tile_info.image->GetBufferPointer();
      double t_start_openslide = clock();
      openslide_read_region(osr, (uint32_t *) q, *tx, *ty, level, actual_size, actual_size);
      t_oslide += clock() - t_start_openslide;
      printf("Loading tile %d (%ld %ld) (%ld %ld)\n", level, *tx, *ty, actual_size, actual_size);

      // Update the counter
      tile_info.TimeStamp = ++tile_cache->counter;
      tile_info.interp = new TileCache::InterpType(tile_info.image);

      // Keep track in the cache
      tile_cache->tiles.push_back(TileCache::TileRef(this, level, ti_x, ti_y));

      // Done
      *interp = tile_info.interp;

      t_findtile += clock() - t_start_method;
      }
    }

  double t_oslide, t_findtile;

  // Read a region of canvas given an affine transform
  void ReadRegion(int level, long x, long y, long w, long h, const Mat &A, unsigned char *out_data)
    {
    // Keep track of some timing 
    double t_start = clock();
    t_oslide = 0.0;
    t_findtile = 0.0;

    // Keep track of number of calls to FindTile
    int n_find_calls = 0;

    // Get the downsample
    double ds = openslide_get_level_downsample(osr, level);
    TileCache::InterpType *interp;
    float cix[2];
    long tx, ty;

    // Output array of floats
    float rgba[4];

    // Get the tile size in level-0 pixels
    unsigned int ts = ds * TILESIZE;

    // Allocate output image
    unsigned char *p = out_data; 

    // Number to skip
    int nskip = 0;

    // Iterate over the pixels in the target region.
    for(long py = 0; py < h; py++)
      {
      // At the start of the line we probably need a new tile
      FindTile(level, ds, ts, x, y + ds * py, A, &interp, &tx, &ty, cix, &nskip);
      ++n_find_calls;

      // Compute the current sampling coordinate
      for(long px = 0; px < w; px++)
        {
        if(interp == NULL && nskip > 0)
          {
          nskip--;
          rgba[0] = rgba[1] = rgba[2] = rgba[3] = 0.0f;
          }

        // Sample the image at the current position cix
        else if((interp == NULL && nskip == 0) ||
          (interp->Interpolate(cix, rgba) == TileCache::InterpType::OUTSIDE))
          {
          // Update the tile
          FindTile(level, ds, ts, x + ds * px, y + ds * py, A, &interp, &tx, &ty, cix, &nskip);
          ++n_find_calls;

          // Now the interpolation may not fail
          if(interp && interp->Interpolate(cix, rgba) == TileCache::InterpType::OUTSIDE)
            {
            printf("Unable to place vertex (%ld,%ld) at level %d in a tile (%ld,%ld)\n", x + px, y + py, level, tx, ty);
            printf("CIX = (%f,%f)\n", cix[0], cix[1]);
            throw std::exception();
            }
          }

        // Update the sampling position
        cix[0] += A(0,0); cix[1] += A(1,0);

        // Copy to bytes
        for(unsigned int j = 0; j < 4; j++)
          *p++ = (unsigned char) rgba[j];
        }
      }

    double t_total = (clock() - t_start) / CLOCKS_PER_SEC;
    printf("TIME ELAPSED %f IN OPENSLIDE %f  FindTile Calls %d Time %f \n", t_total, t_oslide / CLOCKS_PER_SEC, n_find_calls, t_findtile / CLOCKS_PER_SEC);

    }




protected:
  // Pointer to the loaded openslide object
  openslide_t *osr;

  // Dimensions
  std::vector<int64_t> dim_x, dim_y;

  // Reference to a tile
  struct Tile 
    {
    unsigned long TimeStamp;
    TileCache::ImageType::Pointer image;
    TileCache::InterpType *interp;
    Tile() : TimeStamp(0l), interp(NULL) {}
    };

  // 2D array of tiles
  typedef std::vector<std::vector<std::vector<Tile> > > TileArray;
  TileArray tile_array;

  // Collection of cached tiles
  TileCache *tile_cache;

  // Desired output canvas dimensions
  unsigned long canvas[2];
};


#endif

#ifndef __mytest_api_
#define __mytest_api_

#include <openslide.h>
#include <itkVectorImage.h>
#include <itkImage.h>
#include <itkImageFileWriter.h>
#include <vnl/vnl_matrix_fixed.h>
#include "FastLinearInterpolator.h"

const long TILESIZE=512;
const long OVERHANG=2;
 
class TileCache
{
public:
  typedef itk::VectorImage<unsigned char, 2> ImageType;
  typedef FastLinearInterpolator<ImageType, float, 2> InterpType;

  struct TileKey {
    openslide_t *osr;
    int level; 
    long tx, ty;
    
    bool operator < (const TileKey &other) const {
      return 
        (osr < other.osr ||
         (osr == other.osr && level < other.level) ||
         (osr == other.osr && level == other.level && tx < other.tx) ||
         (osr == other.osr && level == other.level && tx == other.tx && ty < other.ty));
    }

  };

  struct CacheEntry {
    unsigned long access_time;
    ImageType::Pointer img;
    InterpType *interp;
    CacheEntry(unsigned long at, ImageType::Pointer p) :
      access_time(at), img(p) { interp = new InterpType(img);}
  };

  typedef std::map<TileKey, CacheEntry> Cache;


  TileCache(unsigned int max_tiles) 
    {
    this->max_tiles = max_tiles;
    }

  InterpType *get_tile(openslide_t *osr, int level, long tx, long ty)
    {
    TileKey tk; tk.osr = osr; tk.level = level; tk.tx = tx; tk.ty = ty;
    Cache::iterator it = cache.find(tk);
    if(it != cache.end())
      {
      // Mark this tile as the latest used
      it->second.access_time = ++counter;

      // Return the tile
      return it->second.interp;
      }
    else
      {
      // If the cache is full, find the oldest entry
      while(cache.size() >= max_tiles)
        {
        unsigned long oldest_time = counter;
        TileKey oldest_key = cache.begin()->first;
        for(Cache::iterator it = cache.begin(); it != cache.end(); ++it)
          {
          if(it->second.access_time < oldest_time)
            {
            oldest_time = it->second.access_time;
            oldest_key = it->first;
            }
          }
        cache.erase(oldest_key);
        }

      // Load the needed tile (plus a little padding)
      long actual_size = TILESIZE + 2 * OVERHANG;
      ImageType::Pointer img = ImageType::New();
      ImageType::RegionType rgn; rgn.SetSize(0, actual_size); rgn.SetSize(1, actual_size);
      img->SetRegions(rgn);
      img->SetNumberOfComponentsPerPixel(4);
      img->Allocate();

      unsigned char *q = img->GetBufferPointer();
      openslide_read_region(osr, (uint32_t *) q, tx, ty, level, actual_size, actual_size);
      printf("Loading tile %d (%ld %ld) (%ld %ld)\n", level, tx, ty, actual_size, actual_size);

      /*
      char fn[1024];
      sprintf(fn, "/tmp/tile_%02d_%06ld_%06ld.nii.gz", level, tx, ty);
      typedef itk::ImageFileWriter<ImageType> WriterType;
      WriterType::Pointer writer = WriterType::New();
      writer->SetInput(img);
      writer->SetFileName(fn);
      writer->Update();
      */

      // Put the image in cache
      CacheEntry new_entry(++counter, img);
      cache.insert(std::make_pair(tk, new_entry));

      // Return the image pointer
      return new_entry.interp;
      }
    }

protected:

  Cache cache;
  unsigned int max_tiles;
  unsigned long counter;
  

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
    dim_x.resize(openslide_get_level_count(osr));
    dim_y.resize(openslide_get_level_count(osr));
    for(unsigned int i = 0; i < dim_x.size(); i++)
      {
      openslide_get_level_dimensions(osr, i, &dim_x[i], &dim_y[i]);
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
    TileCache::InterpType **interp, long *tx, long *ty, float *cix)
    {
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

    // Read the tile and return it
    *interp = tile_cache->get_tile(osr, level, *tx, *ty);
    }

  // Read a region of canvas given an affine transform
  void ReadRegion(int level, long x, long y, long w, long h, const Mat &A, unsigned char *out_data)
    {
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

    // Iterate over the pixels in the target region.
    for(long py = 0; py < h; py++)
      {
      // At the start of the line we probably need a new tile
      FindTile(level, ds, ts, x, y + ds * py, A, &interp, &tx, &ty, cix);

      // Compute the current sampling coordinate
      for(long px = 0; px < w; px++)
        {
        // Sample the image at the current position cix
        if(interp->Interpolate(cix, rgba) != TileCache::InterpType::INSIDE)
          {
          // Update the tile
          FindTile(level, ds, ts, x + ds * px, y + ds * py, A, &interp, &tx, &ty, cix);

          // Now the interpolation may not fail
          if(interp->Interpolate(cix, rgba) == TileCache::InterpType::OUTSIDE)
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

    }




protected:
  // Pointer to the loaded openslide object
  openslide_t *osr;

  // Dimensions
  std::vector<int64_t> dim_x, dim_y;

  // Collection of cached tiles
  TileCache *tile_cache;

  // Desired output canvas dimensions
  unsigned long canvas[2];
};


#endif

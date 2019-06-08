#ifndef __mytest_api_
#define __mytest_api_

#include <openslide.h>
#include <itkVectorImage.h>
#include <itkImage.h>
#include <itkImageFileWriter.h>
#include <vnl/vnl_matrix_fixed.h>
#include "FastLinearInterpolator.h"

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

      // Load the needed tile
      ImageType::Pointer img = ImageType::New();
      ImageType::RegionType rgn; rgn.SetSize(0, 256); rgn.SetSize(1, 256);
      img->SetRegions(rgn);
      img->SetNumberOfComponentsPerPixel(4);
      img->Allocate();

      unsigned char *q = img->GetBufferPointer();
      openslide_read_region(osr, (uint32_t *) q, tx, ty, level, 256, 256);
      printf("Loading tile %d (%ld %ld) (%d %d)\n", level, tx, ty, 256, 256);

      char fn[1024];
      sprintf(fn, "/tmp/tile_%02d_%06ld_%06ld.nii.gz", level, tx, ty);
      typedef itk::ImageFileWriter<ImageType> WriterType;
      WriterType::Pointer writer = WriterType::New();
      writer->SetInput(img);
      writer->SetFileName(fn);
      writer->Update();

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

  // Read a region of canvas given an affine transform
  void ReadRegion(int level, long x, long y, long w, long h, const Mat &A, unsigned char *out_data)
    {
    // Get the downsample
    double ds = openslide_get_level_downsample(osr, level);
    float cix[2];

    // Output array of floats
    float rgba[4];

    // Get the tile size in level-0 pixels
    unsigned int ts = ds * 256;

    Vec vz; vz[2] = 1.0; 
    Vec va;

    // Allocate output image
    unsigned char *p = out_data; 

    // Iterate over the pixels in the target region.
    for(long py = 0; py < h; py++)
      {
      for(long px = 0; px < w; px++)
        {
        // Get the level-0 coordinate of this pixel
        vz[0] = x + px * ds;
        vz[1] = y + py * ds;

        // Apply the affine transform 
        va = A * vz;

        // Determine which tile we need at this level
        long tx = (int) (va[0] / ts);
        long ty = (int) (va[1] / ts);

        // Get the offset into the current tile
        cix[0] = (va[0] - tx * ts) / ds;
        cix[1] = (va[1] - ty * ts) / ds;

        // Read the tile
        TileCache::InterpType *interp = tile_cache->get_tile(osr, level, (long) tx * ts, (long) ty * ts);

        // Get that tile from the tile cache
        interp->Interpolate(cix, rgba);

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

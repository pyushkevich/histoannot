#include "mytestapi.h"


void *make_tile_cache(unsigned int max_tiles) 
{
  TileCache *cache = new TileCache(max_tiles);
  return cache;
}

void *load_openslide(void *tile_cache, const char *path, long canvas_x, long canvas_y)
{
  OpenSlideWrapper *osw = new OpenSlideWrapper((TileCache *)tile_cache, path, canvas_x, canvas_y);
  return osw;
}

int get_openslide_levels(void *osw) 
{
  return static_cast<OpenSlideWrapper *>(osw)->GetNumberOfLevels();
}

double get_openslide_level_downsample(void *osw, int level) 
{
  return static_cast<OpenSlideWrapper *>(osw)->GetLevelDownsample(level);
}

void get_openslide_level_dimensions(void *osw, int level, long *w, long *h) 
{
  static_cast<OpenSlideWrapper *>(osw)->GetLevelDimensions(level, w, h);
}

int get_openslide_best_level_for_downsample(void *osw, double downsample) 
{
  return static_cast<OpenSlideWrapper *>(osw)->GetBestLevelForDownsample(downsample);
}

void *load_region(void *osr, int level, long x, long y, long w, long h, double A[3][3], char *data)
{
  vnl_matrix_fixed<double, 3, 3> Amat;
  for(unsigned int a = 0; a < 3; a++)
    for(unsigned int b = 0; b < 3; b++)
      Amat(a,b) = A[a][b];

  static_cast<OpenSlideWrapper *>(osr)->ReadRegion(level, x, y, w, h, Amat, (unsigned char *) data);
  return NULL;
}


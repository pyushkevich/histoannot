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
#include "os_affine_api.h"


void *load_openslide(const char *path, int canvas_x, int canvas_y)
{
  OpenSlideWrapper *osw = new OpenSlideWrapper(path, canvas_x, canvas_y);
  return osw;
}

void release_openslide(void *osw)
{
  delete (OpenSlideWrapper *) osw;
}

int get_openslide_levels(void *osw) 
{
  return static_cast<OpenSlideWrapper *>(osw)->GetNumberOfLevels();
}

double get_openslide_level_downsample(void *osw, int level) 
{
  return static_cast<OpenSlideWrapper *>(osw)->GetLevelDownsample(level);
}

void get_openslide_level_dimensions(void *osw, int level, int *w, int *h) 
{
  static_cast<OpenSlideWrapper *>(osw)->GetLevelDimensions(level, w, h);
}

int get_openslide_best_level_for_downsample(void *osw, double downsample) 
{
  return static_cast<OpenSlideWrapper *>(osw)->GetBestLevelForDownsample(downsample);
}

void *load_region(void *osr, int level, int x, int y, int w, int h, double A[3][3], char *data)
{
  vnl_matrix_fixed<double, 3, 3> Amat;
  for(unsigned int a = 0; a < 3; a++)
    for(unsigned int b = 0; b < 3; b++)
      Amat(a,b) = A[a][b];

  static_cast<OpenSlideWrapper *>(osr)->ReadRegion(level, x, y, w, h, Amat, (unsigned char *) data);
  return NULL;
}


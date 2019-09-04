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

 
class OpenSlideWrapper
{
public:
  typedef vnl_matrix_fixed<double, 3,3> Mat;
  typedef vnl_vector_fixed<double, 3> Vec;

  // Constructor
  OpenSlideWrapper(const char *path, unsigned int canvas_x, unsigned int canvas_y)
    {
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
    }

  ~OpenSlideWrapper()
    {
    openslide_close(osr);
    }

  int GetNumberOfLevels() const 
    {
    return dim_x.size();
    }

  double GetLevelDownsample(int level) const
    {
    return openslide_get_level_downsample(osr, level);
    }

  void GetLevelDimensions(int level, int *w, int *h) const
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
  void ReadRegion(int level, int x, int y, int w, int h, const Mat &A, unsigned char *out_data)
    {
    // Keep track of some timing 
    double t_start = clock();

    // ITK stuff
    typedef itk::VectorImage<unsigned char, 2> ImageType;
    typedef FastLinearInterpolator<ImageType, float, 2> InterpType;

    // Get the downsample
    double ds = openslide_get_level_downsample(osr, level);
    float cix[2];
    int tx, ty;

    // Output array of floats
    float rgba[4];

    // Get the corners of the request in level 0 space
    double rx0 = x, ry0 = y;
    double rx1 = x + w * ds, ry1 = y + h * ds;

    // Get the source coordinates of the four corners of the tile in level 0 space
    double x_cor[4], y_cor[4];
    x_cor[0] = A(0,0) * rx0 + A(0,1) * ry0 + A(0,2);
    x_cor[1] = A(0,0) * rx1 + A(0,1) * ry0 + A(0,2);
    x_cor[2] = A(0,0) * rx1 + A(0,1) * ry1 + A(0,2);
    x_cor[3] = A(0,0) * rx0 + A(0,1) * ry1 + A(0,2);
    y_cor[0] = A(1,0) * rx0 + A(1,1) * ry0 + A(1,2);
    y_cor[1] = A(1,0) * rx1 + A(1,1) * ry0 + A(1,2);
    y_cor[2] = A(1,0) * rx1 + A(1,1) * ry1 + A(1,2);
    y_cor[3] = A(1,0) * rx0 + A(1,1) * ry1 + A(1,2);

    double x_cor_min = x_cor[0], x_cor_max = x_cor[0], y_cor_min = y_cor[0], y_cor_max = y_cor[0];
    for(int i = 1; i < 4; i++)
      {
      x_cor_min = std::min(x_cor_min, x_cor[i]);
      x_cor_max = std::max(x_cor_max, x_cor[i]);
      y_cor_min = std::min(y_cor_min, y_cor[i]);
      y_cor_max = std::max(y_cor_max, y_cor[i]);
      }

    // Corner of the region to be read from OS (in level 0 space)
    int sx = (int)(x_cor_min - ds);
    int sy = (int)(y_cor_min - ds);

    // Dimensions to be read (in level K pixels)
    int sw = ceil((x_cor_max - x_cor_min) / ds) + 2;
    int sh = ceil((y_cor_max - y_cor_min) / ds) + 2;

    // Allocate and read the source image
    ImageType::Pointer iSrc = ImageType::New();
    ImageType::RegionType rgnSrc;
    rgnSrc.SetSize(0, sw); rgnSrc.SetSize(1, sh);
    iSrc->SetRegions(rgnSrc);
    iSrc->SetNumberOfComponentsPerPixel(4);
    iSrc->Allocate();

    unsigned char *q = iSrc->GetBufferPointer();
    double t_start_openslide = clock();
    openslide_read_region(osr, (uint32_t *) q, sx, sy, level, sw, sh);
    double t_oslide = (clock() - t_start_openslide) / CLOCKS_PER_SEC;

    // Allocate output image
    unsigned char *p = out_data; 

    // Interpolator
    InterpType interp(iSrc);

    // Step in source space corresponding to step (1,0) in target space
    double dx = A(0,0), dy = A(1,0);

    // Iterate over the pixels in the target region.
    for(int py = 0; py < h; py++)
      {
      // Initialize the index
      cix[0] = (A(0,0) * x + A(0,1) * (y+py*ds) + A(0,2) - sx) / ds;
      cix[1] = (A(1,0) * x + A(1,1) * (y+py*ds) + A(1,2) - sy) / ds;

      // Compute the current sampling coordinate
      for(int px = 0; px < w; px++)
        {
        interp.Interpolate(cix, rgba);
        /*
        if(interp.Interpolate(cix, rgba) != InterpType::INSIDE)
          {
          printf("Unable to sample at (%d,%d) at level %d\n", x + px, y + py, level);
          printf("CIX = (%f,%f)\n", cix[0], cix[1]);
          throw std::exception();
          }
        */

        // Update the sampling position
        cix[0] += dx; cix[1] += dy;

        // Copy to bytes
        for(unsigned int j = 0; j < 4; j++)
          *p++ = (unsigned char) rgba[j];
        }
      }

    // Time statistics
    double t_total = (clock() - t_start) / CLOCKS_PER_SEC;
    printf("TIMING: Total = %06d ms,  OpenSlide = %06d ms\n",
        (int)(0.5 + t_total * 1000), 
        (int)(0.5 + t_oslide * 1000));
    }


protected:
  // Pointer to the loaded openslide object
  openslide_t *osr;

  // Dimensions
  std::vector<int64_t> dim_x, dim_y;

  // Desired output canvas dimensions
  unsigned int canvas[2];
};


#endif

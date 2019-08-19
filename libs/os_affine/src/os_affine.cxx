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
#include <Python.h>

// C++ API
extern void *make_tile_cache(unsigned int max_tiles);
extern void *load_openslide(void *tile_cache, const char *path, long canvas_x, long canvas_y);
extern int get_openslide_levels(void *osw);
extern void get_openslide_level_dimensions(void *osw, int level, long *w, long *h); 
extern void *load_region(void *osr, int level, long x, long y, long w, long h, double A[3][3], char *data);
extern double get_openslide_level_downsample(void *osw, int level);
extern int get_openslide_best_level_for_downsample(void *osw, double downsample);

static PyObject *
os_affine_init_cache(PyObject *self, PyObject *args)
{
    int max_tiles;
    if (!PyArg_ParseTuple(args, "i", &max_tiles))
        return NULL;

    void *cache = make_tile_cache(max_tiles);
    PyObject *capsule = PyCapsule_New((void *)cache, "os_affine.cache", NULL);
    return Py_BuildValue("O", capsule);
}

static PyObject *
os_affine_init_osr(PyObject *self, PyObject *args)
{
    const char *path;
    long canvas_x, canvas_y;

    PyObject *cache_capsule;
    if (!PyArg_ParseTuple(args, "Os(ll)", &cache_capsule, &path, &canvas_x, &canvas_y))
        return NULL;

    void *tile_cache = PyCapsule_GetPointer(cache_capsule, "os_affine.cache");
    void *osw = load_openslide(tile_cache, path, canvas_x, canvas_y);

    PyObject *capsule = PyCapsule_New(osw, "os_affine.osw", NULL);
    return Py_BuildValue("O", capsule);
}

static PyObject *
os_affine_get_nlevels(PyObject *self, PyObject *args)
{
  PyObject *capsule;
  if (!PyArg_ParseTuple(args, "O", &capsule))
      return NULL;

  void *osw = PyCapsule_GetPointer(capsule, "os_affine.osw");
  return Py_BuildValue("i", get_openslide_levels(osw));
}

static PyObject *
os_affine_get_downsample_level(PyObject *self, PyObject *args)
{
  PyObject *capsule;
  int level;
  if (!PyArg_ParseTuple(args, "Oi", &capsule, &level))
      return NULL;

  void *osw = PyCapsule_GetPointer(capsule, "os_affine.osw");
  return Py_BuildValue("d", get_openslide_level_downsample(osw, level));
}

static PyObject *
os_affine_get_level_dimensions(PyObject *self, PyObject *args)
{
  PyObject *capsule;
  int level;
  long w = 0l, h = 0l;
  if (!PyArg_ParseTuple(args, "Oi", &capsule, &level))
      return NULL;

  void *osw = PyCapsule_GetPointer(capsule, "os_affine.osw");
  get_openslide_level_dimensions(osw, level, &w, &h);
  return Py_BuildValue("(ll)", w, h);
}

static PyObject *
os_affine_get_best_level_for_downsample(PyObject *self, PyObject *args)
{
  PyObject *capsule;
  double downsample;
  if (!PyArg_ParseTuple(args, "Od", &capsule, &downsample))
      return NULL;

  void *osw = PyCapsule_GetPointer(capsule, "os_affine.osw");
  return Py_BuildValue("i", get_openslide_best_level_for_downsample(osw, downsample));
}

static PyObject *
os_affine_read_region(PyObject *self, PyObject *args)
{
  PyObject *capsule;
  PyObject *byte_array;
  long loc_x, loc_y, sz_x, sz_y;
  int level;
  double A[3][3];
  if (!PyArg_ParseTuple(args, "O(ll)i(ll)((ddd)(ddd)(ddd))O", 
      &capsule, &loc_x, &loc_y, &level, &sz_x, &sz_y, 
      &A[0][0], &A[0][1], &A[0][2],
      &A[1][0], &A[1][1], &A[1][2],
      &A[2][0], &A[2][1], &A[2][2],
      &byte_array))
      return NULL;

  if (!PyByteArray_Check(byte_array))
      return NULL;

  long b_size = 4 * sz_x * sz_y;
  if (PyByteArray_Size(byte_array) < b_size)
      return NULL;

  void *osw = PyCapsule_GetPointer(capsule, "os_affine.osw");
  unsigned char *ba_bytes = (unsigned char *) PyByteArray_AsString(byte_array);

  printf("Reading from level %d location (%ld, %ld) a region of size (%ld, %ld)\n", 
    level, loc_x, loc_y, sz_x, sz_y);

  load_region(osw, level, loc_x, loc_y, sz_x, sz_y, A, (char *)ba_bytes);

  return Py_None;
}



// Methods table
static PyMethodDef MyMethods[] = {
    {"init_cache",  os_affine_init_cache, METH_VARARGS, "Initialize a tile cache"},
    {"init_osr",  os_affine_init_osr, METH_VARARGS, "Load an openslide object"},
    {"get_nlevels", os_affine_get_nlevels, METH_VARARGS, "Get number of levels available"},
    {"get_downsample_level", os_affine_get_downsample_level, METH_VARARGS, "Get downsample for a level"},
    {"get_level_dimensions", os_affine_get_level_dimensions, METH_VARARGS, "Get dimensions for a level"},
    {"get_best_level_for_downsample", os_affine_get_best_level_for_downsample, METH_VARARGS, "Get best level for downsample"},
    {"read_region", os_affine_read_region, METH_VARARGS, "Read a region"},
    {NULL, NULL, 0, NULL}        /* Sentinel */
};


PyMODINIT_FUNC
initos_affine(void)
{
    (void) Py_InitModule("os_affine", MyMethods);
}

int
main(int argc, char *argv[])
{
    /* Pass argv[0] to the Python interpreter */
    Py_SetProgramName(argv[0]);

    /* Initialize the Python interpreter.  Required. */
    Py_Initialize();

    /* Add a static module */
    initos_affine();
}

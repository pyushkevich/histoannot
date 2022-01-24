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
extern void *load_openslide(const char *path, int canvas_x, int canvas_y);
extern void release_openslide(void *osw);
extern int get_openslide_levels(void *osw);
extern void get_openslide_level_dimensions(void *osw, int level, int *w, int *h); 
extern void *load_region(void *osr, int level, int x, int y, int w, int h, double A[3][3], char *data);
extern double get_openslide_level_downsample(void *osw, int level);
extern int get_openslide_best_level_for_downsample(void *osw, double downsample);

static PyObject *
os_affine_init_osr(PyObject *self, PyObject *args)
{
    const char *path;
    int canvas_x, canvas_y;

    if (!PyArg_ParseTuple(args, "s(ii)", &path, &canvas_x, &canvas_y))
        return NULL;

    FILE *dbg = fopen("/tmp/dbg.txt", "at");
    fprintf(dbg, "os_affine_init_osr path=%s\n", path);
    void *osw = load_openslide(path, canvas_x, canvas_y);
    fprintf(dbg, "os_affine_init_osr osw=%lx\n", (long) osw);

    PyObject *capsule = PyCapsule_New(osw, "os_affine.osw", NULL);
    fprintf(dbg, "os_affine_init_osr capsule=%lx\n", (long) capsule);
    return Py_BuildValue("O", capsule);
}

static PyObject *
os_affine_release_osr(PyObject *self, PyObject *args)
{
  PyObject *capsule;
  if (!PyArg_ParseTuple(args, "O", &capsule))
      return NULL;

  FILE *dbg = fopen("/tmp/dbg.txt", "at");
  fprintf(dbg, "os_affine_release_osr capsule=%lx\n", (long) capsule);
  void *osw = PyCapsule_GetPointer(capsule, "os_affine.osw");
  fprintf(dbg, "os_affine_release_osr osw=%lx\n", (long) osw);
  if(osw)
      release_openslide(osw);

  return Py_None;
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
  int w = 0, h = 0;
  if (!PyArg_ParseTuple(args, "Oi", &capsule, &level))
      return NULL;

  void *osw = PyCapsule_GetPointer(capsule, "os_affine.osw");
  get_openslide_level_dimensions(osw, level, &w, &h);
  return Py_BuildValue("(ii)", w, h);
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
  int loc_x, loc_y, sz_x, sz_y;
  int level;
  double A[3][3];
  if (!PyArg_ParseTuple(args, "O(ii)i(ii)((ddd)(ddd)(ddd))O", 
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

  // printf("Reading from level %d location (%ld, %ld) a region of size (%ld, %ld)\n", 
  //  level, loc_x, loc_y, sz_x, sz_y);

  load_region(osw, level, loc_x, loc_y, sz_x, sz_y, A, (char *)ba_bytes);

  return Py_None;
}

// Methods table
static PyMethodDef MyMethods[] = {
    {"init_osr",  os_affine_init_osr, METH_VARARGS, "Load an openslide object"},
    {"release_osr",  os_affine_release_osr, METH_VARARGS, "Deallocate openslide object"},
    {"get_nlevels", os_affine_get_nlevels, METH_VARARGS, "Get number of levels available"},
    {"get_downsample_level", os_affine_get_downsample_level, METH_VARARGS, "Get downsample for a level"},
    {"get_level_dimensions", os_affine_get_level_dimensions, METH_VARARGS, "Get dimensions for a level"},
    {"get_best_level_for_downsample", os_affine_get_best_level_for_downsample, METH_VARARGS, "Get best level for downsample"},
    {"read_region", os_affine_read_region, METH_VARARGS, "Read a region"},
    {NULL, NULL, 0, NULL}        /* Sentinel */
};

#if PY_MAJOR_VERSION >= 3
    static struct PyModuleDef moduledef = {
        PyModuleDef_HEAD_INIT,
        "os_affine",         /* m_name */
        "Affine transforms for OpenSlide",  /* m_doc */
        -1,                  /* m_size */
        MyMethods,    /* m_methods */
        NULL,                /* m_reload */
        NULL,                /* m_traverse */
        NULL,                /* m_clear */
        NULL,                /* m_free */
    };
#endif

PyMODINIT_FUNC
PyInit_os_affine(void)
{
#if PY_MAJOR_VERSION >= 3
    (void) PyModule_Create(&moduledef);
#else
    (void) Py_InitModule("os_affine", MyMethods);
#endif
}

int
main(int argc, char *argv[])
{
    /* Pass argv[0] to the Python interpreter */
#if PY_MAJOR_VERSION >= 3
    wchar_t *program = Py_DecodeLocale(argv[0], NULL);
    Py_SetProgramName(program);
#else
    Py_SetProgramName(argv[0]);
#endif

    /* Initialize the Python interpreter.  Required. */
    Py_Initialize();

    /* Add a static module */
    PyInit_os_affine();

    return 0;
}

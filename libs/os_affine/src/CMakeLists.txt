PROJECT(MYTEST)

CMAKE_MINIMUM_REQUIRED(VERSION 3.6)

FIND_PACKAGE(PythonLibs REQUIRED)
FIND_PACKAGE(ITK REQUIRED)
INCLUDE(${ITK_USE_FILE})

# Look for openslide
find_path( OPENSLIDE_INCLUDE_DIR NAMES openslide.h PATHS
    /usr/local/include/openslide
    /usr/include/openslide)

# Look for the library.
find_library( OPENSLIDE_LIBRARY NAMES openslide )

# Handle the QUIETLY and REQUIRED arguments and set OPENSLIDE_FOUND true if all
# the listed variables are TRUE.

find_package( PackageHandleStandardArgs )
FIND_PACKAGE_HANDLE_STANDARD_ARGS( OPENSLIDE DEFAULT_MSG OPENSLIDE_LIBRARY OPENSLIDE_INCLUDE_DIR )

SET(MYTEST_SRC "os_affine.cxx" "os_affine_impl.cxx")

INCLUDE_DIRECTORIES(${PYTHON_INCLUDE_DIRS} ${OPENSLIDE_INCLUDE_DIR})
ADD_LIBRARY(os_affine SHARED ${MYTEST_SRC})

TARGET_LINK_LIBRARIES(os_affine ${PYTHON_LIBRARIES} ${OPENSLIDE_LIBRARY} ${ITK_LIBRARIES})

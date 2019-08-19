from distutils.core import setup, Extension

module1 = Extension('os_affine',
                    include_dirs = ['/usr/local/include/openslide'],
                    libraries = ['openslide'], 
                    library_dirs = ['/usr/local/lib'],
                    sources = ['os_affine.cxx'])

setup (name = 'PackageName',
       version = '1.0',
       description = 'This is a demo package',
       ext_modules = [module1])

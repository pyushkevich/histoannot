#!/usr/bin/python
import openslide
import sys

if __name__ == "__main__":
    os=openslide.OpenSlide(sys.argv[1])
    os.get_thumbnail((int(sys.argv[3]), int(sys.argv[4]))).save(sys.argv[2])



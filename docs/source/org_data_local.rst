.. _DataOrgLocal:

*********************************
Organizing Histology Data Locally
*********************************

This tutorial explains how to organize locally attached histology data for using the PICSL Histology Annotation Service (PHAS). It is also possible to do organize data on the Google Cloud Storage service (see :ref:`DataOrg`).

Prerequisites
=============

* A machine with Docker installed

Data Organization Overview
==========================

Histology data in PHAS is organized in a hierarchical manner. The hierarchy is as follows::

    project -> task -> specimen -> block -> section -> slide

*project*
  A collection of histology slides that is accessible to a set of users. The project is the top level of the hierarchy.

*task*
  An activity that is performed by the members of a project. Common tasks include browsing, annotation, or training of machine learning classifiers. A task may include all slides in the project or a subset of slides.

*specimen*
  Refers to tissue from an individual tissue donor. Each specimen has a unique identifier. 

*block*
  A tissue block extracted from a specimen. Each block has a name, e.g., "left amygdala". Typically a block will have multiple histology slides with different stains, organized into one or more sections.

*section*
  The concept of a section arises in projects where tissue is serially sectioned. For example, in one block, we may obtain a NISSL slide every 500 microns, immediately followed by other other stains (e.g., myelin, iron). Each repeating sequence of NISSL-myelin-iron is considered a "section". In diagnostic pathology, we would typically have just one section per block.

*slide*
  Individual slides within a section. These will typically have different stains.

Archive Organization
====================
In this tutorial, we assume that your histology images in your archive are in a format that is readable by `OpenSlide <https://openslide.org/formats/>`_. For example, Aperio SVS. 

* We will assume that your histology images are stored in the directory ``/data/archive``. Substitute this with the actual path to your data archive.
* Inside of this directory, there is a separate sub-directory for each specimen (e.g., ``/data/archive/S1``, ``/data/archive/S2``, etc.)
* For each specimen, there is directory ``histo_raw`` that contains histology images and associated metadata files in ``.json`` format.

For example, if we were to list the contents of the archive command, we would see the following listing::

    > ls /data/archive/*/histo_raw/*
    /data/archive/S1/histo_raw/S1_slide_001.json
    /data/archive/S1/histo_raw/S1_slide_001.svs
    /data/archive/S1/histo_raw/S1_slide_002.json
    /data/archive/S1/histo_raw/S1_slide_002.svs
    /data/archive/S2/histo_raw/S2_slide_001.json
    /data/archive/S2/histo_raw/S2_slide_001.svs
    ...

The accompanying ``.json`` files must at a minimum include three fields: ``specimen`` (string), ``block`` (string) and ``stain`` (string). The files may also include optional fields ``section`` (integer), ``slide`` (integer), ``cert`` (string, with special values ``duplicate`` and ``exclude`` used to prevent slides from showing to the user), and ``tags`` (comma-separated list of strings).

Preprocessed Data
=================
PHAS requires your source data to be in a pyramid TIFF format. It also requires a small thumbnail image for each slide. We provide a Docker container that can be used to generate these files. 
 
Obtaining a GCP Key
-------------------
In order for the Docker container to access your bucket, you need to create a *GCP Service Account* and obtain a secure  *Key*

1. Go to https://console.cloud.google.com/iam-admin/serviceaccounts
2. Create a service account with read/write access to your bucket (you can also use an existing service account).
3. Create a key for your service account. You will download a file like ``my-project-7881856a8832.json``
4. Move this file into a folder in your system, e.g., ``/home/user/gcp_keys/my-project-7881856a8832.json``

Running Preprocessing
---------------------
To run preprocessing, run the following docker command for each specimen::

    docker run \
      -v /home/user/gcp_keys:/gcp_keys:ro \
        pyushkevich/histoannot-preproc-simple:latest \
          bash prep_bucket.sh process_dir \
              -k /gcp_keys/my-project-7881856a8832.json \
              gs://histo_bucket/SPECIMEN1/histo_raw \
              gs://histo_bucket/SPECIMEN1/histo_proc

Be sure to substitute correct paths for ``/home/user/gcp_keys``, ``my-project-7881856a8832.json``, and ``gs://histo_bucket/SPECIMEN1`` in the command above.

If successful, you should see a lot of output like this::

    Copying gs://mtl_histology/SPECIMEN1/bf_raw/S1_slide_001.svs...
    / [1 files][  2.1 MiB/  2.1 MiB]
    Operation completed over 1 objects/2.1 MiB.
    vips temp-2: 3888 x 2592 pixels, 8 threads, 3888 x 16 tiles, 256 lines in buffer
    vips temp-2: done in 0.339s
    Copying file:///tmp/tmp.ZZNldB9DMQ/S1_slide_001_pyramidal.tiff [Content-Type=image/tiff]...
    Copying file:///tmp/tmp.ZZNldB9DMQ/S1_slide_001_thumb.png [Content-Type=image/png]...
    / [2 files][  8.4 MiB/  8.4 MiB]
    Operation completed over 2 objects/8.4 MiB.

Result of Preprocessing
-----------------------
After preprocessing completes, listing the contents of your bucket should look ssomething like this::

    > gsutil ls -R gs://histo_bucket
    gs://histo_bucket/SPECIMEN1/histo_proc/S1_slide_001/S1_slide_001_pyramidal.tiff
    gs://histo_bucket/SPECIMEN1/histo_proc/S1_slide_001/S1_slide_001_thumb.png
    gs://histo_bucket/SPECIMEN1/histo_proc/S1_slide_002/S1_slide_002_pyramidal.tiff
    gs://histo_bucket/SPECIMEN1/histo_proc/S1_slide_002/S1_slide_002_thumb.png
    ...
    gs://histo_bucket/SPECIMEN1/histo_raw/S1_slide_001.svs
    gs://histo_bucket/SPECIMEN1/histo_raw/S1_slide_002.svs
    ...

Specifically, note that for each slide, a new folder was created in the ``histo_proc`` directory, containing the pyramidal file and the thumbnail file.

Additional Options for Preprocessing
------------------------------------
If you do not want to override existing files, add the ``-n`` option right before the ``-k`` option in the docker command above. This is useful if you plan to add more files to your ``histo_raw`` directories and don't want to perform unnecessary processing.

By default, the conversion to pyramidal TIFF uses JPEG compression with quality factor 80. Add the ``-j <value>`` option to the docker command (before the ``-k`` option) to change this to another value.

Manifest Files
==============

In addition to organizing the images in the filesystem, you need to create a separate manifest file for each specimen and a master manifest file. PHAS will use these files to figure out how your slides are organized.

Per-Specimen Manifest Files
---------------------------

Individal manifest files can be stored in the GCS bucket. For example, we can create a ``manifest`` subfolder for each specimen::

    gs://histo_bucket/SPECIMEN1/manifest/S1_manifest.csv
    gs://histo_bucket/SPECIMEN2/manifest/S2_manifest.csv
    ...

Each manifest file will be in comma separated value (CSV) format, as follows::

    FileNameNoExt,Stain,Block,Section,Slide,Certainty,Notes
    S1_slide_001,Nissl,BLK1,1,1,"certain",""
    S1_slide_002,Myelin,BLK1,1,2,"certain",""
    S1_slide_003,Nissl,BLK1,1,3,"certain",""
    ...

The columns that matter here are `FileNameNoExt` (which is the **filename of the slide image without extension**), `Stain` (which is the name of the stain), `Section` and `Slide`, discussed above. The other fields can be left blank. Make sure your manifest includes the header row, as above.

The column ``Certainty`` can be used to mark some slides as duplicates. When text ``duplicate`` appears in this column, the slide will be ignored by PHAS.

Master Manifest File
--------------------

Finally, a master manifest file should be created. It can also be placed in your bucket, at the top level folder, i.e., ``gs://histo_bucket/manifest/phas_master_manifest.txt``. The contents of this file are::

    SPECIMEN1   gs://histo_bucket/SPECIMEN1/manifest/S1_manifest.csv
    SPECIMEN2   gs://histo_bucket/SPECIMEN2/manifest/S2_manifest.csv
    ...


Generating Pyramids and Thumbnails Manually
===========================================
If you do not wish to use the Docker script, you can generate pyramid tiffs and thumbnails manually. You will need to install the `VIPS library <https://libvips.github.io/libvips/>`_.

Converting to Pyramid BigTIFF
-----------------------------
Your data needs to be in pyramid bigtiff format. If it is not, PHAS will not work, or will work poorly. To convert your data to this format use VIPS::

    vips tiffsave input.svs output.tiff \
        --vips-progress --compression=jpeg --Q=80 \
        --tile --tile-width=256 --tile-height=256 \
        --pyramid --bigtiff

The input file can be in a number of formats (`tif`, `svs`) but the output file should be a `tiff` file.

Generating thumbnails
-----------------------------
VIPS can also be used to generate a thumbnail. The command below will generate a 1000 pixel wide thumbnail from an Aperio ``.svs`` file::

    vips thumbnail input.svs[level=2] thumb.png 1000 --vips-progress

Organizing Histology Images on Disk or Bucket
---------------------------------------------
You have some freedom as to how to organize data on the filesystem. The following organization is the default. It groups slides by specimen name. Within each specimen, it places raw data separately from the derived/post-processed data (in case you want to be able to delete the latter).

On the filesystem, the following directory organization is recommended::

    /some/path/SPECIMEN1/histo_raw/FILE1.svs
    /some/path/SPECIMEN1/histo_raw/FILE2.svs
    ...
    /some/path/SPECIMEN1/histo_proc/FILE1/FILE1_pyramidal.tiff
    /some/path/SPECIMEN1/histo_proc/FILE1/FILE1_thumb.png
    /some/path/SPECIMEN1/histo_proc/FILE2/FILE2_pyramidal.tiff
    /some/path/SPECIMEN1/histo_proc/FILE2/FILE2_thumb.png
    ...
    /some/path/SPECIMEN2/...

Above `SPECIMEN1` refers to the name of the specimen, and `FILE1` refers to the filenames of the individual slides.


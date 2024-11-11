.. _DataOrgLocal:

*************************
Organizing Histology Data
*************************

This tutorial explains how to organize locally attached histology data for using the PICSL Histology Annotation Service (PHAS). It is also possible to do organize data on the Google Cloud Storage service (see :doc:`gcs`).

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

* We will assume that your histology images for a particular project are stored in the directory ``/data/archive/someproject``. Substitute this with the actual path to your data archive.
* Inside of this directory, there is a separate sub-directory for each specimen (e.g., ``/data/archive/someproject/S1``, ``/data/archive/someproject/S2``, etc.)
* For each specimen, there is directory ``histo_raw`` that contains histology images and associated metadata files in ``.json`` format.

For example, if we were to list the contents of the archive command, we would see the following listing::

    > ls /data/archive/*/histo_raw/*
    /data/archive/someproject/S1/histo_raw/S1_slide_001.json
    /data/archive/someproject/S1/histo_raw/S1_slide_001.svs
    /data/archive/someproject/S1/histo_raw/S1_slide_002.json
    /data/archive/someproject/S1/histo_raw/S1_slide_002.svs
    /data/archive/someproject/S2/histo_raw/S2_slide_001.json
    /data/archive/someproject/S2/histo_raw/S2_slide_001.svs
    ...

The accompanying ``.json`` files must at a minimum include three fields: ``specimen`` (string), ``block`` (string) and ``stain`` (string). The files may also include optional fields ``section`` (integer), ``slide`` (integer), ``cert`` (string, with special values ``duplicate`` and ``exclude`` used to prevent slides from showing to the user), and ``tags`` (comma-separated list of strings). Here is an example of a valid ``.json`` file:

.. code-block:: json

    {
        "specimen": "S1",
        "block": "B06_left",
        "section": 20,
        "stain": "Nissl",
        "tags": [ "diag", "hippocampus" ]
    }

Preprocessed Data
=================
In addition to the raw data, PHAS requires each slide should have a set of derived files. These files include metadata extracted from the image header and a thumbnail. We recommend placing these files into a separate directory, for example for slide ``S1_slide_001.svs`` above, we may place the derived files into ``/data/archive/someproject/S1/histo_proc/S1_slide_001/preproc/``. 

Generating Preprocessed Data using Docker
-----------------------------------------
To run preprocessing for a slide, run the following docker command:

.. code-block:: bash

    # Create the directory for the preprocessed files
    mkdir -p /data/archive/someproject/S1/histo_proc/S1_slide_001/preproc

    # Generate preprocessed files
    docker run \
        -v /data/archive:/data/archive \
        pyushkevich/histo-preproc:latest \
        python3 process_raw_slide.py -i /data/archive/someproject/S1/histo_raw/S1_slide_001.svs \
                                     -s /data/archive/someproject/S1/histo_proc/S1_slide_001/preproc/S1_slide_001

If successful, the directory ``/data/archive/someproject/S1/histo_proc/S1_slide_001/preproc`` will contain files ``S1_slide_001_thumbnail.tiff``, ``S1_slide_001_metadata.json``, and some other files.

Generating Preprocessed Data Manually
-------------------------------------
If you do not want to use the Docker container, you can download the script ``process_raw_slide.py`` from `here <https://github.com/pyushkevich/tau_maps_brain_2021/blob/main/histo-preproc/process_raw_slide.py>`_ instead.

Project Descriptor Json
=======================
Once you have organized the data in this manner, you can create a PHAS project as described in :doc:`quick_start`. For the organization used in the example above, the project descriptor json file would look like this:

.. code-block:: json

    {
        "base_url": "/data/archive/someproject",
        "disp_name": "Some Project",
        "desc": "Project demonstrating data organization in PHAS",
        "manifest_mode": "individual_json",
        "url_schema": {
            "pattern": {
                "raw": "{specimen}/histo_raw/{slide_name}.{slide_ext}",
                "thumb": "{specimen}/histo_proc/{slide_name}/preproc/{slide_name}_thumb.tiff",
                "metadata": "{specimen}/histo_proc/{slide_name}/preproc/{slide_name}_metadata.json"
            },
            "raw_slide_ext": [ "svs" ]
        }
    }


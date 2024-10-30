.. _GoogleCloud:

*********************************
Histology in Google Cloud Storage
*********************************

PHAS supports histology data stored in Google Cloud Storage (GCS) buckets. This works well if you are hosting PHAS in the Google Cloud as well, since the latency between the data and the PHAS server is then relatively low. 

This document assumes that you are familiar with GCS and have installed the `gcloud CLI <https://cloud.google.com/sdk/docs/install>`_ and the `gsutil utility <https://cloud.google.com/storage/docs/gsutil_install>`_ on your system.

Your data organization should follow the pattern suggested in :doc:`org_data_local`, but inside of a GCS buckets. For example, an organized dataset in bucket ``histo_bucket`` might look like this::

    > gsutil ls -R gs://histo_bucket
    gs://histo_bucket/S1/histo_raw/S1_slide_001.svs
    gs://histo_bucket/S1/histo_raw/S1_slide_001.json
    gs://histo_bucket/S1/histo_proc/S1_slide_001/preproc/S1_slide_001_thumbnail.tiff
    gs://histo_bucket/S1/histo_proc/S1_slide_001/preproc/S1_slide_001_metadata.json
    gs://histo_bucket/S1/histo_raw/S1_slide_002.svs
    ...



Obtaining and Using a GCP Key
=============================
In order for PHAS to access your bucket, you need to create a *GCP Service Account* and obtain a secure *Key*

1. Go to https://console.cloud.google.com/iam-admin/serviceaccounts
2. Create a service account with read access (grant Storage Object Viewer and Viewer permissions) to your project
3. Create a json key for your service account. You will download a file like ``my-project-7881856a8832.json``
4. Save this file into a folder in the PHAS instance directory 

For PHAS to be able to connect to the Google Cloud, the environment variable ``GOOGLE_APPLICATION_CREDENTIALS`` should be set to point to the full path of the key, e.g.,  ``/home/foo/phas/instance/secrets/my-project-7881856a8832.json``. This needs to be set in the ``env.sh`` file and, if you are using uwsgi, in the ``phas_uwsgi.ini`` file. A simple test to see if the connection to GCP is working is to run this code. It should run withour errors and print all of your buckets.

.. code-block:: bash

    cd /home/foo/phas
    source env.sh
    python -c "from google.cloud import storage; c=storage.Client(); print([b.name for b in c.list_buckets()])"


Project Descriptor Json
=======================
Once you have organized the data in this manner, you can create a PHAS project as described in :doc:`quick_start`. For the organization used in the example above, the project descriptor json file would look like this, i.e., identical to the one used for local data organization but with the ``gs://`` prefix to point to your bucket.

.. code-block:: json

    {
        "base_url": "gs://histo_bucket",
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


*************************
PHAS Client API Reference
*************************
The PHAS client API allows you to connect to a running PHAS server and query data. It is a convenient wrapper around the RESTful API.

Usage Examples
==============

Connecting to a Server
----------------------
Obtain an API key through the PHAS web interface and save it as a JSON file. Pass the path
to :class:`Client`, or set the ``PHAS_AUTH_KEY`` environment variable.

.. code-block:: python

    from phas.client.api import Client, Task, AnnotationTask, DLTrainingTask, SamplingROITask
    import pandas as pd

    client = Client('https://phas.myserver.org:8888', '/home/user/private/myserver_api_key.json')

Browsing Projects and Tasks
---------------------------
:class:`Client` provides methods to list the projects and tasks accessible to the current user.
Results are dicts compatible with a ``pandas.DataFrame`` constructor.

.. code-block:: python

    # List all accessible projects
    pd.DataFrame(client.project_listing()).set_index('id')

    # List tasks within a project
    pd.DataFrame(client.task_listing('my_project')).set_index('id')

    # Control anonymization of private specimen names
    client.anonymize = True

Working with Tasks and Slides
------------------------------
:class:`Task` provides access to the slides in a task. Use the task-type-specific subclasses
(:class:`AnnotationTask`, :class:`DLTrainingTask`, :class:`SamplingROITask`) for additional
functionality. The :class:`Slide` class provides image access and metadata.

.. code-block:: python

    # List all slides in task 5, filtered to a specific stain
    task = Task(client, 5)
    pd.DataFrame(task.slide_manifest(stain='Nissl')).set_index('id')

    # Access a slide and read its properties
    from phas.client.api import Slide
    slide = Slide(task, slide_id=42)
    print(slide.specimen_public, slide.block_name, slide.section)
    print(slide.dimensions)   # full-resolution pixel dimensions
    print(slide.spacing)      # pixel spacing in mm

    # Download a region of interest as a PIL image
    patch = slide.get_patch(center=(10000, 8000), level=2, size=(512, 512))

    # Download a thumbnail as a NIfTI image
    slide.thumbnail_nifti_image(filename='thumb.nii.gz', max_dim=1000)

Annotations
-----------
Use :class:`AnnotationTask` to read, write and export slide annotations.

.. code-block:: python

    annot_task = AnnotationTask(client, task_id=3)

    # Get the annotation for a slide as a paper.js JSON dict
    data = annot_task.get_slide_annot_json(slide_id=42)

    # Export the annotation as an SVG file
    annot_task.get_slide_annot_svg(slide_id=42, filename='annot.svg')

    # Export / import all annotations in the task to a JSON file
    annot_task.export_task_annots('all_annots.json')
    annot_task.import_task_annots('all_annots.json')

Deep Learning Training Samples
--------------------------------
Use :class:`DLTrainingTask` to access classifier training samples and their patch images.

.. code-block:: python

    dl_task = DLTrainingTask(client, task_id=7)

    # List all training samples on a slide
    samples = dl_task.slide_training_samples(slide_id=42)

    # Download the patch image for a sample
    img = dl_task.get_sample_image(sample_id=samples[0]['id'])  # PIL Image

Sampling ROIs
-------------
Use :class:`SamplingROITask` to read and manage sampling regions of interest.

.. code-block:: python

    roi_task = SamplingROITask(client, task_id=8)

    # List slides that have at least two sampling ROIs
    pd.DataFrame(roi_task.slide_manifest(min_sroi=2)).set_index('id')

    # Get all sampling ROIs on a slide
    rois = roi_task.slide_sampling_rois(slide_id=42)

    # Download a NIfTI mask image of the sampling ROIs
    roi_task.slide_sampling_roi_nifti_image(slide_id=42, filename='rois.nii.gz')

Label Sets
----------
Tasks that involve labeling (training samples, sampling ROIs) have an associated
:class:`Labelset`. Access it through the task's ``labelset`` property.

.. code-block:: python

    labels = dl_task.labelset.label_listing()
    pd.DataFrame(labels).set_index('id')


API Reference
=============
.. automodule:: phas.client.api
    :members:
*************************
PHAS Client API Reference
*************************
The PHAS client API allows you to connect to a running PHAS server and query data. It is a convenient wrapper around the RESTful API.

Usage Examples
==============
To establish a connection, first obtain an API key through the web application, and save the key (which is a Json) file in a secure location, only readable by your user id. You can use the environment variable `PHAS_AUTH_KEY` to point to this file, or you can pass the filename directory to the `Connection` constructor.

.. code-block:: python

    from phas.client.api import Client
    import pandas as pd

    conn = Client('https://phas.myserver.org:8888', '/home/foo/private/myserver_api_key.json')
    print(conn)

The ``Client`` class has methods to list projects and tasks that interface nicely with Pandas. 

.. code-block:: python

    # Generate and print a Pandas dataframe with project details
    pd.DataDrame(conn.project_listing).set_index('id')


.. code-block:: python

    # Generate and print a Pandas dataframe with task details
    pd.DataDrame(conn.task_listing('example_project')).set_index('id')

Classes ``SamplingROITask``, ``AnnotationTask``, ``DLTrainingTask`` encapsulate functionality for specific types of tasks. The following code creates a representation of a sampling ROI placement task and lists all the slides in that task with more than two sampling ROIs.

.. code-block:: python

    from phas.client.api import SamplingROITask

    # Create a representation of task 8
    roitask = SamplingROITask(conn, 8)

    # Generate and print a slide listing using Pandas
    pd.DataDrame(roitask.slide_manifest(min_sroi=2)).set_index('id')


.. code-block:: python

    # Generate and print a listing of annotations on a single slide
    pd.DataDrame(roitask.get_sampling_rois(34032)).set_index('id')


API Reference
=============
.. automodule:: phas.client.api
    :members:
**********************************************
PICSL Histology Annotation Service Quick Start
**********************************************

This quick start shows you how to get started with the PHAS server using a sample dataset. 

Prerequisites
=============
* A Linux/MacOS machine with Python3 (or `Docker <https://docs.docker.com/>`_)
* An open port on this machine. We will assume port number **8888**. 

Install OpenSlide
-----------------
The `OpenSlide library <https://openslide.org/>`_ is required and must be installed sepately from the Python dependencies. This is not required for Docker-based installations.

* On Debian/Ubuntu::

    apt-get install -y libopenslide-dev

* On MacOS::

    brew install openslide

Install PHAS
============
Three installation options are available in this tutorial:

pip
    This is the simplest option and is recommended for most users. PHAS will be installed as a Python package in a virtual environment. 
git 
    This is the recommended option for those who would like to use the bleeding edge version of PHAS or to modify the source code.
Docker
    Running PHAS in a Docker container that we provide is a good solution for Windows users. Another advantage of the Docker container is that it is already set up for running PHAS in production mode with the ``nginx`` web server. 

Install using ``pip``
---------------------
Create a directory where you will store all the files for your PHAS installation. This directory will contain your configuration, your database, and other important files. In this tutorial we will assume your Linux username is ``foo`` and that your installation directory is ``/home/foo/phas``. This command will create this directory::

    # Change to match your username and preferred location
    mkdir -p /home/foo/phas 

Next, create a virtual environment that will contain PHAS and all the Python modules on which it depends::

    cd /home/foo/phas
    python3 -m venv .venv
    source .venv/bin/activate

Now, install the PHAS package::

    pip install phas

Finally, create the instance directory. This directory will contain your PHAS database, configuration scripts, and other important files::

    mkdir -p instance

The full path to the instance directory will be ``/home/foo/phas/instance``.

Install using ``git``
---------------------
In this tutorial we will assume your Linux username is ``foo`` and that your installation directory is ``/home/foo/phas``. This command will clone the PHAS repository in this directory::

    git clone https://github.com/pyushkevich/histoannot.git /home/foo/phas

Next, enter this directory and create a virtual environment that will contain all the Python modules on which PHAS depends::

    cd /home/foo/phas
    python3 -m venv .venv
    source .venv/bin/activate

Now, install all the PHAS dependencies::

    pip install -r phas/requirements.txt

Finally, create the instance directory. This directory will contain your PHAS database, configuration scripts, and other important files::

    mkdir -p instance

The full path to the instance directory will be ``/home/foo/phas/instance``.

Install using Docker
--------------------
To run the Docker container, you need to create an instance directory somewhere on your computer. In this tutorial we assume that you created an instance directory called ``/home/foo/phas/instance``. Replace this with the actual name of your instance directory. For example, on Windows, this could be ``C:\Users\foo\phas\instance``.

You also need to point the container to the directory with your histology slides. In this tutorial, we assume that your data are organized in the folder ``/data/archive``, and you should replace this with your own path. Finally, you may want to replace ``8888`` with the port number at which you would like to be able to access the PHAS web application. 

To launch the docker container, execute:

.. code-block:: Bash

    docker run -d -p 8888:80 --name phas \
        -v /data/archive:/data \
        -v /home/foo/phas/instance:/instance \
        pyushkevich/phas:latest

After running the command above, you should be able to access the web application by pointing your browser to ``http://localhost:8888``. At this point, you should see a login page.

To restart the container (after making changes to your configuration below), you can run:

.. code-block:: bash

    docker restart phas

To interact with the application using ``flask ...`` commands (which are introduced below and used to configure users, projects, and tasks) use the following commands:

.. code-block:: bash

    docker exec -it phas /bin/bash
    source env.sh


Configuration and Launching
===========================

These instructions are slightly different for the native (``pip`` or ``git``) and Docker-based installations. 

Setup the Environment
---------------------
* *Skip this step for Docker-based installations.*

Create a shell script ``env.sh`` in the directory ``/home/foo/phas`` with the contents below, modified to fit your installation.

.. code-block:: Bash

    #!/bin/bash
    source .venv/bin/activate

    # Name of the FLASK application
    export FLASK_APP=phas

    # Path to the instance directory
    export FLASK_INSTANCE_PATH=/home/foo/phas/instance

    # On Mac, if using homebrew to install openslide, set this to the location of the openslide library
    export DYLD_LIBRARY_PATH=$DYLD_LIBRARY_PATH:/opt/homebrew/lib

Before executing the “flask” commands below, run once per terminal session::

    source env.sh

Create Flask Configuration File
-------------------------------
In your instance directory (``/home/foo/phas/instance``), create a file ``config.py`` and add the lines below, customizing them for your installation. Replace the secret key `with your own <https://flask.palletsprojects.com/en/stable/config/#:~:text=%24%20python%20%2Dc%20%27import%20secrets%3B%20print(secrets.token_hex())%27%0A%27192b9bdd22ab9ed4d12e236c78afcb9a393ec15f71bbf5dc987d54727823bcbf%27>`_.

.. code-block:: Python

    # Replace with your own random sequence
    SECRET_KEY="cf55754542254a76fbd839970ddd55fee4088ed594511c90ea3976428a851374"

    # Name of your server printed on the landing page
    HISTOANNOT_PUBLIC_NAME="My Test PHAS Server"

    # URL for your PHAS installation
    HISTOANNOT_PUBLIC_URL="http://127.0.0.1:8888"


Database Creation
-----------------
Run this command to create the sqlite3 database structure for the first time::

    flask init-db

Test Your Configuration
-----------------------
If successful, the command below will print the configuration settings you provided above and list the tables in the database (about 20)::

    flask info

Start the Web Application
-------------------------
* *Skip this step for Docker-based installations. The web application is launched automatically when you run the Docker container.*

To test-drive PHAS, you can use the command below to launch the web application. However, when in production, you should use ``nginx`` and ``uwsgi`` to launch your application instead, as described in :doc:`production`.

.. code-block:: Bash

    flask run --debug --port 8888

You will see this output::

    * Serving Flask app 'histoannot'
    * Debug mode: off
    WARNING: This is a development server. Do not use it in a production deployment. Use a production WSGI server instead.
    * Running on http://127.0.0.1:8888
    Press CTRL+C to quit

Navigate to the URL provided (http://127.0.0.1:8888) and you should see the login page.

Start the Slide Server Process
------------------------------
* *Skip this step for Docker-based installations. The slide server is launched automatically when you run the Docker container.*

In addition to running the main web application with ``flask run``, you need to launch the slide server process, which manages the interface between the web application and the histology images. The server process should be run in a **separate terminal window**.

Open a new terminal window and navigate to the ``/home/foo/phas`` directory::

    source env.sh
    flask slide-server-run


Creating Users, Projects and Tasks
==================================

Open a separate terminal window or tab so that you can interact with the server while it is running. In the terminal go to your ``/home/foo/phas`` directory and run, as before::

    source env.sh

For Docker installations, open a terminal window and run::

    docker exec -it phas /bin/bash
    source env.sh

Create Admin User Account
-------------------------
Create a user (replace ``testuser`` with your own id) and provide them administrator privileges::

    flask users-add -e testuser@gmail.com testuser
    flask users-set-site-admin test user

This will print a URL. Navigate to this URL and set up the password for your account. Now you should see the landing page with the message that you have not been added to any projects yet.

* You can click on your username on the top right of the web application to change your profile and manage other users on the server. 

Download Sample Dataset
-----------------------
The easiest way to get started with PHAS is to download a sample dataset. It contains some blockface images of brain tissue prior to cryosectioning. Download the dataset ``histoannot_sample_data.zip`` from `<https://upenn.box.com/v/phas-sample-data>`_ and unpack it into a folder separate from your main PHAS install. Let’s suppose you called this folder ``/data/archive/histoannot_sample_data``.

Create a directory where you will keep the json descriptor files used to configure projects and tasks::

    mkdir instance/json

Create a json descriptor file for the project you downloaded, called ``instance/json/example_project.json``, with the contents below:

.. code-block:: json

    {
        "base_url": "/data/archive/histoannot_sample_data",
        "disp_name": "Example Project",
        "desc": "Example project with some blockface images",
        "manifest_mode": "individual_json",
        "url_schema": {
            "pattern": {
                "raw": "{specimen}/raw/{slide_name}.{slide_ext}",
                "thumb": "{specimen}/proc/{slide_name}_thumb.png",
                "metadata": "{specimen}/proc/{slide_name}_metadata.json"
            },
            "raw_slide_ext": [ "tiff" ]
        }
    }

* For Docker installations, ``base-url`` should be relative to the directory ``/data``, which is the path on the container to which you mapped your data directory when calling ``docker run``. For example, if you placed the sample dataset in ``/home/foo/phas/histology_data/histoannot_sample_data``, then ``base_url`` should be set to ``/data/histoannot_sample_data``.
  
The commands below configure the project and add your username to it as administrator::

    flask project-add example instance/json/example_project.json
    flask users-set-access-level -p example admin testuser

The commands below import slides from the sample project into the database. You should run this command every time that new slides are added to your data folder::

    flask refresh-slides example

If you edit the ``.json`` file later, you need to run the command below for your edits to take effect::

    flask project-update example instance/json/example_project.json

Configure Browse and Annotation Tasks
-------------------------------------
If you browse to your PHAS URL, you will see that there is a project with one specimen and four slides. However, you cannot view these slides yet because we have not yet set up any tasks. Tasks are specific ways of interacting with histology images, and they include browsing, annotation, placing boxes for training classifiers, and placing sampling regions. 

Each task is specified by creating a json configuration file.

Create file ``instance/json/example_browse.json`` for the browsing task with contents:

.. code-block:: json

    {
        "restrict-access": false,
        "mode": "browse",
        "name": "Browse",
        "desc": "Browse the slide collection"
    }

And create file ``instance/json/example_annot.json`` for the annotation task with contents:

.. code-block:: json

    {
        "restrict-access": true,
        "mode": "annot",
        "name": "Anatomical Labeling",
        "desc": "Labeling anatomical boundaries and regions"
    }

The commands below will intialize these tasks and rebuild the slide index for the tasks::

    flask tasks-add example instance/json/example_browse.json
    flask tasks-add example instance/json/example_annot.json
    flask rebuild-task-slide-index example

You will be able to see the Browse task immediately. To see the Annotation task, go to the “manage users” menu option under your username and give yourself write access to the task. Alternatively, you can use the ``flask users-set-access-level`` command with -t flag to give yourself write access to the newly created task.


Configure a Classification Training Task
----------------------------------------

To create a classifier training task, we first need to create a set of classification labels. Create the file ``instance/json/blockface_labels.json`` with contents:

.. code-block:: json

    [
        { "name" : "gray matter", "color" : "#18b497", "description" : "Gray Matter" },
        { "name" : "white matter", "color" : "#2816ba", "description" : "White Matter" },
        { "name" : "background", "color" : "#f97a8a", "description" : "Ice/Background" }
    ]

Then add this labelset to the server::

    flask labelset-add example blockface_tissue_types instance/json/blockface_labels.json

The labelset should be available for editing under the dropdown menus on the project menu in the web interface.

Then create a task descriptor for generating training patches in file ``instance/json/example_training.json`` with contents:

.. code-block:: json

    {
        "restrict-access": false,
        "name": "Tissue Class Training",
        "stains": [
            "blockface"
        ],
        "dltrain": {
            "labelset": "blockface_tissue_types",
            "min-size": 128,
            "max-size": 128,
            "display-patch-size": 128
        },
        "mode": "dltrain",
        "desc": "Training a deep learning classifier to segment blockface images"
    }

Then add the task to the server::

    flask tasks-add example instance/json/example_training.json
    flask rebuild-task-slide-index example

Now the task will be available in the web interface. 


Configure a Sampling ROI Task
-----------------------------

A sampling ROI task allows you to define sampling ROIs from which quantitative measures can be derived. To set up this task we also first have to define labels.

Create the file ``instance/json/sampling_labels.json`` with contents:

.. code-block:: json

    [
        {
            "name": "Hipp",
            "color": "#ff3300",
            "description": "Hipppocampus"
        },
        {
            "name": "PHG",
            "color": "#ff6600",
            "description": "Parahippocampal Gyrus"
        },
        {
            "name": "FuG",
            "color": "#ff6699",
            "description": "Fusiform Gyrus"
        }
    ]

And create the task descriptor file ``instance/json/example_sroi.json`` with contents:

.. code-block:: json

    {
        "restrict-access": false,
        "name": "Sampling ROI Placement",
        "desc": "Placement of Sampling ROIs for Quantification",
        "mode": "sampling",
        "sampling": {
            "labelset": "blockface_srois"
        }
    }

Then add the labelset and task to the server::

    flask labelset-add example blockface_srois instance/json/sampling_labels.json
    flask tasks-add example instance/json/example_sroi.json
    flask rebuild-task-slide-index example
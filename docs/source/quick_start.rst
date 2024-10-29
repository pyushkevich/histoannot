**********************************************
PICSL Histology Annotation Service Quick Start
**********************************************

This quick start shows you how to get started with the PHAS server using a sample dataset. 

Requirements
============
* A Linux/Unix/MacOS machine with Python3
* An open port on this machine. We will assume port number **8888**.
* We recommend using `tmux <https://github.com/tmux/tmux/wiki>` for this tutorial. It is a terminal multiplexer that allows you to create multiple terminal sessions in the same terminal window and when you disconnect from the server, your terminal sessions and the programs you launch inside them will keep running. 

Installation
============

Install OpenSlide
-----------------
The `OpenSlide library <https://openslide.org/>`_ is required and must be installed sepately from the Python dependencies.

* On Debian/Ubuntu::

    apt-get install -y libopenslide-dev

* On MacOS::

    brew install openslide

Create a Directory for PHAS
---------------------------
Create a directory where you will store all the files for your PHAS installation. This directory will contain your configuration, your database, and other important files. In this tutorial we will assume your Linux username is ``foo`` and that your installation directory is ``/home/foo/phas``. This command will create this directory::

    # Change to match your username and preferred location
    mkdir -p /home/foo/phas 

Create Python Virtual Environment
---------------------------------
Create a virtual environment that will contain PHAS and all the Python modules on which it depends::

    cd /home/foo/phas
    python3 -m venv .venv
    source .venv/bin/activate

Install the PHAS Application
----------------------------
If you would like to get a stable version of PHAS and do not expect to make your own changes to the code, we recommend installing PHAS using ``pip``, the Python package manager::

    pip install phas

If you want the most recent code and/or want to be able to make changes to the code, you can checkout the source code from Github with this command::

    # Source code will be located in /home/foo/phas/histoannot
    git clone https://github.com/pyushkevich/histoannot.git histoannot
    pip install -e histoannot

Environment Variables
---------------------
Create a shell script ``env.sh`` in the ``phas`` that will contain system commands to execute before running the web application. Here are the recommended contents of this file::

    #!/bin/bash
    source .venv/bin/activate

    # Name of the FLASK application
    export FLASK_APP=phas

    # Path to the instance directory (config files and database will go here)
    export FLASK_INSTANCE_PATH=/home/foo/phas/instance

    # On Mac, if using homebrew to install openslide, set this to the location of the openslide library
    export DYLD_LIBRARY_PATH=DYLD_LIBRARY_PATH:/opt/homebrew/lib

Before executing the “flask” commands below, run once per terminal session::

    source env.sh

Configuration and Launching
===========================

Main Configuration File
-----------------------
Create a directory called ``instance`` in the ``/home/foo/phas`` directory. This will contain your database, configuration files, and application cache::

    cd /home/foo/phas
    mkdir -p instance

Create a file ``instance/config.py`` and add the lines below, `replacing the secret code with your own <https://flask.palletsprojects.com/en/stable/config/#:~:text=%24%20python%20%2Dc%20%27import%20secrets%3B%20print(secrets.token_hex())%27%0A%27192b9bdd22ab9ed4d12e236c78afcb9a393ec15f71bbf5dc987d54727823bcbf%27>`. Also you can change 8888 to your preferred port number::

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
When debugging you can use the command below to start the web application. When in production, you should use UWSGI to launch your application instead::

    flask run --port 8888

You will see this output::

    * Serving Flask app 'histoannot'
    * Debug mode: off
    WARNING: This is a development server. Do not use it in a production deployment. Use a production WSGI server instead.
    * Running on http://127.0.0.1:8888
    Press CTRL+C to quit

Navigate to the URL provided (http://127.0.0.1:8888) and you should see the login page.

Start the Slide Server Process
------------------------------
In addition to running the main web application with ``flask run``, you need to launch the slide server process, which manages the interface between the web application and the histology images. The server process should be run in a **separate terminal window**.

Open a new terminal window and navigate to the ``phas`` directory::

    source env.sh
    flask slide-server-run


Creating Users, Projects and Tasks
==================================

Open a third terminal window or tab so that you can interact with the server while it is running. In the terminal go to your phas directory and run, as before::

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
The easiest way to get started with PHAS is to download a sample dataset. It contains some blockface images of brain tissue prior to cryosectioning. Download the dataset ``histoannot_sample_data.zip`` from `<https://upenn.box.com/v/phas-sample-data>`_ and unpack it into a folder separate from your main PHAS install. Let’s suppose you called this folder ``/mydata/histoannot_sample_data``.

Create a directory where you will keep the json descriptor files used to configure projects and tasks::

    mkdir instance/json

Create a json descriptor file for the project you downloaded, called ``instance/json/example_project.json``, with the contents below::

    {
        "base_url": "/mydata/histoannot_sample_data",
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

The commands below configure the project and add your username to it as administrator::

    flask project-add example instance/json/example_project.json
    flask users-set-access-level -p example admin testuser

The commands below import slides from the sample project into the database. You should run this command every time that new slides are added to your data folder::

    flask refresh-slides example


Configure Browse and Annotation Tasks
-------------------------------------
If you browse to your PHAS URL, you will see that there is a project with one specimen and four slides. However, you cannot view these slides yet because we have not yet set up any tasks. Tasks are specific ways of interacting with histology images, and they include browsing, annotation, placing boxes for training classifiers, and placing sampling regions. 

Each task is specified by creating a json configuration file.

Create file ``instance/json/example_browse.json`` for the browsing task with contents::

    {
        "restrict-access": false,
        "mode": "browse",
        "name": "Browse",
        "desc": "Browse the slide collection"
    }

And create file ``instance/json/example_annot.json`` for the annotation task with contents::

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

To create a classifier training task, we first need to create a set of classification labels. Create the file ``instance/json/blockface_labels.json`` with contents::

    [
        { "name" : "gray matter", "color" : "#18b497", "description" : "Gray Matter" },
        { "name" : "white matter", "color" : "#2816ba", "description" : "White Matter" },
        { "name" : "background", "color" : "#f97a8a", "description" : "Ice/Background" }
    ]

Then add this labelset to the server::

    flask labelset-add example blockface_tissue_types instance/json/blockface_labels.json

The labelset should be available for editing under the dropdown menus on the project menu in the web interface.

Then create a task descriptor for generating training patches in file ``instance/json/example_training.json`` with contents::

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

Create the file ``instance/json/sampling_labels.json`` with contents::

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

And create the task descriptor file ``instance/json/example_sroi.json`` with contents::

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
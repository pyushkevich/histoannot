**********************************************
PICSL Histology Annotation Service Quick Start
**********************************************

This quick start shows you how to get started with the PHAS server using a sample dataset. 

Requirements
============
* A Linux/Unix/MacOS machine with Python3
* An open port on this machine. We will assume port number **8888**.

Installation
============

Install OpenSlide
-----------------
The `OpenSlide library <https://openslide.org/>`_ is required and must be installed sepately from the Python dependencies.

* On Debian/Ubuntu::

    apt-get install -y libopenslide-dev

* On MacOS::

    brew install openslide

Install Redis
-------------
The `Redis library <https://redis.io/docs/latest/>`_ is used to for coordination between the server and worker processes. 

* On Linux see `the instructions on redis.io <https://redis.io/docs/latest/operate/oss_and_stack/install/install-redis/install-redis-on-linux/>`_

* On MacOS::

    brew install redis
    brew services start redis


Checkout The Code Repository
----------------------------
Checkout the code from Github with this command. Then enter the directory where to code was checked out::

    git clone https://github.com/pyushkevich/histoannot.git phas
    cd phas


Create Python Virtual Environment
---------------------------------
This step is highly recommended, creating a virtual environment specifically for PHAS::

    python3 -m venv .venv
    source .venv/bin/activate


Install Python Dependencies
---------------------------
This command will install all the dependencies into the virtual environment::

    pip install -r histoannot/requirements.txt


Environment Variables
---------------------
Create a shell script ``env.sh`` in the ``phas`` that will contain system commands to execute before running the web application. Here are the recommended contents of this file::

    #!/bin/bash
    source .venv/bin/activate

    # Name of the FLASK application
    export FLASK_APP=histoannot

    # On Mac, if using homebrew to install openslide, set this to the location of the openslide library
    export DYLD_LIBRARY_PATH=DYLD_LIBRARY_PATH:/opt/homebrew/lib

Before executing the “flask” commands below, run once per terminal session::

    source env.sh

Configuration and Launching
===========================

Main Configuration File
-----------------------
Create a directory called ``instance`` in the ``phas`` directory. This will contain your database, configuration files, and application cache::

    mkdir -p instance

Create a file ``instance/config.py`` and add the lines below, replacing the secret code with your own. Also you can change 8888 to your preferred port number::

    SECRET_KEY="lfkwelkjrwleklmasndikfbsqr"
    HISTOANNOT_SERVER_MODE="master"
    HISTOANNOT_PUBLIC_NAME="My Test PHAS Server"
    HISTOANNOT_PUBLIC_URL="http://127.0.0.1:8888"


Database Creation
-----------------
Run this command to create the sqlite3 database structure for the first time::

    flask init-db

Use the commands below to to verify that the database tables have been created. You should see the names of about 20 tables listed::

    sqlite3 instance/histoannot.sqlite
    .tables
    .exit

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

Start a Worker Process
----------------------
In addition to running the main server, you need to run one or more worker processes. These processes perform asynchronous tasks, such as extracting patches from histology images during classifier training. The worker process will need to run in a **separate terminal window**.

Open a new terminal window and navigate to the ``phas`` directory::

    source env.sh
    flask preload-worker-run


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
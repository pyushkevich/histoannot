**********************************************
PICSL Histology Annotation Service Quick Start
**********************************************

This quick start shows you how to set up a PHAS server in a Docker container. 

Requirements
============
* A Linux/Unix/MacOS machine with Docker installed
* An open port on this machine. We will assume port number **5555**.
* A directory containing large histology images in pyramid format. We will assume that the path to this directory on your Linux machine is `/home/phas/hdata`. 
* An empty directory where the database and other data for the running instance of your PHAS server will be stored. We assume that the path to this directory is `/home/phas/instance`.


Preparing your Histology Data
=============================

Converting to Pyramid BigTIFF
-----------------------------
Your data needs to be in pyramid bigtiff format. If it is not, PHAS will not work, or will work poorly. To convert your data to this format, you can use software **vips**, available from `<https://jcupitt.github.io/libvips/API/current/>`_:: 

    vips tiffsave input.tif output.tif \
        --vips-progress --compression=jpeg --Q=80 \
        --tile --tile-width=256 --tile-height=256 \
        --pyramid --bigtiff

The input file can be in a number of formats (`tif`, `svs`) but the output file should be a `tif` file. We assume that all of your data has been preprocessed with **vips**. 

Organizing Histology Images on Disk
-----------------------------------
Your histology data must be organized as follows. Data are organized into **specimens**, **blocks**, **sections** and **slides**. Specimen refers to an individual case. Block refers to a block within this case. Section and slice refer to the slicing of a block. For example, we may have a protocol where we obtain five 50um slices every 1mm apart (the five slices would have different stains). Then each set of slices obtained 1mm apart is called a section, while the invidual 50um slices are called slices.

On the filesystem, the following directory organization is required::

    /home/phas/hdata/SPECIMEN1/histo_raw/FILE1.tif
    /home/phas/hdata/SPECIMEN1/histo_raw/FILE2.tif
    ...
    /home/phas/hdata/SPECIMEN1/histo_proc/FILE1/FILE1_thumbnail.tiff
    /home/phas/hdata/SPECIMEN1/histo_proc/FILE2/FILE2_thumbnail.tiff
    ...
    /home/phas/hdata/SPECIMEN2/...

Above `SPECIMEN1` refers to the name of the specimen, and `FILE1` refers to the filenames of the individual `.tif` files for that specimen. The thumbnail images are smaller versions of the main slides, e.g., 1000 pixels wide.

Generating Manifest Files
-------------------------
In addition to organizing the images in the filesystem, you need to create a separate manifest file for each specimen and a master manifest file. Individal manifest files can be stored in `/home/phas/hdata`::

    /home/phas/hdata/SPECIMEN1/manifest.csv
    /home/phas/hdata/SPECIMEN2/manifest.csv
    ...

Each manifest file will be in comma separated value (CSV) format, as follows::

    Slide,Stain,Block,Section,Slice,Certainty,Notes
    FILE1,Nissl,BLK1,2,1,"certain",""
    FILE2,HE,BLK1,2,2,"certain",""
    FILE3,Nissl,BLK1,2,1,"certain",""
    ...

The columns that matter here are `Slide` (which is the **filename of the slide image without extension**), `Stain` (which is the name of the stain), `Section` and `Slice`, discussed above. The other fields can be left blank. Make sure your manifest includes the header row, as above.

Finally, a master manifest file should be created in `/home/phas/hdata/master_manifest.txt` with the following contents::

    SPECIMEN1   /home/phas/hdata/SPECIMEN1/manifest.csv
    SPECIMEN2   /home/phas/hdata/SPECIMEN2/manifest.csv
    ...


Projects
--------
A single PHAS server can serve multiple projects. Each project represents a separate collection of histology data, e.g., different set of scanned slides. Each project can have its own root directory, and its own manifest files.

Starting PHAS in a Docker Container
===================================

Before launching the container, we will populate the instance directory with a simple config file. In an editor create a file `/home/phas/instance/config.py` and include the following lines::

    HISTOANNOT_SERVER_MODE="master"
    SECRET_KEY="92340wjdflksn2839our"

Replace the secret key string with your own string. It is used for encrypting cookies and should be unique to your server.

We also need to create at least one project. In this example, we are using only a single project. Projects are described by ``.json`` files located in the ``projects`` directory. The internal name of each project matches the name of the ``.json`` file. Create a file ``projects/default.json`` in the ``instance`` directory and populate it as follows::

    {
        "base_url": "/home/phas/hdata",
        "disp_name": "Default Project",
        "desc" : "This is an example project"
    }


We are now ready to run the container as a background service. Execute the following commands::

    docker pull pyushkevich/histoannot-master:latest
    docker run -d -p 5555:5000 \
        -v /home/phas/hdata/:/home/phas/hdata \
        -v /home/phas/instance:/tk/node_dzi/instance \
        pyushkevich/histoannot-master:latest

To verify that the container is running, run `docker ps`. The output should look like this::

    CONTAINER ID        IMAGE               COMMAND                   CREATED             STATUS       PORTS                    NAMES
    de19b4ece187        phas_master         "/bin/sh -c \"supervi…"   4 minutes ago       Up 4 minutes       0.0.0.0:5555->5000/tcp   sweet_bhaskar

To test that the actual service is running, navigate your browser to `http://localhost:5555/hello`. The browser should display the string `HISTOANNOT MASTER`

To stop the service, type `docker stop sweet_bhaskar` (replace 'sweet_bhaskar' with the actual name in `docker ps` output)


Configure the PHAS Instance
===========================
The service is running but it has no data. We need to run a few commands inside of the docker container to make it work. 

Configure a Task
----------------
Tasks in PHAS are separate projects that allow annotation to be performed in parallel without interference between different workflows. You need to set up at least one task. Tasks are set up using `.json` files. There are two types of tasks: annotation (drawing curves and text on slides) and deep learning training (placing boxes over objects in histology slides). For now let's configure an annotation task.

Create the directory `/home/phas/instance/tasks` and open file `/home/phas/instance/tasks/task1.json` in an editor. Paste the following content::

	{
		"mode": "annot",
		"name": "Anatomical Labeling",
		"desc": "This is my first task",
		"restrict_access": false
	}

Open a Shell to the Container
-----------------------------
To configure the server, we need to open a shell in the running container. Run `docker ps` and copy the name of the container. In our case, the container is called `sweet_bhaskar`, yours will have a similar random name.

To open a shell to the container, enter::

	docker exec -it sweet_bhaskar /bin/bash

You will now be logged in as user `root` inside the container. Run the following commands::

	flask --help

This will give you a listing of all available configuration commands. 


Configuring Access and Database
-------------------------------
Next run::

	flask passwd-set

This allows you to set an "invitation code" that users will need to register in your server. This blocks random users form accessing your data.

To initialize the database run::

	flask init-db

This will create a file  `histoannot.sqlite` in your `/home/phas/instance` folder. Take good care of this file and back it up often! It contains your database!


Configuring a Project
---------------------
Configuring a project involves two steps:
    1. Creating a ``.json`` file in the ``instance/projects`` directory
    2. Initializing the project in the database::

        flask projects-init


Connecting to Histology Data
----------------------------
Run the following command to tell the PHAS server where the histology data are located. The server will scan the `hdata` directory and make the slides in your manifest files available to users.::

	flask refresh-slides /home/phas/hdata/master_manifest.txt

Run this command whenever you add new slides to your `/home/phas/hdata` directory (after updating the manifest files).


Creating a Task
---------------
Lastly, we need to create a task. We already edited a JSON file, and now we need to tell the server to create a task based on it. Run::

	flask task-add --json instance/tasks/task1.json


Take it for a Spin
==================
The moment of truth... Point your browser to `http://localhost:5555`. You should be able to:

* See the login page
* Click on the register page and register as a new user with the invitation code created above
* Login with your new credentials and see a listing of available tasks
* Be able to navigate down to a slide and perform annotation








 

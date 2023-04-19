**********************************************
PICSL Histology Annotation Service Quick Start
**********************************************

This quick start shows you how to set up a PHAS server in a Docker container. 

Requirements
============
* A Linux/Unix/MacOS machine with Docker installed
* An open port on this machine. We will assume port number **5555**.
* A directory containing large histology images in pyramid format. We will assume that the path to this directory on your Linux machine is ``/home/phas/hdata``.
* An empty directory where the database and other data for the running instance of your PHAS server will be stored. We assume that the path to this directory is ``/home/phas/instance``.


Preparing your Histology Data
=============================

Histology data can be accessed by PHAS in two ways:

* In a local directory on the server
* In a Google Cloud Storage bucket

  * In this case, the data will be copied and locally cached on the server

For every histology slide, a minimum of two files are required:

* A pyramidal TIFF file (in ``.svs`` or ``.tiff`` format)
* A thumbnail (about 1000 pixels in width, in ``.png`` or ``.jpeg`` format)

Please see :ref:`DataOrg` for a tutorial on how to organize your data

Projects
========
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

Furthermore, if you organized your files in a manner different than the default, you need to include a **schema** element in your project JSON file. This describes where the raw images and thumbnails (and other relevant information) will be found.

Here is an example of a project ``.json`` file with a schema element::

    {
        "base_url": "gs://mybucket",
        "disp_name": "Custom project",
        "desc" : "This is a custom project",
        "pattern": {
            "raw": "raw_data/{specimen}/{slide_name}.{slide_ext}",
            "x16": "derived_data/{specimen}/{slide_name}_x16_pyramid.tiff",
            "thumb": "derived_data/{specimen}/{slide_name}_thumb.png"
        }
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
To initialize the database for the first time, run::

	flask init-db

This will create a file  `histoannot.sqlite` in your `/home/phas/instance` folder. Take good care of this file and back it up often! It contains your database!

**WARNING**: Running ``flask init-db`` will delete all the data in your database. Do not run this command after initial installation unless you are sure you have a backup.

Configuring a Project
---------------------
Configuring a project involves two steps:

    1. Creating a ``.json`` file (see above)
    2. Coming up with a name for your project (e.g., ``diag``)
    3. Initializing the project in the database, like this::

        flask project-add diag some/path/project_diag.json


Connecting to Histology Data
----------------------------
Run the following command to tell the PHAS server where the histology data are located. The server will scan the `hdata` directory and make the slides in your manifest files available to users.::

	flask refresh-slides /home/phas/hdata/master_manifest.txt

Run this command whenever you add new slides to your `/home/phas/hdata` directory (after updating the manifest files).

Creating a Task
---------------
Next, we need to create a task. We already edited a JSON file, and now we need to tell the server to create a task based on it. Run::

	flask task-add --json some/path/task1.json


Adding a User
-------------
To create users and invite them by email, issue the command below. This only works if you have configured email on your server.::

    flask users-add -e testuser@gmail.com -n testuser

Alternatively, add a user without sending an email (without the ``-n`` flag) and you will be provided an invitation link that you can send manually.::

    flask users-add -e testuser@gmail.com testuser

To give this user admin permissions, issue the command below. After that, you can create new users through the web interface.

    flask users-set-site-admin testuser
    flask users-set-access-level -P diag A testuser

Take it for a Spin
==================
The moment of truth... Point your browser to `http://localhost:5555`. You should be able to:

* See the login page
* Click on the register page and register as a new user with the invitation code created above
* Login with your new credentials and see a listing of available tasks
* Be able to navigate down to a slide and perform annotation








 

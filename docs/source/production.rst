***********************************
Running in a Production Environment
***********************************

These instructions describe how to configure the application to run in a production environment. Running the web application using ``flask run`` is discouraged due to slow performance. These instructions instead show how to run PHAS using a combination of ``nginx`` web server and ``uwsgi`` software.

These instructions assume you are using a Ubuntu Linux and have root-level access to the system. Please get advice from your local system administrator on modifying these instructions to fit your environment.

Installing Required Packages
============================
To install NGINX, run the following commands::

    sudo apt-get install -y software-properties-common
    sudo apt-get install -y nginx
    sudo rm /etc/nginx/sites-enabled/default

To install UWSGI, run the follwing commands::

    sudo pip3 install uwsgi
    sudo mkdir -p /var/log/uwsgi


Configuring NGINX
=================
We will assume that PHAS is intalled in directory ``/home/foo/phas`` so that the instance directory containing your database is in ``/home/foo/phas/instance``. Substitute your installation directory in the configuration files below.

Create and edit file ``/etc/nginx/conf.d/phas01.conf`` (the filename is arbitrary) with the following contents::

    server {
        listen      80;
        server_name localhost;
        charset     utf-8;
        client_max_body_size 8G;
        location / {
            try_files $uri @histoannot;
        }
        location @histoannot {
            include uwsgi_params;
            uwsgi_pass unix:/home/foo/phas/instance/phas01_uwsgi.sock;
        }
        location /static {
            root /home/foo/phas;
        }
    }

The snippet above assumes you are using the ``http:`` protocol and port 80. We recommend using ``https:`` but setting this up requires `obtaining an SSL certificate <https://letsencrypt.org/getting-started/>`. Once obtained, you can replace ``listen 80`` with the following lines, edited to match your specific environment::

    listen 443 ssl;
    server_name phas.mydomain.org;
    ssl_certificate /etc/letsencrypt/live/phas.mydomain.org/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/phas.mydomain.org/privkey.pem

After editing the NGINX config file, run this command to restart the NGINX server::

    sudo systemctl restart nginx.service


Configuring UWSGI
=================
Create a file ``/etc/uwsgi/apps-enabled/phas01_uwsgi.ini`` (name is arbitrary) with the following contents::

    [uwsgi]
    # Application's base folder: change to match yours
    base = /home/foo/phas
    venv = %(base)/.venv

    # User and group: change to match yours
    uid = foo
    gid = foo

    # Maybe not necessary
    plugins = python3

    # Python module to import: do not change
    app = histoannot
    module = %(app):create_app()

    # Python path: should not need changing
    pythonpath = %(base)
    pythonpath = %(base)/libs/os_affine/build

    # Socket file's location: must match NGINX config
    socket = %(base)/instance/phas01_uwsgi.sock

    # Permissions for the socket file
    chmod-socket = 666

    # The variable that holds a flask application inside the module imported above
    callable = app

    # Location of log files: alternatively place in /var/log/uwsgi
    logto = %(base)/instance/phas01_uwsgi.log

    # Reload file: touching this file will cause the application to reload
    touch-reload = %(base)/instance/phas01_uwsgi_touch.me

    # Threads
    enable-threads = true

    # Resources (can be adjusted to improve performance)
    workers = %(%k * 2)
    threads = %(%k * 1)
    stats = 127.0.0.1:9191

    # Increase buffer size for DataTables requests
    buffer-size = 32768

At this point you can test that UWSGI is working by running the command::

    sudo systemctl restart uwsgi.service 

With this command running, you should be able to navigate to ``http://phas.mydomain.org`` or ``https://phas.mydomain.org`` (replace with your own domain) and see the PHAS landing page. If not, please check NGINX and UWSGI log files for errors.

Run the Slide Server as a systemd Service
=========================================
The last step is to configure a service that will execute the workers that support the main uwsgi application. Create a file called ``/etc/systemd/system/phas01-slide-server.service`` with the following contents::

    [Unit]
    Description=PHAS Slide Server
    After=network.target

    [Service]
    Type=simple
    User=foo
    WorkingDirectory=/home/foo/phas
    Environment=PYTHONUNBUFFERED=1
    ExecStart=/bin/bash -c "source env.sh && flask slide-server-run"
    Restart=always
    StandardOutput=journal
    StandardError=journal

    [Install]
    WantedBy=multi-user.target

Run the following commands to install and start the service::

    sudo systemctl enable phas01-slide-server
    sudo systemctl start phas01-slide-server

To monitor the output from the service, run::

    journalctl -u phas01-slide-server -f


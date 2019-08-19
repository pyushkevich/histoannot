#!/bin/bash

# Start NGINX
/etc/init.d/nginx start

# Start the UWSGI service
uwsgi --ini /tk/node_dzi/docker/dzi_node/histoannot_uwsgi.ini --uid user --gid user

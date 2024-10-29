FROM python:3.12-alpine
RUN apk update
RUN apk install -y build-essential wget

# Install NGINX
RUN apt-get install -y software-properties-common \
 && add-apt-repository ppa:nginx/stable \
 && apt-get install -y nginx \
 && rm /etc/nginx/sites-enabled/default

# Install uwsgi
RUN /usr/local/bin/python -m pip install --upgrade pip
RUN pip install venv
RUN pip install uwsgi && mkdir -p /var/log/uwsgi  

# Install openslide
RUN apt-get install -y libopenslide-dev 

# Switch to user foo
USER foo:foo
RUN mkdir /home/foo/phas/instance
WORKDIR /home/foo/phas

# Create a virtual environment
RUN python -m venv .venv
RUN source .venv/bin/activate

# Copy the app to a location
COPY phas histoannot

# Install the application from this location with requirements
RUN pip install -e histoannot

# Create the environment file
COPY docker/env.sh env.sh

# Create and update the configuration file
COPY docker/config.py instance/config.py

# Configure nginx
COPY docker/dzi_node/nginx.conf /etc/nginx/nginx.conf
COPY docker/dzi_node/phas_nginx.conf /etc/nginx/conf.d/

# Configure UWSGI
COPY docker/dzi_node/phas_uwsgi.ini /etc/uwsgi/apps-enabled/phas01_uwsgi.ini

# Configure Supervisor
COPY docker/dzi_node/supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Entrypoint
CMD "supervisord"

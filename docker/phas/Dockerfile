FROM python:3.12
RUN apt-get update
RUN apt-get install -y build-essential wget

# Install NGINX
#RUN apt-get install -y software-properties-common \
# && add-apt-repository ppa:nginx/stable \
# && apt-get install -y nginx \
# && rm /etc/nginx/sites-enabled/default
RUN apt-get install -y nginx && rm /etc/nginx/sites-enabled/default
RUN apt-get install -y supervisor

# Install uwsgi
RUN /usr/local/bin/python -m pip install --upgrade pip
RUN pip install uwsgi && mkdir -p /var/log/uwsgi  

# Install openslide
RUN apt-get install -y libopenslide-dev 

# Switch to user foo
RUN useradd -ms /bin/bash foo && mkdir -p /home/foo && chown foo:foo /home/foo
USER foo:foo
SHELL ["/bin/bash", "-c"] 
RUN mkdir -p /home/foo/phas/uwsgi
WORKDIR /home/foo/phas

# Create a virtual environment
RUN python -m venv .venv

# Copy the app to a location
COPY . histoannot

# Create the environment file
COPY docker/phas/env.sh env.sh
COPY docker/phas/slide_server.sh slide_server.sh

# Install the application from this location with requirements
RUN source env.sh && pip install ./histoannot

# Configure nginx
COPY docker/phas/nginx.conf /etc/nginx/nginx.conf
COPY docker/phas/phas_nginx.conf /etc/nginx/conf.d/

# Configure UWSGI
COPY docker/phas/phas_uwsgi.ini /etc/uwsgi/apps-enabled/phas01_uwsgi.ini

# Configure Supervisor
COPY docker/phas/supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Entrypoint
USER root
CMD "supervisord"

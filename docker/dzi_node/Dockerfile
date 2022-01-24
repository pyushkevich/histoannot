FROM python:3.9
RUN apt-get update -y
RUN apt-get install -y build-essential
RUN apt-get install -y wget cmake ninja-build

# Install NGINX
RUN apt-get install -y software-properties-common \
 && add-apt-repository ppa:nginx/stable \
 && apt-get install -y nginx \
 && rm /etc/nginx/sites-enabled/default

# Install openslide
RUN apt-get install -y libopenslide-dev 

# Base directory 
RUN mkdir /tk
WORKDIR /tk

# Install ITK
RUN wget https://github.com/InsightSoftwareConsortium/ITK/releases/download/v5.2.1/InsightToolkit-5.2.1.tar.gz
RUN tar -zxf InsightToolkit-5.2.1.tar.gz

# Compile ITK
RUN mkdir itk_build && cd itk_build \
 && cmake \
    -G Ninja \
    -DCMAKE_BUILD_TYPE:STRING=Release \
    -DBUILD_TESTING:BOOL=OFF \
    -DBUILD_SHARED_LIBS:BOOL=OFF \
    /tk/InsightToolkit-5.2.1 \
 && ninja

# Downloading gcloud package
RUN wget -q https://dl.google.com/dl/cloudsdk/release/google-cloud-sdk.tar.gz

# Installing the package
RUN mkdir -p /usr/local/gcloud \
  && tar -C /usr/local/gcloud -xf google-cloud-sdk.tar.gz \
  && bash /usr/local/gcloud/google-cloud-sdk/install.sh --quiet 

ENV PATH $PATH:/usr/local/gcloud/google-cloud-sdk/bin

# Configure REDIS and Supervisor
RUN apt-get install -y redis supervisor

# Build App
RUN mkdir /tk/node_dzi
WORKDIR /tk/node_dzi

# Install requirements
COPY histoannot/requirements.txt histoannot/requirements.txt
RUN /usr/local/bin/python -m pip install --upgrade pip
RUN pip install -r histoannot/requirements.txt

# Copy our module
COPY libs/os_affine libs/os_affine

# Compile the python module
RUN mkdir -p libs/os_affine/build \
 && cd libs/os_affine/build \
 && cmake \
    -G Ninja \
    -DITK_DIR:PATH=/tk/itk_build \
    -DCMAKE_BUILD_TYPE:STRING=Release \
    ../src \
 && ninja \
 && ln -s libos_affine.so os_affine.so

# Env for Flask
ENV FLASK_APP "histoannot"
ENV PYTHONPATH $PYTHONPATH:/tk/node_dzi/libs/os_affine/build

# Read the server mode argument
ARG SERVER_MODE=master

# Install uwsgi
RUN pip install uwsgi && mkdir -p /var/log/uwsgi  

# Configure nginx
COPY docker/dzi_node/nginx_master.conf /etc/nginx/nginx.conf
COPY docker/dzi_node/${SERVER_MODE}_nginx.conf /etc/nginx/conf.d/

# Configure worker services
COPY docker/dzi_node/histoannot_uwsgi.ini .

# Configure Supervisor
COPY docker/dzi_node/${SERVER_MODE}_supervisor.conf /etc/supervisor/conf.d/supervisord.conf

# Copy the actual code (at the end of Dockerfile for faster builds)
COPY histoannot histoannot

# Copy the config file
COPY docker/dzi_node/${SERVER_MODE}_config.py instance/config.py

# Entrypoint
CMD "supervisord"

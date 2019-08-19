FROM ubuntu:latest
RUN apt-get update -y
RUN apt-get install -y python-pip python-dev build-essential
RUN apt-get install -y wget cmake ninja-build

# Install NGINX
RUN apt-get install -y software-properties-common \
 && add-apt-repository ppa:nginx/stable \
 && apt-get install -y nginx \
 && rm /etc/nginx/sites-enabled/default

# Install openslide
RUN apt-get install -y libopenslide-dev python-openslide

# Base directory 
RUN mkdir /tk
WORKDIR /tk

# Install ITK
RUN wget https://sourceforge.net/projects/itk/files/itk/4.12/InsightToolkit-4.12.2.tar.gz 
RUN tar -zxf InsightToolkit-4.12.2.tar.gz

# Compile ITK
RUN mkdir itk_build && cd itk_build \
 && cmake \
    -G Ninja \
    -DCMAKE_BUILD_TYPE:STRING=Release \
    -DBUILD_TESTING:BOOL=OFF \
    -DBUILD_SHARED_LIBS:BOOL=OFF \
    /tk/InsightToolkit-4.12.2 \
 && ninja

# Downloading gcloud package
RUN wget -q https://dl.google.com/dl/cloudsdk/release/google-cloud-sdk.tar.gz

# Installing the package
RUN mkdir -p /usr/local/gcloud \
  && tar -C /usr/local/gcloud -xf google-cloud-sdk.tar.gz \
  && bash /usr/local/gcloud/google-cloud-sdk/install.sh --quiet 

ENV PATH $PATH:/usr/local/gcloud/google-cloud-sdk/bin

# Copy our module
RUN mkdir /tk/node_dzi
WORKDIR /tk/node_dzi
COPY . /tk/node_dzi/

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

# Install requirements
RUN pip install -r histoannot/requirements.txt

# Env for Flask
ENV FLASK_APP "histoannot:create_mini_app()"
ENV PYTHONPATH $PYTHONPATH:/tk/node_dzi/libs/os_affine/build
ENV PATH $PATH:/root/.local/bin

# Install nginx
USER root

# Install uwsgi
RUN pip install uwsgi && mkdir -p /var/log/uwsgi  

# Configure nginx
RUN ln -s /tk/node_dzi/docker/dzi_node/histoannot_nginx.conf /etc/nginx/conf.d/

# Copy the runme script
RUN cp docker/dzi_node/runme.sh .

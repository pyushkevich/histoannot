FROM ubuntu:latest
RUN apt-get update -y
RUN apt-get install -y python-pip python-dev build-essential
RUN apt-get install -y wget cmake ninja-build

# Create user to run as not root
RUN useradd -ms /bin/bash user
RUN mkdir /tk && chown user:user /tk
USER user
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

# Install openslide
USER root
RUN apt-get install -y libopenslide-dev python-openslide

# Downloading gcloud package
RUN wget -q https://dl.google.com/dl/cloudsdk/release/google-cloud-sdk.tar.gz

# Installing the package
RUN mkdir -p /usr/local/gcloud \
  && tar -C /usr/local/gcloud -xf google-cloud-sdk.tar.gz \
  && bash /usr/local/gcloud/google-cloud-sdk/install.sh --quiet 

ENV PATH $PATH:/usr/local/gcloud/google-cloud-sdk/bin

# Copy our module
RUN mkdir /tk/node_dzi && chown user:user /tk/node_dzi
WORKDIR /tk/node_dzi
COPY --chown=user . /tk/node_dzi/

# Compile the python module
USER user
RUN mkdir -p libs/os_affine/build \
 && cd libs/os_affine/build \
 && cmake \
    -G Ninja \
    -DITK_DIR:PATH=/tk/itk_build \
    ../src \
 && ninja \
 && ln -s libos_affine.so os_affine.so

# Install requirements
RUN pip install -r histoannot/requirements.txt

# Env for Flask
ENV FLASK_APP "histoannot:create_mini_app()"
ENV PYTHONPATH $PYTHONPATH:/tk/node_dzi/libs/os_affine/build
ENV PATH $PATH:/home/user/.local/bin

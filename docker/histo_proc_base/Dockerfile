# Need CUDA!
FROM pyushkevich/itksnap:v3.8.0-beta

# Some packages
RUN apt-get update
RUN apt-get install -y wget gcc openslide-tools python2.7
RUN apt-get install -y build-essential pkg-config glib2.0-dev
RUN apt-get install -y libexpat1-dev libtiff5-dev libjpeg-dev libgsf-1-dev
RUN apt-get install -y libopenslide-dev

# Downloading gcloud package
RUN curl https://dl.google.com/dl/cloudsdk/release/google-cloud-sdk.tar.gz > /tmp/google-cloud-sdk.tar.gz

# Installing the package
RUN mkdir -p /usr/local/gcloud \
  && tar -C /usr/local/gcloud -xvf /tmp/google-cloud-sdk.tar.gz \
  && bash /usr/local/gcloud/google-cloud-sdk/install.sh --quiet

# Install VIPS
ARG VIPS_VERSION=8.8.0
ARG VIPS_URL=https://github.com/libvips/libvips/releases/download
RUN wget ${VIPS_URL}/v${VIPS_VERSION}/vips-${VIPS_VERSION}.tar.gz \
  && tar xf vips-${VIPS_VERSION}.tar.gz \
  && cd vips-${VIPS_VERSION} \
  && ./configure \
  && make V=0 \
  && make install

# Install ImageMagick
RUN apt-get update && apt-get install -y imagemagick

# Install OpenSlide for Python
RUN pip install --upgrade pip
RUN pip install openslide-python

# Adding the package path to local
ENV PATH $PATH:/usr/local/gcloud/google-cloud-sdk/bin
ENV LD_LIBRARY_PATH /usr/local/lib:$LD_LIBRARY_PATH

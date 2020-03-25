#
#   PICSL Histology Annotator
#   Copyright (C) 2019 Paul A. Yushkevich
#
#   This program is free software: you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation, either version 3 of the License, or
#   (at your option) any later version.
#
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
import urlparse
import os
import threading
from google.cloud import storage
import StringIO

# This class handles remote URLs for Google cloud. The remote URLs must have format
# "gs://bucket/path/to/blob.ext"

class GCSHandler:

    _client = None  # type: storage.Client

    # Constructor
    def __init__(self):
        self._bucket_cache = {}
        self._blob_cache = {}

    # Get client (on demand)
    def get_client(self):
        if self._client is None:
            self._client = storage.Client()
        return self._client

    # Process a URL
    def _get_blob(self, uri):

        # Check the cache
        if uri in self._blob_cache:
            return self._blob_cache[uri]

        # Unpack the URL
        o = urlparse.urlparse(uri)

        # Make sure that it includes gs
        if o.scheme != "gs":
            raise ValueError('URL should have schema "gs"')

        # Find the bucket, if not found add it to the cache
        if o.netloc in self._bucket_cache:
            bucket = self._bucket_cache[o.netloc]
        else:
            bucket = self.get_client().get_bucket(o.netloc)
            self._bucket_cache[o.netloc] = bucket

        # Place the blob in the cache
        blob = bucket.get_blob(o.path.strip('/'))
        self._blob_cache[uri] = blob

        # Get the blob in the bucket
        return blob

    # Check if a URL refers to an existing file
    def exists(self, uri):
        return self._get_blob(uri) is not None

    # Get the MD5 hash
    def get_md5hash(self, uri):
        return self._get_blob(uri).md5_hash

    # Download a remote resource locally
    def download(self, uri, local_file):

        # Make sure the path containing local_file exists
        dir_path = os.path.dirname(local_file)
        if not os.path.exists(dir_path):
            os.makedirs(dir_path)

        # Perform the download
        blob = self._get_blob(uri)
        with open(local_file, "wb") as file_obj:
            worker = threading.Thread(target=self.get_client().download_blob_to_file, args=(blob, file_obj))
            worker.start()
            while worker.isAlive():
                worker.join(1.0)
                print('GCS: downloaded: %d of %s' % (os.stat(local_file).st_size, uri))

    # Download a text file directory to memory
    def download_text_file(self, uri):
        blob = self._get_blob(uri)
        sfile = StringIO.StringIO()
        self.get_client().download_blob_to_file(blob, sfile)
        return sfile.getvalue()

    # Get the remote download size
    def get_size(self, uri):
        return self._get_blob(uri).size



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
from urllib.parse import urlparse

from .project_ref import ProjectRef
from .db import get_db
import os
import json
from flask import g, current_app




# This class is used to describe a histology slide and associated data that resides in
# a remote (cloud-based) location and may be cached locally
class SlideRef:

    # Project reference
    _proj = None  # type: ProjectRef

    # Initialize a slide reference with a remote URL.
    # slide_info is a dict with fields specimen, block, slide_name, slide_ext
    def __init__(self, project, specimen, block, slide_name, slide_ext):
        """
        Slide reference constructor

        Args:
            project(ProjectRef): project object
            specimen(str): private name of the specimen
            block(str): name of the block
            name(str): name/ID of the slide (must be unique)
            ext(str): extension of the slide
        """

        # Find the project configuration
        self._proj = project

        # Organize the slide identifiers into a dictionary
        self._d = {
            "specimen" : specimen,
            "block" : block,
            "slide_name": slide_name,
            "slide_ext" : slide_ext
        }

    def __str__(self):
        return "SlideRef (project:{}, dict:{})".format(self._proj, self._d)

    # Access the slide properties
    @property
    def specimen(self):
        return self._d['specimen']

    @property
    def block(self):
        return self._d['block']

    @property
    def slide_name(self):
        return self._d['slide_name']

    @property
    def slide_ext(self):
        return self._d['slide_ext']

    # Get the dictionary
    def get_dict(self):
        return self._d

    # Get a tuple identifying the slide
    def get_id_tuple(self):
        return (self._proj.name,
                self._d["specimen"], self._d["block"], self._d["slide_name"], self._d["slide_ext"])

    # Generate the filename for the resource (local or remote)
    def get_resource_url(self, resource, local = True):
        return self._proj.get_resource_url(resource, self._d, local)

    # Check whether a resource exists (locally or remotely)
    def resource_exists(self, resource, local = True):
        return self._proj.resource_exists(resource, self._d, local)

    # Get a list of available overlays
    def get_available_overlays(self, local = True):
        return self._proj.get_available_overlays(self._d, local)

    # Get a local copy of the resource, copying it if necessary
    def get_local_copy(self, resource, check_hash=False, dry_run=False):
        return self._proj.get_local_copy(resource, self._d, check_hash, dry_run)

    # Get the download progress (fraction of local file size to remote)
    def get_download_progress(self, resource):
        return self._proj.get_download_progress(resource, self._d)

    # Get the project of this slide ref
    def get_project_ref(self):
        return self._proj

    # Get the metadata for this slide
    def get_metadata(self):
        metadata_fn = self.get_local_copy('metadata')
        if metadata_fn is not None:
            with open(metadata_fn, 'r') as metadata_fd:
                try:
                    return json.load(metadata_fd)
                except json.JSONDecodeError:
                    print('Failed to read JSON from ' + metadata_fn)
        return None

    # Get the spacing of the slide
    def get_pixel_spacing(self, resolution):
        md = self.get_metadata()
        if md and 'spacing' in md:
            spacing = md['spacing']
            if resolution == 'x16':
                spacing = [ 16.0 * x for x in spacing ]
            return spacing
        return None

    # Get dimensions
    def get_dims(self):
        md = self.get_metadata()
        if md and 'dimensions' in md and len(md['dimensions']) == 2:
            return md['dimensions']
        else:
            return None


# Get a slide ref by database ID
def get_slide_ref(slice_id, project=None):
    """
    Create a slide reference from a database slide ID
    :param slice_id: database ID of a slide
    :type slide_id: int
    :param project: optional project reference to associate with
    :type project: ProjectRef
    :return:
    """
    db = get_db()

    # Load slide from database
    row = db.execute('SELECT * from slide_info WHERE id = ?', (slice_id,)).fetchone()

    # Handle missing data
    if row is None:
        return None

    # Create a project reference
    if project is None:
        project = ProjectRef(row['project'])

    # Create a slide reference
    return SlideRef(project, row['specimen_private'], row['block_name'], row['slide_name'], row['slide_ext'])



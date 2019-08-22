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
import os
from flask import Flask, request
from flask_pure import Pure
from . import cache
from . import db
from . import auth
from . import slide
from . import dltrain
from . import dzi
from . import delegate

# The default naming schema for slide objects
_default_histo_url_schema = {
    # The filename patterns
    "pattern" : {
        "raw" :        "{baseurl}/{specimen}/histo_raw/{slide_name}.{slide_ext}",
        "x16" :        "{baseurl}/{specimen}/histo_proc/{slide_name}/preproc/{slide_name}_x16.png",
        "affine" :     "{baseurl}/{specimen}/histo_proc/{slide_name}/recon/{slide_name}_recon_iter10_affine.mat",
        "thumb" :      "{baseurl}/{specimen}/histo_proc/{slide_name}/preproc/{slide_name}_thumbnail.tiff"
    },

    # The maximum number of bytes that may be cached locally for 'raw' data
    "cache_capacity" : 32 * 1024 ** 3
}

# Needed for AJAX redirects to DZI nodes
def _add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    if request.method == 'OPTIONS':
        response.headers['Access-Control-Allow-Methods'] = 'DELETE, GET, POST, PUT'
        headers = request.headers.get('Access-Control-Request-Headers')
        if headers:
            response.headers['Access-Control-Allow-Headers'] = headers
    return response

# Common configuration code
def create_app(test_config = None):

    # create and configure the app
    app = Flask(__name__, instance_relative_config=True)

    # Handle configurtion
    app.config['SECRET_KEY'] = 'dev'
    app.config['HISTOANNOT_URL_SCHEMA'] = _default_histo_url_schema
    app.config['HISTOANNOT_DELEGATE_DZI'] = False

    if test_config is None:
        # load the instance config, if it exists, when not testing
        app.config.from_pyfile('config.py', silent=True)
    else:
        # load the test config if passed in
        app.config.from_mapping(test_config)

    # ensure the instance folder exists
    try:
        os.makedirs(app.instance_path)
    except OSError:
        pass

    # Dump config
    if 'HISTOANNOT_URL_BASE' not in app.config:
        raise ValueError('Missing HISTOANNOT_URL_BASE in config')

    # DZI blueprint used in every mode
    app.register_blueprint(dzi.bp)
    dzi.init_app(app)

    # Server mode determines what we do next
    if app.config['HISTOANNOT_SERVER_MODE'] == "master":

        # Configure database
        app.config['DATABASE'] = os.path.join(app.instance_path, 'histoannot.sqlite')

        # Database connection
        db.init_app(app)

        # Auth commands
        auth.init_app(app)

        # Auth blueprint
        app.register_blueprint(auth.bp)

        # Slide blueprint
        app.register_blueprint(slide.bp)

	# Delegation blueprint
	app.register_blueprint(delegate.bp)
	delegate.init_app(app)

        # DLTrain blueprint
        app.register_blueprint(dltrain.bp);
        # Pure CSS
        app.config['PURECSS_RESPONSIVE_GRIDS'] = True
        app.config['PURECSS_USE_CDN'] = True
        app.config['PURECSS_USE_MINIFIED'] = True
        Pure(app)

        app.add_url_rule('/', endpoint='index')

    # Supporting 'dzi' node (serves images/tiles but no database)
    elif app.config['HISTOANNOT_SERVER_MODE'] == "dzi_node":

        # a simple page that says hello. This is needed for load balancers
        @app.route('/')
        def hello():
            return 'HISTOANNOT DZI NODE'

        # Allow CORS headers
	app.after_request(_add_cors_headers)

    else:
        raise ValueError('Missing or unknown HISTOANNOT_SERVER_MODE')


    return app


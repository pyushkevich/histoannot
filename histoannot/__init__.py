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
from flask import Flask, request, g
from flask_pure import Pure
from . import cache
from . import db
from . import auth
from . import slide
from . import dltrain
from . import dzi
from . import delegate
from . import project_ref
from . import project_cli
from glob import glob
from histoannot.project_ref import RemoteResourceCache, ProjectRef
from socket import gethostname

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

    # ensure the instance folder exists
    try:
        os.makedirs(app.instance_path)
    except OSError:
        pass

    # Handle configurtion
    app.config['SECRET_KEY'] = 'dev'
    app.config['HISTOANNOT_DELEGATE_DZI'] = False

    # Descriptive keys
    app.config['HISTOANNOT_PUBLIC_NAME'] = 'PICSL Histology Annotation System'

    # Read the config file
    if test_config is None:
        # load the instance config, if it exists, when not testing
        app.config.from_pyfile('config.py', silent=True)
    else:
        # load the test config if passed in
        app.config.from_mapping(test_config)


    # SERVER_NAME in Flask is a mess. It should only be set for dzi_node 
    # (worker) instances, but avoid it for master instances, because it will
    # cause havoc with requests that refer to the server differently (by IP, etc)
    if not app.config.get('SERVER_NAME'):
        if app.config['HISTOANNOT_SERVER_MODE'] == 'dzi_node':
            # When server name is missing and we are on a worker node, this is
            # a problem. It is also a problem if we are running in command-line mode
            app.config['SERVER_NAME'] = gethostname()

    # DZI blueprint used in every mode
    app.register_blueprint(dzi.bp)
    dzi.init_app(app)

    # Set the default queues
    app.config['PRELOAD_QUEUE'] = "%s_preload" % (app.config.get('HISTOANNOT_REDIS_PREFIX',''),)

    # Server mode determines what we do next
    if app.config['HISTOANNOT_SERVER_MODE'] == "master":

        # Configure database
        app.config['DATABASE'] = os.path.join(app.instance_path, 'histoannot.sqlite')

        # Database connection
        db.init_app(app)

        # Auth commands
        auth.init_app(app)

        # Project CLI commands
        project_cli.init_app(app)

        # Auth blueprint
        app.register_blueprint(auth.bp)

        # Slide blueprint
        app.register_blueprint(slide.bp)
        slide.init_app(app)

        # Delegation blueprint
        app.register_blueprint(delegate.bp)
        delegate.init_app(app)

        # DLTrain blueprint
        app.register_blueprint(dltrain.bp)
        dltrain.init_app(app)

        # Pure CSS
        app.config['PURECSS_RESPONSIVE_GRIDS'] = True
        app.config['PURECSS_USE_CDN'] = True
        app.config['PURECSS_USE_MINIFIED'] = True
        Pure(app)

        app.add_url_rule('/', endpoint='index')

        # Enable hello world
        @app.route('/hello')
        def hello():
            return 'HISTOANNOT MASTER'

    # Supporting 'dzi' node (serves images/tiles but no database)
    elif app.config['HISTOANNOT_SERVER_MODE'] == "dzi_node":

        # A master must be configured
        if 'HISTOANNOT_MASTER_URL' not in app.config:
            raise ValueError('Missing HISTOANNOT_MASTER_URL in config')

        # a simple page that says hello. This is needed for load balancers
        @app.route('/')
        def hello():
            return 'HISTOANNOT DZI NODE'

        # Allow CORS headers
        app.after_request(_add_cors_headers)

    else:
        raise ValueError('Missing or unknown HISTOANNOT_SERVER_MODE')


    return app


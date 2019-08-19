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
from flask import Flask
from flask_pure import Pure
from . import cache
from . import db
from . import auth
from . import slide
from . import dltrain
from . import dzi

def create_app(test_config=None):

    # create and configure the app
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY='dev',
        DATABASE=os.path.join(app.instance_path, 'histoannot.sqlite'),
    )

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

    # a simple page that says hello
    @app.route('/hello')
    def hello():
        return 'Hello, World!'

    # Database connection
    db.init_app(app)

    # Auth commands
    auth.init_app(app)

    # Auth blueprint
    app.register_blueprint(auth.bp)

    # Slide blueprint
    app.register_blueprint(slide.bp)
    app.add_url_rule('/', endpoint='index')

    # DLTrain blueprint
    app.register_blueprint(dltrain.bp);

    # Initialize the image cache
    config_map = {
        'DEEPZOOM_TILE_SIZE': 254,
        'DEEPZOOM_OVERLAP': 1,
        'DEEPZOOM_LIMIT_BOUNDS': True
    }
    slide.bp.cache = cache.DeepZoomSource(2000, 5, config_map)

    # Pure CSS
    app.config['PURECSS_RESPONSIVE_GRIDS'] = True
    app.config['PURECSS_USE_CDN'] = True
    app.config['PURECSS_USE_MINIFIED'] = True
    Pure(app)

    return app


def create_mini_app(test_config=None):

    # create and configure the app
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY='dev',
    )
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

    # DZI blueprint
    app.register_blueprint(dzi.bp)

    # a simple page that says hello
    @app.route('/')
    def hello():
        return 'HISTOANNOT DZI NODE'

    # Initialize the image cache
    config_map = {
        'DEEPZOOM_TILE_SIZE': 254,
        'DEEPZOOM_OVERLAP': 1,
        'DEEPZOOM_LIMIT_BOUNDS': True
    }
    dzi.bp.cache = cache.DeepZoomSource(2000, 5, config_map)

    return app

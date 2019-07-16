import os
from flask import Flask
from flask_pure import Pure
from . import cache
from . import db
from . import auth
from . import slide

def create_app(test_config=None):

    # create and configure the app
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY='dev',
        DATABASE=os.path.join(app.instance_path, 'flaskr.sqlite'),
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

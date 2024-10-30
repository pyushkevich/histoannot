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
from flask import Flask, request, current_app
from flask_pure import Pure
from . import db
from . import auth
from . import slide
from . import dltrain
from . import dzi
from . import delegate
from . import project_cli
from . import admin
import click
from flask.cli import with_appcontext


# Common configuration code
def create_app(test_config = None):
    
    # Allow instance path to be set via environment variable
    instance_path = os.getenv('FLASK_INSTANCE_PATH', None)

    # create and configure the app
    app = Flask(__name__, instance_path=instance_path, instance_relative_config=True)

    # Handle configuration
    app.config.from_object('phas.default_settings')
    if test_config is None:
        app.config.from_pyfile('config.py', silent=True)
    else:
        app.config.from_mapping(test_config)

    # ensure the instance folder exists
    try:
        os.makedirs(app.instance_path)
    except OSError:
        pass

    # DZI blueprint used in every mode
    app.register_blueprint(dzi.bp)
    dzi.init_app(app)

    # Configure database
    app.config['DATABASE'] = os.path.join(app.instance_path, 'phas.sqlite')
    
    # Configure the openslide server
    n_proc = app.config.get('SLIDE_SERVER_NUMPROC',8)
    app.config['SLIDE_SERVER_ADDR'] = [
        os.path.join(app.instance_path, 'oslserver', f'oslserver_{i:02d}.sock') for i in range(n_proc) ]
    app.config['SLIDE_SERVER_CACHE_PAGE_SIZE_MB'] = app.config.get('SLIDE_SERVER_CACHE_PAGE_SIZE_MB', 1)
    app.config['SLIDE_SERVER_CACHE_SIZE_IN_PAGES'] = app.config.get('SLIDE_SERVER_CACHE_SIZE_IN_PAGES', 2048)

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

    # Admin bluepring
    app.register_blueprint(admin.bp)

    # Pure CSS
    Pure(app)

    app.add_url_rule('/', endpoint='index')
    
    # Add the info command
    app.cli.add_command(flask_info)

    return app

@click.command('info')
@with_appcontext
def flask_info():
    print(f'FLASK application {__name__}')
    print(f'  Public name:               {current_app.config["HISTOANNOT_PUBLIC_NAME"]}')
    print(f'  Instance directory:        {current_app.instance_path}')
    print(f'  Static folder path:        {current_app.static_folder}')
    print(f'  Config options:')
    for key, value in current_app.config.items():
        print(f'    {key}: {value}')
    print(f'  Database tables:')
    mydb = db.get_db()
    rc = mydb.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
    for row in rc:
        print(f'    {row[0]}')
        
        



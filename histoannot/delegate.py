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
from flask import Blueprint, Flask, request
import click
import time
from flask.cli import with_appcontext
import urllib
import urllib2
from histoannot.db import get_db

# Create blueprint
bp = Blueprint('delegate', __name__)

# Handle pings

@bp.route('/delegate/ping', methods=('POST','GET'))
def ping():
    db=get_db()
    if 'url' in request.form and 'cpu_percent' in request.form:
        db.execute('INSERT OR REPLACE INTO dzi_node(url,t_ping,cpu_percent) VALUES(?,?,?)',
                (request.form['url'], time.time(), request.form['cpu_percent']))
        db.commit()
    return "ok"


# -------------------------------------------------
# DELEGATION URLS
# -------------------------------------------------
@click.command('delegates-list')
@with_appcontext
def delegate_dzi_list_command():
    """List all active DZI delegates"""

    # Arbitrary cutoff of 2 minutes
    t_test = time.time() - 120
    db = get_db()
    rc = db.execute('SELECT * FROM dzi_node WHERE t_ping > ?', (t_test,)).fetchall()
    for row in rc:
        print('%s  %d %f' % (row['url'], time.time() - row['t_ping'], row['cpu_percent']))

def init_app(app):
    app.cli.add_command(delegate_dzi_list_command)


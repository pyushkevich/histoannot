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
from flask import Blueprint, request, current_app
import click
import time
from flask.cli import with_appcontext
import urllib
from .db import get_db

# Create blueprint
bp = Blueprint('delegate', __name__)

# Handle pings

@bp.route('/delegate/ping', methods=('POST','GET'))
def ping():
    print("GETTING PINGED")
    print(request.form)
    db=get_db()
    if 'url' in request.form and 'cpu_percent' in request.form:
        db.execute('INSERT OR REPLACE INTO dzi_node(url,t_ping,cpu_percent) VALUES(?,?,?)',
                (request.form['url'], time.time(), request.form['cpu_percent']))
        db.commit()
    return "ok"


# Check if a node is alive
def check_dzi_node_alive(url, timeout=5):

    try:
        # Try to open the URL
        resp = urllib.request.urlopen(url, timeout=timeout).read()

        # Check against expected string
        if resp == b'HISTOANNOT DZI NODE':
            print('check_dzi_node_alive (%s): Success' % url)
            return True

    except urllib.error.URLError as e:
        print(e)

    # If we are here, the check failed.
    print('check_dzi_node_alive (%s): Failure' % url)
    return False


# Find a delegate to handle a slide. Returns URL of a verified live delegate or
# None if delegation is disabled or there are no live delegates
def find_delegate_for_slide(slide_id=None, project=None, slide_name=None,
                            ping_timeout=120, check_alive=False):

    # Are we delegating DZI service to separate nodes?
    if current_app.config.get('HISTOANNOT_DELEGATE_DZI', False) is False:
        return None

    # Initialize return value to None
    del_url = None

    # We must have heard from a delegate after this time
    t_test = time.time() - ping_timeout

    # Need database
    db=get_db()

    # If missing slide_id, we need to get it
    if slide_id is None:
        rc = db.execute('SELECT id FROM slide_info WHERE project=? AND slide_name=?',
                        (project, slide_name)).fetchone()
        slide_id=rc['id']

    # Check if the slide is already being delegated
    rc = db.execute(
            "SELECT DN.* FROM slide_dzi_node SDN "
            "LEFT JOIN dzi_node DN on SDN.url = DN.url "
            "WHERE SDN.slide_id=? AND DN.t_ping > ?", (slide_id,t_test)).fetchone()

    # Check if delegation node is present
    del_url = rc['url'] if rc is not None else None

    # Check if the delegation node is alive
    if del_url is None or (check_alive is True and not check_dzi_node_alive(del_url)):

        # Need to assign a new url to the slide
        rc_all = db.execute(
                "SELECT * FROM dzi_node WHERE t_ping > ? "
                "ORDER BY RANDOM()", (t_test,)).fetchall()

        for row in rc_all:
            if check_dzi_node_alive(row['url']):
                del_url = row['url']
                break

    # If we found a delegate, store it with the node
    if del_url is not None:
        print('DELEGATING slide %d to %s' % (slide_id, del_url))
        db.execute("REPLACE INTO slide_dzi_node(url,slide_id) VALUES (?,?)",
                   (del_url, slide_id))
    else:
        print('NOT DELEGATING slide %d', (slide_id, del_url))
        db.execute("DELETE FROM slide_dzi_node WHERE slide_id=?", (slide_id,));

    db.commit()

    # Return the URL
    return del_url


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


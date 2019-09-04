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
import functools

from flask import (
    Blueprint, flash, g, redirect, render_template, request, session, url_for, current_app
)
from flask.cli import with_appcontext
from werkzeug.security import check_password_hash, generate_password_hash

from histoannot.db import get_db
import os
import click
import hashlib
import getpass

bp = Blueprint('auth', __name__, url_prefix='/auth')


@bp.route('/register', methods=('GET', 'POST'))
def register():
    error = None
    print("WTF ", request.form)
    if request.method == 'POST':

        if session.get('is_invited', False) is False:

            # Handle the invitation portion
            syspass = request.form['syspass']
            if not syspass:
                error = 'Invitation code is required'
            else:
                # Read system password 
                passfile=os.path.join(current_app.instance_path,'password.txt')
                target=''
                with open(passfile, 'r') as infile:  
                    target=infile.read().replace('\n','')
                    if target != hashlib.md5(syspass.encode()).hexdigest():
                        error = 'Invalid invitation code'

            if error is None:
                session['is_invited'] = True

        else:
            print('HERE ', session.get('is_invited', False))

            # Invited user, get the registration info
            username = request.form['username']
            password = request.form['password']

            db = get_db()
            error = None

            if not username:
                error = 'Username is required.'
            elif not password:
                error = 'Password is required.'
            else:
                rc = db.execute('SELECT username FROM user WHERE username=?',(username,))
                if rc.fetchone() is not None:
                    error = 'Username "%s" is already taken' % (username,)
                else:
                    # Process user
                    db.execute(
                        'INSERT INTO user (username, password) VALUES (?, ?)',
                        (username, generate_password_hash(password))
                    )
                    db.commit()

                    # Take user to main page
                    user = db.execute('SELECT * FROM user WHERE username = ?', (username,)).fetchone()
                    session.clear()
                    session['user_id'] = user['id']
                    return redirect(url_for('index'))

    else:
        session.clear()

    if error is not None:
        flash(error)

    return render_template('auth/register.html', invited=session.get('is_invited', False))


def set_system_password():
    syspass=getpass.getpass("System password:")
    passfile=os.path.join(current_app.instance_path,'password.txt')
    with open(passfile, 'w') as outfile:
        outfile.write(hashlib.md5(syspass.encode()).hexdigest());


@bp.route('/login', methods=('GET', 'POST'))
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        db = get_db()
        error = None
        user = db.execute(
            'SELECT * FROM user WHERE username = ?', (username,)
        ).fetchone()

        if user is None:
            error = 'Incorrect username.'
        elif not check_password_hash(user['password'], password):
            error = 'Incorrect password.'

        if error is None:
            session.clear()
            session['user_id'] = user['id']
            return redirect(url_for('index'))

        flash(error)

    return render_template('auth/login.html')

@bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# Run before every page to know who the user is
@bp.before_app_request
def load_logged_in_user():
    user_id = session.get('user_id')

    if user_id is None:
        g.user = None
    else:
        g.user = get_db().execute(
            'SELECT * FROM user WHERE id = ?', (user_id,)
        ).fetchone()


# Decorator to require login for all views
def login_required(view):
    @functools.wraps(view)
    def wrapped_view(**kwargs):
        if g.user is None:
            return redirect(url_for('auth.login'))

        return view(**kwargs)

    return wrapped_view


@click.command('passwd-set')
@with_appcontext
def passwd_set_command():
    """Set the system password for new user registration"""
    set_system_password()
    click.echo('Updated the system password')


def init_app(app):
    app.cli.add_command(passwd_set_command)

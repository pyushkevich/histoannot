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
import time

from flask import (
    Blueprint, flash, abort, g, redirect, render_template, request, session, url_for, current_app, make_response, jsonify
)
from flask.cli import with_appcontext
from werkzeug.security import check_password_hash, generate_password_hash
from flask_mail import Message, Mail

from .db import get_db
from .common import AccessLevel

import click
import uuid
import json
import pandas

bp = Blueprint('auth', __name__, url_prefix='/auth')


# Interval for caching user data in the session (in seconds)
_USER_CACHE_TTL = 300 # 5 minutes

# Landing page
@bp.route('/login', methods=('GET', 'POST'))
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['passwd']
        db = get_db()
        error = None
        user = db.execute(
            'SELECT * FROM user WHERE username = ?', (username,)
        ).fetchone()

        if user is None:
            error = 'Incorrect username or password.'
        elif user['disabled'] > 0:
            error = 'User account is disabled.'
        elif user['password'] is None:
            error = 'You have not created a password yet. Please reset your password.'
        elif not check_password_hash(user['password'], password):
            error = 'Incorrect username or password.'

        if error is None:
            session.clear()
            session['user_id'] = user['id']
            session['user_is_site_admin'] = user['site_admin']
            session['user_api_key'] = False
            return redirect(url_for('index'))

        flash(error)

    return render_template('auth/login.html')


@bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))


# A decorator to cache function results in the session
def cache_in_session(fetch_function):
    def wrapper(*args, **kwargs):
        # A key must be provided to identify the cached data
        key = kwargs.get('key', None)
        if key is None:
            raise ValueError('A cache key must be provided to cache_in_session decorator')
        
        # Check the session cache first
        user_data, timestamp = session.get(key, (None, None))
        
        # If the user data are missing or expired, fetch from DB
        if user_data is None or timestamp is None or (time.time() - timestamp) > _USER_CACHE_TTL:
            user_data = fetch_function(*args, **kwargs)
            session[key] = (user_data, time.time())
        
        return user_data
    return wrapper
        
@cache_in_session
def fetch_user_info(user_id, key):
    row = get_db().execute('SELECT * FROM user WHERE id = ? AND disabled=0', (user_id,)).fetchone()
    if row:
        return dict(row)
    else:
        return None

# Run before every page to know who the user is
@bp.before_app_request
def load_logged_in_user():
    g.in_request = True
    user_id = session.get('user_id')

    if user_id is None:
        g.user = None
    else:
        g.user = fetch_user_info(user_id, key='user_data')
        g.login_via_api_key = session.get('user_api_key', False)
        g.anonymize = session.get('anonymize', False)
        if g.user is not None:
            print(f'User {g.user["username"]} loaded, anonymize={g.anonymize}')


def _login_required(view, site_admin=False):
    @functools.wraps(view)
    def wrapped_view(**kwargs):

        # On the dzi worker node, there is no database, access is managed through
        # nginx server and firewall
        if current_app.config['HISTOANNOT_SERVER_MODE'] == 'dzi_node':
            return view(**kwargs)

        # The user must exist, if not redirect to the login page
        if g.user is None:
            current_app.logger.warning('Unauthorized access to %s' % request.url if request is not None else None)
            return redirect(url_for('auth.login'))

        # The user must be an administrator, eslse send a not found page
        if site_admin is True and g.user['site_admin'] < 1:
            print(g.user['site_admin'])
            current_app.logger.warning('Unauthorized admin access to %s by user %s' %
                                       (request.url if request is not None else None, g.user['username']))
            return abort(404)

        # The user should have a completed profile
        if g.user['email'] is None:
            return redirect(url_for('auth.edit_user_profile'))

        return view(**kwargs)

    return wrapped_view


# Decorator to require login for all views
def login_required(view):
    return _login_required(view, False)

# Decorator to require login for all views
def site_admin_access_required(view):
    return _login_required(view, True)

# Load project access level (cached in session)
@cache_in_session
def fetch_project_access(user_id, project, key):
    db = get_db()
    rc = db.execute('SELECT * FROM project_access WHERE user=? AND project=?',
                    (user_id, project)).fetchone()
    print(f'Fetched project access for user {user_id} project {project}: {rc}')
    return dict(rc) if rc is not None else None

def _project_access_required(view, min_access_level, api_access_required=False):
    @functools.wraps(view)
    @login_required
    def wrapped_view(**kwargs):
        # On the dzi worker node, there is no database, access is managed through
        # nginx server and firewall
        if current_app.config['HISTOANNOT_SERVER_MODE'] == 'dzi_node':
            return view(**kwargs)

        # The project keyword must exist
        if 'project' not in kwargs:
            abort(404, "Project not specified")

        project = kwargs['project']
        pa = fetch_project_access(g.user['id'], project, key=f'project_access_{project}')

        error = None
        if pa is None:
            error = "No privileges on parent project"
        elif AccessLevel.check_access(pa['access'], min_access_level) is False:
            error = "Insufficient privileges on parent project"
        elif api_access_required and pa['api_permission'] < 1:
            error = "Missing API access permission"
        if error is not None:
            url = request.url if request is not None else None
            current_app.logger.warning(f'Unauthorized {min_access_level} access to project {project} at URL {url}: {error}')
            abort(403, "Insufficient privileges on project {project}")
        else:
            return view(**kwargs)

    return wrapped_view


def access_project_read(view):
    return _project_access_required(view, 'read')

def access_project_write(view):
    return _project_access_required(view, 'write')

def access_project_admin(view):
    return _project_access_required(view, 'admin')

def api_access_project_read(view):
    return _project_access_required(view, 'read', api_access_required=True)

def api_access_project_write(view):
    return _project_access_required(view, 'write', api_access_required=True)

def api_access_project_admin(view):
    return _project_access_required(view, 'admin', api_access_required=True)

# Load task/project access level (cached in session)
@cache_in_session
def fetch_task_access(user_id, task, key):
    db = get_db()
    rc = db.execute('SELECT * FROM task_project_access WHERE user=? AND task=?',
                    (user_id, task)).fetchone()
    print(f'Fetched task access for user {user_id} task {task}: {rc}')
    return dict(rc) if rc is not None else None

def _task_access_required(view, min_access_level, api_access_required=False):
    @functools.wraps(view)
    @login_required
    def wrapped_view(**kwargs):

        # The project keyword must exist
        if 'task_id' not in kwargs:
            abort(404, "Task ID not specified")

        task_id = kwargs['task_id']
        tpa = fetch_task_access(g.user['id'], task_id, key=f'task_access_{task_id}')

        error = None
        if tpa is None:
            error = "No privileges on parent project"
        elif AccessLevel.check_access(tpa['project_access'], min_access_level) is False:
            error = "Insufficient privileges on parent project"
        elif api_access_required and tpa['api_permission'] < 1:
            error = "Missing API access permission"
        elif tpa['restrict_access'] > 0 and AccessLevel.check_access(tpa['task_access'], min_access_level) is False:
            error = "Insufficient privileges on task"

        if error is not None:
            url = request.url if request is not None else None
            current_app.logger.warning(f'Unauthorized {min_access_level} access to task {task_id} at URL {url}: {error}')
            abort(403, "Insufficient privileges on task {task_id}")

        else:
            return view(**kwargs)

    return wrapped_view

def access_task_read(view):
    return _task_access_required(view, 'read')

def access_task_write(view):
    return _task_access_required(view, 'write')

def access_task_admin(view):
    return _task_access_required(view, 'admin')

def api_access_task_read(view):
    return _task_access_required(view, 'read', api_access_required=True)

def api_access_task_write(view):
    return _task_access_required(view, 'write', api_access_required=True)

def api_access_task_admin(view):
    return _task_access_required(view, 'admin', api_access_required=True)

def get_mail():
    if 'mail' not in g:
        g.mail = Mail(current_app)
    return g.mail


@bp.route('/pwreset', methods=('GET', 'POST'))
def reset():
    if request.method == 'GET':
        return render_template('auth/request_reset.html', email='')

    # Check if the email address is available
    db=get_db()
    error = None

    rq_email = request.form['email']
    rc = db.execute('SELECT * FROM user WHERE email=? AND disabled = 0 COLLATE NOCASE', (rq_email,)).fetchone()

    if rc is None:
        error = "There is no active user with email address %s" % (rq_email,)

    else:
        send_user_resetlink(rc['id'])
        error = "An email with a password reset link has been sent to %s" % (rq_email,)

    flash(error)
    return render_template('auth/request_reset.html', email=rq_email)


def send_user_resetlink(user_id, email=None, expiry=86400):

    if email is None:
        db=get_db()
        rc=db.execute('SELECT * FROM user WHERE id=? AND email IS NOT NULL AND disabled=0', (user_id,)).fetchone()
        if rc is not None:
            email = rc['email']
        else:
            return None

    # Create a password reset link
    url = create_password_reset_link(user_id)

    # Create an email
    mail = get_mail()
    server_name = current_app.config['HISTOANNOT_PUBLIC_NAME']
    msg = Message("Password Reset Link for %s" % (server_name,))
    msg.add_recipient(email)
    msg.html = """
        <p>You have requested a link to reset your password on %s.</p>
        <p>Please follow this link to reset the password for user <b>%s</b>:</p>
        <a href="%s">%s</a>
        """ % (server_name, rc['username'], url, url)

    mail.send(msg)


def send_user_invitation(user_id, url, is_new: bool):
    # Get user record
    db=get_db()
    rc=db.execute('SELECT * FROM user WHERE id=? AND email IS NOT NULL AND disabled=0', (user_id,)).fetchone()
    if rc is not None:
        # Send the email
        mail = get_mail()
        server_name = current_app.config['HISTOANNOT_PUBLIC_NAME']
        if is_new:
            msg = Message("Account created on %s" % (server_name,) )
            msg.html = """
                <p>A new account with username <b>%s</b> was created for you on %s.</p> 
                <p>Please follow the link below to activate your account and create a password:</p>
                <a href="%s">%s</a>""" % (rc['username'], server_name, url, url)
        else:
            msg = Message("Account updated on %s" % (server_name,) )
            msg.html = """
                <p>The account with username <b>%s</b> on %s was updated.</p> 
                <p>Please follow the link below to activate your account and create/reset your password:</p>
                <a href="%s">%s</a>""" % (rc['username'], server_name, url, url)

        msg.add_recipient(rc['email'])
        mail.send(msg)

        print('Password link sent to %s at %s' % (rc['username'],rc['email']))
    else:
        print('User %d does not email or is not active' % (user_id,))


# Check if an email address is present in the database
def email_exists(email, user_id=None):
    db = get_db()

    if user_id is None:
        rc_email = db.execute('SELECT COUNT(id) as n FROM user '
                              'WHERE email=? AND disabled=0 '
                              'COLLATE NOCASE',
                              (email,)).fetchone()
    else:
        rc_email = db.execute('SELECT COUNT(id) as n FROM user '
                              'WHERE email=? AND id <> ? AND disabled=0 '
                              'COLLATE NOCASE',
                              (email, user_id)).fetchone()
    return rc_email['n'] > 0


@bp.route('/pwreset/<resetkey>', methods=('GET', 'POST'))
def reset_password(resetkey):

    # Check the reset key against the database
    db=get_db()
    rc = db.execute('SELECT U.*, t_expires, activated '
                    'FROM user U LEFT JOIN password_reset PR on U.id = PR.user '
                    'WHERE reset_key = ?', (resetkey,)).fetchone()

    # If nothing is found, then this is an invalid key, hacking attempt
    if rc is None:
        abort(403, description="Invalid link")

    # If the link is expired, tell user
    if rc['t_expires'] < time.time():
        abort(404, description="This link has expired. Please request another link.")

    # If the link has been used, tell user
    if rc['activated'] > 0:
        abort(404, description="This link has already been used. Please request another link.")

    if rc['disabled'] > 0:
        abort(404, description="This link is for a non-active user.")

    # Error that will be flashed to the user
    error = None

    # If the request is empty, we need to have the user provide information
    if request.method == 'POST':

        # The user is posting a completed profile. We must now update their password
        rq_password = request.form['password']

        # TODO: perform additional validation outside of JS in case robot was used
        if not rq_password:
            error = 'Password is required.'
        else:
            # TODO: implement user prototypes and invitation codes (create user here)
            # Update user's profile
            db.execute('UPDATE user SET password=? WHERE id=?',
                       (generate_password_hash(rq_password), rc['id']))

            # Disable the activation link
            if resetkey is not None:
                db.execute('UPDATE password_reset SET activated=1 WHERE reset_key=?',
                           (resetkey,))

            db.commit()

            # Update the session, the user is now considered logged in
            session.clear()
            session['user_id'] = rc['id']
            session['user_is_site_admin'] = rc['site_admin']

            # Go to the home page
            return redirect(url_for('index'))

    # Render the page again
    if error is not None:
        flash(error)

    return render_template('auth/reset.html', username=rc['username'])


@bp.route('/profile/edit', methods=('GET', 'POST'))
def edit_user_profile():
    db=get_db()

    # The user must exist (same logic as @login_required)
    if g.user is None:
        return redirect(url_for('auth.login'))

    # Get the user information
    rc = db.execute('SELECT U.* FROM user U where U.id = ? ', (g.user['id'],)).fetchone()

    error = None
    rq_email = None
    if request.method == 'POST':

        # The user is posting a completed profile.
        rq_email = request.form['email']

        if not rq_email:
            error = 'Email address is required.'
        elif email_exists(rq_email, rc['id']):
            # Make sure that we are not duplicating another email address in the
            # system. Every active user must have a unique email address
            error = 'This email address is already in use by another user.'
        else:

            # Update user's profile
            db.execute('UPDATE user SET email=?WHERE id=?', (rq_email, rc['id']))
            db.commit()

            # Go to the home page
            return redirect(url_for('index'))

    else:
        # If the user is missing some part of the profile, flash that
        if rc['email'] is None:
            error = "Please update your email address before proceeding to the site."
            
    # Reset the user cache in the session
    session.pop('user_data', (None, None))

    # Render the page again
    if error is not None:
        flash(error)

    return render_template('auth/edit_profile.html',
                           username=rc['username'],
                           email = rq_email if rq_email is not None else rc['email'])


@bp.route('/api/generate_key', methods=('GET', 'POST'))
@login_required
def generate_api_key():
    """
    Generate a new API key for the user. This invalidates previous API keys.
    The user can login with the API key instead of username/password for limited
    access to the site.
    """

    db = get_db()
    api_key = str(uuid.uuid4())
    db.execute('INSERT INTO user_api_key(api_key,user,t_expires) '
               'VALUES (?,?,?)', (api_key, g.user['id'], time.time() + 365*24*60*60))
    db.commit()

    return json.dumps({"api_key": api_key})


@bp.route('/api/login', methods=('POST',))
def login_with_api_key():
    if request.method == 'POST':
        api_key = request.form['api_key']
        error = None

        db = get_db()
        user = db.execute("""
            SELECT U.*, AK.t_expires 
            FROM user_api_key AK LEFT JOIN user U on AK.user = U.id
            WHERE AK.api_key = ?""", (api_key,)).fetchone()
        
        if user is None:
            error = 'Invalid API key'
        elif user['disabled'] > 0:
            error = 'User account is disabled'
        elif user['t_expires'] < time.time():
            error = 'API key has expired'

        if error is None:
            session.clear()
            session['user_id'] = user['id']
            session['user_is_site_admin'] = user['site_admin']
            session['user_api_key'] = True
            return make_response({"status": "ok"}, 200)

        return make_response({"status": "failed", "error": error}, 401)


@bp.route('/sitemap', methods=('GET',))
@login_required
def list_api_routes():
    links = []
    for rule in current_app.url_map.iter_rules():
        if rule.rule.find('/api/') != -1:
            links.append(rule.rule)
    links.sort()

    return jsonify(links)


@bp.route('/api/anonymize/toggle', methods=('GET',))
def toggle_anonymize():
    anonymize = session.get('anonymize', False)
    session['anonymize'] = not anonymize
    return redirect(request.referrer or url_for('index'))

@bp.route('/api/anonymize', methods=('GET',))
def get_anonymize_setting():
    anonymize = session.get('anonymize', False)
    return jsonify({'anonymize': anonymize})

@bp.route('/api/set_anonymize/<int:anon>', methods=('POST',))
def set_anonymize_setting(anon):
    session['anonymize'] = bool(anon)
    return jsonify({'anonymize': session['anonymize']})

@bp.route('/api/project/<project>/private_access', methods=('GET',))
def get_project_private_access(project:str):
    if g.user is not None:
        # Check if the user has access to the project
        pa = fetch_project_access(g.user['id'], project, key=f'project_access_{project}')
        if pa is not None and pa['anon_permission'] > 0:
            return jsonify({'private_access': True})
    return jsonify({'private_access': False})
    

def check_anon(project):
    """
    This method checks the user's anonymization setting, access to the current project
    and returns True of the user should see anonymized data
    
    Parameters:
    - project: project name
    """
    if g.get('in_request', False) is False:
        # Not in a request context, CLI command, private access is allowed
        return False
    
    elif g.anonymize is False:
        if g.user is not None:
            # Check if the user has access to the project
            pa = fetch_project_access(g.user['id'], project, key=f'project_access_{project}')
            if pa is not None and pa['anon_permission'] > 0:
                return False
    return True


def get_user_id(username):
    db=get_db()
    rc = db.execute('SELECT id FROM user WHERE username=?',(username,)).fetchone()
    return rc['id'] if rc is not None else None


def create_password_reset_link(user_id, expiry = 86400):

    db=get_db()
    reset_key = str(uuid.uuid4())
    db.execute('INSERT INTO password_reset(reset_key,user,t_expires) '
               'VALUES (?,?,?)', (reset_key, user_id, time.time() + expiry))
    db.commit()

    return '%s/auth/pwreset/%s' % \
            (current_app.config.get('HISTOANNOT_PUBLIC_URL', 
                current_app.config.get('SERVER_NAME')),
            reset_key)


class UserException(Exception):
    """Base class for user exceptions"""
    pass


class UserExistsException(UserException):
    """Exception indicating user exists"""
    pass


class DuplicateEmailException(UserException):
    """Exception indicating email exists"""
    pass


def add_user(username, expiry, email, notify):

    # Check if the user is already in the system
    if get_user_id(username) is not None:
        raise UserExistsException('User %s is already in the system' % (username,))

    if email is not None and email_exists(email):
        raise DuplicateEmailException('Email address %s is already in use by another user.' % (email,))

    db = get_db()
    rc = db.execute('SELECT * FROM user WHERE username=?', (username,))
    if rc.rowcount > 0:
        raise UserExistsException('User %s is already in the system' % (username,))

    # Create the username with an unguessable password
    dummy_password = str(uuid.uuid4())
    db.execute('INSERT INTO user(username, email, password) VALUES (?,?,?)',
               (username,email,dummy_password))
    db.commit()

    # Extract the user record from the database
    row = db.execute('SELECT * FROM user WHERE username=?', (username,)).fetchone()
    user_id = row['id']

    # Generate a password reset link for the user.
    url = create_password_reset_link(row['id'], expiry)
    if notify is True and email is not None:
        send_user_invitation(row['id'], url, True)

    # Return user details as a dict
    d = { x : row[x] for x in ('id', 'username', 'email') }
    d['url'] = url
    return d


@click.command('users-add')
@click.argument('username')
@click.option('-x', '--expiry', type=click.INT, help='Expiration time for the password reset link, in seconds', default=86400)
@click.option('-e', '--email', help='User email address')
@click.option('-n', '--notify', is_flag=True, help='Send the user a notification email')
@with_appcontext
def user_add_command(username, expiry, email, notify):
    """Create a new user and generate a password reset link"""
    d = add_user(username, expiry, email, notify)
    print("User: %d  Email: %s  ResetLink: %s" % (d['id'], d['email'], d['url']))


@click.command('users-get-reset-link')
@click.argument('username')
@click.option('-x', '--expiry', type=click.INT, help='Expiration time for the password reset link, in seconds', default=86400)
@click.option('--csv', is_flag=True, help="Read usernames from CSV file with field 'username'")
@click.option('--notify', '-n', is_flag=True, help='Send the user a notification email')
@with_appcontext
def user_get_reset_link_command(username, expiry, csv=False, notify=False):
    
    # Read one or more user ids from file
    if csv is True:
        df = pandas.read_csv(username)
        users = df['username'].unique()
    else:
        users = [ username ]

    # For each user id, generate a link
    for u in users:
        id = get_user_id(u)
        if id is not None:
            url = create_password_reset_link(id, expiry)
            print("%s: %s" % (u, url))
            if notify is True:
                send_user_invitation(id, url, False)
        else:
            print('User %s is not in the system' % (u,))
            continue


def init_app(app):
    app.cli.add_command(user_add_command)
    app.cli.add_command(user_get_reset_link_command)

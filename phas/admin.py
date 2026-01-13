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
from flask import(
    Blueprint, flash, g, redirect, render_template, request, url_for, make_response,
    current_app, send_from_directory, session, send_file
)

from .auth import site_admin_access_required, create_password_reset_link, add_user, send_user_invitation
from .db import get_db
from .common import abort_json, success_json
from .project_ref import ProjectRef
import json
import re

bp = Blueprint('admin', __name__)

# The index
@bp.route('/admin/users')
@site_admin_access_required
def user_management():

    # Render the entry page
    return render_template('admin/user_management.html')


# Generate JSON for user from a single row in 'user' table
def user_row_to_json(row):
    d = {x: row[x] for x in ('id', 'username', 'email', 'disabled', 'site_admin')}
    if row['disabled'] > 0:
        d['access'] = 'inactive'
    elif row['site_admin'] > 0:
        d['access'] = 'site_admin'
    else:
        d['access'] = 'active'
    return d

def group_row_to_json(row):
    d = {x: row[x] for x in ('id', 'username', 'disabled', 'site_admin')}
    if row['disabled'] > 0:
        d['access'] = 'inactive'
    elif row['site_admin'] > 0:
        d['access'] = 'site_admin'
    else:
        d['access'] = 'active'
    return d

# List users with access level
@bp.route('/api/admin/users')
@site_admin_access_required
def user_listing():

    # Get the current user
    db = get_db()

    # Result array
    listing = []

    rc = db.execute('SELECT * FROM user WHERE is_group=FALSE ORDER BY id')
    for row in rc.fetchall():
        listing.append(user_row_to_json(row))

    # Generate a bunch of json
    return json.dumps(listing)


# List users with access level
@bp.route('/api/admin/groups')
@site_admin_access_required
def group_listing():

    # Get the current user
    db = get_db()

    # Result array
    listing = []

    rc = db.execute('SELECT * FROM user WHERE is_group=TRUE ORDER BY id')
    for row in rc.fetchall():
        listing.append(group_row_to_json(row))

    # Generate a bunch of json
    return json.dumps(listing)


# Get reset link for a user
@bp.route('/api/admin/user/<int:user_id>/get_reset_link/<int:notify>')
@site_admin_access_required
def user_get_reset_link(user_id, notify):
    # Default to a one-week expiry
    url = create_password_reset_link(user_id, 3600*24*7)
    if notify > 0:
        send_user_invitation(user_id, url, False)
    return json.dumps({"reset_link": url})


# Email validation
def validate_email(email):
    pat = "([A-Za-z0-9]+[.-_])*[A-Za-z0-9]+@[A-Za-z0-9-]+(\\.[A-Z|a-z]{2,})+"
    return True if re.match(pat, email) else False


# Change the user's global access level
@bp.route('/api/admin/user/<int:user_id>/update_profile', methods=('POST',))
@site_admin_access_required
def user_update_profile(user_id):
    # Read the data from json
    data = json.loads(request.get_data())

    # Validate requested access
    access = data.get("access", None)
    email = data.get("email", None)
    is_group = data.get("is_group", None)
    
    if access not in ('active', 'inactive', 'site_admin'):
        return abort_json("Invalid JSON in request")

    if is_group is None:
        return abort_json("Invalid JSON in request")

    if not is_group and (email is None or not validate_email(email)):
        return abort_json("Email address fails validation")
    
    # Block user from revoking their own site-admin access
    if g.user['id'] == user_id and access != 'site_admin':
        return abort_json("Attempt to revoke sysadmin privilege of active user")

    # Update the access of the user
    db = get_db()
    db.execute('UPDATE user SET disabled=?, site_admin=?, email=? WHERE id=? and is_group=?',
               (access=='inactive', access=='site_admin', email if not is_group else None, user_id, is_group))
    db.commit()

    # Get an updated JSON for the user
    row = db.execute('SELECT * FROM user WHERE id=? and is_group=?', (user_id,is_group)).fetchone()
    return json.dumps(user_row_to_json(row))


# Create a new user
@bp.route('/api/admin/user/create', methods=('POST',))
@site_admin_access_required
def user_create():
    # Read the data from json
    data = json.loads(request.get_data())

    # Validate requested access
    username = data.get("username", None)
    access = data.get("access", None)
    email = data.get("email", None)
    is_group = data.get("is_group", None)

    if access not in ('active', 'inactive', 'site_admin'):
        return abort_json("Invalid JSON in request")

    if username is None or len(username) < 1:
        return abort_json("Missing user or email")

    if is_group is None:
        return abort_json("Invalid JSON in request")

    if not is_group and (email is None or not validate_email(email)):
        return abort_json("Email address fails validation")

    # Try to create the user
    d = add_user(username, 7*24*3600, email, is_group, True)
    if d is None or 'id' not in d:
        return abort_json("Failed to add user")

    # Update the access of the user
    db = get_db()
    db.execute('UPDATE user SET disabled=?, site_admin=? WHERE id=?',
               (access=='inactive', access=='site_admin', d['id']))
    db.commit()

    # Get an updated JSON for the user
    row = db.execute('SELECT * FROM user WHERE id=?', (d['id'],)).fetchone()

    # Return the user information and the reset URL
    return json.dumps({'user': user_row_to_json(row), 'url': d['url']})


# Delete a user
@bp.route('/api/admin/user/<int:user_id>/delete', methods=('GET',))
@site_admin_access_required
def user_delete(user_id):
    db = get_db()

    # A user can only be deleted if they have not made any contributions
    row = db.execute('SELECT count(id) as n FROM edit_meta WHERE creator=? or editor=?',
                     (user_id,user_id)).fetchone()
    if int(row['n']) > 0:
        return abort_json("The user cannot be deleted because they have created annotations")

    # Clean up all the tables that reference user
    db.execute('DELETE FROM password_reset WHERE user=?', (user_id,))
    db.execute('DELETE FROM user_api_key WHERE user=?', (user_id,))
    db.execute('DELETE FROM task_access WHERE user=?', (user_id,))
    db.execute('DELETE FROM project_access WHERE user=?', (user_id,))
    db.execute('DELETE FROM user_task_slide_preferences WHERE user=?', (user_id,))
    db.execute('DELETE FROM user WHERE id=?', (user_id,))
    db.commit()

    # Return success
    return success_json()


def user_project_access_row_to_json(row):
    d = {x: row[x] for x in ('project', 'access', 'anon_permission', 'api_permission')}
    return d


# Get a listing of projects with access for a particular user
@bp.route('/api/admin/user/<int:user_id>/project_access', methods=('GET',))
@site_admin_access_required
def user_get_project_access_listing(user_id):
    db = get_db()
    rc = db.execute('SELECT P.id as project, IFNULL(PA.access, "none") access, PA.anon_permission, PA.api_permission '
                    'FROM project P LEFT JOIN '
                    '  (SELECT * FROM project_access WHERE user=?) PA '
                    '  ON P.id = PA.project '
                    'ORDER BY P.id', (user_id,))

    listing = []
    for row in rc.fetchall():
        listing.append(user_project_access_row_to_json(row))
    print(listing)
    return json.dumps(listing)



def user_task_access_row_to_json(row):
    d = {x: row[x] for x in ('task', 'task_name', 'access')}
    return d


# Get a listing of privileged tasks with access for a particular user
@bp.route('/api/admin/user/<int:user_id>/project/<project>/task_access', methods=('GET',))
@site_admin_access_required
def user_get_task_access_listing(user_id, project):
    db = get_db()
    rc = db.execute('SELECT TI.id as task, TI.name as task_name, IFNULL(TA.access,"none") access '
                    'FROM task_info TI LEFT JOIN '
                    '   (SELECT * from task_access where user=?) TA ON TI.id=TA.task '
                    'WHERE project=? and restrict_access=TRUE '
                    'ORDER BY TI.id', (user_id, project))
    listing = []
    for row in rc.fetchall():
        listing.append(user_task_access_row_to_json(row))
    return json.dumps(listing)


# Set access for a project
@bp.route('/api/admin/user/<int:user_id>/project/<project>/access/<access_level>/anon/<int:anon_permission>/api/<int:api_permission>', methods=('GET',))
@site_admin_access_required
def user_set_project_access(user_id, project, access_level, anon_permission=0, api_permission=0):
    try:
        # Use the project API to change access level
        pr = ProjectRef(project)
        pr.user_set_access_level(user_id, access_level, anon_permission, api_permission)

        # Get the updated project access level
        db = get_db()
        rc = db.execute('SELECT P.id as project, IFNULL(PA.access, "none") access, anon_permission, api_permission '
                        'FROM project P LEFT JOIN '
                        '  (SELECT * FROM project_access WHERE user=?) PA '
                        '  ON P.id = PA.project '
                        'WHERE project=?', (user_id, project))
        return json.dumps(user_project_access_row_to_json(rc.fetchone()))

    except:
        return abort_json('Unable to change project access')


# Set access for a task
@bp.route('/api/admin/user/<int:user_id>/project/<project>/task/<int:task_id>/access/<access_level>', methods=('GET',))
@site_admin_access_required
def user_set_task_access(user_id, project, task_id, access_level):
    try:
        # Use the project API to change access level
        pr = ProjectRef(project)
        pr.user_set_task_access_level(task_id, user_id, access_level)

        # Get the updated task access level
        db = get_db()
        rc = db.execute('SELECT TI.id as task, TI.name as task_name, IFNULL(TA.access,"none") access '
                        'FROM task_info TI LEFT JOIN '
                        '   (SELECT * FROM task_access WHERE user=?) TA ON TI.id=TA.task '
                        'WHERE project=? AND task=? AND restrict_access=TRUE ', (user_id, project, task_id))
        d_task = user_task_access_row_to_json(rc.fetchone())

        # Get the updated project access level
        rc = db.execute('SELECT P.id as project, IFNULL(PA.access, "none") access, anon_permission, api_permission '
                        'FROM project P LEFT JOIN '
                        '  (SELECT * FROM project_access WHERE user=?) PA '
                        '  ON P.id = PA.project '
                        'WHERE project=?', (user_id, project))
        d_project = user_project_access_row_to_json(rc.fetchone())

        return json.dumps({"task": d_task, "project": d_project})

    except:
        return abort_json('Unable to change task access')


def group_membership_row_to_json(row):
    d = {x: row[x] for x in ('group_id', 'group_name', 'member')}
    return d

@bp.route('/api/admin/user/<int:user_id>/group_membership', methods=('GET',))
@site_admin_access_required
def user_get_user_group_membership_listing(user_id):
    db = get_db()
    rc = db.execute(
        'SELECT G.id as group_id, G.username as group_name, IFNULL(GM.user_id>=0,0) as member '
        'FROM user G LEFT JOIN '
        '  (SELECT * FROM group_membership WHERE user_id=?) GM ON G.id=GM.group_id '
        'WHERE G.is_group = TRUE '
        'ORDER BY group_name', (user_id,))
    listing = []
    for row in rc.fetchall():
        listing.append(group_membership_row_to_json(row))
    return json.dumps(listing)


@bp.route('/api/admin/user/<int:user_id>/set_group_membership/group/<int:group_id>/membership/<int:membership>', methods=('GET',))
@site_admin_access_required
def user_set_user_group_membership(user_id, group_id, membership):
    db = get_db()
    if membership > 0:
        # Add membership
        db.execute('INSERT OR IGNORE INTO group_membership (group_id, user_id) VALUES (?, ?)', (group_id, user_id))
    else:
        # Remove membership
        db.execute('DELETE FROM group_membership WHERE group_id=? AND user_id=?', (group_id, user_id))
    db.commit()
    
    row = db.execute(
        'SELECT G.id as group_id, G.username as group_name, IFNULL(GM.user_id>=0,0) as member '
        'FROM user G LEFT JOIN '
        '  (SELECT * FROM group_membership WHERE user_id=?) GM ON G.id=GM.group_id '
        'WHERE G.is_group = TRUE AND G.id=? ',
        (user_id, group_id)).fetchone()
    return json.dumps(group_membership_row_to_json(row))
***********************
Permission Model
***********************

PHAS uses a hierarchical permission model with two levels: project-level permissions and task-level permissions.

Access Levels
=============

There are four access levels in PHAS:

* **none**: No access to the resource
* **read**: Read-only access (view slides, annotations)
* **write**: Read and write access (create and edit annotations)
* **admin**: Full administrative access (manage users, configure tasks)

Project-Level vs Task-Level Permissions
========================================

Project-Level Permissions
-------------------------

Project-level permissions control access to the entire project and act as a **wildcard** for tasks that do not have specific access restrictions (i.e., tasks with ``restrict-access: false``).

When a user has project-level access, they automatically have the same level of access to all non-restricted tasks in the project.

Task-Level Permissions
----------------------

Task-level permissions provide fine-grained access control for individual tasks. Tasks can be configured with ``restrict-access: true`` in their JSON descriptor to enable task-specific access control.

When a task has ``restrict-access: true``:

* The task-level permission **overrides** the project-level permission for that specific task
* Users can have **higher** task-level access than project-level access
* Users can have **lower** task-level access than project-level access

Permission Precedence
=====================

The effective permission for a user on a task is determined as follows:

1. If the task has ``restrict-access: false`` (default):
   
   * Use the user's **project-level** permission
   
2. If the task has ``restrict-access: true``:
   
   * Use the user's **task-level** permission
   * Project-level permission is ignored for this task

Examples
========

Example 1: Project Permission as Wildcard
------------------------------------------

User Alice has:

* Project permission: ``read``
* Task "Browse" (``restrict-access: false``): No specific permission set
* Task "Annotate" (``restrict-access: false``): No specific permission set

Result: Alice has ``read`` access to both tasks via her project permission.

Example 2: Task Permission Override
------------------------------------

User Bob has:

* Project permission: ``none``
* Task "Browse" (``restrict-access: false``): No specific permission set
* Task "Annotate" (``restrict-access: true``): ``write``

Result: 

* Bob has ``none`` access to the "Browse" task (uses project permission)
* Bob has ``write`` access to the "Annotate" task (uses task permission)
* Bob will see the project in his project list because he has access to at least one task

Example 3: Mixed Permissions
-----------------------------

User Carol has:

* Project permission: ``read``
* Task "Browse" (``restrict-access: false``): No specific permission set
* Task "Annotate" (``restrict-access: true``): ``write``
* Task "Admin" (``restrict-access: true``): ``admin``

Result:

* Carol has ``read`` access to "Browse" (uses project permission)
* Carol has ``write`` access to "Annotate" (task permission overrides)
* Carol has ``admin`` access to "Admin" (task permission overrides)

Setting Permissions
===================

Project-Level Permissions
-------------------------

Use the Flask CLI to set project-level permissions::

    flask users-set-access-level -p <project> <access_level> <username>

For example::

    flask users-set-access-level -p example read alice

Task-Level Permissions
----------------------

Use the Flask CLI to set task-level permissions::

    flask users-set-access-level -p <project> -t <task_name> <access_level> <username>

For example::

    flask users-set-access-level -p example -t "Annotate" write bob

**Note**: Task-level permissions can now be set independently of project-level permissions. You can give a user task access even if they have ``none`` project access.

Security Considerations
=======================

The new permission model allows for more flexible access control:

* Users with ``project: none`` can still access specific tasks if granted task-level permissions
* This enables scenarios where users should only see specific tasks in a project
* Project administrators can grant temporary or limited access to specific tasks without giving broader project access

However, note that:

* Users with any level of task access will see the project in their project listing
* API permissions and anonymization permissions are still managed at the project level
* Administrators should carefully configure ``restrict-access`` flags on tasks that require strict access control

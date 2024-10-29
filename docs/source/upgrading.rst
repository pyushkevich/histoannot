*********
Upgrading
*********

When you upgrade to a new release of PHAS, in addition to changes in the code, there may be changes in the database schema. These changes need to be applied manually. This document will be updated to list specific command that need to be run to upgrade to each new release of PHAS.

Before Upgrading
================

* Check and write down the currently installed version of PHAS. Use ``pip list`` if you installed using ``pip`` or ``git status`` if you installed using ``git clone``.
* **BACKUP YOUR DATABASE**, which is located in the instance directory (``instance/histoannot.sqlite``)

Instructions for Specific Releases
==================================
These instructions will be added in future relesease of PHAS.

Generic Instructions
====================
Use these instructions if your upgrade is not covered in the specific instructions above. The folder ``histoannot/sqlmod`` contains commands that can be used to add features to the database that have been added over the course of PHAS development. Most of these commands add new tables, columns, or views to the database schema. These commands are **destructive** so that if a table that you are adding already exists, it will be deleted, losing all your data.

The safest way to check is to see when any particular ``add_xyz.sql`` script was committed to the PHAS repository and only run those scripts that are newer than your current installation of PHAS. If you are using ``git pull`` to update PHAS code, you will see what if any ``add_xyz.sql`` were added. If you are using ``pip install --upgrade`` then you will have to check by hand.

A second check is to see if the tables being added already exist in your database. Use the following code to list the tables::
    
    sqlite3 instance/histoannot.sqlite
    sqlite> .tables

To execute an ``add_xyz.sql`` script run::

    # Backup the database!!!
    mkdir -p $HOME/phas_database_backup
    cp -av instance/histoannot.sqlite $HOME/phas_database_backup_$(date +%F).sqlite

    # Run the script
    sqlite3 instance/histoannot.sqlite < histoannot/sqlmod/add_xyz.sql
#!/bin/bash
source .venv/bin/activate

# Name of the FLASK application
export FLASK_APP=phas

# Path to the instance directory (config files and database will go here)
export FLASK_INSTANCE_PATH=/home/foo/phas/instance
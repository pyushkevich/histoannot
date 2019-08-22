import os

# Get the base
HISTOANNOT_URL_BASE=os.environ.get("HISTOANNOT_URL_BASE", default=None)
SECRET_KEY=os.environ.get("HISTOANNOT_SECRET_KEY", default="dev")
HISTOANNOT_SERVER_MODE=os.environ.get("HISTOANNOT_SERVER_MODE", default="master")

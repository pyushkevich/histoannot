import os

# Get the base
SECRET_KEY=os.environ.get("HISTOANNOT_SECRET_KEY", "dev")
HISTOANNOT_SERVER_MODE=os.environ.get("HISTOANNOT_SERVER_MODE", "dzi_node")
HISTOANNOT_MASTER_URL=os.environ.get("HISTOANNOT_MASTER_URL")

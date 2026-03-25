# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PHAS (PICSL Histology Annotation Server) is a Flask-based web application for annotating histology slides for anatomical segmentation and deep learning training. Documentation: https://picsl-histoannot.readthedocs.io

## Commands

**Install (development):**
```bash
pip install -e .
```

**Run the development server:**
```bash
export FLASK_APP=phas
export FLASK_INSTANCE_PATH=/path/to/instance
flask run
```

**Initialize the database:**
```bash
flask init-db
```

**Build documentation:**
```bash
cd docs && make html
```

**Build package:**
```bash
python -m build
```

There are no automated tests in this project.

## Architecture

The app uses the Flask application factory pattern — `phas:create_app()` in `phas/__init__.py` registers all blueprints and configures caching (SimpleCache or UWSGICache).

### Blueprints

| Blueprint | File | Purpose |
|-----------|------|---------|
| `slide` | `slide.py` | Main annotation UI, project/task management, annotation save/load, most REST API |
| `auth` | `auth.py` | Login/logout, API key management, ORCID OAuth, permission decorators |
| `dltrain` | `dltrain.py` | Deep learning training sample management, label sets, sampling ROIs |
| `dzi` | `dzi.py` | Deep Zoom Image tile server using OpenSlide; manages a pool of slide server processes |
| `admin` | `admin.py` | Site admin panel |
| `delegate` | `delegate.py` | Forwards requests to DZI nodes in distributed deployments |
| `frontend` | `frontend.py` | Customizable static/templated pages |

### Database

SQLite via `db.py`. Schema in `phas/sql/schema.sql`; views (used for permission enforcement) in `phas/sql/schema_views.sql`. Incremental migrations are in `phas/sql/deltas/` (numbered, applied in order).

**Permission model:** Project-level and task-level access are independent (none/read/write/admin). Views `effective_project_access` and `effective_task_access` resolve permissions including group membership.

### Key Data Hierarchy

Specimens → Blocks → Slides, organized within Projects and Tasks. Tasks contain JSON configuration and restrict which slides are accessible.

### Project Configuration

`ProjectRef` (`project_ref.py`) manages per-project configuration, remote resource caching, and Google Cloud Storage integration. CLI project management commands (import slides, parse manifests from CSV/JSON/Google Sheets) live in `project_cli.py`.

### Image Serving

`dzi.py` spawns a pool of worker processes (one per slide) to serve OpenSlide tiles via the Deep Zoom protocol. In distributed mode, `delegate.py` routes tile requests to separate DZI nodes registered in the `dzi_node` table.

### Frontend

Server-rendered Jinja2 templates with jQuery, OpenSeadragon (slide viewer), Paper.js (annotation drawing), and Pure.css.

### Python Client

`phas/client/api.py` provides a `Client` class for programmatic access to a remote PHAS server using API key authentication.

## Deployment

Docker deployment uses nginx + uWSGI + supervisord. Configuration files are in `docker/phas/`. The `env.sh` script sets required environment variables.

from flask import(
    Blueprint, flash, g, redirect, render_template, render_template_string, request, url_for, make_response,
    current_app, send_from_directory, session, send_file, abort
)

from flask.templating import render_template_string
import os
import markdown

bp = Blueprint('frontend', __name__)


@bp.route('/frontend/static/<path:filename>')
def custom_static(filename):
    """Serve static files from CUSTOM_PAGES_PATH/static directory."""
    custom_pages_path = current_app.config.get('CUSTOM_PAGES_PATH', None)
    if not custom_pages_path:
        abort(404)
    
    static_dir = os.path.join(custom_pages_path, 'static')
    if not os.path.isdir(static_dir):
        abort(404)
    
    # Security check - prevent directory traversal
    full_path = os.path.realpath(os.path.join(static_dir, filename))
    if not full_path.startswith(os.path.realpath(static_dir)):
        abort(403)
    
    print(f'SENDING {static_dir} {filename}')
    return send_from_directory(static_dir, filename)


@bp.route('/about')
def about():
    html=None
    custom_pages_path = current_app.config.get('CUSTOM_PAGES_PATH', None)
    title = current_app.config.get('CUSTOM_ABOUT_TITLE', "About PHAS")
    if custom_pages_path:
        for ext in ['.html', '.md']:
            page = os.path.join(custom_pages_path, 'about'+ext)
            if os.path.isfile(page):
                with open(page,'rt') as f:
                    html = f.read()
                    if ext == '.md':
                        html = markdown.markdown(html, extensions=['md_in_html'])
                    print(f'LOADED CUSTOM ABOUT PAGE FROM {custom_pages_path}')
                    return render_template_string(html)
        abort(404)
    else:
        return render_template('about.html')
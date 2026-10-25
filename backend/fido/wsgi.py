"""Production entry point: gunicorn -c gunicorn.conf.py fido.wsgi:app"""

from .app import create_app

app = create_app()

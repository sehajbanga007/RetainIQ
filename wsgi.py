"""Entry point for production servers (Render, Railway, etc.).

    gunicorn wsgi:app --workers 1 --threads 8 --timeout 180

The demo data and model are built during the deploy's build step (render.yaml),
so the server starts in seconds. If they are missing, they are built here instead.
Use exactly ONE worker: the scheduler and live simulator run inside the process.
"""
from run import setup
from app.main import create_app

setup()
app = create_app()

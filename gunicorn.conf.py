# gunicorn.conf.py
# Gunicorn configuration with scheduler hooks

import os

# Server socket
bind = f"0.0.0.0:{os.getenv('PORT', '8080')}"
workers = 2
threads = 4
timeout = 120

# Only start the scheduler in the master process, not in workers
# This prevents duplicate schedulers
preload_app = True

def on_starting(server):
    """Called before the master process is initialized"""
    pass

def when_ready(server):
    """Called after the server is ready to accept connections"""
    # Import here to avoid circular imports
    from app import startup
    startup()

def worker_exit(server, worker):
    """Called when a worker exits"""
    pass

def on_exit(server):
    """Called on server shutdown"""
    from app import scheduler
    try:
        if scheduler.running:
            scheduler.shutdown(wait=False)
    except Exception:
        pass

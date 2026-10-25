"""Gunicorn settings for hosting the Fido backend.

Exactly one worker process: all session state lives in that process's memory.
Threads serve concurrent requests; each open Server-Sent Events stream uses one.
"""

import os

bind = f"0.0.0.0:{os.environ.get('PORT', '5000')}"
workers = 1
worker_class = "gthread"
threads = int(os.environ.get("FIDO_THREADS", "64"))
timeout = 0  # SSE streams are long-lived by design

"""Gunicorn production settings for the PaperVault web service."""

from __future__ import annotations

import os


bind = os.getenv("GUNICORN_BIND", f"0.0.0.0:{os.getenv('PORT', '5001')}")
workers = int(os.getenv("GUNICORN_WORKERS", "2"))
threads = int(os.getenv("GUNICORN_THREADS", "4"))
worker_class = "gthread"

# AI provider calls may legitimately take longer than an ordinary search.
timeout = int(os.getenv("GUNICORN_TIMEOUT", "120"))
graceful_timeout = int(os.getenv("GUNICORN_GRACEFUL_TIMEOUT", "30"))
keepalive = int(os.getenv("GUNICORN_KEEPALIVE", "5"))

# Verify or build the derived SQLite index once in the Gunicorn master before
# workers fork. This prevents every worker racing to materialise the same HF
# artifact on a cold boot; request-time connections remain short-lived/read-only.
preload_app = True

accesslog = "-"
errorlog = "-"
capture_output = True

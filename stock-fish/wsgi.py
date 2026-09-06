"""WSGI entrypoint for the Docker deployment.

Gunicorn runs exactly one process for this app so the local scheduler has a
single durable owner.
"""

from __future__ import annotations

from stockfish_core.api import create_app
from stockfish_core.yaogu.scheduler import CandidateScheduler

app = create_app()
core = app.extensions["stockfish_core"]
core.candidate_scheduler = CandidateScheduler(core.candidates)
core.candidate_scheduler.start()

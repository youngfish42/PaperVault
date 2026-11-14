from __future__ import annotations

from flask import Blueprint, current_app, jsonify

bp = Blueprint("health_v1", __name__)


@bp.get("/healthz")
def healthz():
    repo = current_app.extensions["paper_repository"]
    repo.ensure_loaded()
    return jsonify({
        "status": "ok",
        "papers": repo.paper_count(),
        "confs": repo.conference_count(),
    })

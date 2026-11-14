from __future__ import annotations

from typing import List

from flask import Blueprint, current_app, jsonify

from ...schemas import ConfOut, ConfYear

bp = Blueprint("confs_v1", __name__)


@bp.get("/confs")
def list_confs():
    repo = current_app.extensions["paper_repository"]
    repo.ensure_loaded()

    items: List[ConfOut] = []
    for stats in repo.conference_stats():
        years = [
            ConfYear(year=year, count=count)
            for year, count in stats.years.items()
        ]
        items.append(ConfOut(name=stats.name, total=stats.total, years=years))

    return jsonify({"items": [it.model_dump() for it in items], "total": len(items)})

from __future__ import annotations

from collections import Counter
from typing import List

from flask import Blueprint, current_app, jsonify

from ...schemas import ConfOut, ConfYear

bp = Blueprint("confs_v1", __name__)


@bp.get("/confs")
def list_confs():
    repo = current_app.extensions["paper_repository"]
    repo.ensure_loaded()

    items: List[ConfOut] = []
    for name in sorted(repo.confs().keys()):
        papers = repo.confs()[name]
        year_counter = Counter(p.year for p in papers)
        years = [ConfYear(year=y, count=c) for y, c in sorted(year_counter.items())]
        items.append(ConfOut(name=name, total=len(papers), years=years))

    return jsonify({"items": [it.model_dump() for it in items], "total": len(items)})

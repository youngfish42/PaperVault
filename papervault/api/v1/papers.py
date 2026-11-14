from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request
from pydantic import ValidationError

from ...errors import ApiError
from ...schemas import PageMeta, PaperOut, PaperSearchParams
from ...services.papers import SearchCriteria, search_papers

bp = Blueprint("papers_v1", __name__)


@bp.get("/papers")
def list_papers():
    raw = request.args.to_dict(flat=False)
    flat = {}
    for k, v in raw.items():
        flat[k] = v if k == "conf" else (v[-1] if isinstance(v, list) else v)

    try:
        params = PaperSearchParams.model_validate(flat)
    except ValidationError as exc:
        raise ApiError(
            "Invalid query parameters.",
            status_code=400,
            code="BAD_REQUEST",
            details=exc.errors(include_url=False),
        )

    settings = current_app.extensions["settings"]
    if params.size > settings.max_page_size:
        raise ApiError(
            f"size must be <= {settings.max_page_size}",
            status_code=400,
            code="BAD_REQUEST",
        )

    repo = current_app.extensions["paper_repository"]
    criteria = SearchCriteria(
        query=params.q,
        field=params.field,
        confs=params.conf,
        since=params.since,
        until=params.until,
        author=params.author,
        sort=params.sort,
        page=params.page,
        size=params.size,
    )

    page_items, total = search_papers(repo, criteria)
    items = [
        PaperOut(
            id=p.id,
            conf=p.conf,
            year=p.year,
            title=p.title,
            url=p.url,
            authors=p.authors,
            abstract=p.abstract,
            code=p.code,
        ).model_dump()
        for p in page_items
    ]
    meta = PageMeta(page=params.page, size=params.size, total=total).model_dump()
    return jsonify({"items": items, "meta": meta})

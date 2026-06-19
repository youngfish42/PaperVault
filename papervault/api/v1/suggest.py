from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request
from pydantic import ValidationError

from ...errors import ApiError
from ...schemas import SuggestRequest, SuggestResponse
from ...services.suggest import suggest_keywords

bp = Blueprint("suggest_v1", __name__)


@bp.post("/suggest")
def post_suggest():
    payload = request.get_json(silent=True) or {}
    if not payload and request.form:
        payload = request.form.to_dict()

    try:
        req = SuggestRequest.model_validate(payload)
    except ValidationError as exc:
        raise ApiError(
            "Invalid suggestion request.",
            status_code=400,
            code="BAD_REQUEST",
            details=exc.errors(include_url=False),
        )

    settings = current_app.extensions["settings"]
    result = suggest_keywords(
        req.query,
        model=req.model or settings.openai_model,
        temperature=settings.openai_temperature,
        max_keywords=req.max_keywords or settings.openai_max_keywords,
    )

    body = SuggestResponse(
        keywords=result.keywords,
        timecost_ms=round(result.elapsed_ms, 1),
        model=result.model,
    )
    return jsonify(body.model_dump())

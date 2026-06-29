from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request
from pydantic import ValidationError

from ...errors import ApiError
from ...schemas import SuggestRequest, SuggestResponse
from ...services.ai_providers import get_all_presets
from ...services.suggest import SuggestionRequest, suggest_keywords

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
    temperature = (
        req.temperature
        if req.temperature is not None
        else getattr(settings, "openai_temperature", 0.5)
    )
    max_keywords = req.max_keywords or getattr(settings, "openai_max_keywords", 10)
    max_tokens = req.max_tokens or getattr(settings, "anthropic_max_tokens", 512)

    internal = SuggestionRequest(
        query=req.query,
        provider=req.provider,
        base_url=req.base_url,
        model=req.model,
        api_key=req.api_key,
        protocol=req.protocol,
        temperature=temperature,
        max_keywords=max_keywords,
        max_tokens=max_tokens,
    )

    result = suggest_keywords(internal)

    body = SuggestResponse(
        keywords=result.keywords,
        timecost_ms=round(result.elapsed_ms, 1),
        model=result.model,
        provider=result.provider,
        protocol=result.protocol,
    )
    return jsonify(body.model_dump())


@bp.get("/ai/providers")
def list_providers():
    return jsonify({"items": [p.as_dict() for p in get_all_presets()]})

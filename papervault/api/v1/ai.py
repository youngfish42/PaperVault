"""AI-powered endpoints that complement ``v1/suggest``.

Currently:

* ``POST /v1/ai/rerank`` — score and order a small batch of already-retrieved
  papers by LLM-judged relevance to a query.

The legacy ``/v1/ai/providers`` endpoint stays in ``v1/suggest`` for
back-compat; moving it here is purely cosmetic and not worth the diff.
"""

from __future__ import annotations

import logging
from typing import List

from flask import Blueprint, current_app, jsonify, request
from pydantic import ValidationError

from ...errors import ApiError
from ...schemas import (
    RerankEntry,
    RerankRequest,
    RerankResponse,
)
from ...services.rerank import rank_papers

logger = logging.getLogger("papervault.ai")

bp = Blueprint("ai_v1", __name__)


@bp.post("/ai/rerank")
def post_rerank():
    """Re-rank a batch of paper ids by query relevance.

    Body (``RerankRequest``) shape::

        {
            "query": "<str>",
            "paper_ids": ["abc...", ...],   # 1..300 ids
            "provider": "<optional>",
            "base_url": "<optional>",
            "model": "<optional>",
            "api_key": "<optional>",
            "protocol": "<optional>",
            "temperature": <optional float>
        }

    Response (``RerankResponse``) shape::

        {
            "ordered": [{"paper_id":"<id>", "score": 0.0..1.0}, ...],
            "timecost_ms": <float>,
            "model": "<str>",
            "provider": "<str>",
            "protocol": "<str>"
        }
    """

    payload = request.get_json(silent=True) or {}
    if not payload and request.form:
        payload = request.form.to_dict()

    try:
        req = RerankRequest.model_validate(payload)
    except ValidationError as exc:
        raise ApiError(
            "Invalid re-rank request.",
            status_code=400,
            code="BAD_REQUEST",
            details=exc.errors(include_url=False),
        )

    repo = current_app.extensions.get("paper_repository")
    if repo is None:
        raise ApiError(
            "Paper repository is not initialised.",
            status_code=503,
            code="REPO_NOT_READY",
        )

    by_id = {p.id: p for p in repo.all_papers()}
    papers = [by_id[pid] for pid in req.paper_ids if pid in by_id]
    missing = [pid for pid in req.paper_ids if pid not in by_id]
    if missing:
        # Not fatal — the model can still rank the survivors — but worth
        # logging because it usually means the caller passed stale ids.
        logger.info("Re-rank skipped %d unknown paper id(s)", len(missing))

    result = rank_papers(
        query=req.query,
        papers=papers,
        provider=req.provider,
        base_url=req.base_url,
        model=req.model,
        api_key=req.api_key,
        protocol=req.protocol,
        temperature=req.temperature,
    )

    body = RerankResponse(
        ordered=[RerankEntry(paper_id=pid, score=result.scores.get(pid, 0.5))
                 for pid in result.ordered_ids],
        timecost_ms=round(result.elapsed_ms, 1),
        model=result.model,
        provider=result.provider,
        protocol=result.protocol,
    )
    return jsonify(body.model_dump())

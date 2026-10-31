"""AI-powered endpoints that complement ``v1/suggest``.

Currently:

* ``POST /v1/ai/rerank`` — score and order a small batch of already-retrieved
  papers by LLM-judged relevance to a query.

The legacy ``/v1/ai/providers`` endpoint stays in ``v1/suggest`` for
back-compat; moving it here is purely cosmetic and not worth the diff.
"""

from __future__ import annotations

import logging

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
            "skipped_ids": ["<unknown id>", ...],
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

    # Resolve each requested id through the repository's pre-built index.
    # ``get_by_id`` is O(1) per id, so the total per-request cost here is
    # O(len(paper_ids)) instead of O(corpus). Falls back to a single
    # ``all_papers`` scan if the repo predates the indexed accessor (older
    # tests still stub the legacy shape).
    papers = []
    skipped_ids: list[str] = []
    getter = getattr(repo, "get_by_id", None)
    if callable(getter):
        for pid in req.paper_ids:
            paper = getter(pid)
            if paper is None:
                skipped_ids.append(pid)
            else:
                papers.append(paper)
    else:  # pragma: no cover - legacy stub path
        by_id = {p.id: p for p in repo.all_papers()}
        for pid in req.paper_ids:
            paper = by_id.get(pid)
            if paper is None:
                skipped_ids.append(pid)
            else:
                papers.append(paper)
    if skipped_ids:
        # Not fatal — the model can still rank the survivors — but worth
        # logging because it usually means the caller passed stale ids.
        # The caller also gets a ``skipped_ids`` field in the response so
        # the drop is visible without diffing request against response.
        logger.info("Re-rank skipped %d unknown paper id(s)", len(skipped_ids))

    if not papers:
        # Every requested id was stale: there is nothing to send to the
        # LLM. Short-circuit at the handler so we don't even touch the
        # ``rank_papers`` provider-resolution path (which would otherwise
        # also reach an empty-papers branch but cost an extra import and
        # one extra hop). The response advertises an empty ``ordered``
        # plus the full ``skipped_ids`` so the UI can render a single
        # "all results stale, please refresh" notice.
        body = RerankResponse(
            ordered=[],
            skipped_ids=skipped_ids,
            timecost_ms=0.0,
            model="",
            provider="",
            protocol="",
        )
        return jsonify(body.model_dump())

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
        skipped_ids=skipped_ids,
        timecost_ms=round(result.elapsed_ms, 1),
        model=result.model,
        provider=result.provider,
        protocol=result.protocol,
    )
    return jsonify(body.model_dump())

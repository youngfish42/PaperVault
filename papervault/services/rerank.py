"""LLM-powered post-search re-ranking service.

Given a query and a small batch of already-retrieved papers (typically the
top few hundred returned by the search backend), ask the configured LLM to
judge each paper's relevance to the query and return an ordered ``id→score``
list. The caller can then re-sort the visible results by that score.

Design notes
------------
* **Dispatch reuse.** Provider / model / API-key resolution is identical to
  ``services.suggest``; we import ``_resolve_provider`` from there instead
  of duplicating it, so adding a new preset automatically lights up both
  endpoints.

* **Bounded input.** ``paper_ids`` is capped at 300 server-side via the
  pydantic schema. With a 280-char abstract truncation that's still ~30k
  input tokens, but capped to keep any single call tractable. The caller is
  expected to feed the top-N by some cheap pre-sort (typically year desc).

* **Robust JSON parsing.** LLMs frequently wrap JSON in code fences or
  stuff a leading paragraph before it. We accept both, fall back to a
  regex on the first ``{...}`` block if json.loads fails, and finally
  raise ``UpstreamError`` only when nothing parses.

* **Score normalisation.** Output is constrained to ``[0, 1]`` at the
  schema layer. Inputs above 1 are divided by 10 (handles models that
  score 0-10 by reflex) or 100 (0-100); inputs already in range are kept.
  The schema will then reject anything outside ``[0, 1]`` so a bad LLM
  emit can never leak a >1 score to the UI.

* **Missing items.** Any input ``paper_id`` the LLM forgets to score is
  appended at the tail with score 0.5 so the caller never loses entries
  from its batch.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from ..errors import ApiError, UpstreamError
from .ai_clients import ChatResult, call_anthropic, call_openai_compatible
from .ai_providers import (
    PROTOCOL_ANTHROPIC,
    PROTOCOL_OPENAI,
)
from .suggest import ProviderHints, _resolve_provider, _settings_or_none

logger = logging.getLogger("papervault.rerank")


@dataclass(slots=True)
class RerankResult:
    """Re-rank outcome mirroring ``SuggestionResult``."""

    ordered_ids: List[str]
    scores: Dict[str, float]
    model: str
    elapsed_ms: float
    provider: str
    protocol: str


@dataclass(slots=True)
class _PaperSnippet:
    """Minimal paper view passed through the LLM prompt.

    Only the fields the model needs to judge relevance. ``id`` is the 16-char
    ``Paper.id`` SHA-1 prefix — same identifier the search backend returns
    so callers can re-hydrate full records on the wire.
    """

    id: str
    title: str
    authors: List[str]
    abstract: Optional[str]


# Title + author line + first ~280 chars of abstract. Calibrated so 200
# papers fit comfortably inside ~30k tokens for the LLM.
_ABSTRACT_MAX_CHARS = 280
_AUTHORS_MAX = 4

_FENCED_RE = re.compile(r"```(?:json)?(.*?)```", flags=re.DOTALL)
_OBJECT_RE = re.compile(r"\{.*\}", flags=re.DOTALL)


def _truncate_abstract(text: Optional[str]) -> str:
    if not text:
        return ""
    text = text.strip()
    if len(text) <= _ABSTRACT_MAX_CHARS:
        return text
    return text[: _ABSTRACT_MAX_CHARS - 1].rstrip() + "…"


def _snippet(paper) -> _PaperSnippet:
    """Coerce a ``Paper`` dataclass (or anything with the same fields) into a snippet."""
    return _PaperSnippet(
        id=paper.id,
        title=(paper.title or "").strip(),
        authors=list(paper.authors or [])[:_AUTHORS_MAX],
        abstract=_truncate_abstract(getattr(paper, "abstract", None)),
    )


def _build_prompt(query: str, papers: Sequence[_PaperSnippet]) -> Tuple[str, str]:
    """Construct the (system, user) prompt for relevance ranking.

    The user payload is a numbered list of compact paper summaries plus a
    strict instruction to emit exactly one JSON object matching the schema.
    """

    system = (
        "You are a relevance judge for a computer-science paper search engine. "
        "Given a user query and a batch of candidate papers, score each "
        "paper's relevance to the query on a 0.0–1.0 scale (1.0 = perfect "
        "match, 0.0 = unrelated). Return your answer as strict JSON only."
    )

    lines = [
        f'Query: "{query}"',
        "",
        "Papers:",
    ]
    for idx, p in enumerate(papers, 1):
        authors = ", ".join(p.authors) if p.authors else "(no authors)"
        abstract = p.abstract or "(no abstract)"
        lines.append(f"[{idx}] id={p.id}")
        lines.append(f"    title: {p.title}")
        lines.append(f"    authors: {authors}")
        lines.append(f"    abstract: {abstract}")
        lines.append("")

    lines.append(
        'Return a single JSON object of the form '
        '{"ordered":[{"paper_id":"<id>","score":0.0}, ...]} '
        "with paper_id values copied verbatim from the input above. "
        "Include every paper you received. The list order is your ranking "
        "(most relevant first). Do not write any prose outside the JSON."
    )

    return system, "\n".join(lines)


def _normalise_score(raw) -> Optional[float]:
    """Map an LLM-emitted score onto ``[0.0, 1.0]``.

    Different models use 0-10, 0-100, or 0-1; we accept any. Negative
    values are clamped to 0; values >100 are rejected (clearly out of any
    sensible scale) and return ``None`` so the caller can skip them rather
    than emit a schema-invalid entry.
    """

    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    if v != v:  # NaN
        return None
    # Common out-of-range scales: 0–10 and 0–100.
    if v > 1.0:
        if v <= 10.0:
            v = v / 10.0
        elif v <= 100.0:
            v = v / 100.0
        else:
            return None
    elif v < 0.0:
        v = 0.0
    return max(0.0, min(1.0, v))


def _parse_rerank_response(
    content: str, expected_ids: Sequence[str]
) -> Dict[str, float]:
    """Decode an LLM emit into ``{paper_id: score}``.

    Tried in order: strict ``json.loads`` on the whole string, then a
    fenced-code-block extraction, then a single-object regex extraction.
    Raises ``UpstreamError`` if none of these yield a parseable payload
    or if the payload is not shaped like the expected object.
    """

    payload: Optional[str] = None
    for extract in (
        lambda: content.strip(),
        lambda: (m.group(1) if (m := _FENCED_RE.search(content)) else None),
        lambda: (m.group(0) if (m := _OBJECT_RE.search(content)) else None),
    ):
        try:
            candidate = extract()
        except Exception:
            candidate = None
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            payload = candidate
            break

    if payload is None:
        logger.warning("Cannot parse rerank JSON: %r", content[:200])
        raise UpstreamError(
            "Rerank provider returned non-JSON output.",
            code="LLM_BAD_JSON",
        )

    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:  # pragma: no cover - already validated above
        raise UpstreamError(
            "Rerank provider returned non-JSON output.",
            code="LLM_BAD_JSON",
        ) from exc

    ordered = parsed.get("ordered") if isinstance(parsed, dict) else None
    if not isinstance(ordered, list):
        raise UpstreamError(
            "Rerank provider returned no ordered list.",
            code="LLM_BAD_JSON",
        )

    expected = set(expected_ids)
    scores: Dict[str, float] = {}
    # Observability counters: silently dropping malformed scores or
    # invented ids used to be invisible at the call site. Aggregate the
    # drops here and emit one ``logger.info`` line per parse so a
    # spike (e.g. model regressed to emitting ``score=150`` for every
    # item) shows up in normal request logs instead of requiring a
    # bespoke metric.
    invented_ids = 0
    unparseable_scores = 0
    for item in ordered:
        if not isinstance(item, dict):
            continue
        pid = item.get("paper_id")
        if not isinstance(pid, str):
            continue
        if pid not in expected:
            # The LLM sometimes invents ids; ignore them silently rather
            # than erroring out. Strict schema validation happens upstream.
            invented_ids += 1
            continue
        raw_score = item.get("score")
        score = _normalise_score(raw_score)
        if score is None:
            # Out-of-range (>100), NaN, or non-numeric. We still want
            # the paper in the response (the ``missing`` tail in
            # ``rank_papers`` will assign it 0.5), so just count and
            # move on.
            unparseable_scores += 1
            continue
        scores[pid] = score

    if invented_ids or unparseable_scores:
        logger.info(
            "Rerank parse: kept=%d invented=%d unparseable_score=%d",
            len(scores),
            invented_ids,
            unparseable_scores,
        )

    return scores


def rank_papers(
    query: str,
    papers: Sequence,
    *,
    provider: Optional[str] = None,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    protocol: Optional[str] = None,
    temperature: Optional[float] = None,
) -> RerankResult:
    """Score and order ``papers`` by LLM-judged relevance to ``query``.

    ``papers`` may be ``Paper`` dataclasses (from ``services.papers``) or
    anything that exposes ``.id``, ``.title``, ``.authors``, ``.abstract``.
    The function does not mutate the input list.
    """

    if not query or not query.strip():
        raise ApiError(
            "Re-rank query must not be empty.",
            status_code=400,
            code="BAD_REQUEST",
        )
    if not papers:
        # No papers to rank → return an explicit empty result without
        # touching the LLM. Provider / protocol / model fields are empty
        # strings instead of the legacy ``"-"`` placeholder so the UI can
        # treat "empty string" as "no provider was actually consulted"
        # rather than rendering a literal dash.
        return RerankResult(
            ordered_ids=[],
            scores={},
            model="",
            elapsed_ms=0.0,
            provider="",
            protocol="",
        )

    snippets = [_snippet(p) for p in papers]
    id_order = [s.id for s in snippets]
    # ``_resolve_provider`` already accepts any duck-typed object with
    # the five dispatch fields; the dedicated ``ProviderHints`` dataclass
    # avoids dragging unused keyword-suggestion fields (``max_keywords`` /
    # ``temperature`` default) into rerank's call site.
    hints = ProviderHints(
        provider=provider,
        base_url=base_url,
        model=model,
        api_key=api_key,
        protocol=protocol,
    )
    resolved = _resolve_provider(hints)

    # ``temperature`` defaults to 0.0 for rerank because deterministic
    # ordering is more useful than diverse suggestions; callers can still
    # override.
    effective_temperature = temperature if temperature is not None else 0.0

    # Anthropic requires ``max_tokens``; use settings fallback chain.
    if resolved.protocol == PROTOCOL_ANTHROPIC:
        settings = _settings_or_none()
        anthropic_max_tokens = int(
            getattr(settings, "anthropic_max_tokens", 0) or 2048
        )
    else:
        anthropic_max_tokens = None  # unused on openai-compatible path

    system, user = _build_prompt(query, snippets)
    start = time.time()
    if resolved.protocol == PROTOCOL_ANTHROPIC:
        chat: ChatResult = call_anthropic(
            api_key=resolved.api_key,
            base_url=resolved.base_url,
            model=resolved.model,
            system=system,
            user=user,
            temperature=effective_temperature,
            max_tokens=anthropic_max_tokens,
        )
    elif resolved.protocol == PROTOCOL_OPENAI:
        chat = call_openai_compatible(
            api_key=resolved.api_key,
            base_url=resolved.base_url,
            model=resolved.model,
            system=system,
            user=user,
            temperature=effective_temperature,
        )
    else:  # pragma: no cover - ``_resolve_provider`` rejects unknown protocols
        raise ApiError(
            f"Unsupported protocol: {resolved.protocol!r}",
            status_code=400,
            code="BAD_REQUEST",
        )
    elapsed_ms = (time.time() - start) * 1000.0

    scores = _parse_rerank_response(chat.content, id_order)

    # Two-pass ordering:
    #   1. Scored papers come first, descending by score.
    #   2. Any paper_id the LLM forgot to score is appended at the tail
    #      with a neutral 0.5 score so the caller never loses items.
    scored_ids = sorted(
        scores.keys(), key=lambda pid: scores[pid], reverse=True
    )
    missing = [pid for pid in id_order if pid not in scores]
    for pid in missing:
        scores[pid] = 0.5

    return RerankResult(
        ordered_ids=scored_ids + missing,
        scores=scores,
        model=chat.raw_model or resolved.model,
        elapsed_ms=elapsed_ms,
        provider=resolved.preset.key,
        protocol=resolved.protocol,
    )

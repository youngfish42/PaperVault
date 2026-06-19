"""OpenAI-powered keyword suggestion service."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import List, Tuple

from openai import OpenAI

from ..errors import UpstreamError

logger = logging.getLogger("papervault.suggest")

_FENCED_RE = re.compile(r"```json(.*?)```", flags=re.DOTALL)


@dataclass(slots=True)
class SuggestionResult:
    keywords: List[str]
    model: str
    elapsed_ms: float


def suggest_keywords(query: str, *, model: str, temperature: float,
                     max_keywords: int) -> SuggestionResult:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise UpstreamError("OPENAI_API_KEY is not configured.", code="OPENAI_NOT_CONFIGURED")

    client = OpenAI(api_key=api_key, base_url=os.environ.get("OPENAI_API_BASE"))
    prompt = (
        f'Please just return the top-{max_keywords} related keywords of papers on "{query}" '
        'in JSON format with the key named "keywords". '
        'The output must start with "```json" and end with "```".'
    )

    start = time.time()
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a helpful assistant for search suggestion of paper "
                        "in the field of artificial intelligence"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=temperature,
        )
    except Exception as exc:  # network / auth / quota
        logger.exception("OpenAI call failed: %s", exc)
        raise UpstreamError("Suggestion service is temporarily unavailable.",
                            code="OPENAI_CALL_FAILED")

    elapsed_ms = (time.time() - start) * 1000.0

    keywords, raw_model = _extract_keywords(response, max_keywords)
    return SuggestionResult(keywords=keywords, model=raw_model or model, elapsed_ms=elapsed_ms)


def _extract_keywords(response, max_keywords: int) -> Tuple[List[str], str]:
    try:
        choice = response.choices[0]
        content = choice.message.content or ""
        raw_model = getattr(response, "model", "")
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Malformed OpenAI response: %s", exc)
        raise UpstreamError("Malformed response from suggestion provider.",
                            code="OPENAI_BAD_RESPONSE")

    fenced = _FENCED_RE.search(content)
    payload = fenced.group(1) if fenced else content
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        logger.warning("Cannot parse keyword JSON: %s | content=%r", exc, content[:200])
        raise UpstreamError("Suggestion provider returned non-JSON output.",
                            code="OPENAI_BAD_JSON")

    keywords = parsed.get("keywords") if isinstance(parsed, dict) else None
    if not isinstance(keywords, list):
        raise UpstreamError("Suggestion provider returned no keywords.",
                            code="OPENAI_NO_KEYWORDS")
    keywords = [str(k) for k in keywords][:max_keywords]
    return keywords, raw_model

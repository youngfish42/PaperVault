"""LLM-powered keyword suggestion service.

This module powers the *Guess You Like* widget. It speaks the OpenAI
Chat-Completions wire protocol so it can drive either DeepSeek (default)
or OpenAI-hosted models without bringing in a second SDK.

Provider resolution order (first match wins):

1. ``PAPERVAULT_SUGGEST_PROVIDER`` env / ``settings.suggest_provider``
   explicitly pins a backend (``deepseek`` or ``openai``).
2. Otherwise, when ``DEEPSEEK_API_KEY`` is set we pick DeepSeek; when only
   ``OPENAI_API_KEY`` is set we fall back to OpenAI.
3. When neither key is set we surface a 503 so the frontend can keep the
   panel quietly empty instead of rendering a "network error" toast.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

from flask import current_app
from openai import OpenAI

from ..errors import ApiError, UpstreamError

logger = logging.getLogger("papervault.suggest")

_FENCED_RE = re.compile(r"```json(.*?)```", flags=re.DOTALL)


@dataclass(slots=True)
class SuggestionResult:
    keywords: List[str]
    model: str
    elapsed_ms: float


@dataclass(slots=True)
class _ProviderConfig:
    name: str
    api_key: str
    base_url: Optional[str]
    model: str


def _resolve_provider(model_override: Optional[str]) -> _ProviderConfig:
    """Select the LLM provider based on settings + environment.

    ``model_override`` (when truthy) wins over the provider's default model
    so callers can still pin a specific deployment per-request.
    """

    try:
        settings = current_app.extensions["settings"]
    except (RuntimeError, KeyError):  # outside Flask app context / tests
        settings = None

    preferred = (
        getattr(settings, "suggest_provider", None)
        or os.environ.get("PAPERVAULT_SUGGEST_PROVIDER", "")
    ).lower()
    deepseek_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()

    def _build_deepseek() -> _ProviderConfig:
        base_url = (
            os.environ.get("DEEPSEEK_API_BASE")
            or getattr(settings, "deepseek_base_url", None)
            or "https://api.deepseek.com"
        )
        default_model = getattr(settings, "deepseek_model", "") or "deepseek-chat"
        return _ProviderConfig(
            name="deepseek",
            api_key=deepseek_key,
            base_url=base_url,
            model=model_override or default_model,
        )

    def _build_openai() -> _ProviderConfig:
        default_model = getattr(settings, "openai_model", "") or "gpt-3.5-turbo"
        return _ProviderConfig(
            name="openai",
            api_key=openai_key,
            base_url=os.environ.get("OPENAI_API_BASE") or None,
            model=model_override or default_model,
        )

    if preferred == "deepseek":
        if not deepseek_key:
            raise ApiError(
                "DEEPSEEK_API_KEY is not configured; keyword suggestion is disabled.",
                status_code=503,
                code="LLM_NOT_CONFIGURED",
            )
        return _build_deepseek()

    if preferred == "openai":
        if not openai_key:
            raise ApiError(
                "OPENAI_API_KEY is not configured; keyword suggestion is disabled.",
                status_code=503,
                code="LLM_NOT_CONFIGURED",
            )
        return _build_openai()

    if deepseek_key:
        return _build_deepseek()
    if openai_key:
        return _build_openai()

    raise ApiError(
        "Neither DEEPSEEK_API_KEY nor OPENAI_API_KEY is configured; "
        "keyword suggestion is disabled.",
        status_code=503,
        code="LLM_NOT_CONFIGURED",
    )


def suggest_keywords(query: str, *, model: str, temperature: float,
                     max_keywords: int) -> SuggestionResult:
    provider = _resolve_provider(model_override=model)

    client = OpenAI(api_key=provider.api_key, base_url=provider.base_url)
    prompt = (
        f'Please just return the top-{max_keywords} related keywords of papers on "{query}" '
        'in JSON format with the key named "keywords". '
        'The output must start with "```json" and end with "```".'
    )

    start = time.time()
    try:
        response = client.chat.completions.create(
            model=provider.model,
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
        logger.exception(
            "%s call failed: %s", provider.name, exc
        )
        raise UpstreamError(
            "Suggestion service is temporarily unavailable.",
            code="LLM_CALL_FAILED",
        )

    elapsed_ms = (time.time() - start) * 1000.0

    keywords, raw_model = _extract_keywords(response, max_keywords)
    return SuggestionResult(
        keywords=keywords,
        model=raw_model or provider.model,
        elapsed_ms=elapsed_ms,
    )


def _extract_keywords(response, max_keywords: int) -> Tuple[List[str], str]:
    try:
        choice = response.choices[0]
        content = choice.message.content or ""
        raw_model = getattr(response, "model", "")
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Malformed LLM response: %s", exc)
        raise UpstreamError("Malformed response from suggestion provider.",
                            code="LLM_BAD_RESPONSE")

    fenced = _FENCED_RE.search(content)
    payload = fenced.group(1) if fenced else content
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        logger.warning("Cannot parse keyword JSON: %s | content=%r", exc, content[:200])
        raise UpstreamError("Suggestion provider returned non-JSON output.",
                            code="LLM_BAD_JSON")

    keywords = parsed.get("keywords") if isinstance(parsed, dict) else None
    if not isinstance(keywords, list):
        raise UpstreamError("Suggestion provider returned no keywords.",
                            code="LLM_NO_KEYWORDS")
    keywords = [str(k) for k in keywords][:max_keywords]
    return keywords, raw_model

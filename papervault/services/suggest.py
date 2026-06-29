"""LLM-powered keyword suggestion service.

Dispatch model:

* The catalog in :mod:`papervault.services.ai_providers` owns the
  default ``protocol / base_url / model`` per provider.
* The :func:`_resolve_provider` step merges three sources in priority
  order: request overrides (``provider`` / ``base_url`` / ``model`` /
  ``api_key`` / ``protocol`` from the API caller) → Flask app settings
  → environment variables named in each preset's ``env_*_var`` fields.
* The actual wire call is delegated to
  :mod:`papervault.services.ai_clients`, which knows the OpenAI
  Chat Completions and Anthropic Messages protocols.

The legacy "DeepSeek-or-OpenAI" key detection is kept as the *last*
fallback so old deployments continue to work after upgrading.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from flask import current_app

from ..errors import ApiError, UpstreamError
from .ai_clients import ChatResult, call_anthropic, call_openai_compatible
from .ai_providers import (
    PROTOCOL_ANTHROPIC,
    PROTOCOL_OPENAI,
    ProviderPreset,
    get_preset,
)

logger = logging.getLogger("papervault.suggest")

_FENCED_RE = re.compile(r"```json(.*?)```", flags=re.DOTALL)


@dataclass(slots=True)
class SuggestionResult:
    keywords: List[str]
    model: str
    elapsed_ms: float
    provider: str
    protocol: str


@dataclass(slots=True)
class SuggestionRequest:
    query: str
    provider: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    api_key: Optional[str] = None
    protocol: Optional[str] = None
    temperature: float = 0.5
    max_keywords: int = 10
    max_tokens: int = 512


@dataclass(slots=True)
class _ResolvedProvider:
    preset: ProviderPreset
    api_key: str
    base_url: str
    model: str
    protocol: str
    source: str = field(default="env")


def _settings_or_none():
    try:
        return current_app.extensions["settings"]
    except (RuntimeError, KeyError):
        return None


def _resolve_provider(req: SuggestionRequest) -> _ResolvedProvider:
    """Pick the preset and resolve all dispatch fields.

    Priority for each field (highest first):

    1. ``req`` — explicit value passed by the API handler.
    2. ``settings.<key>`` — Flask app settings (env-driven).
    3. ``os.environ[<env_key_var>]`` / legacy DeepSeek↔OpenAI fallback.

    ``api_key`` *only* falls back to env vars — it is never read from
    global Flask settings to avoid leaking into templates.
    """

    settings = _settings_or_none()
    explicit_provider = req.provider or getattr(settings, "suggest_provider", None)
    preset = get_preset(explicit_provider)

    # If the caller did not pin a provider, fall back to whichever of the
    # long-standing legacy keys (DeepSeek / OpenAI) is set. This keeps
    # pre-P2 deployments working without forcing operators to rename env
    # vars the day they upgrade.
    if not explicit_provider and preset.key == "custom":
        legacy = _legacy_provider()
        if legacy is not None:
            preset = get_preset(legacy)

    base_url = (
        req.base_url
        or (preset.base_url if preset.key != "custom" else "")
        or os.environ.get(preset.env_base_var, "").strip()
    ).strip().rstrip("/") or None

    model = (
        req.model
        or os.environ.get(preset.env_model_var, "").strip()
        or (preset.model if preset.key != "custom" else "")
    ).strip()

    protocol = (req.protocol or preset.protocol).strip().lower()
    if protocol not in {PROTOCOL_OPENAI, PROTOCOL_ANTHROPIC}:
        raise ApiError(
            f"Unsupported protocol: {protocol!r}",
            status_code=400,
            code="BAD_REQUEST",
        )

    if preset.key == "custom" and (not base_url or not model):
        raise ApiError(
            "Custom provider requires both `base_url` and `model`.",
            status_code=400,
            code="BAD_REQUEST",
        )

    api_key = (req.api_key or os.environ.get(preset.env_key_var, "")).strip()
    if not api_key:
        api_key = _legacy_key_fallback(preset.key)

    if not api_key:
        if preset.key == "custom":
            raise ApiError(
                "Custom provider requires an `api_key`.",
                status_code=400,
                code="BAD_REQUEST",
            )
        env_name = preset.env_key_var or "(unset preset)"
        raise ApiError(
            f"{env_name} is not configured; keyword suggestion is disabled.",
            status_code=503,
            code="LLM_NOT_CONFIGURED",
        )

    return _ResolvedProvider(
        preset=preset,
        api_key=api_key,
        base_url=base_url or preset.base_url,
        model=model,
        protocol=protocol,
        source="request" if req.api_key else "env",
    )


def _legacy_provider() -> Optional[str]:
    """Return ``"deepseek"`` / ``"openai"`` if their legacy key is set, else None."""

    if os.environ.get("DEEPSEEK_API_KEY", "").strip():
        return "deepseek"
    if os.environ.get("OPENAI_API_KEY", "").strip():
        return "openai"
    return None


def _legacy_key_fallback(provider_key: str) -> str:
    """Bridge legacy DeepSeek↔OpenAI defaults for back-compat.

    The pre-P2 dispatch used environment-only detection. After P2 the
    catalog is the source of truth, but a long-running deployment with
    only ``DEEPSEEK_API_KEY`` / ``OPENAI_API_KEY`` set should still pick
    up a provider without forcing every operator to rename env vars.
    """

    if provider_key == "deepseek":
        return os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if provider_key == "openai":
        return os.environ.get("OPENAI_API_KEY", "").strip()
    return ""


def _build_prompt(query: str, max_keywords: int) -> Tuple[str, str]:
    system = (
        "You are a helpful assistant for search suggestion of paper "
        "in the field of artificial intelligence"
    )
    user = (
        f'Please just return the top-{max_keywords} related keywords of papers on "{query}" '
        'in JSON format with the key named "keywords". '
        'The output must start with "```json" and end with "```".'
    )
    return system, user


def suggest_keywords(req: SuggestionRequest) -> SuggestionResult:
    resolved = _resolve_provider(req)
    system, user = _build_prompt(req.query, req.max_keywords)

    start = time.time()
    if resolved.protocol == PROTOCOL_ANTHROPIC:
        result: ChatResult = call_anthropic(
            api_key=resolved.api_key,
            base_url=resolved.base_url,
            model=resolved.model,
            system=system,
            user=user,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
        )
    else:
        result = call_openai_compatible(
            api_key=resolved.api_key,
            base_url=resolved.base_url,
            model=resolved.model,
            system=system,
            user=user,
            temperature=req.temperature,
        )

    elapsed_ms = (time.time() - start) * 1000.0
    keywords, raw_model = _extract_keywords(result.content, req.max_keywords)
    return SuggestionResult(
        keywords=keywords,
        model=raw_model or resolved.model,
        elapsed_ms=elapsed_ms,
        provider=resolved.preset.key,
        protocol=resolved.protocol,
    )


def _extract_keywords(content: str, max_keywords: int) -> Tuple[List[str], str]:
    fenced = _FENCED_RE.search(content)
    payload = fenced.group(1) if fenced else content
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        logger.warning("Cannot parse keyword JSON: %s | content=%r", exc, content[:200])
        raise UpstreamError(
            "Suggestion provider returned non-JSON output.",
            code="LLM_BAD_JSON",
        )

    keywords = parsed.get("keywords") if isinstance(parsed, dict) else None
    if not isinstance(keywords, list):
        raise UpstreamError(
            "Suggestion provider returned no keywords.",
            code="LLM_NO_KEYWORDS",
        )
    return [str(k) for k in keywords][:max_keywords], ""

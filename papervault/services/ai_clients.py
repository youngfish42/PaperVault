"""Vendor-neutral SDK dispatch for LLM calls.

Two functions, one per wire format. They exist in their own module so the
suggest service (today) and the future AI search service (P3) can share
exactly one place to add retry / timeout / token-usage logging.

Both functions return a :class:`ChatResult` carrying the assistant text
and the *raw* model id echoed by the upstream. We deliberately do not
parse JSON or otherwise shape the response here — each caller knows
what schema it expects.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, List, Optional

from ..errors import UpstreamError

logger = logging.getLogger("papervault.ai_clients")


@dataclass(slots=True)
class ChatResult:
    content: str
    raw_model: str


def call_openai_compatible(
    *,
    api_key: str,
    base_url: Optional[str],
    model: str,
    system: str,
    user: str,
    temperature: float,
    max_tokens: Optional[int] = None,
    timeout: float = 30.0,
) -> ChatResult:
    """Issue a Chat Completions request via the official ``openai`` SDK.

    Args:
        max_tokens: Optional maximum tokens for the response.
            Not all OpenAI-compatible providers support this parameter.
    """

    if not api_key:
        raise UpstreamError(
            "API key is not configured for this provider.",
            code="LLM_NOT_CONFIGURED",
        )

    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
    create_kwargs = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
    }
    if max_tokens is not None and max_tokens > 0:
        create_kwargs["max_tokens"] = max_tokens

    try:
        response = client.chat.completions.create(**create_kwargs)
    except Exception as exc:
        logger.exception("OpenAI-compatible call failed: %s", exc)
        raise UpstreamError(
            "Suggestion service is temporarily unavailable.",
            code="LLM_CALL_FAILED",
        )

    try:
        choice = response.choices[0]
        content = choice.message.content or ""
        raw_model = getattr(response, "model", "") or model
    except Exception as exc:
        logger.exception("Malformed OpenAI-compatible response: %s", exc)
        raise UpstreamError(
            "Suggestion provider returned a malformed response.",
            code="LLM_BAD_RESPONSE",
        )

    return ChatResult(content=content, raw_model=raw_model)


def call_anthropic(
    *,
    api_key: str,
    base_url: str,
    model: str,
    system: str,
    user: str,
    temperature: float,
    max_tokens: int = 512,
    timeout: float = 30.0,
    extra_headers: Optional[Iterable[tuple]] = None,
) -> ChatResult:
    """Issue a Messages request via the official ``anthropic`` SDK.

    ``base_url`` is the *endpoint root*, not the full ``/v1/messages`` URL.
    The official SDK always appends ``/v1/messages`` to whatever base URL
    it is given, so we normalise away a trailing ``/v1`` first; this
    means both ``https://api.anthropic.com`` and the StepFun-style
    ``https://api.stepfun.com/step_plan/v1`` resolve to the same wire
    path without forcing callers to memorise the SDK's convention.
    ``StepFun.step_plan`` and Anthropic's native API both speak this
    protocol, so a single function covers both.
    """

    if not api_key:
        raise UpstreamError(
            "API key is not configured for this provider.",
            code="LLM_NOT_CONFIGURED",
        )

    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - import guard
        raise UpstreamError(
            "anthropic SDK is not installed; run `pip install 'anthropic>=0.40'`.",
            code="LLM_SDK_MISSING",
        ) from exc

    sdk_base = base_url.rstrip("/")
    if sdk_base.endswith("/v1"):
        sdk_base = sdk_base[: -len("/v1")]

    kwargs: dict = {
        "api_key": api_key,
        "base_url": sdk_base,
        "timeout": timeout,
    }
    if extra_headers:
        kwargs["default_headers"] = dict(extra_headers)

    client = anthropic.Anthropic(**kwargs)
    try:
        response = client.messages.create(
            model=model,
            system=system,
            messages=[{"role": "user", "content": user}],
            temperature=temperature,
            max_tokens=max_tokens,
        )
    except Exception as exc:
        logger.exception("Anthropic-compatible call failed: %s", exc)
        raise UpstreamError(
            "Suggestion service is temporarily unavailable.",
            code="LLM_CALL_FAILED",
        )

    try:
        parts: List[str] = []
        for block in response.content:
            text = getattr(block, "text", None)
            if text:
                parts.append(text)
        content = "".join(parts)
        raw_model = getattr(response, "model", "") or model
    except Exception as exc:
        logger.exception("Malformed Anthropic-compatible response: %s", exc)
        raise UpstreamError(
            "Suggestion provider returned a malformed response.",
            code="LLM_BAD_RESPONSE",
        )

    return ChatResult(content=content, raw_model=raw_model)

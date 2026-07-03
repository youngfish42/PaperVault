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
    json_mode: bool = False,
) -> ChatResult:
    """Issue a Chat Completions request via the official ``openai`` SDK.

    Args:
        max_tokens: Optional maximum tokens for the response.
            Not all OpenAI-compatible providers support this parameter.
        json_mode: When ``True`` the request opts into the provider's
            native JSON-output mode (``response_format={"type": "json_object"}``).
            This is best-effort: if the provider rejects the flag with a
            400 we fall back to the plain call. Pure OpenAI, DeepSeek and
            most well-behaved OpenAI-compatible endpoints honor it.
    """

    if not api_key:
        raise UpstreamError(
            "API key is not configured for this provider.",
            code="LLM_NOT_CONFIGURED",
        )

    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
    create_kwargs: dict[str, object] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
    }
    if max_tokens is not None and max_tokens > 0:
        create_kwargs["max_tokens"] = max_tokens
    if json_mode:
        create_kwargs["response_format"] = {"type": "json_object"}

    try:
        try:
            response = client.chat.completions.create(**create_kwargs)
        except Exception as exc:
            # Some OpenAI-compatible vendors reject ``response_format`` with
            # a 400. Detect via a small keyword whitelist so we survive
            # provider-specific / localised error phrasings (some Chinese
            # gateways translate everything but the parameter name; a few
            # only say ``unsupported parameter`` without echoing the field).
            # Any of these hits triggers the plain retry.
            msg = str(exc).lower() if exc else ""
            fallback_markers = (
                "response_format",
                "json_object",
                "unsupported parameter",
                "unknown parameter",
                "invalid parameter",
                "not supported",
            )
            if json_mode and any(m in msg for m in fallback_markers):
                logger.warning(
                    "Provider %s rejected response_format=json_object; retrying plain.",
                    model,
                )
                create_kwargs.pop("response_format", None)
                response = client.chat.completions.create(**create_kwargs)
            else:
                raise
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
    # ``StepFun.step_plan`` enables a "thinking" mode by default that
    # consumes the entire ``max_tokens`` budget on internal reasoning and
    # returns ``stop_reason='max_tokens'`` with no visible text. The
    # ``thinking`` field is not part of the official Anthropic SDK
    # signature (the SDK forwards unknown kwargs through ``extra_body``),
    # so we pass it via ``client.messages.create(..., extra_body=...)``
    # below. Anthropic's native API treats ``type: disabled`` as a
    # no-op (its default state), so the same payload is safe to send
    # everywhere.
    try:
        try:
            response = client.messages.create(
                model=model,
                system=system,
                messages=[{"role": "user", "content": user}],
                temperature=temperature,
                max_tokens=max_tokens,
                extra_body={"thinking": {"type": "disabled"}},
            )
        except TypeError as type_exc:
            # Older anthropic SDK (<0.40) does not accept ``extra_body`` on
            # ``messages.create``; fall back to the plain call so callers
            # with pinned SDK versions still work. We narrow the trigger to
            # the specific kwargs-rejection message so unrelated TypeErrors
            # (e.g. a future SDK renaming a stable kwarg) are not silently
            # swallowed into a retry.
            msg = str(type_exc)
            if "extra_body" not in msg and "unexpected keyword" not in msg:
                raise
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

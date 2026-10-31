"""Zhihu OpenAPI OAuth client (site login flow).

Ported from the deepseek-harness-server implementation: authorization URL
generation plus authorization-code exchange (token + user profile) against
``openapi.zhihu.com``. Note that Zhihu's protocol differs from standard
OAuth2 wording: the authorize step takes ``app_id`` (not ``client_id``) and
the token step authenticates with ``app_id`` + ``app_key``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from typing import Any
from urllib.parse import urlencode

import requests

from ..config import Settings

JsonMapping = Mapping[str, Any]


class ZhihuOAuthError(RuntimeError):
    def __init__(
        self,
        stage: str,
        message: str,
        *,
        payload: Any | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(f"Zhihu OAuth {stage} request failed: {message}")
        self.stage = stage
        self.payload = payload
        self.status_code = status_code


@dataclass(frozen=True)
class ZhihuOAuthProfile:
    open_id: str
    account: str
    name: str
    headline: str
    avatar_url: str | None = None


@dataclass(frozen=True)
class ZhihuOAuthExchangeResult:
    access_token: str
    profile: ZhihuOAuthProfile
    expires_at: str | None = None
    refresh_token: str | None = None
    raw: dict[str, Any] | None = None


def build_authorization_url(settings: Settings, *, state: str, redirect_uri: str) -> str:
    """Zhihu authorize URL. The query param is ``app_id``, not ``client_id``."""

    query = {
        "response_type": "code",
        "app_id": settings.zhihu_client_id,
        "redirect_uri": redirect_uri,
        "state": state,
    }
    return f"{settings.zhihu_authorize_url}?{urlencode(query)}"


def exchange_code(
    settings: Settings,
    code: str,
    *,
    redirect_uri: str | None = None,
) -> ZhihuOAuthExchangeResult:
    """Exchange an authorization code for an access token + user profile."""

    payload: dict[str, str] = {
        "grant_type": "authorization_code",
        "app_id": settings.zhihu_client_id,
        "app_key": settings.zhihu_client_secret,
        "code": code,
    }
    if redirect_uri:
        payload["redirect_uri"] = redirect_uri
    try:
        response = requests.post(
            settings.zhihu_token_url,
            data=payload,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            timeout=settings.zhihu_timeout_seconds,
        )
        token_payload = _json_response(response, "token")
    except requests.RequestException as exc:
        raise ZhihuOAuthError("token", f"network error: {exc}") from exc

    access_token = token_payload["access_token"]
    # Zhihu may embed the profile directly in the token response; only hit
    # /user when it didn't. A login without any usable profile data is
    # treated as a failure rather than an anonymous session.
    profile_payload = _fetch_profile(settings, access_token) or _profile_payload(token_payload)
    if not _has_profile_data(profile_payload):
        raise ZhihuOAuthError("profile", "no usable profile data returned by Zhihu", payload=token_payload)
    profile = _build_profile(token_payload, profile_payload)
    return ZhihuOAuthExchangeResult(
        access_token=access_token,
        refresh_token=_first_str([token_payload], "refresh_token", "refreshToken"),
        expires_at=_expires_at(_first_int([token_payload], "expires_in", "expiresIn")),
        profile=profile,
        raw=dict(token_payload),
    )


def _fetch_profile(settings: Settings, access_token: str) -> dict[str, Any] | None:
    if not settings.zhihu_user_url:
        return None
    response = requests.get(
        settings.zhihu_user_url,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {access_token}",
        },
        timeout=settings.zhihu_timeout_seconds,
    )
    payload = _json_response(response, "profile")
    extracted = _extract_payload(payload)
    return dict(extracted) if isinstance(extracted, Mapping) else None


def _json_response(response: requests.Response, stage: str) -> dict[str, Any]:
    """Parse a Zhihu API response, tolerating its non-standard envelope.

    Zhihu wraps payloads in ``data``/``payload``/``result`` and reports
    failures as HTTP 200 with a business ``code`` (0/20000/200 mean ok).
    Redirects usually mean wrong credentials or endpoint.
    """

    if 300 <= response.status_code < 400:
        raise ZhihuOAuthError(
            stage,
            "endpoint redirected; check OAuth URL and credentials",
            status_code=response.status_code,
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise ZhihuOAuthError(stage, "endpoint returned non-JSON response", status_code=response.status_code) from exc
    if not response.ok:
        message = _error_message(payload) or _payload_summary(payload) or response.text[:200] or response.reason_phrase
        raise ZhihuOAuthError(stage, message, payload=payload, status_code=response.status_code)
    if not isinstance(payload, dict):
        raise ZhihuOAuthError(stage, "endpoint returned invalid JSON", status_code=response.status_code)
    code = payload.get("code")
    if not _is_success_code(code) or _oauth_error_details(payload):
        message = _error_message(payload) or _payload_summary(payload) or "request failed"
        raise ZhihuOAuthError(stage, message, payload=payload, status_code=response.status_code)
    extracted = _extract_payload(payload)
    return dict(extracted)


# --- payload parsing helpers ----------------------------------------------


def _extract_payload(payload: JsonMapping) -> JsonMapping:
    for key in ("data", "payload", "result"):
        value = payload.get(key)
        if isinstance(value, Mapping):
            return value
    return payload


def _profile_payload(*payloads: JsonMapping) -> dict[str, Any] | None:
    for payload in payloads:
        for key in ("profile", "user", "member", "me", "viewer", "account"):
            value = payload.get(key)
            if isinstance(value, Mapping):
                return dict(value)
    return None


def _has_profile_data(payload: JsonMapping | None) -> bool:
    if payload is None:
        return False
    for key in (
        "uid", "hash_id", "url_token", "account", "name", "fullname",
        "username", "headline", "description", "avatar_path", "avatarUrl",
        "avatar_url", "open_id", "openid", "openId", "id", "sub",
    ):
        value = payload.get(key)
        if value is not None and str(value).strip():
            return True
    return False


def _build_profile(
    token_payload: JsonMapping,
    profile_payload: Mapping[str, Any] | None,
) -> ZhihuOAuthProfile:
    candidates: list[JsonMapping] = []
    if profile_payload is not None:
        candidates.append(profile_payload)
    candidates.append(token_payload)

    open_id = _first_str(
        candidates,
        "open_id", "openid", "openId", "hash_id", "hashId",
        "user_id", "userId", "uid", "id", "sub",
    )
    if not open_id:
        open_id = f"zhihu-oauth-{sha256(str(token_payload.get('access_token', '')).encode('utf-8')).hexdigest()[:24]}"

    account = _first_str(
        candidates, "account", "url_token", "urlToken", "hash_id", "hashId", "username", "slug",
    ) or f"uid-{open_id}"
    name = _first_str(
        candidates, "name", "fullname", "username", "full_name", "screen_name", "nickname",
    ) or account
    headline = _first_str(
        candidates, "headline", "description", "bio", "tagline", "business",
    ) or "知乎账号"
    avatar_url = _first_str(
        candidates, "avatar_url", "avatarUrl", "avatar_path", "avatarPath", "avatar", "image_url",
    )

    return ZhihuOAuthProfile(
        open_id=open_id,
        account=account,
        name=name,
        headline=headline,
        avatar_url=avatar_url,
    )


def _first_str(payloads: list[JsonMapping], *keys: str) -> str | None:
    for payload in payloads:
        for key in keys:
            value = payload.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
    return None


def _first_int(payloads: list[JsonMapping], *keys: str) -> int | None:
    text = _first_str(payloads, *keys)
    if text is None:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _expires_at(expires_in: int | None) -> str | None:
    if expires_in is None or expires_in <= 0:
        return None
    return (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()


def _error_message(payload: Any) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    for key in ("error_description", "error", "message", "msg", "detail"):
        value = payload.get(key)
        if value and not isinstance(value, Mapping):
            return str(value)
    nested = payload.get("data") or payload.get("payload")
    if isinstance(nested, Mapping):
        return _error_message(nested)
    return None


def _payload_summary(payload: Any) -> str | None:
    try:
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    except TypeError:
        return None
    text = text.strip()
    return text[:500] if text else None


def _is_success_code(code: Any) -> bool:
    if code is None:
        return True
    return str(code).strip() in {"0", "20000", "200"}


def _oauth_error_details(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        return {}
    nested_error = payload.get("error")
    if isinstance(nested_error, Mapping):
        return dict(nested_error)
    if nested_error:
        return {**dict(payload), "message": nested_error}
    if payload.get("error_description"):
        return dict(payload)
    if "code" in payload and not _is_success_code(payload.get("code")):
        return dict(payload)
    nested = payload.get("data") or payload.get("payload")
    if isinstance(nested, Mapping):
        return _oauth_error_details(nested)
    return {}

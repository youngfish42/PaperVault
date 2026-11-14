"""Regression tests for the Zhihu OpenAPI OAuth login flow.

Zhihu deviates from stock OAuth2 in three ways that this suite pins down:
the authorize step takes ``app_id`` (not ``client_id``), the callback
frequently omits the ``state`` query parameter, and the grant is returned
as ``authorization_code`` (not ``code``). All behaviours mirror the
reference implementation in deepseek-harness-server.
"""

from __future__ import annotations

import types

import pytest
import requests as real_requests

from papervault.config import Settings
from papervault.services import zhihu_oauth


class _FakeResponse:
    def __init__(self, status_code: int = 200, payload=None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.reason_phrase = ""

    @property
    def ok(self) -> bool:
        return self.status_code < 400

    def json(self):
        if self._payload is None:
            raise ValueError("no JSON payload")
        return self._payload


def _patch_requests(monkeypatch, post_response=None, get_response=None) -> dict:
    calls: dict = {"post": None, "get": None}

    def post(url, data=None, headers=None, timeout=None):
        calls["post"] = {"url": url, "data": data, "headers": headers}
        return post_response

    def get(url, headers=None, timeout=None):
        calls["get"] = {"url": url, "headers": headers}
        return get_response

    fake = types.SimpleNamespace(post=post, get=get, RequestException=real_requests.RequestException)
    monkeypatch.setattr(zhihu_oauth, "requests", fake)
    return calls


@pytest.fixture
def zhihu_client(tmp_path, sample_cache_path, monkeypatch):
    """App configured for Zhihu OAuth, built with the shared sample cache."""

    from tests.conftest import _build_app

    monkeypatch.setenv("ZHIHU_OAUTH_CLIENT_ID", "846")
    monkeypatch.setenv("ZHIHU_OAUTH_CLIENT_SECRET", "app-key")
    monkeypatch.setenv("ZHIHU_OAUTH_REDIRECT_URI", "https://papervault.top/api/v1/auth/oauth/zhihu/callback")
    app = _build_app(tmp_path, sample_cache_path, monkeypatch)
    return app.test_client()


# --- unit level: authorization URL + code exchange --------------------------


def test_authorization_url_uses_app_id_param():
    settings = Settings(
        zhihu_client_id="846",
        zhihu_client_secret="app-key",
        zhihu_authorize_url="https://openapi.zhihu.com/authorize",
    )
    url = zhihu_oauth.build_authorization_url(settings, state="st-1", redirect_uri="https://papervault.top/api/v1/auth/oauth/zhihu/callback")
    assert url.startswith("https://openapi.zhihu.com/authorize?")
    assert "app_id=846" in url
    assert "client_id=" not in url
    assert "state=st-1" in url
    assert "redirect_uri=https%3A%2F%2Fpapervault.top" in url


def test_exchange_code_parses_envelope_and_profile(monkeypatch):
    settings = Settings(zhihu_client_id="846", zhihu_client_secret="app-key")
    calls = _patch_requests(
        monkeypatch,
        post_response=_FakeResponse(payload={"code": 20000, "message": "success", "data": {"access_token": "tok123", "expires_in": 2592000, "refresh_token": "r1"}}),
        get_response=_FakeResponse(payload={"id": "ov-x1", "url_token": "guobao", "name": "国宝", "headline": "工程师", "avatar_url": "https://pic.zhimg.com/a.jpg"}),
    )

    result = zhihu_oauth.exchange_code(settings, "abc", redirect_uri="https://papervault.top/cb")

    assert calls["post"]["url"] == "https://openapi.zhihu.com/access_token"
    assert calls["post"]["data"]["grant_type"] == "authorization_code"
    assert calls["post"]["data"]["app_id"] == "846"
    assert calls["post"]["data"]["app_key"] == "app-key"
    assert calls["get"]["url"] == "https://openapi.zhihu.com/user"
    assert calls["get"]["headers"]["Authorization"] == "Bearer tok123"
    assert result.access_token == "tok123"
    assert result.profile.open_id == "ov-x1"
    assert result.profile.account == "guobao"
    assert result.profile.name == "国宝"
    assert result.profile.headline == "工程师"
    assert result.profile.avatar_url == "https://pic.zhimg.com/a.jpg"
    assert result.refresh_token == "r1"
    assert result.expires_at is not None


def test_exchange_code_accepts_profile_embedded_in_token_response(monkeypatch):
    # Apps without /user access (zhihu_user_url empty) get the profile from
    # the nested `user` object of the token response instead.
    settings = Settings(zhihu_client_id="846", zhihu_client_secret="app-key", zhihu_user_url="")
    calls = _patch_requests(
        monkeypatch,
        post_response=_FakeResponse(payload={"access_token": "tok456", "user": {"open_id": "ov-e1", "name": "内嵌用户"}}),
        get_response=None,
    )

    result = zhihu_oauth.exchange_code(settings, "abc")

    assert calls["get"] is None  # /user is skipped when the token response carries the profile
    assert result.profile.open_id == "ov-e1"
    assert result.profile.name == "内嵌用户"


def test_exchange_code_rejects_business_error(monkeypatch):
    settings = Settings(zhihu_client_id="846", zhihu_client_secret="app-key")
    _patch_requests(
        monkeypatch,
        post_response=_FakeResponse(payload={"error": "invalid_grant", "error_description": "bad code"}),
    )

    with pytest.raises(zhihu_oauth.ZhihuOAuthError):
        zhihu_oauth.exchange_code(settings, "abc")


def test_exchange_code_rejects_http_error(monkeypatch):
    settings = Settings(zhihu_client_id="846", zhihu_client_secret="app-key")
    _patch_requests(
        monkeypatch,
        post_response=_FakeResponse(status_code=401, payload={"error_description": "unauthorized"}),
    )

    with pytest.raises(zhihu_oauth.ZhihuOAuthError) as excinfo:
        zhihu_oauth.exchange_code(settings, "abc")
    assert excinfo.value.status_code == 401


# --- endpoint level: /auth/oauth/zhihu[/callback] ---------------------------


def test_zhihu_start_redirects_to_openapi(zhihu_client):
    resp = zhihu_client.get("/api/v1/auth/oauth/zhihu")
    assert resp.status_code == 302
    location = resp.headers["Location"]
    assert location.startswith("https://openapi.zhihu.com/authorize?")
    assert "app_id=846" in location
    assert "client_id=" not in location
    assert "state=" in location
    assert "redirect_uri=https%3A%2F%2Fpapervault.top%2Fapi%2Fv1%2Fauth%2Foauth%2Fzhihu%2Fcallback" in location


def test_zhihu_callback_without_state_logs_in(zhihu_client, monkeypatch):
    # Start seeds the server-side state; Zhihu then returns without `state`.
    start = zhihu_client.get("/api/v1/auth/oauth/zhihu")
    assert start.status_code == 302
    _patch_requests(
        monkeypatch,
        post_response=_FakeResponse(payload={"code": 20000, "data": {"access_token": "tok123"}}),
        get_response=_FakeResponse(payload={"id": "ov-x1", "name": "国宝", "url_token": "guobao"}),
    )
    resp = zhihu_client.get("/api/v1/auth/oauth/zhihu/callback?code=abc")
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/#/?login=success"

    me = zhihu_client.get("/api/v1/auth/me").get_json()
    assert me["authenticated"] is True
    assert me["user"]["provider"] == "zhihu"
    assert me["user"]["id"] == "ov-x1"
    assert me["user"]["name"] == "国宝"


def test_zhihu_callback_with_matching_state_logs_in(zhihu_client, monkeypatch):
    start = zhihu_client.get("/api/v1/auth/oauth/zhihu")
    assert start.status_code == 302
    state = start.headers["Location"].split("state=")[1].split("&")[0]
    _patch_requests(
        monkeypatch,
        post_response=_FakeResponse(payload={"code": 20000, "data": {"access_token": "tok123"}}),
        get_response=_FakeResponse(payload={"id": "ov-x1", "name": "国宝"}),
    )
    resp = zhihu_client.get(f"/api/v1/auth/oauth/zhihu/callback?code=abc&state={state}")
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/#/?login=success"


def test_zhihu_callback_with_authorization_code_param_logs_in(zhihu_client, monkeypatch):
    # Real Zhihu callbacks carry the grant as ``authorization_code`` (with the
    # echoed state) — no ``code`` param at all. This is the exact production
    # URL shape; it must not be rejected as an invalid state.
    start = zhihu_client.get("/api/v1/auth/oauth/zhihu")
    assert start.status_code == 302
    state = start.headers["Location"].split("state=")[1].split("&")[0]
    calls = _patch_requests(
        monkeypatch,
        post_response=_FakeResponse(payload={"code": 20000, "data": {"access_token": "tok123"}}),
        get_response=_FakeResponse(payload={"id": "ov-x1", "name": "国宝"}),
    )
    resp = zhihu_client.get(f"/api/v1/auth/oauth/zhihu/callback?state={state}&authorization_code=f36be8ed0cd14279b88756c26bd94246")
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/#/?login=success"
    assert calls["post"]["data"]["code"] == "f36be8ed0cd14279b88756c26bd94246"

    me = zhihu_client.get("/api/v1/auth/me").get_json()
    assert me["authenticated"] is True
    assert me["user"]["provider"] == "zhihu"


def test_zhihu_callback_without_code_returns_distinct_error(zhihu_client):
    # A callback that carries neither ``code`` nor ``authorization_code`` must
    # fail with its own code, not be conflated with a state mismatch.
    zhihu_client.get("/api/v1/auth/oauth/zhihu")
    resp = zhihu_client.get("/api/v1/auth/oauth/zhihu/callback")
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "MISSING_AUTHORIZATION_CODE"


def test_zhihu_callback_rejects_mismatched_state(zhihu_client):
    zhihu_client.get("/api/v1/auth/oauth/zhihu")
    resp = zhihu_client.get("/api/v1/auth/oauth/zhihu/callback?code=abc&state=wrong")
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "INVALID_OAUTH_STATE"


def test_zhihu_callback_maps_exchange_failure_to_502(zhihu_client, monkeypatch):
    zhihu_client.get("/api/v1/auth/oauth/zhihu")
    _patch_requests(
        monkeypatch,
        post_response=_FakeResponse(status_code=401, payload={"error_description": "unauthorized"}),
    )
    resp = zhihu_client.get("/api/v1/auth/oauth/zhihu/callback?code=abc")
    assert resp.status_code == 502
    assert resp.get_json()["error"]["code"] == "OAUTH_FAILED"


def test_zhihu_start_unconfigured_returns_503(tmp_path, sample_cache_path, monkeypatch):
    from tests.conftest import _build_app

    monkeypatch.setenv("ZHIHU_OAUTH_CLIENT_ID", "")
    monkeypatch.setenv("ZHIHU_OAUTH_CLIENT_SECRET", "")
    app = _build_app(tmp_path, sample_cache_path, monkeypatch)
    client = app.test_client()

    resp = client.get("/api/v1/auth/oauth/zhihu")
    assert resp.status_code == 503
    assert resp.get_json()["error"]["code"] == "OAUTH_UNAVAILABLE"


def test_unknown_provider_returns_503(zhihu_client):
    resp = zhihu_client.get("/api/v1/auth/oauth/wechat")
    assert resp.status_code == 503
    assert resp.get_json()["error"]["code"] == "OAUTH_UNAVAILABLE"


def test_github_start_unchanged(tmp_path, sample_cache_path, monkeypatch):
    from tests.conftest import _build_app

    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_ID", "gh-cid")
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_SECRET", "gh-secret")
    monkeypatch.setenv("GITHUB_OAUTH_REDIRECT_URI", "https://papervault.top/api/v1/auth/oauth/github/callback")
    app = _build_app(tmp_path, sample_cache_path, monkeypatch)
    client = app.test_client()

    resp = client.get("/api/v1/auth/oauth/github")
    assert resp.status_code == 302
    assert resp.headers["Location"].startswith("https://github.com/login/oauth/authorize?")
    assert "client_id=gh-cid" in resp.headers["Location"]

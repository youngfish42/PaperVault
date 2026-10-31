"""HTTP-level tests for the multi-provider suggest endpoint and the
provider catalog endpoint.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from papervault.services import suggest


@pytest.fixture
def app_with_sample(monkeypatch, tmp_path):
    monkeypatch.setenv("PAPERVAULT_OFFLINE", "1")
    monkeypatch.setenv("PAPERVAULT_SUGGEST_PROVIDER", "")
    for v in (
        "OPENAI_API_KEY",
        "DEEPSEEK_API_KEY",
        "ANTHROPIC_API_KEY",
        "STEPFUN_API_KEY",
        "QWEN_API_KEY",
        "GLM_API_KEY",
    ):
        monkeypatch.delenv(v, raising=False)

    from papervault import create_app
    from papervault.config import Settings

    cache_path = tmp_path / "cache.jsonl.gz"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    static_dir = tmp_path / "static" / "dist"
    static_dir.mkdir(parents=True, exist_ok=True)

    settings = Settings(
        base_dir=tmp_path,
        cache_path=cache_path,
        static_folder=static_dir,
    )
    app = create_app(settings, eager_load=False)
    app.config.update(TESTING=True)
    return app


@pytest.fixture
def client(app_with_sample):
    return app_with_sample.test_client()


def _code(resp_body):
    return resp_body["error"]["code"]


def test_list_providers_endpoint(client):
    resp = client.get("/api/v1/ai/providers")
    assert resp.status_code == 200
    body = resp.get_json()
    keys = [it["key"] for it in body["items"]]
    assert "openai" in keys
    assert "stepfun" in keys
    assert "custom" in keys

    stepfun = next(it for it in body["items"] if it["key"] == "stepfun")
    assert stepfun["protocol"] == "anthropic"
    assert stepfun["base_url"] == "https://api.stepfun.com/step_plan/v1"


def test_suggest_dispatches_stepfun_when_key_in_request(client, monkeypatch):
    monkeypatch.setattr(
        suggest, "call_anthropic",
        lambda **kwargs: suggest.ChatResult(
            content='{"keywords": ["alpha", "beta"]}', raw_model="step-3.7-flash"
        ),
    )

    resp = client.post(
        "/api/v1/suggest",
        json={
            "query": "agent",
            "provider": "stepfun",
            "api_key": "sk-from-ui",
            "max_keywords": 5,
        },
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["provider"] == "stepfun"
    assert body["protocol"] == "anthropic"
    assert body["keywords"] == ["alpha", "beta"]


def test_suggest_503_when_neither_key_nor_preset(client, monkeypatch):
    resp = client.post(
        "/api/v1/suggest",
        json={"query": "agent", "provider": "deepseek", "max_keywords": 5},
    )
    assert resp.status_code == 503
    assert _code(resp.get_json()) == "LLM_NOT_CONFIGURED"


def test_suggest_invalid_provider_returns_400(client):
    resp = client.post(
        "/api/v1/suggest",
        json={"query": "agent", "provider": "openai", "protocol": "weirdproto"},
    )
    assert resp.status_code == 400
    assert _code(resp.get_json()) == "BAD_REQUEST"


def test_suggest_query_too_short_returns_400(client):
    resp = client.post("/api/v1/suggest", json={"query": ""})
    assert resp.status_code == 400
    assert _code(resp.get_json()) == "BAD_REQUEST"

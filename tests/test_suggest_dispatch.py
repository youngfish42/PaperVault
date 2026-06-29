"""Dispatch tests for ``suggest``.

These prove the resolver chooses the right SDK based on preset key and
that request-side overrides win over env vars / settings. The SDKs are
replaced with fakes for hermetic, offline tests.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

from papervault.errors import ApiError
from papervault.services import suggest


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    for v in (
        "OPENAI_API_KEY",
        "DEEPSEEK_API_KEY",
        "ANTHROPIC_API_KEY",
        "STEPFUN_API_KEY",
        "QWEN_API_KEY",
        "GLM_API_KEY",
        "PAPERVAULT_SUGGEST_PROVIDER",
    ):
        monkeypatch.delenv(v, raising=False)


def test_resolve_anthropic_preset_uses_stepfun_base_url(monkeypatch):
    monkeypatch.setenv("STEPFUN_API_KEY", "sk-stepfun")

    req = suggest.SuggestionRequest(query="x", provider="stepfun", max_keywords=5)
    resolved = suggest._resolve_provider(req)

    assert resolved.preset.key == "stepfun"
    assert resolved.protocol == "anthropic"
    assert resolved.base_url == "https://api.stepfun.com/step_plan/v1"
    assert resolved.model == "step-3.7-flash"
    assert resolved.api_key == "sk-stepfun"


def test_resolve_anthropic_native_uses_anthropic_base_url(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anth")

    req = suggest.SuggestionRequest(query="x", provider="anthropic", max_keywords=5)
    resolved = suggest._resolve_provider(req)

    assert resolved.preset.key == "anthropic"
    assert resolved.base_url == "https://api.anthropic.com"
    assert resolved.model == "claude-haiku-4-5"


def test_request_overrides_win_over_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")

    req = suggest.SuggestionRequest(
        query="x",
        provider="openai",
        model="gpt-5",
        api_key="sk-request",
        max_keywords=5,
    )
    resolved = suggest._resolve_provider(req)
    assert resolved.api_key == "sk-request"
    assert resolved.model == "gpt-5"
    assert resolved.source == "request"


def test_unknown_preset_falls_back_to_custom(monkeypatch):
    req = suggest.SuggestionRequest(
        query="x",
        provider="nope",
        base_url="https://example.com/v1",
        model="custom-model",
        api_key="sk-custom",
        max_keywords=5,
    )
    resolved = suggest._resolve_provider(req)
    assert resolved.preset.key == "custom"
    assert resolved.base_url == "https://example.com/v1"
    assert resolved.model == "custom-model"


def test_custom_without_url_raises(monkeypatch):
    req = suggest.SuggestionRequest(query="x", provider="custom", max_keywords=5)
    with pytest.raises(ApiError) as exc:
        suggest._resolve_provider(req)
    assert exc.value.code == "BAD_REQUEST"


def test_missing_key_raises_503_with_preset_env_name(monkeypatch):
    req = suggest.SuggestionRequest(query="x", provider="stepfun", max_keywords=5)
    with pytest.raises(ApiError) as exc:
        suggest._resolve_provider(req)
    assert exc.value.code == "LLM_NOT_CONFIGURED"
    assert "STEPFUN_API_KEY" in exc.value.message


def test_dispatch_to_anthropic_for_stepfun(monkeypatch):
    monkeypatch.setenv("STEPFUN_API_KEY", "sk-stepfun")

    monkeypatch.setattr(
        suggest, "call_anthropic",
        lambda **kwargs: suggest.ChatResult(
            content='{"keywords": ["a", "b"]}', raw_model="step-3.7-flash"
        ),
    )

    req = suggest.SuggestionRequest(
        query="x", provider="stepfun", max_keywords=5
    )
    result = suggest.suggest_keywords(req)
    assert result.provider == "stepfun"
    assert result.protocol == "anthropic"
    assert result.keywords == ["a", "b"]
    assert result.model == "step-3.7-flash"


def test_dispatch_to_openai_for_deepseek(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds")

    monkeypatch.setattr(
        suggest, "call_openai_compatible",
        lambda **kwargs: suggest.ChatResult(
            content='{"keywords": ["alpha", "beta"]}', raw_model="deepseek-chat"
        ),
    )

    req = suggest.SuggestionRequest(
        query="x", provider="deepseek", max_keywords=5
    )
    result = suggest.suggest_keywords(req)
    assert result.provider == "deepseek"
    assert result.protocol == "openai-compatible"
    assert result.keywords == ["alpha", "beta"]


def test_legacy_env_fallback_for_deepseek_when_provider_unspecified(monkeypatch):
    """Old deployments set only DEEPSEEK_API_KEY; keep them working."""

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-legacy")

    monkeypatch.setattr(
        suggest, "call_openai_compatible",
        lambda **kwargs: suggest.ChatResult(
            content='{"keywords": ["k"]}', raw_model="deepseek-chat"
        ),
    )

    req = suggest.SuggestionRequest(query="x", max_keywords=5)
    result = suggest.suggest_keywords(req)
    assert result.provider == "deepseek"


def test_missing_base_url_raises_bad_request(monkeypatch):
    """Custom preset without base_url should raise BAD_REQUEST."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    req = suggest.SuggestionRequest(
        query="x",
        provider="custom",
        model="custom-model",
        base_url="",
        max_keywords=5,
    )
    with pytest.raises(ApiError) as exc:
        suggest._resolve_provider(req)
    assert exc.value.code == "BAD_REQUEST"
    assert "base_url" in exc.value.message.lower()


def test_settings_deepseek_base_url_used(monkeypatch, tmp_path):
    """Settings-based deepseek_base_url should be used when available."""
    from papervault.config import Settings

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds")

    cache_path = tmp_path / "cache.jsonl.gz"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        base_dir=tmp_path,
        cache_path=cache_path,
        static_folder=tmp_path / "static",
        deepseek_base_url="https://custom.deepseek.endpoint.com",
    )

    from papervault import create_app
    app = create_app(settings, eager_load=False)
    app.config["TESTING"] = True

    with app.app_context():
        req = suggest.SuggestionRequest(query="x", provider="deepseek", max_keywords=5)
        resolved = suggest._resolve_provider(req)
        assert resolved.base_url == "https://custom.deepseek.endpoint.com"


def test_env_overrides_preset_default_base_url(monkeypatch):
    """OPENAI_API_BASE (env) should override the preset's default base_url
    when neither req nor settings provides it. This preserves the
    pre-P2 contract that AGENTS.md documents.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_API_BASE", "https://my-proxy.example.com/v1")

    req = suggest.SuggestionRequest(query="x", provider="openai", max_keywords=5)
    resolved = suggest._resolve_provider(req)

    assert resolved.base_url == "https://my-proxy.example.com/v1"


def test_env_overrides_preset_default_model(monkeypatch):
    """PAPERVAULT_OPENAI_MODEL (env) should override the preset's default
    model when neither req nor settings provides it.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("PAPERVAULT_OPENAI_MODEL", "gpt-5-pro")

    req = suggest.SuggestionRequest(query="x", provider="openai", max_keywords=5)
    resolved = suggest._resolve_provider(req)

    assert resolved.model == "gpt-5-pro"


def test_settings_overrides_env_for_deepseek_base_url(monkeypatch, tmp_path):
    """settings.deepseek_base_url should take precedence over env."""
    from papervault.config import Settings

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds")
    monkeypatch.setenv("PAPERVAULT_DEEPSEEK_BASE_URL", "https://env.deepseek.example.com")

    cache_path = tmp_path / "cache.jsonl.gz"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        base_dir=tmp_path,
        cache_path=cache_path,
        static_folder=tmp_path / "static",
        deepseek_base_url="https://settings.deepseek.example.com",
    )

    from papervault import create_app
    app = create_app(settings, eager_load=False)
    app.config["TESTING"] = True

    with app.app_context():
        req = suggest.SuggestionRequest(query="x", provider="deepseek", max_keywords=5)
        resolved = suggest._resolve_provider(req)
        assert resolved.base_url == "https://settings.deepseek.example.com"


def test_settings_deepseek_model_overrides_preset(monkeypatch, tmp_path):
    """settings.deepseek_model should override preset's default model."""
    from papervault.config import Settings

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds")

    cache_path = tmp_path / "cache.jsonl.gz"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        base_dir=tmp_path,
        cache_path=cache_path,
        static_folder=tmp_path / "static",
        deepseek_model="deepseek-reasoner",
    )

    from papervault import create_app
    app = create_app(settings, eager_load=False)
    app.config["TESTING"] = True

    with app.app_context():
        req = suggest.SuggestionRequest(query="x", provider="deepseek", max_keywords=5)
        resolved = suggest._resolve_provider(req)
        assert resolved.model == "deepseek-reasoner"

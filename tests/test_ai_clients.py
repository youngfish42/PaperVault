"""Tests for the vendor-neutral SDK dispatch.

We monkeypatch the SDK clients rather than spinning up the openai or
anthropic packages. The goal is to prove that protocol routing and
the response-extraction contract work; the SDKs themselves are owned
by their respective vendors.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from papervault.errors import UpstreamError
from papervault.services import ai_clients


@pytest.fixture
def fake_openai_response():
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = "```json\n{\"keywords\": [\"a\", \"b\"]}\n```"
    resp.model = "gpt-4o-mini-2024-07-18"
    return resp


@pytest.fixture
def fake_anthropic_response():
    block = MagicMock()
    block.text = '{"keywords": ["x", "y"]}'
    resp = MagicMock()
    resp.content = [block]
    resp.model = "claude-haiku-4-5"
    return resp


def test_call_openai_compatible_returns_content_and_model(
    fake_openai_response, monkeypatch
):
    import openai as openai_module

    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return fake_openai_response

    fake_chat = MagicMock()
    fake_chat.completions = FakeCompletions()

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs
            self.chat = fake_chat

    monkeypatch.setattr(openai_module, "OpenAI", FakeOpenAI)

    result = ai_clients.call_openai_compatible(
        api_key="sk-test",
        base_url="https://example.com/v1",
        model="gpt-test",
        system="sys",
        user="hi",
        temperature=0.3,
    )
    assert result.raw_model == "gpt-4o-mini-2024-07-18"
    assert "keywords" in result.content
    assert captured["model"] == "gpt-test"
    assert captured["temperature"] == 0.3
    assert captured["client_kwargs"]["base_url"] == "https://example.com/v1"


def test_call_openai_compatible_missing_key_raises():
    with pytest.raises(UpstreamError) as exc:
        ai_clients.call_openai_compatible(
            api_key="",
            base_url=None,
            model="m",
            system="s",
            user="u",
            temperature=0.5,
        )
    assert exc.value.code == "LLM_NOT_CONFIGURED"


def test_call_openai_compatible_with_max_tokens(fake_openai_response, monkeypatch):
    """max_tokens should be passed to the API when provided."""
    import openai as openai_module

    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return fake_openai_response

    fake_chat = MagicMock()
    fake_chat.completions = FakeCompletions()

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs
            self.chat = fake_chat

    monkeypatch.setattr(openai_module, "OpenAI", FakeOpenAI)

    result = ai_clients.call_openai_compatible(
        api_key="sk-test",
        base_url="https://example.com/v1",
        model="gpt-test",
        system="sys",
        user="hi",
        temperature=0.3,
        max_tokens=256,
    )
    assert "max_tokens" in captured
    assert captured["max_tokens"] == 256


def test_call_openai_compatible_without_max_tokens(fake_openai_response, monkeypatch):
    """max_tokens should not be passed when not provided."""
    import openai as openai_module

    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return fake_openai_response

    fake_chat = MagicMock()
    fake_chat.completions = FakeCompletions()

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs
            self.chat = fake_chat

    monkeypatch.setattr(openai_module, "OpenAI", FakeOpenAI)

    result = ai_clients.call_openai_compatible(
        api_key="sk-test",
        base_url="https://example.com/v1",
        model="gpt-test",
        system="sys",
        user="hi",
        temperature=0.3,
    )
    assert "max_tokens" not in captured


def test_call_anthropic_returns_content_and_model(
    fake_anthropic_response, monkeypatch
):
    import anthropic as anthropic_module

    captured = {}

    class FakeMessages:
        def create(self, **kwargs):
            captured.update(kwargs)
            return fake_anthropic_response

    class FakeAnthropic:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs
            self.messages = FakeMessages()

    monkeypatch.setattr(anthropic_module, "Anthropic", FakeAnthropic)

    result = ai_clients.call_anthropic(
        api_key="sk-anthropic",
        base_url="https://api.stepfun.com/step_plan/v1",
        model="step-3.7-flash",
        system="sys",
        user="hi",
        temperature=0.5,
        max_tokens=256,
    )
    assert result.raw_model == "claude-haiku-4-5"
    assert result.content == '{"keywords": ["x", "y"]}'
    assert captured["model"] == "step-3.7-flash"
    assert captured["max_tokens"] == 256
    assert captured["client_kwargs"]["base_url"] == "https://api.stepfun.com/step_plan"


def test_call_anthropic_missing_key_raises():
    with pytest.raises(UpstreamError) as exc:
        ai_clients.call_anthropic(
            api_key="",
            base_url="https://api.anthropic.com",
            model="claude-haiku-4-5",
            system="s",
            user="u",
            temperature=0.5,
        )
    assert exc.value.code == "LLM_NOT_CONFIGURED"


def test_call_anthropic_strips_trailing_v1(monkeypatch):
    """StepFun-style ``/step_plan/v1`` and bare ``api.anthropic.com`` must
    both resolve to ``<root>/v1/messages`` after the SDK appends the path.
    We assert the value the SDK actually sees.
    """

    import anthropic as anthropic_module

    captured = {}

    class FakeMessages:
        def create(self, **kwargs):
            captured.update(kwargs)
            block = MagicMock()
            block.text = "ok"
            resp = MagicMock()
            resp.content = [block]
            resp.model = "m"
            return resp

    class FakeAnthropic:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs
            self.messages = FakeMessages()

    monkeypatch.setattr(anthropic_module, "Anthropic", FakeAnthropic)

    for raw, expected in (
        ("https://api.stepfun.com/step_plan/v1", "https://api.stepfun.com/step_plan"),
        ("https://api.anthropic.com", "https://api.anthropic.com"),
        ("https://api.anthropic.com/v1/", "https://api.anthropic.com"),
    ):
        captured.clear()
        ai_clients.call_anthropic(
            api_key="k",
            base_url=raw,
            model="m",
            system="s",
            user="u",
            temperature=0.5,
        )
        assert captured["client_kwargs"]["base_url"] == expected, (raw, expected, captured)


def test_call_anthropic_missing_sdk(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "anthropic":
            raise ImportError("anthropic not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(UpstreamError) as exc:
        ai_clients.call_anthropic(
            api_key="k",
            base_url="https://api.anthropic.com",
            model="claude-haiku-4-5",
            system="s",
            user="u",
            temperature=0.5,
        )
    assert exc.value.code == "LLM_SDK_MISSING"

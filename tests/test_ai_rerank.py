"""HTTP-level tests for the post-search re-rank endpoint.

The actual LLM client is monkey-patched the same way as in
``test_suggest_api.py`` because the goal here is to lock down the
flask-pydantic-routing layer and the JSON parsing contract, not to
re-prove the SDKs.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from papervault.services import ai_clients, rerank
from papervault.services.rerank import _normalise_score, _parse_rerank_response


@pytest.fixture
def fake_papers(monkeypatch):
    """Seed two papers into the repository stub so ``paper_ids`` resolves.

    ``PaperRepository.all_papers`` is replaced with a stub that returns a
    fixed list with deterministic ids; we don't touch disk to keep the
    suite hermetic.
    """

    from papervault import services as svc
    from papervault.services.papers import Paper

    repo = MagicMock()
    p1 = Paper(
        id="aaaa1111aaaa1111",
        conf="ICLR",
        year="2024",
        title="Time-series forecasting with LLMs",
        title_format="time series forecasting with llms",
        url="",
        authors=["A. Smith", "B. Jones"],
        abstract="We apply language models to time-series forecasting.",
        code=None,
    )
    p2 = Paper(
        id="bbbb2222bbbb2222",
        conf="NeurIPS",
        year="2024",
        title="Foundation models for tabular data",
        title_format="foundation models for tabular data",
        url="",
        authors=["C. Lee"],
        abstract="Pretrained transformers for tabular prediction tasks.",
        code=None,
    )
    repo.all_papers.return_value = [p1, p2]
    # Mirror the production ``PaperRepository`` so the handler hits the
    # O(1) indexed accessor instead of the legacy O(N) fallback.
    _by_id = {p1.id: p1, p2.id: p2}
    repo.get_by_id.side_effect = lambda pid: _by_id.get(pid)

    def _seed(app):
        app.extensions["paper_repository"] = repo

    return [p1, p2], _seed


@pytest.fixture
def app_with_sample(monkeypatch, tmp_path, fake_papers):
    monkeypatch.setenv("PAPERVAULT_OFFLINE", "1")
    monkeypatch.setenv("PAPERVAULT_SUGGEST_PROVIDER", "")
    # ``create_app`` calls ``load_dotenv()`` which would otherwise re-load
    # whatever ``.env`` happens to sit in the working tree — neutralise it
    # so the test runs purely on the controlled env below. Patch the name
    # *already bound in papervault.app* because that module did
    # ``from dotenv import load_dotenv`` at import time.
    import papervault.app as _pvapp

    monkeypatch.setattr(_pvapp, "load_dotenv", lambda *a, **k: None)

    for v in (
        "OPENAI_API_KEY",
        "OPENAI_API_BASE",
        "DEEPSEEK_API_KEY",
        "ANTHROPIC_API_KEY",
        "STEPFUN_API_KEY",
        "QWEN_API_KEY",
        "GLM_API_KEY",
        "PAPERVAULT_OPENAI_MODEL",
        "PAPERVAULT_DEEPSEEK_BASE_URL",
        "PAPERVAULT_DEEPSEEK_MODEL",
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
    fake_papers[1](app)
    return app


@pytest.fixture
def client(app_with_sample):
    return app_with_sample.test_client()


def _code(resp_body):
    return resp_body["error"]["code"]


def test_rerank_dispatches_openai_with_request_keys(client, monkeypatch):
    monkeypatch.setattr(
        rerank, "call_openai_compatible",
        lambda **kwargs: ai_clients.ChatResult(
            content=(
                '{"ordered":[{"paper_id":"aaaa1111aaaa1111","score":9.1},'
                '{"paper_id":"bbbb2222bbbb2222","score":2.3}]}'
            ),
            raw_model="gpt-4o-mini-2024-07-18",
        ),
    )

    resp = client.post(
        "/api/v1/ai/rerank",
        json={
            "query": "time series llm",
            "paper_ids": ["aaaa1111aaaa1111", "bbbb2222bbbb2222"],
            "provider": "openai",
            "api_key": "sk-from-ui",
            "temperature": 0.1,
        },
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["provider"] == "openai"
    assert body["protocol"] == "openai-compatible"
    # Score 9.1 normalised to 0.91; 2.3 to 0.23.
    assert body["ordered"][0]["paper_id"] == "aaaa1111aaaa1111"
    assert body["ordered"][0]["score"] == pytest.approx(0.91)
    assert body["ordered"][1]["paper_id"] == "bbbb2222bbbb2222"
    assert body["ordered"][1]["score"] == pytest.approx(0.23)


def test_rerank_parses_fenced_json(client, monkeypatch):
    """LLMs often wrap their JSON in ```json ... ```; accept that shape."""
    monkeypatch.setattr(
        rerank, "call_openai_compatible",
        lambda **kwargs: ai_clients.ChatResult(
            content=(
                "Sure, here you go:\n"
                "```json\n"
                '{"ordered":[{"paper_id":"aaaa1111aaaa1111","score":0.8}]}\n'
                "```\n"
                "Hope that helps!"
            ),
            raw_model="gpt-4o-mini",
        ),
    )

    resp = client.post(
        "/api/v1/ai/rerank",
        json={
            "query": "time series",
            "paper_ids": ["aaaa1111aaaa1111"],
            "provider": "openai",
            "api_key": "sk-test",
        },
    )
    assert resp.status_code == 200
    assert resp.get_json()["ordered"][0]["score"] == 0.8


def test_rerank_appends_missing_ids_at_tail(client, monkeypatch):
    """An LLM that forgets to score one paper must not make it disappear."""
    monkeypatch.setattr(
        rerank, "call_openai_compatible",
        lambda **kwargs: ai_clients.ChatResult(
            content=(
                '{"ordered":[{"paper_id":"aaaa1111aaaa1111","score":0.7}]}'
            ),
            raw_model="gpt-4o-mini",
        ),
    )

    resp = client.post(
        "/api/v1/ai/rerank",
        json={
            "query": "time series",
            "paper_ids": ["aaaa1111aaaa1111", "bbbb2222bbbb2222"],
            "provider": "openai",
            "api_key": "sk-test",
        },
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert len(body["ordered"]) == 2
    assert body["ordered"][0]["paper_id"] == "aaaa1111aaaa1111"
    # The forgotten one is appended with a neutral 0.5 score.
    assert body["ordered"][1] == {
        "paper_id": "bbbb2222bbbb2222",
        "score": 0.5,
    }


def test_rerank_drops_unknown_ids_from_response(client, monkeypatch):
    """LLMs sometimes invent ids; ignore them silently."""
    monkeypatch.setattr(
        rerank, "call_openai_compatible",
        lambda **kwargs: ai_clients.ChatResult(
            content=(
                '{"ordered":[{"paper_id":"aaaa1111aaaa1111","score":0.5},'
                '{"paper_id":"ghost-id-not-in-input","score":1.0}]}'
            ),
            raw_model="gpt-4o-mini",
        ),
    )

    resp = client.post(
        "/api/v1/ai/rerank",
        json={
            "query": "time series",
            "paper_ids": ["aaaa1111aaaa1111"],
            "provider": "openai",
            "api_key": "sk-test",
        },
    )
    assert resp.status_code == 200
    ordered = resp.get_json()["ordered"]
    assert len(ordered) == 1
    assert ordered[0]["paper_id"] == "aaaa1111aaaa1111"


def test_rerank_reports_stale_ids_in_skipped_field(client, monkeypatch):
    """Stale (unknown) paper_ids must be surfaced via ``skipped_ids``.

    The handler used to drop them silently with only an INFO log entry,
    which left the UI unable to distinguish "model trimmed list" from
    "client passed dangling references". The response contract now lists
    every dropped id so the caller can show a recoverable warning.
    """

    monkeypatch.setattr(
        rerank, "call_openai_compatible",
        lambda **kwargs: ai_clients.ChatResult(
            content='{"ordered":[{"paper_id":"aaaa1111aaaa1111","score":0.7}]}',
            raw_model="gpt-4o-mini",
        ),
    )

    resp = client.post(
        "/api/v1/ai/rerank",
        json={
            "query": "time series",
            "paper_ids": [
                "aaaa1111aaaa1111",
                "stale-id-1",
                "stale-id-2",
            ],
            "provider": "openai",
            "api_key": "sk-test",
        },
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert [e["paper_id"] for e in body["ordered"]] == ["aaaa1111aaaa1111"]
    # Order of ``skipped_ids`` mirrors the request order so the UI can
    # line them up without resorting.
    assert body["skipped_ids"] == ["stale-id-1", "stale-id-2"]


def test_rerank_skipped_ids_empty_when_all_resolve(client, monkeypatch):
    """Happy path: every id maps; ``skipped_ids`` is an empty list."""

    monkeypatch.setattr(
        rerank, "call_openai_compatible",
        lambda **kwargs: ai_clients.ChatResult(
            content=(
                '{"ordered":[{"paper_id":"aaaa1111aaaa1111","score":0.8},'
                '{"paper_id":"bbbb2222bbbb2222","score":0.4}]}'
            ),
            raw_model="gpt-4o-mini",
        ),
    )

    resp = client.post(
        "/api/v1/ai/rerank",
        json={
            "query": "time series",
            "paper_ids": ["aaaa1111aaaa1111", "bbbb2222bbbb2222"],
            "provider": "openai",
            "api_key": "sk-test",
        },
    )
    assert resp.status_code == 200
    assert resp.get_json()["skipped_ids"] == []


def test_rerank_503_when_no_api_key(client):
    resp = client.post(
        "/api/v1/ai/rerank",
        json={
            "query": "time series",
            "paper_ids": ["aaaa1111aaaa1111"],
            "provider": "openai",
        },
    )
    assert resp.status_code == 503
    assert _code(resp.get_json()) == "LLM_NOT_CONFIGURED"


def test_rerank_400_when_query_empty(client):
    resp = client.post(
        "/api/v1/ai/rerank",
        json={
            "query": "",
            "paper_ids": ["aaaa1111aaaa1111"],
            "provider": "openai",
            "api_key": "sk-test",
        },
    )
    assert resp.status_code == 400
    assert _code(resp.get_json()) == "BAD_REQUEST"


def test_rerank_400_when_paper_ids_empty(client):
    resp = client.post(
        "/api/v1/ai/rerank",
        json={
            "query": "time series",
            "paper_ids": [],
            "provider": "openai",
            "api_key": "sk-test",
        },
    )
    assert resp.status_code == 400
    assert _code(resp.get_json()) == "BAD_REQUEST"


def test_rerank_400_when_paper_ids_too_many(client):
    resp = client.post(
        "/api/v1/ai/rerank",
        json={
            "query": "time series",
            "paper_ids": [f"pid-{i:04d}" for i in range(301)],
            "provider": "openai",
            "api_key": "sk-test",
        },
    )
    assert resp.status_code == 400
    assert _code(resp.get_json()) == "BAD_REQUEST"


def test_rerank_502_on_unparseable_response(client, monkeypatch):
    monkeypatch.setattr(
        rerank, "call_openai_compatible",
        lambda **kwargs: ai_clients.ChatResult(
            content="Sorry, I cannot rank papers right now.",
            raw_model="gpt-4o-mini",
        ),
    )

    resp = client.post(
        "/api/v1/ai/rerank",
        json={
            "query": "time series",
            "paper_ids": ["aaaa1111aaaa1111"],
            "provider": "openai",
            "api_key": "sk-test",
        },
    )
    assert resp.status_code == 502
    assert _code(resp.get_json()) in {"UPSTREAM_ERROR", "LLM_BAD_JSON"}


# --- pure unit tests for the small parser/normaliser helpers ---

def test_normalise_score_handles_0_to_1():
    assert _normalise_score(0.0) == 0.0
    assert _normalise_score(0.5) == 0.5
    assert _normalise_score(1.0) == 1.0


def test_normalise_score_handles_0_to_10():
    assert _normalise_score(7.0) == 0.7
    assert _normalise_score(10.0) == 1.0
    assert _normalise_score(0.0) == 0.0


def test_normalise_score_handles_0_to_100():
    assert _normalise_score(85.0) == 0.85
    assert _normalise_score(100.0) == 1.0


def test_normalise_score_clamps_outliers():
    # Negative values are clamped to zero (still useful for ordinal ranking).
    assert _normalise_score(-0.5) == 0.0
    # Values just above 1.0 are *rescaled* (assumed 0-10 scale), not
    # clamped — distinguishing "raw 0.99" from "raw 9.9" matters because
    # 0-10-emitting LLMs are common. Truly out-of-range values >100 are
    # rejected as None.
    assert _normalise_score(1.5) == 0.15
    assert _normalise_score(150.0) is None


def test_normalise_score_rejects_garbage():
    assert _normalise_score(None) is None
    assert _normalise_score("abc") is None
    assert _normalise_score(float("nan")) is None


def test_parse_rerank_response_accepts_fenced():
    scores = _parse_rerank_response(
        "```json\n{\"ordered\":[{\"paper_id\":\"a\",\"score\":0.9}]}\n```",
        expected_ids=["a"],
    )
    assert scores == {"a": 0.9}


def test_parse_rerank_response_accepts_bare_object():
    scores = _parse_rerank_response(
        '{"ordered":[{"paper_id":"a","score":0.4},'
        '{"paper_id":"b","score":0.6}]}',
        expected_ids=["a", "b"],
    )
    assert scores == {"a": 0.4, "b": 0.6}


def test_parse_rerank_response_normalises_0_to_10_scale():
    scores = _parse_rerank_response(
        '{"ordered":[{"paper_id":"a","score":9}]}',
        expected_ids=["a"],
    )
    assert scores["a"] == 0.9


def test_parse_rerank_response_rejects_non_json():
    from papervault.errors import UpstreamError

    with pytest.raises(UpstreamError):
        _parse_rerank_response(
            "Sorry, I cannot do that.",
            expected_ids=["a"],
        )


# --- HTTP tests for fixes derived from PR #105 code review ---


def test_rerank_dedupes_duplicate_paper_ids(client, monkeypatch):
    """Duplicate ids in the request must collapse to a single response entry.

    Before the fix, the handler accepted duplicates as-is. When the LLM
    then forgot to score a duplicated id, both copies survived as
    ``missing`` and surfaced as duplicate ``paper_id`` entries in the
    response. The ``RerankRequest._dedupe_paper_ids`` validator collapses
    duplicates while preserving first-seen order; downstream code only
    ever sees the deduped list.
    """

    captured: dict = {}

    def _fake_call(**kwargs):
        # Snapshot the user prompt so we can also assert the LLM only
        # received the paper once (no "[1] id=...  [2] id=..." dupes).
        captured["user"] = kwargs["user"]
        return ai_clients.ChatResult(
            content=(
                '{"ordered":[{"paper_id":"aaaa1111aaaa1111","score":0.8}]}'
            ),
            raw_model="gpt-4o-mini",
        )

    monkeypatch.setattr(rerank, "call_openai_compatible", _fake_call)

    resp = client.post(
        "/api/v1/ai/rerank",
        json={
            "query": "time series",
            "paper_ids": [
                "aaaa1111aaaa1111",
                "aaaa1111aaaa1111",
                "bbbb2222bbbb2222",
                "aaaa1111aaaa1111",
            ],
            "provider": "openai",
            "api_key": "sk-test",
        },
    )
    assert resp.status_code == 200
    body = resp.get_json()
    # Exactly two distinct entries in deduped first-seen order.
    paper_ids = [e["paper_id"] for e in body["ordered"]]
    assert paper_ids == ["aaaa1111aaaa1111", "bbbb2222bbbb2222"]
    # The LLM prompt only contained each id once.
    assert captured["user"].count("id=aaaa1111aaaa1111") == 1
    assert captured["user"].count("id=bbbb2222bbbb2222") == 1


def test_rerank_short_circuits_when_all_ids_stale(client, monkeypatch):
    """If every paper_id is unknown, skip the LLM call and return an
    empty result with all ids reflected in ``skipped_ids``.

    The provider / protocol / model fields are empty strings (not the
    legacy ``"-"`` placeholder) so the UI can detect "no LLM was
    consulted" without string-matching a sentinel.
    """

    def _explode(**kwargs):
        raise AssertionError(
            "LLM client must not be invoked when every paper_id is stale."
        )

    monkeypatch.setattr(rerank, "call_openai_compatible", _explode)
    monkeypatch.setattr(rerank, "call_anthropic", _explode)

    resp = client.post(
        "/api/v1/ai/rerank",
        json={
            "query": "time series",
            "paper_ids": ["deadbeefdeadbeef", "cafebabecafebabe"],
            "provider": "openai",
            "api_key": "sk-test",
        },
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ordered"] == []
    assert body["skipped_ids"] == ["deadbeefdeadbeef", "cafebabecafebabe"]
    assert body["model"] == ""
    assert body["provider"] == ""
    assert body["protocol"] == ""
    assert body["timecost_ms"] == 0.0


def test_rerank_dispatches_anthropic_path(client, monkeypatch):
    """Cover the Anthropic dispatch branch the PR description claims to
    test. The original suite only ever monkey-patched
    ``call_openai_compatible``; the Anthropic limb of ``rank_papers``
    (including the ``anthropic_max_tokens`` default) was untouched.
    """

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    captured: dict = {}

    def _fake_anthropic(**kwargs):
        captured.update(kwargs)
        return ai_clients.ChatResult(
            content=(
                '{"ordered":[{"paper_id":"aaaa1111aaaa1111","score":0.7}]}'
            ),
            raw_model="claude-3-5-sonnet-20241022",
        )

    monkeypatch.setattr(rerank, "call_anthropic", _fake_anthropic)
    monkeypatch.setattr(
        rerank, "call_openai_compatible",
        lambda **kwargs: pytest.fail(
            "OpenAI path must not be hit when provider=anthropic."
        ),
    )

    resp = client.post(
        "/api/v1/ai/rerank",
        json={
            "query": "time series",
            "paper_ids": ["aaaa1111aaaa1111"],
            "provider": "anthropic",
        },
    )
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["protocol"] == "anthropic"
    assert body["provider"] == "anthropic"
    # ``max_tokens`` must be forwarded; the rerank service falls back to
    # ``settings.anthropic_max_tokens`` (or 2048 if unset) — either way
    # it is a positive integer, never ``None``.
    assert isinstance(captured.get("max_tokens"), int)
    assert captured["max_tokens"] > 0
    # Temperature defaults to 0.0 for deterministic ordering.
    assert captured.get("temperature") == 0.0

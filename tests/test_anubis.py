"""Tests for `collector/anubis.py` — Anubis challenge detection, PoW solving
and the session-level solve-and-retry flow."""

from __future__ import annotations

import hashlib
import json

import pytest
import requests

from collector import anubis
from collector.anubis import (
    AnubisUnsolvableError,
    _solve_pow,
    get_with_anubis,
    is_challenge,
)

CHALLENGE_PAGE = """<!doctype html><html><head><title>Making sure you&#39;re not a bot!</title>
<script id="anubis_challenge" type="application/json">{"rules":{"algorithm":"fast","difficulty":1},
"challenge":{"id":"01a0bec0-test","randomData":"deadbeef","difficulty":1,"spent":false}}</script>
</head><body>pow</body></html>"""

REAL_PAGE = "<html><body><ul><li class='entry'>paper</li></ul></body></html>"


def _resp(url, text, status=200):
    r = requests.Response()
    r.status_code = status
    r.url = url
    r._content = text.encode("utf-8")
    r.headers["Content-Type"] = "text/html"
    return r


class FakeSession:
    """Minimal requests.Session stand-in: dispatch canned responses per call."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def get(self, url, **kw):
        self.calls.append(url)
        if not self._responses:
            raise AssertionError(f"unexpected GET {url}")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_is_challenge():
    assert is_challenge(_resp("https://dblp.org/x", CHALLENGE_PAGE))
    assert not is_challenge(_resp("https://dblp.org/x", REAL_PAGE))
    plain = _resp("https://dblp.org/x", REAL_PAGE)
    plain.headers["Content-Type"] = "application/json"
    assert not is_challenge(plain)


def test_solve_pow_satisfies_difficulty():
    for difficulty in (1, 2, 3):
        hex_hash, nonce, _ = _solve_pow("deadbeef", difficulty)
        digest = bytes.fromhex(hex_hash)
        assert digest == hashlib.sha256(f"deadbeef{nonce}".encode()).digest()
        full = difficulty // 2
        assert all(b == 0 for b in digest[:full])
        if difficulty % 2:
            assert digest[full] >> 4 == 0


def test_solve_pow_budget_exhaustion(monkeypatch):
    monkeypatch.setattr(anubis, "MAX_SOLVE_NONCE", 0)
    with pytest.raises(AnubisUnsolvableError):
        _solve_pow("deadbeef", 4)


def test_get_with_anubis_solves_then_fetches(monkeypatch):
    monkeypatch.setattr(anubis.time, "sleep", lambda s: None)
    url = "https://dblp.org/db/journals/aeog/aeog94.html"
    session = FakeSession([
        _resp(url, CHALLENGE_PAGE),
        _resp("https://dblp.org/.within.website/x/cmd/anubis/api/pass-challenge",
              "", status=302),
        _resp(url, REAL_PAGE),
    ])
    resp = get_with_anubis(session, url)
    assert resp.text == REAL_PAGE
    # 1 original + 1 pass-challenge + 1 refetch
    assert len(session.calls) == 3
    assert "pass-challenge" in session.calls[1]


def test_get_with_anubis_gives_up_after_rounds(monkeypatch):
    monkeypatch.setattr(anubis.time, "sleep", lambda s: None)
    url = "https://dblp.org/db/journals/aeog/aeog94.html"
    # Every response is a challenge page: pass-challenge never clears it.
    session = FakeSession(
        [_resp(url, CHALLENGE_PAGE),
         _resp("https://dblp.org/.within.website/x/cmd/anubis/api/pass-challenge", "", status=302)]
        * (anubis.MAX_CHALLENGE_ROUNDS + 1)
    )
    with pytest.raises(AnubisUnsolvableError):
        get_with_anubis(session, url)


def test_get_with_anubis_retries_429_and_honours_retry_after(monkeypatch):
    sleeps = []
    monkeypatch.setattr(anubis.time, "sleep", lambda s: sleeps.append(s))
    url = "https://dblp.org/db/journals/aeog/aeog94.html"
    limited = _resp(url, "slow down", status=429)
    limited.headers["Retry-After"] = "7"
    session = FakeSession([
        limited,
        _resp(url, "slow down", status=429),
        _resp(url, REAL_PAGE),
    ])
    resp = get_with_anubis(session, url)
    assert resp.status_code == 200
    assert resp.text == REAL_PAGE
    assert sleeps[0] == 7.0  # Retry-After 优先


def test_get_with_anubis_raises_when_429_persists(monkeypatch):
    """429 重试耗尽必须抛 RateLimitedError（AnubisUnsolvableError 子类）——
    若返回 429 响应，上游会把空响应体解析成 0 篇并写 empty 标记，持续限流
    被静默掩盖。"""
    monkeypatch.setattr(anubis.time, "sleep", lambda s: None)
    url = "https://dblp.org/db/journals/aeog/aeog94.html"
    session = FakeSession([
        _resp(url, "slow down", status=429),
    ] * (anubis.RATE_LIMIT_ATTEMPTS + 1))
    with pytest.raises(anubis.RateLimitedError):
        get_with_anubis(session, url)
    assert issubclass(anubis.RateLimitedError, anubis.AnubisUnsolvableError)


def test_rate_limit_delay_caps_retry_after(monkeypatch):
    sleeps = []
    monkeypatch.setattr(anubis.time, "sleep", lambda s: sleeps.append(s))
    url = "https://dblp.org/db/journals/aeog/aeog94.html"
    limited = _resp(url, "slow down", status=429)
    limited.headers["Retry-After"] = "3600"
    session = FakeSession([limited, _resp(url, REAL_PAGE)])
    resp = get_with_anubis(session, url)
    assert resp.status_code == 200
    assert sleeps == [anubis.MAX_RATE_LIMIT_DELAY]


def test_pass_challenge_malformed_payload_raises_unsolvable():
    session = FakeSession([])
    bad_json = _resp("https://dblp.org/x", CHALLENGE_PAGE.replace('"fast"', 'fast'))
    with pytest.raises(anubis.AnubisUnsolvableError):
        anubis._pass_challenge(session, bad_json)

    missing_keys = _resp("https://dblp.org/x", CHALLENGE_PAGE.replace('"randomData"', '"randomDataX"'))
    with pytest.raises(anubis.AnubisUnsolvableError):
        anubis._pass_challenge(session, missing_keys)


def test_pass_challenge_request_failure_raises_unsolvable():
    session = FakeSession([requests.ConnectionError("boom")])
    with pytest.raises(anubis.AnubisUnsolvableError):
        anubis._pass_challenge(session, _resp("https://dblp.org/x", CHALLENGE_PAGE))


def test_get_with_anubis_does_not_retry_5xx_at_this_layer(monkeypatch):
    """5xx 交由 session 自带的 urllib3 Retry（或调用方）处理，本层不重试，
    避免双层重试叠加出数十次请求。"""
    monkeypatch.setattr(anubis.time, "sleep", lambda s: None)
    url = "https://dblp.org/db/journals/aeog/aeog94.html"
    session = FakeSession([_resp(url, "server error", status=503)])
    resp = get_with_anubis(session, url)
    assert resp.status_code == 503
    assert len(session.calls) == 1


def test_search_abs_from_dblp_propagates_unsolvable(monkeypatch):
    """AnubisUnsolvableError 不得被 aaai 回退吞掉（否则会退化成对挑战页
    的裸 GET 并静默返回空摘要）。"""
    from collector.sources import dblp as dblp_source

    def _raise(session, url, **kw):
        raise AnubisUnsolvableError("cannot solve")

    monkeypatch.setattr(dblp_source, "get_with_anubis", _raise)
    with pytest.raises(AnubisUnsolvableError):
        dblp_source.search_abs_from_dblp("https://dblp.org/rec/journals/aeog/X21.html")

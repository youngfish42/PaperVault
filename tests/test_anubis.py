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


def test_get_with_anubis_retries_rate_limits(monkeypatch):
    monkeypatch.setattr(anubis.time, "sleep", lambda s: None)
    url = "https://dblp.org/db/journals/aeog/aeog94.html"
    session = FakeSession([
        _resp(url, "slow down", status=503),
        _resp(url, "slow down", status=429),
        _resp(url, REAL_PAGE),
    ])
    resp = get_with_anubis(session, url)
    assert resp.status_code == 200
    assert resp.text == REAL_PAGE


def test_get_with_anubis_returns_last_response_when_rate_limit_persists(monkeypatch):
    monkeypatch.setattr(anubis.time, "sleep", lambda s: None)
    url = "https://dblp.org/db/journals/aeog/aeog94.html"
    session = FakeSession([
        _resp(url, "slow down", status=503),
    ] * (anubis.RATE_LIMIT_ATTEMPTS + 1))
    resp = get_with_anubis(session, url)
    assert resp.status_code == 503

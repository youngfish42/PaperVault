"""Tests that discovery's HTTP helpers route DBLP requests through the
Anubis solver (and keep the plain path for other hosts)."""

from __future__ import annotations

import requests

from discovery import base as dbase
from discovery.dblp import DBLPDiscovery


def _resp(url, text, status=200):
    r = requests.Response()
    r.status_code = status
    r.url = url
    r._content = text.encode("utf-8")
    r.headers["Content-Type"] = "text/html"
    return r


def test_get_text_routes_dblp_through_anubis(monkeypatch):
    calls = []

    def _fake(session, url, **kw):
        calls.append(url)
        return _resp(url, "<html>ok</html>")

    monkeypatch.setattr(dbase, "get_with_anubis", _fake)
    inst = DBLPDiscovery(existing_conf=[])
    assert inst._get_text("https://dblp.org/db/journals/josis/index.html") == "<html>ok</html>"
    assert calls == ["https://dblp.org/db/journals/josis/index.html"]


def test_head_ok_dblp_uses_anubis_get_not_head(monkeypatch):
    calls = []

    def _fake(session, url, **kw):
        calls.append((url, kw))
        return _resp(url, "<html>ok</html>")

    monkeypatch.setattr(dbase, "get_with_anubis", _fake)
    inst = DBLPDiscovery(existing_conf=[])
    assert inst._head_ok("https://dblp.org/db/conf/gis/gis2023.html") is True
    assert len(calls) == 1


def test_head_ok_dblp_404_is_false(monkeypatch):
    def _fake(session, url, **kw):
        return _resp(url, "not found", status=404)

    monkeypatch.setattr(dbase, "get_with_anubis", _fake)
    inst = DBLPDiscovery(existing_conf=[])
    assert inst._head_ok("https://dblp.org/db/conf/gis/gis1992.html") is False


def test_head_ok_dblp_challenge_page_is_not_exists(monkeypatch):
    """A leftover challenge page must not count as 'exists'."""
    def _fake(session, url, **kw):
        return _resp(url, '<script id="anubis_challenge" type="application/json">{}</script>')

    monkeypatch.setattr(dbase, "get_with_anubis", _fake)
    inst = DBLPDiscovery(existing_conf=[])
    assert inst._head_ok("https://dblp.org/db/conf/gis/gis2023.html") is False

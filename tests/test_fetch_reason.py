"""Behaviour tests for :func:`scripts.fetch_abstracts.fetch_abstract_for_paper`.

We assert the *new* 4-tuple return contract (spec AC-8 / AC-9 / TR-3.*):

* On success   → ``err_info == {}``.
* On failure   → ``err_info["reason"]`` is a member of
  :data:`REASON_ENUM` and ``err_info["last_source"]`` reflects the last
  source that was actually attempted.
* Venue-index URLs short-circuit *before* any HTTP call and yield
  ``reason == "venue_index"``.

The tests stub out every network-facing helper via ``monkeypatch`` so
they stay fully offline.
"""

from __future__ import annotations

import pytest

from scripts import fetch_abstracts as fa


def _empty_last_time() -> dict:
    """Shape mirrors what the CLI runner passes into the fetcher."""
    return {"crossref": 0.0, "semanticscholar": 0.0, "arxiv": 0.0, "openalex": 0.0}


def test_venue_index_url_short_circuits_without_http(monkeypatch):
    """AC-3 / AC-4: venue-index URLs must never hit any fetcher."""
    called = {"n": 0}

    def _boom(*_a, **_kw):
        called["n"] += 1
        raise AssertionError("fetcher should not be called for venue-index URLs")

    monkeypatch.setattr(fa, "_fetch_doi_sources_concurrent", _boom)
    monkeypatch.setattr(fa, "fetch_arxiv_abstract", _boom)

    paper = {
        "paper_url": "https://aclanthology.org/2021.acl-long.0/",
        "paper_name": "Front matter",
    }
    abstract, _lt, source, err = fa.fetch_abstract_for_paper(
        paper, _empty_last_time()
    )
    assert abstract is None
    assert source == ""
    assert err["reason"] == "venue_index"
    assert err["last_source"] == ""
    assert called["n"] == 0


def test_success_returns_empty_err_info(monkeypatch):
    """A successful DOI hit must yield ``err_info == {}``."""
    def _ok_doi(_doi, title, last_time, **_kw):
        return "This is a valid abstract that is well over five characters.", title, "crossref", last_time

    monkeypatch.setattr(fa, "_fetch_doi_sources_concurrent", _ok_doi)

    paper = {
        "paper_url": "https://doi.org/10.1234/example",
        "paper_name": "Example Paper",
    }
    abstract, _lt, source, err = fa.fetch_abstract_for_paper(
        paper, _empty_last_time()
    )
    assert abstract and len(abstract) > 5
    assert source == "crossref"
    assert err == {}


def test_no_doi_no_arxiv_hit_yields_no_abstract_available(monkeypatch):
    """DOI-less paper whose title lookup finds nothing → ``no_abstract_available``."""
    def _arxiv_miss(title, last_time, **_kw):
        return None, None, last_time

    # Should never be called since there's no DOI:
    monkeypatch.setattr(fa, "_fetch_doi_sources_concurrent", lambda *a, **k: pytest.fail("DOI path not expected"))
    monkeypatch.setattr(fa, "fetch_arxiv_abstract", _arxiv_miss)

    paper = {
        "paper_url": "https://example.org/foo",
        "paper_name": "Something Without DOI",
    }
    abstract, _lt, _source, err = fa.fetch_abstract_for_paper(
        paper, _empty_last_time()
    )
    assert abstract is None
    assert err["reason"] == "no_abstract_available"
    assert err["last_source"] == "arxiv"
    assert err["reason"] in fa.REASON_ENUM


def test_no_doi_no_title_yields_no_doi(monkeypatch):
    """Neither DOI in URL nor title → ``reason == "no_doi"``."""
    monkeypatch.setattr(fa, "_fetch_doi_sources_concurrent", lambda *a, **k: pytest.fail("DOI path not expected"))
    monkeypatch.setattr(fa, "fetch_arxiv_abstract", lambda *a, **k: pytest.fail("arxiv path not expected"))

    paper = {"paper_url": "https://example.org/foo", "paper_name": ""}
    abstract, _lt, _source, err = fa.fetch_abstract_for_paper(
        paper, _empty_last_time()
    )
    assert abstract is None
    assert err["reason"] == "no_doi"


def test_doi_empty_yields_empty_abstract(monkeypatch):
    """DOI resolves but every source returns empty → ``empty_abstract``."""
    def _empty(_doi, title, last_time, **_kw):
        return None, None, "", last_time

    def _arxiv_miss(title, last_time, **_kw):
        return None, None, last_time

    monkeypatch.setattr(fa, "_fetch_doi_sources_concurrent", _empty)
    monkeypatch.setattr(fa, "fetch_arxiv_abstract", _arxiv_miss)

    paper = {
        "paper_url": "https://doi.org/10.1234/example",
        "paper_name": "Empty Result Paper",
    }
    abstract, _lt, _source, err = fa.fetch_abstract_for_paper(
        paper, _empty_last_time()
    )
    assert abstract is None
    assert err["reason"] == "empty_abstract"
    assert err["reason"] in fa.REASON_ENUM
    assert err["last_source"] == "arxiv"


def test_arxiv_title_mismatch_flagged(monkeypatch):
    """arxiv returns a candidate whose title disagrees → ``title_mismatch``."""
    def _empty_doi(_doi, title, last_time, **_kw):
        return None, None, "", last_time

    def _arxiv_wrong_title(title, last_time, **_kw):
        return "Some abstract text but it belongs to a different paper.", "TOTALLY DIFFERENT TITLE", last_time

    monkeypatch.setattr(fa, "_fetch_doi_sources_concurrent", _empty_doi)
    monkeypatch.setattr(fa, "fetch_arxiv_abstract", _arxiv_wrong_title)

    paper = {
        "paper_url": "https://doi.org/10.1234/example",
        "paper_name": "The Actual Paper Title",
    }
    abstract, _lt, _source, err = fa.fetch_abstract_for_paper(
        paper, _empty_last_time()
    )
    assert abstract is None
    assert err["reason"] == "title_mismatch"
    assert err["title_mismatch"] is True
    assert err["last_source"] == "arxiv"

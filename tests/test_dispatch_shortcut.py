"""Task 6: main pipeline dispatch shortcuts.

Verifies that :func:`scripts.fetch_abstracts.fetch_abstract_for_paper`:

* Short-circuits without hitting the network when the URL host is
  ``openaccess.thecvf.com`` or ``openreview.net`` (delegated to the
  specialised fetchers), tagging the failure with
  ``reason == "delegated_to_specialised_fetcher"``.
* Successfully invokes the domain dispatcher for a supported host
  (``aclanthology.org``) and returns the resulting abstract with
  ``source == "acl"``.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from scripts import fetch_abstracts as fa
from papervault.services.abstract_fetchers.base import AbstractResult


def _empty_last_time() -> dict:
    return {"crossref": 0.0, "semanticscholar": 0.0, "arxiv": 0.0, "openalex": 0.0}


@pytest.mark.parametrize(
    "url",
    [
        "https://openaccess.thecvf.com/content/CVPR2023/html/Author_Paper_CVPR_2023_paper.html",
        "https://openreview.net/pdf?id=abcdef",
        "https://www.openreview.net/forum?id=xyz",
    ],
)
def test_delegated_hosts_shortcut_without_http(url):
    """theCVF / OpenReview must not trigger any HTTP call from the main pipeline."""
    called = {"n": 0}

    def _boom(*_a, **_kw):
        called["n"] += 1
        raise AssertionError("no HTTP allowed for delegated hosts")

    paper = {"paper_url": url, "paper_name": "Some title"}
    with patch.object(fa, "_dispatch_domain_fetcher", side_effect=_boom), \
         patch.object(fa, "_fetch_doi_sources_concurrent", side_effect=_boom), \
         patch.object(fa, "fetch_arxiv_abstract", side_effect=_boom):
        abstract, _lt, source, err = fa.fetch_abstract_for_paper(paper, _empty_last_time())
    assert abstract is None
    assert source == ""
    assert err["reason"] == "delegated_to_specialised_fetcher"
    assert called["n"] == 0


def test_dispatcher_success_bypasses_doi_chain(monkeypatch):
    """A supported ACL URL that the dispatcher can handle must not touch DOI/arxiv."""
    ok_result = AbstractResult(
        ok=True,
        url="https://aclanthology.org/2023.acl-long.1/",
        abstract="A synthetic ACL abstract that comfortably clears every length gate the pipeline enforces downstream.",
        source="acl",
        http_status=200,
    )
    monkeypatch.setattr(fa, "_dispatch_domain_fetcher", MagicMock(return_value=ok_result))
    monkeypatch.setattr(
        fa, "_fetch_doi_sources_concurrent",
        MagicMock(side_effect=AssertionError("DOI chain must not run on dispatcher success")),
    )
    monkeypatch.setattr(
        fa, "fetch_arxiv_abstract",
        MagicMock(side_effect=AssertionError("arxiv fallback must not run on dispatcher success")),
    )

    paper = {
        "paper_url": "https://aclanthology.org/2023.acl-long.1/",
        "paper_name": "Real title",
    }
    abstract, _lt, source, err = fa.fetch_abstract_for_paper(paper, _empty_last_time())
    assert source == "acl"
    assert abstract == ok_result.abstract
    assert err == {}


def test_dispatcher_failure_degrades_to_doi_chain(monkeypatch):
    """When the dispatcher returns ok=False we must still run the DOI/arxiv fallback."""
    monkeypatch.setattr(
        fa, "_dispatch_domain_fetcher",
        MagicMock(return_value=AbstractResult(ok=False, url="", reason="empty_abstract")),
    )

    doi_called = {"n": 0}

    def _doi(_doi, title, last_time, **_kw):
        doi_called["n"] += 1
        return "DOI-based abstract text that is long enough to clear the caller's own length gate.", title, "crossref", last_time

    monkeypatch.setattr(fa, "_fetch_doi_sources_concurrent", _doi)

    paper = {
        "paper_url": "https://aclanthology.org/2020.emnlp-main.42/",
        "paper_name": "Fallback title",
    }
    # Give the paper a DOI so ``_fetch_doi_sources_concurrent`` gets invoked.
    monkeypatch.setattr(fa, "extract_doi", lambda _u: "10.1234/example")

    abstract, _lt, source, err = fa.fetch_abstract_for_paper(paper, _empty_last_time())
    assert source == "crossref"
    assert abstract is not None
    assert err == {}
    assert doi_called["n"] == 1


def test_delegated_shortcut_precedes_dispatcher(monkeypatch):
    """AC-5: even if openreview.net were in the registry, the shortcut wins first."""
    monkeypatch.setattr(
        fa, "_dispatch_domain_fetcher",
        MagicMock(side_effect=AssertionError("dispatcher must not run for delegated hosts")),
    )
    paper = {"paper_url": "https://openreview.net/forum?id=abc", "paper_name": "T"}
    _abs, _lt, _src, err = fa.fetch_abstract_for_paper(paper, _empty_last_time())
    assert err["reason"] == "delegated_to_specialised_fetcher"

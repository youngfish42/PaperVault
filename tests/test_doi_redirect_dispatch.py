"""Tests for the DOI redirect dispatch step in ``scripts/fetch_abstracts``.

When the DOI chain (Crossref / S2 / OpenAlex) fails for a ``doi.org``
URL, :func:`fetch_abstract_for_paper` follows the redirect once and
re-dispatches the *final* landing-page URL to the domain-fetcher
registry (Springer LNCS / ISCA DOIs hit their fetchers here). The step

* only fires for ``doi.org`` URLs,
* only re-dispatches when the final host is registered AND differs from
  the original host,
* degrades silently (no ``err_info`` pollution) on any failure,
* can be disabled via ``doi_redirect_enabled=False`` (``--no-doi-redirect``).

All network-facing helpers are stubbed — the suite runs fully offline.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests

from scripts import fetch_abstracts as fa
from papervault.services.abstract_fetchers.base import AbstractResult


DOI_URL = "https://doi.org/10.1007/978-3-540-40899-4_1"
SPRINGER_URL = "https://link.springer.com/chapter/10.1007/978-3-540-40899-4_1"

SPRINGER_ABSTRACT = (
    "We describe a surgical planning environment that permits the "
    "determination or refinement of the location of a therapeutic "
    "neurosurgical intervention using information derived from an "
    "electrophysiological database."
)


def _patch_scrape_get(monkeypatch, get_mock):
    """``_dispatch_via_doi_redirect`` fetches through the thread-local
    scrape session (``_get_scrape_session``) — patch that factory so the
    mocked ``get`` is what the helper calls."""
    session = MagicMock()
    session.get = get_mock
    monkeypatch.setattr(fa, "_get_scrape_session", lambda: session)


def _empty_last_time() -> dict:
    return {"crossref": 0.0, "semanticscholar": 0.0, "arxiv": 0.0, "openalex": 0.0}


def _paper() -> dict:
    return {"paper_url": DOI_URL, "paper_name": "A 3-Dimensional Database of Deep Brain Functional Anatomy"}


def _stub_doi_chain_empty(monkeypatch):
    monkeypatch.setattr(
        fa, "_fetch_doi_sources_concurrent",
        lambda _doi, _title, last_time, **_kw: (None, None, "", last_time),
    )


def _stub_arxiv_miss(monkeypatch):
    monkeypatch.setattr(
        fa, "fetch_arxiv_abstract",
        lambda _title, last_time, **_kw: (None, None, last_time),
    )


def _redirect_response(final_url: str) -> MagicMock:
    resp = MagicMock()
    resp.url = final_url
    resp.status_code = 200
    return resp


def _dispatch_by_url(url: str) -> AbstractResult:
    """Domain dispatcher stub: doi.org URLs miss, Springer URLs hit."""
    if "doi.org" in url:
        return AbstractResult(ok=False, url=url, reason="no_abstract_available")
    if "link.springer.com" in url:
        return AbstractResult(
            ok=True, url=url, abstract=SPRINGER_ABSTRACT,
            source="springer", http_status=200,
        )
    return AbstractResult(ok=False, url=url, reason="no_abstract_available")


def test_doi_chain_failure_redirects_to_springer_fetcher(monkeypatch):
    """DOI chain empty → one redirect GET → final URL dispatched → success
    with ``source == "springer"`` and ``err_info == {}``."""
    _stub_doi_chain_empty(monkeypatch)
    monkeypatch.setattr(
        fa, "fetch_arxiv_abstract",
        MagicMock(side_effect=AssertionError("arxiv must not run on redirect success")),
    )
    dispatch_mock = MagicMock(side_effect=_dispatch_by_url)
    monkeypatch.setattr(fa, "_dispatch_domain_fetcher", dispatch_mock)
    get_mock = MagicMock(return_value=_redirect_response(SPRINGER_URL))
    _patch_scrape_get(monkeypatch, get_mock)

    abstract, _lt, source, err = fa.fetch_abstract_for_paper(_paper(), _empty_last_time())

    assert abstract == SPRINGER_ABSTRACT
    assert source == "springer"
    assert err == {}
    get_mock.assert_called_once()
    # First dispatch tried the original doi.org URL, then the final URL.
    dispatched_urls = [c.args[0] for c in dispatch_mock.call_args_list]
    assert dispatched_urls == [DOI_URL, SPRINGER_URL]


def test_redirect_to_unregistered_host_falls_through_to_arxiv(monkeypatch):
    """Final URL on an unregistered host → silent degrade → arxiv fallback."""
    _stub_doi_chain_empty(monkeypatch)
    arxiv_called = {"n": 0}

    def _arxiv(_title, last_time, **_kw):
        arxiv_called["n"] += 1
        return "arxiv abstract text long enough for the caller gate.", "A 3-Dimensional Database of Deep Brain Functional Anatomy", last_time

    monkeypatch.setattr(fa, "fetch_arxiv_abstract", _arxiv)
    monkeypatch.setattr(fa, "_dispatch_domain_fetcher", MagicMock(side_effect=_dispatch_by_url))
    _patch_scrape_get(monkeypatch, MagicMock(return_value=_redirect_response("https://some-unregistered-host.example/paper/1")),)

    abstract, _lt, source, err = fa.fetch_abstract_for_paper(_paper(), _empty_last_time())

    assert source == "arxiv"
    assert abstract is not None
    assert err == {}
    assert arxiv_called["n"] == 1


def test_redirect_fetcher_failure_does_not_pollute_err_info(monkeypatch):
    """Final URL dispatched but the fetcher returns ok=False → the failure
    reason must NOT override the synthesised ``empty_abstract`` from the
    normal fall-through logic."""
    _stub_doi_chain_empty(monkeypatch)
    _stub_arxiv_miss(monkeypatch)

    def _dispatch_fail(url: str) -> AbstractResult:
        return AbstractResult(ok=False, url=url, reason="empty_abstract", source="springer")

    monkeypatch.setattr(fa, "_dispatch_domain_fetcher", MagicMock(side_effect=_dispatch_fail))
    _patch_scrape_get(monkeypatch, MagicMock(return_value=_redirect_response(SPRINGER_URL)),)

    abstract, _lt, _source, err = fa.fetch_abstract_for_paper(_paper(), _empty_last_time())

    assert abstract is None
    assert err["reason"] == "empty_abstract"
    assert err["last_source"] == "arxiv"


def test_redirect_network_error_degrades_silently(monkeypatch):
    """A requests exception on the redirect GET is swallowed and the
    pipeline continues with the arxiv fallback."""
    _stub_doi_chain_empty(monkeypatch)
    _stub_arxiv_miss(monkeypatch)
    monkeypatch.setattr(fa, "_dispatch_domain_fetcher", MagicMock(side_effect=_dispatch_by_url))
    _patch_scrape_get(monkeypatch, MagicMock(side_effect=requests.exceptions.ConnectionError("boom")),)

    abstract, _lt, _source, err = fa.fetch_abstract_for_paper(_paper(), _empty_last_time())

    assert abstract is None
    assert err["reason"] == "empty_abstract"


def test_redirect_disabled_skips_redirect_get(monkeypatch):
    """``doi_redirect_enabled=False`` (``--no-doi-redirect``) must not issue
    the redirect GET at all."""
    _stub_doi_chain_empty(monkeypatch)
    _stub_arxiv_miss(monkeypatch)
    monkeypatch.setattr(fa, "_dispatch_domain_fetcher", MagicMock(side_effect=_dispatch_by_url))
    get_mock = MagicMock(side_effect=AssertionError("redirect GET must not run when disabled"))
    _patch_scrape_get(monkeypatch, get_mock)

    abstract, _lt, _source, err = fa.fetch_abstract_for_paper(
        _paper(), _empty_last_time(), doi_redirect_enabled=False
    )

    assert abstract is None
    assert err["reason"] == "empty_abstract"
    assert get_mock.call_count == 0


def test_redirect_step_skipped_for_non_doi_urls(monkeypatch):
    """A registered-host URL whose own fetcher fails must go straight to
    the DOI/arxiv chain — no redirect GET (the host is not doi.org)."""
    monkeypatch.setattr(
        fa, "_dispatch_domain_fetcher",
        MagicMock(return_value=AbstractResult(ok=False, url="", reason="empty_abstract", source="acl")),
    )
    monkeypatch.setattr(fa, "extract_doi", lambda _u: "10.1234/example")
    _stub_doi_chain_empty(monkeypatch)
    _stub_arxiv_miss(monkeypatch)
    get_mock = MagicMock(side_effect=AssertionError("no redirect GET for non-doi.org URLs"))
    _patch_scrape_get(monkeypatch, get_mock)

    paper = {"paper_url": "https://aclanthology.org/2023.acl-long.1/", "paper_name": "Real title"}
    abstract, _lt, _source, err = fa.fetch_abstract_for_paper(paper, _empty_last_time())

    assert abstract is None
    # Domain fetcher produced a concrete reason → surfaced verbatim.
    assert err["reason"] == "empty_abstract"
    assert get_mock.call_count == 0


def test_redirect_to_same_host_is_ignored(monkeypatch):
    """A redirect that stays on doi.org (e.g. a 404 interstitial) is not
    re-dispatched."""
    _stub_doi_chain_empty(monkeypatch)
    _stub_arxiv_miss(monkeypatch)
    dispatch_mock = MagicMock(side_effect=_dispatch_by_url)
    monkeypatch.setattr(fa, "_dispatch_domain_fetcher", dispatch_mock)
    _patch_scrape_get(monkeypatch, MagicMock(return_value=_redirect_response(DOI_URL)),)

    abstract, _lt, _source, err = fa.fetch_abstract_for_paper(_paper(), _empty_last_time())

    assert abstract is None
    dispatched_urls = [c.args[0] for c in dispatch_mock.call_args_list]
    assert dispatched_urls == [DOI_URL]  # only the initial dispatch
    assert err["reason"] == "empty_abstract"


def test_scrape_session_disables_urllib3_status_retries():
    """Review M2: the scrape session (DOI redirect resolution + PDF
    download) must mount ``Retry(total=0, respect_retry_after_header=False)``
    so 429/503 surface immediately instead of sleeping inside urllib3
    (the failure mode that stalled --openalex-title for 84 minutes)."""
    if hasattr(fa._thread_local, "scrape_session"):
        del fa._thread_local.scrape_session
    try:
        session = fa._get_scrape_session()
        for scheme in ("https", "http"):
            retries = session.get_adapter(f"{scheme}://example.org").max_retries
            assert retries.total == 0
            assert retries.respect_retry_after_header is False
    finally:
        # Do not leak the real session into tests that patch the factory.
        if hasattr(fa._thread_local, "scrape_session"):
            del fa._thread_local.scrape_session

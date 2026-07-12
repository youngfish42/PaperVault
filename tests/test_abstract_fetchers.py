"""Regression tests for :mod:`papervault.services.abstract_fetchers`.

Covers Task 5 acceptance criteria (spec AC-4) with fully-offline
fixtures. HTTP is mocked at the ``SESSION.get`` boundary — no fetcher
actually reaches the network, so tests are safe to run in CI with
``PAPERVAULT_OFFLINE=1``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from papervault.services import abstract_fetchers as af
from papervault.services.abstract_fetchers.base import (
    AbstractResult,
    MIN_ABSTRACT_CHARS,
)


FIXTURES = Path(__file__).parent / "fixtures" / "abstract_fetchers"


def _fixture_html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _make_response(html: str, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.text = html
    resp.status_code = status
    return resp


# ---------- Registry & dispatch --------------------------------------------

def test_registry_contains_all_six_domains():
    hosts = set(af.FETCHER_REGISTRY.keys())
    assert {
        "aclanthology.org",
        "proceedings.mlr.press",
        "ojs.aaai.org",
        "ijcai.org",
        "vldb.org",
        "ceur-ws.org",
    }.issubset(hosts)


def test_dispatch_unknown_host_returns_no_abstract_available():
    res = af.dispatch("https://example.com/paper")
    assert isinstance(res, AbstractResult)
    assert res.ok is False
    assert res.reason == "no_abstract_available"


def test_dispatch_empty_url_short_circuits():
    res = af.dispatch("")
    assert res.ok is False
    assert res.reason == "no_abstract_available"


def test_dispatch_strips_www_prefix():
    res = af.dispatch("https://www.vldb.org/pvldb/vol16/p1234-doe.pdf")
    # www.vldb.org must be canonicalised to vldb.org and hit the fetcher.
    assert res.source == "vldb"
    assert res.reason == "no_abstract_available"


# ---------- Domain fetchers with fixture HTML ------------------------------

@pytest.mark.parametrize(
    "url, fixture, expected_source",
    [
        ("https://aclanthology.org/2023.acl-long.1/", "acl_paper.html", "acl"),
        ("https://proceedings.mlr.press/v202/foo23a.html", "mlr_paper.html", "mlr"),
        ("https://ojs.aaai.org/index.php/AAAI/article/view/12345", "aaai_paper.html", "aaai"),
        ("https://ijcai.org/proceedings/2023/456", "ijcai_paper.html", "ijcai"),
    ],
)
def test_domain_fetchers_extract_abstract(url, fixture, expected_source):
    html = _fixture_html(fixture)
    with patch("papervault.services.abstract_fetchers._http.SESSION") as sess:
        sess.get.return_value = _make_response(html)
        res = af.dispatch(url)
    assert res.ok is True, f"expected success, got reason={res.reason!r}"
    assert res.source == expected_source
    assert res.abstract is not None
    assert len(res.abstract) >= MIN_ABSTRACT_CHARS
    # Sanity: fetcher should have stripped the leading "Abstract" label
    assert not res.abstract.lower().startswith("abstract ")


# ---------- PDF-only sites short-circuit without HTTP ----------------------

@pytest.mark.parametrize(
    "url, expected_source",
    [
        ("https://vldb.org/pvldb/vol16/p1234-doe.pdf", "vldb"),
        ("https://ceur-ws.org/Vol-3456/paper42.pdf", "ceur"),
    ],
)
def test_pdf_only_sites_return_no_abstract_available(url, expected_source):
    called = {"n": 0}

    def _boom(*_a, **_kw):
        called["n"] += 1
        raise AssertionError("PDF-only fetchers must not hit the network")

    with patch("papervault.services.abstract_fetchers._http.SESSION") as sess:
        sess.get.side_effect = _boom
        res = af.dispatch(url)
    assert res.ok is False
    assert res.reason == "no_abstract_available"
    assert res.source == expected_source
    assert called["n"] == 0


# ---------- Error surface --------------------------------------------------

def test_http_429_maps_to_rate_limited():
    with patch("papervault.services.abstract_fetchers._http.SESSION") as sess:
        sess.get.return_value = _make_response("", status=429)
        res = af.dispatch("https://aclanthology.org/2023.acl-long.1/")
    assert res.ok is False
    assert res.reason == "rate_limited"
    assert res.http_status == 429


def test_http_404_maps_to_doi_not_found():
    with patch("papervault.services.abstract_fetchers._http.SESSION") as sess:
        sess.get.return_value = _make_response("", status=404)
        res = af.dispatch("https://proceedings.mlr.press/v202/foo23a.html")
    assert res.ok is False
    assert res.reason == "doi_not_found"
    assert res.http_status == 404


def test_empty_page_falls_through_to_empty_abstract():
    with patch("papervault.services.abstract_fetchers._http.SESSION") as sess:
        sess.get.return_value = _make_response("<html><body>no abstract here</body></html>")
        res = af.dispatch("https://ojs.aaai.org/index.php/AAAI/article/view/1")
    assert res.ok is False
    assert res.reason == "empty_abstract"


# ---------- ACL: real-DOM structure regression ----------------------------

def test_acl_modern_layout_strips_h5_heading():
    """Verified against aclanthology.org/2023.acl-long.1/ and /P19-1001/
    on 2026-07-12: modern ACL pages label the abstract with
    ``<h5 class="card-title">Abstract</h5>`` (NOT ``<strong>``) and do
    NOT ship any ``<meta name="citation_abstract">`` tag.

    The fetcher must strip the ``<h5>`` heading so the returned prose
    does not start with the word "Abstract".
    """
    html = _fixture_html("acl_paper.html")
    with patch("papervault.services.abstract_fetchers._http.SESSION") as sess:
        sess.get.return_value = _make_response(html)
        res = af.dispatch("https://aclanthology.org/2099.acl-fixture.1/")
    assert res.ok is True, f"expected success, got reason={res.reason!r}"
    assert res.source == "acl"
    assert res.abstract is not None
    # Heading must not leak into the returned text.
    assert not res.abstract.lower().startswith("abstract ")
    assert "synthetic method for automated abstract fetching" in res.abstract
    # Real modern ACL abstracts are natural-language prose only -- they
    # never contain literal HTML tags or entity references. Guard the
    # fixture against regressing back to embedded ``&lt;h5&gt;`` /
    # ``&lt;span&gt;`` markers, which would drift away from the actual
    # aclanthology.org DOM the fixture is meant to mirror.
    assert "<" not in res.abstract
    assert "&lt;" not in res.abstract
    assert "&quot;" not in res.abstract


# ---------- IJCAI: real-DOM structure regression (review issue R3) ---------

def test_ijcai_real_layout_extracts_abstract_and_excludes_keywords():
    """Verified against ijcai.org/proceedings/{2017,2019,2023,2024}/0001
    on 2026-07-12: the abstract sits inside its own <div class="col-md-12">
    with no heading and no "Abstract" prefix, immediately followed by
    a second col-md-12 that only holds <div class="keywords">.

    The fetcher MUST return the abstract prose and MUST NOT leak any
    text from the keywords block into it.
    """
    html = _fixture_html("ijcai_paper.html")
    with patch("papervault.services.abstract_fetchers._http.SESSION") as sess:
        sess.get.return_value = _make_response(html)
        res = af.dispatch("https://ijcai.org/proceedings/2023/0001")
    assert res.ok is True, f"expected success, got reason={res.reason!r}"
    assert res.source == "ijcai"
    assert res.abstract is not None
    # Keywords / topic labels must never bleed into the abstract text.
    assert "Keywords:" not in res.abstract
    assert "Fixture Topic" not in res.abstract
    assert "Another Fixture Topic" not in res.abstract
    # Abstract prose must actually be present.
    assert "synthetic IJCAI proceedings abstract" in res.abstract


def test_ijcai_heading_fallback_stops_at_next_heading():
    """Fallback path (mirror / archived pages that DO carry an explicit
    <h2>Abstract</h2>) must break at the *next* heading so BibTeX /
    References / footer nav stay strictly outside the returned text.

    This is the direct regression for review issue R3 -- the previous
    implementation iterated find_next_siblings() with no stop
    condition and would slurp every following sibling.
    """
    html = _fixture_html("ijcai_paper_heading.html")
    with patch("papervault.services.abstract_fetchers._http.SESSION") as sess:
        sess.get.return_value = _make_response(html)
        res = af.dispatch("https://ijcai.org/proceedings/mirror/heading-style")
    assert res.ok is True, f"expected success, got reason={res.reason!r}"
    assert res.source == "ijcai"
    assert res.abstract is not None
    # Prose from BOTH abstract paragraphs must be captured.
    assert "mirror-style fixture" in res.abstract
    assert "multi-paragraph abstracts" in res.abstract
    # Content that appears ONLY past the next <h2> heading must NOT
    # leak in. (The words "BibTeX"/"References" themselves appear as
    # ordinary prose inside the abstract paragraphs of this fixture,
    # so we assert on markers that are unique to the following
    # sections: the @inproceedings entry key, the reference list
    # items, and the footer nav string.)
    assert "fixture2026" not in res.abstract
    assert "@inproceedings" not in res.abstract
    assert "Fixture Reference One" not in res.abstract
    assert "Fixture Reference Two" not in res.abstract
    assert "Home | Contact | About" not in res.abstract


# ---------- Guard: no PDF / OCR dependency leak ----------------------------

def test_no_pdf_deps_leak():
    """spec AC-4 Non-Goals: this iteration must not import PDF/OCR libs."""
    forbidden = {
        "pdfminer",
        "pdfminer.six",
        "pypdf",
        "PyPDF2",
        "grobid_client",
        "grobid",
        "pytesseract",
    }
    # Force fresh import of the whole package
    for name in list(sys.modules):
        if name.startswith("papervault.services.abstract_fetchers"):
            del sys.modules[name]
    import papervault.services.abstract_fetchers  # noqa: F401
    leaked = forbidden.intersection(sys.modules.keys())
    assert not leaked, f"forbidden PDF/OCR modules imported: {leaked}"

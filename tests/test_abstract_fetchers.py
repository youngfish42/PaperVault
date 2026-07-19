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

def test_registry_contains_all_expected_domains():
    """Mirror of :data:`FETCHER_REGISTRY` — must be updated whenever a
    fetcher is added or a legacy host alias is dropped. Using ``==``
    (not ``.issubset``) so a silent drop of e.g. legacy ``aaai.org``
    would fail this test loudly rather than being masked.

    P1 (2026-07): ``jmlr.org``, ``isca-archive.org`` and the legacy
    ``aaai.org`` host were added so the diagnostic-report shortlist
    stops falling into the DOI fallback chain for those venues; the
    equality assertion below is the single source of truth for that
    invariant.
    """
    hosts = set(af.FETCHER_REGISTRY.keys())
    assert hosts == {
        "aclanthology.org",
        "proceedings.mlr.press",
        "ojs.aaai.org",
        "aaai.org",
        "ijcai.org",
        "vldb.org",
        "ceur-ws.org",
        "jmlr.org",
        "isca-archive.org",
    }


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
        ("https://www.jmlr.org/papers/v25/23-1044.html", "jmlr_paper.html", "jmlr"),
        ("https://www.isca-archive.org/interspeech_2023/gong23c_interspeech.html", "isca_paper.html", "isca"),
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


def test_aaai_legacy_host_routes_to_same_fetcher():
    """Legacy ``aaai.org/ojs/index.php/AAAI/article/view/<id>`` URLs
    301-redirect to ``ojs.aaai.org/...``. requests.Session follows the
    redirect transparently, so the *dispatched* fetcher is still the
    AAAI one and it must succeed on the same fixture HTML."""
    html = _fixture_html("aaai_paper.html")
    with patch("papervault.services.abstract_fetchers._http.SESSION") as sess:
        sess.get.return_value = _make_response(html)
        res = af.dispatch("https://aaai.org/ojs/index.php/AAAI/article/view/4093")
    assert res.ok is True, f"expected success, got reason={res.reason!r}"
    assert res.source == "aaai"
    assert res.abstract is not None
    assert len(res.abstract) >= MIN_ABSTRACT_CHARS


@pytest.mark.parametrize(
    "url",
    [
        "https://aaai.org/",
        "https://aaai.org/conference/aaai/aaai-24/",
        "https://www.aaai.org/about/",
        # Post-second-review fix: the previous substring check
        # ``"/article/view/" in path`` would have LET THESE PASS the
        # guard (and then wasted an HTTP request only to return
        # ``empty_abstract``). The segment-anchored matcher rejects
        # them cleanly at the guard.
        "https://aaai.org/somepage?next=/article/view/1",
        "https://aaai.org/newsarticle/viewer/1234/",
        "https://aaai.org/misc/article-view-index.html",
    ],
)
def test_aaai_legacy_host_non_article_paths_short_circuit(url):
    """Post-review fix: adding ``aaai.org`` to ``allowed_hosts`` also
    exposes non-article pages (conference home, news, membership) to
    the AAAI fetcher. Those must be rejected *before* the network call
    with reason=``no_abstract_available`` so we do not (a) burn HTTP
    quota, and (b) pollute the ``empty_abstract`` diagnostic bucket."""
    with patch("papervault.services.abstract_fetchers._http.SESSION") as sess:
        sess.get.side_effect = AssertionError(
            "AAAI fetcher must NOT hit the network for non-article aaai.org URLs"
        )
        res = af.dispatch(url)
    assert res.ok is False
    assert res.source == "aaai"
    assert res.reason == "no_abstract_available"


def test_isca_meta_only_page_returns_empty_abstract():
    """Post-review fix: <meta name="description"> on ISCA pages is
    typically an auto-truncated 150-char snippet. Silently accepting
    it as a full abstract would (a) poison the cache with truncated
    prose and (b) permanently block the DOI fallback chain. The
    fetcher must return ``empty_abstract`` instead so the outer
    pipeline can degrade to Crossref / S2 / arXiv / OpenAlex."""
    html = _fixture_html("isca_meta_only.html")
    with patch("papervault.services.abstract_fetchers._http.SESSION") as sess:
        sess.get.return_value = _make_response(html)
        res = af.dispatch(
            "https://www.isca-archive.org/interspeech_2099/fixture_metaonly.html"
        )
    assert res.ok is False
    assert res.source == "isca"
    assert res.reason == "empty_abstract"


def test_jmlr_legacy_layout_captures_text_between_heading_and_links():
    """Verified against real JMLR pages (v1 meila00a, v5 evendar03a /
    lanckriet04a) on 2026-07-18: the pre-v10 layout puts the abstract
    prose as **raw text nodes** immediately after ``<h3>Abstract</h3>``
    (interleaved with inline ``<i>/<sup>`` math markup), followed by a
    ``<font color="gray"><p>[abs]</p></font>`` download-links block.

    The fetcher's legacy branch must:

    * walk ``heading.next_siblings`` (which includes NavigableString)
      instead of ``find_next_siblings()`` (which skips text nodes),
    * capture inline elements such as ``<i>`` and ``<sup>``,
    * stop before the ``<font>`` / ``<p>`` link block so ``[abs]``,
      ``[pdf]``, ``[ps.gz]`` markers never leak in.

    The fixture uses distinctive ``MATH_TOKEN_*`` sentinels that only
    appear inside the ``<i>`` and ``<sup>`` elements, so the assertions
    below genuinely witness the inline-capture branch of
    ``_collect_between_heading_and_links`` (a bare letter like ``t`` or
    a common word would be entailed by the surrounding prose and would
    give a false positive).
    """
    html = _fixture_html("jmlr_legacy.html")
    with patch("papervault.services.abstract_fetchers._http.SESSION") as sess:
        sess.get.return_value = _make_response(html)
        res = af.dispatch("https://www.jmlr.org/papers/v05/legacy01.html")
    assert res.ok is True, f"expected success, got reason={res.reason!r}"
    assert res.source == "jmlr"
    assert res.abstract is not None
    # Abstract prose is present.
    assert "raw text nodes right after" in res.abstract
    # Inline <i> and <sup> math markup were picked up (unique sentinels
    # that appear ONLY inside those inline elements in the fixture).
    assert "MATH_TOKEN_XI" in res.abstract
    assert "MATH_TOKEN_SUP_ETA" in res.abstract
    # Download-links markers must NOT leak in.
    assert "[abs]" not in res.abstract
    assert "[pdf]" not in res.abstract
    assert "[ps.gz]" not in res.abstract
    # Author byline (which appears BEFORE the heading) must not appear.
    assert "Alice Fixture" not in res.abstract
    assert "Bob Placeholder" not in res.abstract


def test_jmlr_hybrid_short_modern_falls_back_to_legacy():
    """Post-second-review fix: when the modern ``<p class="abstract">``
    selector matches but the shell only carries a short placeholder
    (e.g. ``TBD`` on rehosted v1..v5 URLs), the fetcher must retry the
    legacy walk and keep whichever branch produced the longer prose.

    The previous gate ``if not text:`` skipped the legacy branch as
    soon as modern matched *anything* (even a 3-char stub), silently
    reporting ``empty_abstract`` when a full legacy abstract was
    sitting one branch away."""
    html = _fixture_html("jmlr_hybrid_short_modern.html")
    with patch("papervault.services.abstract_fetchers._http.SESSION") as sess:
        sess.get.return_value = _make_response(html)
        res = af.dispatch("https://www.jmlr.org/papers/v12/hybrid01.html")
    assert res.ok is True, f"expected success, got reason={res.reason!r}"
    assert res.source == "jmlr"
    assert res.abstract is not None
    assert len(res.abstract) >= MIN_ABSTRACT_CHARS
    # Real legacy prose must be surfaced …
    assert "legacy-layout abstract that sits as raw text nodes" in res.abstract
    # … and the empty modern shell must NOT be the returned value.
    assert res.abstract.strip() != "TBD"


def test_aaai_double_label_strips_h2_and_strong():
    """Post-second-review fix: on AAAI OJS pages that emit BOTH an
    ``<h2>Abstract</h2>`` heading and an in-body ``<strong>Abstract</strong>``
    label, both labels must be stripped so the returned prose does
    not carry the literal word ``Abstract`` as its prefix.

    The previous implementation passed an explicit
    ``tags=("h1","h2","h3","h4")`` tuple to ``strip_abstract_label``
    which silently dropped ``strong`` from the default tuple, letting
    the second label leak into the abstract."""
    html = _fixture_html("aaai_double_label.html")
    with patch("papervault.services.abstract_fetchers._http.SESSION") as sess:
        sess.get.return_value = _make_response(html)
        res = af.dispatch("https://ojs.aaai.org/index.php/AAAI/article/view/99999")
    assert res.ok is True, f"expected success, got reason={res.reason!r}"
    assert res.source == "aaai"
    assert res.abstract is not None
    # Neither label should survive at the start of the prose.
    assert not res.abstract.lower().startswith("abstract")
    # Sanity: real prose is present.
    assert "synthetic AAAI OJS page" in res.abstract


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
    """spec AC-4 Non-Goals: this iteration must not import PDF/OCR libs.

    ``pdfminer.six`` is the PyPI *distribution* name, not an importable
    module name (the top-level package it installs is just ``pdfminer``),
    so we only guard the actual import roots here.

    The fresh-import dance mutates ``sys.modules`` — we must snapshot the
    entries we remove and restore them in ``finally`` so later tests that
    ``patch("papervault.services.abstract_fetchers._http.SESSION")`` keep
    seeing the same module object the test file bound at import time
    (otherwise the module-level ``af`` alias would point at a stale copy).
    """
    forbidden = {
        "pdfminer",
        "pypdf",
        "PyPDF2",
        "grobid_client",
        "grobid",
        "pytesseract",
    }
    prefix = "papervault.services.abstract_fetchers"
    saved = {name: sys.modules[name] for name in list(sys.modules) if name.startswith(prefix)}
    try:
        for name in saved:
            del sys.modules[name]
        import papervault.services.abstract_fetchers  # noqa: F401
        leaked = forbidden.intersection(sys.modules.keys())
        assert not leaked, f"forbidden PDF/OCR modules imported: {leaked}"
    finally:
        # Drop any freshly-imported copy and restore the originals so
        # downstream tests keep patching the same module objects.
        for name in list(sys.modules):
            if name.startswith(prefix):
                del sys.modules[name]
        sys.modules.update(saved)

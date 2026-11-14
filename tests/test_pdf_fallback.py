"""Tests for the last-resort PDF abstract fallback.

* ``pdf_extract.extract_abstract_from_pdf_bytes`` unit tests run against
  minimal single-page PDFs generated in-process (valid xref/trailer, one
  Helvetica text stream) — no binary fixtures needed.
* ``scripts.fetch_abstracts._fetch_pdf_abstract`` tests stub the HTTP
  layer (streamed GET) — fully offline.
* ``fetch_abstract_for_paper`` integration asserts the fallback fires
  only for direct .pdf URLs and honours ``pdf_fallback_enabled=False``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from papervault.services.abstract_fetchers.pdf_extract import (
    PDF_ABSTRACT_MAX_CHARS,
    extract_abstract_from_pdf_bytes,
)
from scripts import fetch_abstracts as fa


def _make_pdf(lines) -> bytes:
    """Minimal single-page PDF that pdfminer can parse (valid xref)."""
    content = ["BT /F1 12 Tf 72 720 Td 14 TL"]
    for ln in lines:
        esc = ln.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        content.append(f"({esc}) Tj T*")
    content.append("ET")
    stream = "\n".join(content).encode("latin-1")

    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n"
    ).encode()
    return bytes(out)


ABSTRACT_LINES = [
    "This paper presents a synthetic mechanism used to verify PDF abstract",
    "extraction. The prose must be long enough to clear the sixty character",
    "minimum gate and stop before the introduction section begins.",
]

GOOD_PDF = _make_pdf(
    ["A Synthetic Test Paper", "Alice Fixture and Bob Placeholder", "", "Abstract"]
    + ABSTRACT_LINES
    + ["", "1. Introduction", "This introduction must never be extracted."]
)


# ---------- extract_abstract_from_pdf_bytes ----------------------------------

def test_pdf_extract_happy_path():
    abstract = extract_abstract_from_pdf_bytes(GOOD_PDF)
    assert abstract.startswith("This paper presents a synthetic mechanism")
    assert len(abstract) >= fa.S2_MIN_ABSTRACT_CHARS
    # Title / authors / introduction must stay out.
    assert "Synthetic Test Paper" not in abstract
    assert "Alice Fixture" not in abstract
    assert "introduction must never" not in abstract


def test_pdf_extract_inline_abstract_prefix():
    """IEEE style: "Abstract—The prose ..." on the same line."""
    pdf = _make_pdf(
        ["Some Paper", "", "Abstract-This inline abstract variant starts right after the"]
        + ["dash and is long enough to clear the minimum length gate easily.", "", "Index Terms-testing"]
    )
    abstract = extract_abstract_from_pdf_bytes(pdf)
    assert abstract.startswith("This inline abstract variant")
    assert "Index Terms" not in abstract


def test_pdf_extract_no_abstract_section():
    pdf = _make_pdf(["Some Paper", "", "1. Introduction", "No abstract here at all."])
    assert extract_abstract_from_pdf_bytes(pdf) == ""


def test_pdf_extract_rejects_copyright_block():
    """A block starting with the ACM copyright boilerplate is junk."""
    pdf = _make_pdf(
        ["Some Paper", "", "Abstract", "Permission to make digital or hard copies of all or"]
        + ["part of this work for personal or classroom use is granted without fee."]
    )
    assert extract_abstract_from_pdf_bytes(pdf) == ""


def test_pdf_extract_caps_at_max_chars():
    long_lines = ["word " * 60] * 40  # ~12000 chars of abstract prose
    pdf = _make_pdf(["T", "", "Abstract"] + long_lines + ["", "Keywords", "x"])
    abstract = extract_abstract_from_pdf_bytes(pdf)
    assert abstract
    assert len(abstract) <= PDF_ABSTRACT_MAX_CHARS


def test_pdf_extract_stops_at_pvldb_reference_format():
    """PVLDB layout: the "PVLDB Reference Format:" block follows the abstract
    immediately (before "1. Introduction") and must not leak in.
    Regression from validation run 2026-10-21 (vol14 p521-farias ratio 0.60)."""
    pdf = _make_pdf(
        ["Some PVLDB Paper", "", "Abstract"]
        + ABSTRACT_LINES
        + ["", "PVLDB Reference Format:", "Alice Fixture. Some PVLDB Paper. PVLDB, 14(4): 521-533, 2021."]
        + ["", "1. Introduction", "Body text."]
    )
    abstract = extract_abstract_from_pdf_bytes(pdf)
    assert abstract.startswith("This paper presents a synthetic mechanism")
    assert "Reference Format" not in abstract
    assert "PVLDB, 14(4)" not in abstract


def test_pdf_extract_rejects_word_jammed_text_layer():
    """Broken text layer with no space glyphs (some CEUR workshop exports)
    yields "ABSTRACTAnnotatingvideos..." — a long block with almost no
    spaces is unusable and must be rejected.
    Regression from validation run 2026-10-21 (Vol-1141 paper_10)."""
    jammed = [
        "ThispaperpresentsasyntheticmechanismusedtoverifyPDFabstractextraction",
        "withoutanyspaceglyphsinthelayertheresultisunusablegarbagetextthatmust",
        "berejectedbythespaceratiodetectorinsteadofenteringthecache",
    ]
    pdf = _make_pdf(["Some Paper", "", "Abstract"] + jammed + ["", "1. Introduction", "x"])
    assert extract_abstract_from_pdf_bytes(pdf) == ""


def test_pdf_extract_garbage_bytes():
    assert extract_abstract_from_pdf_bytes(b"not a pdf at all") == ""
    assert extract_abstract_from_pdf_bytes(b"") == ""


# ---------- _fetch_pdf_abstract (HTTP stubbed) --------------------------------

def _patch_scrape_get(monkeypatch, get_mock):
    """``_download_pdf_bytes`` fetches through the thread-local scrape
    session (``_get_scrape_session``) — patch that factory so the mocked
    ``get`` is what the helper calls."""
    session = MagicMock()
    session.get = get_mock
    monkeypatch.setattr(fa, "_get_scrape_session", lambda: session)


def _pdf_response(url: str, payload: bytes, status: int = 200, ctype: str = "application/pdf") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.url = url
    resp.headers = {"Content-Type": ctype}
    resp.iter_content = lambda chunk_size=65536: [payload[i:i + chunk_size] for i in range(0, len(payload), chunk_size)]
    return resp


def test_fetch_pdf_abstract_success(monkeypatch):
    url = "https://www.vldb.org/pvldb/vol13/p1234-doe.pdf"
    _patch_scrape_get(monkeypatch, MagicMock(return_value=_pdf_response(url, GOOD_PDF)),)
    abstract, reason = fa._fetch_pdf_abstract(url)
    assert reason == ""
    assert abstract.startswith("This paper presents a synthetic mechanism")


def test_fetch_pdf_abstract_truncates_oversize(monkeypatch):
    """The stream is capped at PDF_FALLBACK_MAX_BYTES regardless of content length."""
    url = "https://ceur-ws.org/Vol-3456/paper42.pdf"
    resp = _pdf_response(url, GOOD_PDF + b"junk" * 100)
    _patch_scrape_get(monkeypatch, MagicMock(return_value=resp))
    abstract, reason = fa._fetch_pdf_abstract(url)
    assert reason == ""
    assert abstract  # the tiny PDF sits within the first bytes


def test_fetch_pdf_abstract_non_pdf_final_url_rejected(monkeypatch):
    url = "https://example.org/paper.pdf"
    resp = _pdf_response("https://example.org/landing.html", b"<html>no</html>", ctype="text/html")
    _patch_scrape_get(monkeypatch, MagicMock(return_value=resp))
    abstract, reason = fa._fetch_pdf_abstract(url)
    assert abstract is None
    assert reason == "empty_abstract"


def test_fetch_pdf_abstract_oversize_content_length_skips_download(monkeypatch):
    """Declared Content-Length above the cap → skip without consuming the
    body (a truncated download would fail pdfminer's xref parse anyway)."""
    url = "https://www.vldb.org/pvldb/vol17/p3644-dayan.pdf"
    resp = _pdf_response(url, GOOD_PDF)
    resp.headers = {"Content-Type": "application/pdf", "Content-Length": str(fa.PDF_FALLBACK_MAX_BYTES + 1)}
    iter_called = {"n": 0}

    def _iter(chunk_size=65536):
        iter_called["n"] += 1
        return []

    resp.iter_content = _iter
    _patch_scrape_get(monkeypatch, MagicMock(return_value=resp))
    abstract, reason = fa._fetch_pdf_abstract(url)
    assert abstract is None
    assert reason == "empty_abstract"
    assert iter_called["n"] == 0


def test_fetch_pdf_abstract_http_error(monkeypatch):
    url = "https://example.org/paper.pdf"
    _patch_scrape_get(monkeypatch, MagicMock(return_value=_pdf_response(url, b"", status=500)),)
    abstract, reason = fa._fetch_pdf_abstract(url)
    assert abstract is None
    assert reason == "network"
    assert reason in fa.REASON_ENUM


# ---------- fetch_abstract_for_paper integration -------------------------------

def _empty_last_time() -> dict:
    return {"crossref": 0.0, "semanticscholar": 0.0, "arxiv": 0.0, "openalex": 0.0}


def _stub_chain_empty(monkeypatch):
    monkeypatch.setattr(
        fa, "_fetch_doi_sources_concurrent",
        lambda _doi, _title, last_time, **_kw: (None, None, "", last_time),
    )
    monkeypatch.setattr(
        fa, "fetch_arxiv_abstract",
        lambda _title, last_time, **_kw: (None, None, last_time),
    )


def test_pdf_url_falls_back_to_pdf_source(monkeypatch):
    """VLDB-style direct PDF URL: domain fetcher short-circuits, DOI chain
    and arxiv miss, PDF fallback delivers with ``source == "pdf"``."""
    _stub_chain_empty(monkeypatch)
    url = "https://www.vldb.org/pvldb/vol13/p1234-doe.pdf"
    _patch_scrape_get(monkeypatch, MagicMock(return_value=_pdf_response(url, GOOD_PDF)),)
    paper = {"paper_url": url, "paper_name": "Some VLDB Paper"}
    abstract, _lt, source, err = fa.fetch_abstract_for_paper(paper, _empty_last_time())
    assert source == "pdf"
    assert abstract is not None and len(abstract) >= 5
    assert err == {}


def test_pdf_fallback_disabled_skips_download(monkeypatch):
    _stub_chain_empty(monkeypatch)
    url = "https://www.vldb.org/pvldb/vol13/p1234-doe.pdf"
    get_mock = MagicMock(side_effect=AssertionError("PDF download must not run when disabled"))
    _patch_scrape_get(monkeypatch, get_mock)
    paper = {"paper_url": url, "paper_name": "Some VLDB Paper"}
    abstract, _lt, _source, err = fa.fetch_abstract_for_paper(
        paper, _empty_last_time(), pdf_fallback_enabled=False
    )
    assert abstract is None
    # VLDB fetcher short-circuit reason is surfaced (unchanged behaviour).
    assert err["reason"] == "no_abstract_available"
    assert get_mock.call_count == 0


def test_non_pdf_url_does_not_trigger_pdf_fallback(monkeypatch):
    _stub_chain_empty(monkeypatch)
    get_mock = MagicMock(side_effect=AssertionError("no PDF download for non-.pdf URLs"))
    _patch_scrape_get(monkeypatch, get_mock)
    paper = {"paper_url": "https://example.org/paper/1", "paper_name": "Some Paper"}
    abstract, _lt, _source, err = fa.fetch_abstract_for_paper(paper, _empty_last_time())
    assert abstract is None
    assert err["reason"] in fa.REASON_ENUM
    assert get_mock.call_count == 0


def test_pdf_fallback_failure_reasons_do_not_break_err_synthesis(monkeypatch):
    """PDF extraction failure must be silent — the synthesized reason stays
    the VLDB short-circuit one, not a PDF-specific value."""
    _stub_chain_empty(monkeypatch)
    url = "https://www.vldb.org/pvldb/vol13/p1234-doe.pdf"
    bad_pdf = _make_pdf(["Some Paper", "", "1. Introduction", "No abstract."])
    _patch_scrape_get(monkeypatch, MagicMock(return_value=_pdf_response(url, bad_pdf)),)
    paper = {"paper_url": url, "paper_name": "Some VLDB Paper"}
    abstract, _lt, _source, err = fa.fetch_abstract_for_paper(paper, _empty_last_time())
    assert abstract is None
    assert err["reason"] == "no_abstract_available"
    assert err["reason"] in fa.REASON_ENUM


def test_pdf_extract_stops_at_introduction_without_space():
    """Review m5: real PDFs often typeset "1.Introduction" with no space
    after the dot — the end marker must still cut there."""
    pdf = _make_pdf(["T", "", "Abstract"] + ABSTRACT_LINES + ["", "1.Introduction", "Body text."])
    abstract = extract_abstract_from_pdf_bytes(pdf)
    assert abstract.startswith("This paper presents a synthetic mechanism")
    assert "Body text." not in abstract


def test_fetch_pdf_abstract_rejects_sub_threshold_block(monkeypatch):
    """Review m4: a stub Abstract section (< MIN_ABSTRACT_CHARS) must
    degrade to empty_abstract, never enter the cache."""
    short_pdf = _make_pdf(["Some Paper", "", "Abstract", "Too short.", "", "1. Introduction", "x"])
    url = "https://example.org/paper.pdf"
    _patch_scrape_get(monkeypatch, MagicMock(return_value=_pdf_response(url, short_pdf)))
    abstract, reason = fa._fetch_pdf_abstract(url)
    assert abstract is None
    assert reason == "empty_abstract"

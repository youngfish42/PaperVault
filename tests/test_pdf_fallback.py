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


def test_pdf_extract_garbage_bytes():
    assert extract_abstract_from_pdf_bytes(b"not a pdf at all") == ""
    assert extract_abstract_from_pdf_bytes(b"") == ""


# ---------- _fetch_pdf_abstract (HTTP stubbed) --------------------------------

def _pdf_response(url: str, payload: bytes, status: int = 200, ctype: str = "application/pdf") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.url = url
    resp.headers = {"Content-Type": ctype}
    resp.iter_content = lambda chunk_size=65536: [payload[i:i + chunk_size] for i in range(0, len(payload), chunk_size)]
    return resp


def test_fetch_pdf_abstract_success(monkeypatch):
    url = "https://www.vldb.org/pvldb/vol13/p1234-doe.pdf"
    monkeypatch.setattr(
        fa._COLLECTOR_SESSION, "get",
        MagicMock(return_value=_pdf_response(url, GOOD_PDF)),
    )
    abstract, reason = fa._fetch_pdf_abstract(url)
    assert reason == ""
    assert abstract.startswith("This paper presents a synthetic mechanism")


def test_fetch_pdf_abstract_truncates_oversize(monkeypatch):
    """The stream is capped at PDF_FALLBACK_MAX_BYTES regardless of content length."""
    url = "https://ceur-ws.org/Vol-3456/paper42.pdf"
    resp = _pdf_response(url, GOOD_PDF + b"junk" * 100)
    monkeypatch.setattr(fa._COLLECTOR_SESSION, "get", MagicMock(return_value=resp))
    abstract, reason = fa._fetch_pdf_abstract(url)
    assert reason == ""
    assert abstract  # the tiny PDF sits within the first bytes


def test_fetch_pdf_abstract_non_pdf_final_url_rejected(monkeypatch):
    url = "https://example.org/paper.pdf"
    resp = _pdf_response("https://example.org/landing.html", b"<html>no</html>", ctype="text/html")
    monkeypatch.setattr(fa._COLLECTOR_SESSION, "get", MagicMock(return_value=resp))
    abstract, reason = fa._fetch_pdf_abstract(url)
    assert abstract is None
    assert reason == "empty_abstract"


def test_fetch_pdf_abstract_http_error(monkeypatch):
    url = "https://example.org/paper.pdf"
    monkeypatch.setattr(
        fa._COLLECTOR_SESSION, "get",
        MagicMock(return_value=_pdf_response(url, b"", status=500)),
    )
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
    monkeypatch.setattr(
        fa._COLLECTOR_SESSION, "get",
        MagicMock(return_value=_pdf_response(url, GOOD_PDF)),
    )
    paper = {"paper_url": url, "paper_name": "Some VLDB Paper"}
    abstract, _lt, source, err = fa.fetch_abstract_for_paper(paper, _empty_last_time())
    assert source == "pdf"
    assert abstract is not None and len(abstract) >= 5
    assert err == {}


def test_pdf_fallback_disabled_skips_download(monkeypatch):
    _stub_chain_empty(monkeypatch)
    url = "https://www.vldb.org/pvldb/vol13/p1234-doe.pdf"
    get_mock = MagicMock(side_effect=AssertionError("PDF download must not run when disabled"))
    monkeypatch.setattr(fa._COLLECTOR_SESSION, "get", get_mock)
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
    monkeypatch.setattr(fa._COLLECTOR_SESSION, "get", get_mock)
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
    monkeypatch.setattr(
        fa._COLLECTOR_SESSION, "get",
        MagicMock(return_value=_pdf_response(url, bad_pdf)),
    )
    paper = {"paper_url": url, "paper_name": "Some VLDB Paper"}
    abstract, _lt, _source, err = fa.fetch_abstract_for_paper(paper, _empty_last_time())
    assert abstract is None
    assert err["reason"] == "no_abstract_available"
    assert err["reason"] in fa.REASON_ENUM

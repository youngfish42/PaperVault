"""VLDB / PVLDB (``vldb.org/pvldb/vol*``) abstract fetcher — PDF-only site.

VLDB proceedings pages are essentially a PDF landing page: the abstract
text lives inside the PDF, not the HTML. Per spec Non-Goals L21, this
iteration deliberately does **not** ship PDF extraction (no pdfminer,
no pypdf, no Grobid, no OCR). We short-circuit to
``ok=False, reason="no_abstract_available"`` so the caller can either
skip the record or degrade to DOI-based fallbacks upstream.

The absence of PDF-parsing imports is asserted by
``tests/test_abstract_fetchers.py::test_no_pdf_deps_leak`` to guard
against silent regressions.
"""

from __future__ import annotations

from .base import AbstractResult, Fetcher


class _VLDBFetcher(Fetcher):
    allowed_hosts = ("vldb.org",)
    source = "vldb"

    def fetch(self, url: str) -> AbstractResult:
        return AbstractResult(
            ok=False, url=url, source=self.source, reason="no_abstract_available",
        )


VLDB_FETCHER = _VLDBFetcher()

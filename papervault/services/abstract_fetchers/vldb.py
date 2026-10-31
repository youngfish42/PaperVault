"""VLDB / PVLDB (``vldb.org/pvldb/vol*``) abstract fetcher — PDF-only site.

VLDB proceedings pages are essentially a PDF landing page: the abstract
text lives inside the PDF, not the HTML. The fetcher short-circuits to
``ok=False, reason="no_abstract_available"`` so the caller degrades to
DOI-based fallbacks upstream; when those also fail, the last-resort PDF
fallback in ``scripts/fetch_abstracts.py`` (``_fetch_pdf_abstract`` +
:mod:`.pdf_extract`) downloads the PDF directly and extracts the
Abstract section.

``pdfminer`` is imported lazily inside :mod:`.pdf_extract` only, so this
package still pulls no PDF library at import time — asserted by
``tests/test_abstract_fetchers.py::test_no_pdf_deps_leak``.
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

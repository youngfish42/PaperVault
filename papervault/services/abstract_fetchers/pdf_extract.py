"""Last-resort PDF abstract extraction.

Used by ``scripts/fetch_abstracts.py`` when every metadata source has
failed and the paper URL points directly at a PDF (``vldb.org`` /
``ceur-ws.org`` / ``web.archive.org`` snapshots, ...). The text layer
of the first three pages usually contains the abstract between an
"Abstract" heading and the "1. Introduction" (or "Keywords") section
marker.

``pdfminer.six`` is imported lazily *inside* the function so that
importing the ``abstract_fetchers`` package never pulls a PDF library
into ``sys.modules`` —
``tests/test_abstract_fetchers.py::test_no_pdf_deps_leak`` asserts the
package stays PDF-free at import time even though pdfminer is now an
intentional (optional-at-runtime) dependency.
"""

from __future__ import annotations

import io
import re

from ._http import clean_text

# Hard cap for the extracted block; real abstracts are well under this.
PDF_ABSTRACT_MAX_CHARS = 2000
# pdfminer page slice: abstracts virtually always live in pages 0-2.
PDF_EXTRACT_PAGES = 3

# Standalone heading line: "Abstract" / "ABSTRACT" with optional trailing
# punctuation ("Abstract.", "ABSTRACT —").
_HEADING_RE = re.compile(r"^\s*abstract\s*[.—\-:]*\s*$", re.IGNORECASE | re.MULTILINE)
# Inline prefix: "Abstract—The prose starts on the same line ...".
_INLINE_RE = re.compile(r"^\s*abstract\s*[—\-.:]\s*", re.IGNORECASE | re.MULTILINE)
# Section markers that terminate the abstract block.
_END_RE = re.compile(
    r"^\s*(?:1\.?\s+introduction|i\.?\s+introduction|keywords?|index terms|key words)\b",
    re.IGNORECASE | re.MULTILINE,
)
# ACM-style copyright boilerplate sometimes lands right after the
# "Abstract" keyword; a block starting with it is scraping junk.
_COPYRIGHT_RE = re.compile(r"^\s*permission to make digital", re.IGNORECASE)


def extract_abstract_from_pdf_bytes(data: bytes) -> str:
    """Extract the Abstract section from PDF bytes; return "" when absent.

    Defensive by construction: any parser failure, missing section or
    copyright-boilerplate block degrades to "" (caller maps to
    ``empty_abstract``).
    """
    if not data:
        return ""
    from pdfminer.high_level import extract_text  # lazy: see module docstring

    text = ""
    try:
        text = extract_text(io.BytesIO(data), page_numbers=range(PDF_EXTRACT_PAGES))
    except Exception:
        return ""
    if not text:
        return ""

    start = None
    m = _HEADING_RE.search(text)
    if m is not None:
        start = m.end()
    else:
        m = _INLINE_RE.search(text)
        if m is not None:
            start = m.end()
    if start is None:
        return ""

    block = text[start:start + PDF_ABSTRACT_MAX_CHARS * 2]
    end = _END_RE.search(block)
    if end is not None:
        block = block[: end.start()]
    block = block[:PDF_ABSTRACT_MAX_CHARS]
    abstract = clean_text(block)
    if _COPYRIGHT_RE.match(abstract):
        return ""
    return abstract

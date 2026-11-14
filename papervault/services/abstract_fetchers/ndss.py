"""NDSS Symposium (``www.ndss-symposium.org``) abstract fetcher.

Verified against real pages on 2026-10-19:

* Legacy edition pages (e.g.
  ``https://www.ndss-symposium.org/ndss2000/first-step-towards-automated-detection-buffer-overrun-vulnerabilities-paper/``)
  render the abstract as a ``<p class="wp-block-paragraph">`` inside
  ``<main> > <section class="new-wrapper">``, preceded by a strong-only
  author byline and followed by metadata rows (``Date:``,
  ``Associated Event:``).

* Modern pages (e.g.
  ``https://www.ndss-symposium.org/ndss-paper/lmsanitator-...``) use
  ``<main> > <article> > <div class="entry-content"> > <div
  class="paper-data">`` with the same paragraph conventions (strong-only
  byline first, abstract paragraphs after).

So both layouts reduce to: take the paper container, drop strong-only
byline paragraphs and bold-label metadata rows, and join what is left.

* ``<meta name="description">`` (Yoast) carries the full abstract on
  this site — unlike ISCA it is NOT auto-truncated — so it is a safe
  final fallback when the body container yields nothing.

Path guard: bare edition index pages (``/ndss2000/``, ``/``, ``/ndss-paper/``)
have no abstract and short-circuit before the network call.

Registry note: the dispatcher canonicalises hosts by stripping a
leading ``www.``, so the key registered here is the canonical
``ndss-symposium.org`` (listing ``www.ndss-symposium.org`` would never
match).
"""

from __future__ import annotations

from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .base import AbstractResult, Fetcher, MIN_ABSTRACT_CHARS
from ._http import clean_text, http_get, strip_abstract_label


def _looks_like_paper_page(url: str) -> bool:
    """Return False for bare edition index pages (``/``, ``/ndss2000/``)
    and for direct media links (``/wp-content/uploads/.../paper.pdf``
    passes the ≥2-segment test but is not a paper page)."""
    path = (urlparse(url).path or "").lower()
    if path.endswith((".pdf", ".ppt", ".pptx", ".zip", ".ps", ".doc", ".docx")):
        return False
    segments = [seg for seg in path.split("/") if seg]
    return len(segments) >= 2


def _is_strong_only(p) -> bool:
    """Author bylines are paragraphs whose entire text is bold."""
    strong = p.find("strong")
    if strong is None:
        return False
    return clean_text(strong.get_text()) == clean_text(p.get_text())


def _is_metadata_row(p) -> bool:
    """Metadata rows start with a bold label ending in ':' (Date:, ...)."""
    strong = p.find("strong")
    if strong is None:
        return False
    return clean_text(strong.get_text()).endswith(":")


class _NdssFetcher(Fetcher):
    allowed_hosts = ("ndss-symposium.org",)
    source = "ndss"

    def fetch(self, url: str) -> AbstractResult:
        if not _looks_like_paper_page(url):
            return AbstractResult(
                ok=False, url=url, source=self.source,
                reason="no_abstract_available",
            )

        resp, err = http_get(url)
        if err is not None:
            return err
        soup = BeautifulSoup(resp.text, "html.parser")

        text = ""
        container = soup.find("div", class_="paper-data")
        if container is None:
            container = soup.find("section", class_="new-wrapper")
        if container is not None:
            strip_abstract_label(container)
            parts = []
            # ``recursive=False``: the modern template emits *nested*
            # paragraphs (``<p><strong><p>byline</p></strong></p>`` —
            # invalid HTML that html.parser keeps nested), so a deep
            # ``find_all("p")`` would return every paragraph twice and
            # duplicate the abstract.
            for p in container.find_all("p", recursive=False):
                if _is_strong_only(p) or _is_metadata_row(p):
                    continue
                part = clean_text(p.get_text(" "))
                if part:
                    parts.append(part)
            text = clean_text(" ".join(parts))

        # Container residue too short to be an abstract → still try the
        # (full, non-truncated on this site) meta description fallback.
        if len(text) < MIN_ABSTRACT_CHARS:
            meta = soup.find("meta", attrs={"name": "description"})
            if meta is not None:
                text = clean_text(meta.get("content"))

        if not text or len(text) < MIN_ABSTRACT_CHARS:
            return AbstractResult(
                ok=False, url=url, source=self.source,
                reason="empty_abstract", http_status=resp.status_code,
            )
        return AbstractResult(
            ok=True, url=url, abstract=text, source=self.source,
            http_status=resp.status_code,
        )


NDSS_FETCHER = _NdssFetcher()

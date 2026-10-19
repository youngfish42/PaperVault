"""USENIX (``www.usenix.org``) abstract fetcher.

Verified against real pages on 2026-10-19:

* Legacy layout (pre-2020, e.g.
  ``https://www.usenix.org/conference/nsdi12/technical-sessions/presentation/zaharia``)
  and the modern Drupal layout (e.g.
  ``https://www.usenix.org/conference/nsdi24/presentation/zhang-zili-pipelining``)
  BOTH place the abstract inside::

      <div class="field field-name-field-paper-description ...">
        <div class="field-items"><div class="field-item">
          <p>... full abstract prose ...</p>   (legacy pages use <p class="p1">)

  so one selector covers both templates.

* A further fallback handles the oldest ``/legacy/events/...`` HTML
  pages where the abstract follows a literal "Abstract" heading as one
  or more ``<p>`` paragraphs (commonly ``<p class="p1">``).

Path guard: ``usenix.org`` also serves the front page, membership and
conference landing pages. Only ``/conference/...`` URLs that also
contain ``/presentation/`` (plus non-PDF paper pages under
``/legacy/events/``) are fetched; everything else short-circuits
before the network call.

Registry note: the dispatcher canonicalises hosts by stripping a
leading ``www.``, so the ``usenix.org`` key is the one that actually
matches; ``www.usenix.org`` is listed in ``allowed_hosts`` for
documentation parity with how the venue appears in the cache.
"""

from __future__ import annotations

from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .base import AbstractResult, Fetcher, MIN_ABSTRACT_CHARS
from ._http import clean_text, http_get, strip_abstract_label


def _looks_like_presentation(url: str) -> bool:
    """Return True iff the URL looks like a USENIX paper/presentation page."""
    path = (urlparse(url).path or "").lower()
    if "/conference/" in path and "/presentation/" in path:
        return True
    if "/legacy/events/" in path and not path.endswith(".pdf"):
        return True
    return False


def _extract_heading_walk(soup) -> str:
    """Fallback for the oldest layout: abstract prose in ``<p>`` siblings
    following an "Abstract" heading (headings or ``<strong>`` labels)."""
    for heading in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "strong"]):
        if not heading.get_text(strip=True).lower().startswith("abstract"):
            continue
        parts = []
        for sib in heading.find_next_siblings():
            if sib.name != "p":
                break
            parts.append(sib.get_text(" "))
        text = clean_text(" ".join(parts))
        if text:
            return text
    return ""


class _UsenixFetcher(Fetcher):
    allowed_hosts = ("www.usenix.org", "usenix.org")
    source = "usenix"

    def fetch(self, url: str) -> AbstractResult:
        if not _looks_like_presentation(url):
            return AbstractResult(
                ok=False, url=url, source=self.source,
                reason="no_abstract_available",
            )

        resp, err = http_get(url)
        if err is not None:
            return err
        soup = BeautifulSoup(resp.text, "html.parser")

        text = ""
        node = soup.find("div", class_="field-name-field-paper-description")
        if node is not None:
            strip_abstract_label(node)
            text = clean_text(node.get_text(" "))
        if not text:
            text = _extract_heading_walk(soup)

        if not text or len(text) < MIN_ABSTRACT_CHARS:
            return AbstractResult(
                ok=False, url=url, source=self.source,
                reason="empty_abstract", http_status=resp.status_code,
            )
        return AbstractResult(
            ok=True, url=url, abstract=text, source=self.source,
            http_status=resp.status_code,
        )


USENIX_FETCHER = _UsenixFetcher()

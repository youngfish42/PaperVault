"""JMLR (``jmlr.org``) abstract fetcher.

Verified against real pages on 2026-07-18 (e.g.
``https://www.jmlr.org/papers/v22/17-679.html``, ``/v25/23-1044.html``):

* The abstract sits inside::

      <h3>Abstract</h3>
      <p class="abstract">
        ... full abstract prose ...
      </p>

  There is no ``<meta name="citation_abstract">`` on JMLR pages, and the
  ``citation_abstract_html_url`` meta only points back to the paper URL
  itself. The ``<p class="abstract">`` node is stable across at least
  volumes 22-26 (JMLR 2021-2025).

* Some very old JMLR volumes (pre v10) render abstracts inside::

      <b>Abstract:</b><br>... prose ...

  which we detect as a defensive fallback so those legacy pages don't
  drop into ``empty_abstract``.

Both ``www.jmlr.org`` and ``jmlr.org`` are accepted; the ACL/MLR
convention of canonicalising to the ``www``-stripped host lives in the
dispatcher (:mod:`papervault.services.abstract_fetchers`), so we only
need to list the canonical form here.
"""

from __future__ import annotations

from bs4 import BeautifulSoup

from .base import AbstractResult, Fetcher, MIN_ABSTRACT_CHARS
from ._http import clean_text, http_get


class _JMLRFetcher(Fetcher):
    allowed_hosts = ("jmlr.org",)
    source = "jmlr"

    def fetch(self, url: str) -> AbstractResult:
        resp, err = http_get(url)
        if err is not None:
            return err
        soup = BeautifulSoup(resp.text, "html.parser")

        text = ""

        node = soup.find("p", class_="abstract")
        if node is not None:
            text = clean_text(node.get_text(" "))

        if not text:
            for heading in soup.find_all(["h2", "h3", "h4"]):
                if heading.get_text(strip=True).lower().startswith("abstract"):
                    parts = []
                    for sibling in heading.find_next_siblings():
                        name = getattr(sibling, "name", None)
                        if name in ("h1", "h2", "h3", "h4", "h5", "h6"):
                            break
                        piece = clean_text(sibling.get_text(" "))
                        if piece:
                            parts.append(piece)
                    text = " ".join(parts).strip()
                    if text:
                        break

        if not text:
            for b in soup.find_all("b"):
                label = b.get_text(strip=True).lower().rstrip(":")
                if label != "abstract":
                    continue
                parent = b.parent
                if parent is None:
                    continue
                b.extract()
                text = clean_text(parent.get_text(" "))
                if text:
                    break

        if not text or len(text) < MIN_ABSTRACT_CHARS:
            return AbstractResult(
                ok=False, url=url, source=self.source,
                reason="empty_abstract", http_status=resp.status_code,
            )
        return AbstractResult(
            ok=True, url=url, abstract=text, source=self.source,
            http_status=resp.status_code,
        )


JMLR_FETCHER = _JMLRFetcher()

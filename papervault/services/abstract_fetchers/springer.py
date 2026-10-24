"""SpringerLink (``link.springer.com``) abstract fetcher.

Verified against real pages on 2026-10-19 (e.g.
``https://link.springer.com/chapter/10.1007/978-3-540-40899-4_1``):

* Chapter / article pages render the abstract inside::

      <section aria-labelledby="Abs1" data-title="Abstract">
        <div class="c-article-section" id="Abs1-section">
          <h2 id="Abs1" class="c-article-section__title">Abstract</h2>
          <div class="c-article-section__content" id="Abs1-content">
            <p>... full abstract prose ...</p>

  The ``Abs1`` anchor is stable across Springer book chapters (LNCS /
  MICCAI) and journal articles; the ``<h2>`` label is stripped via
  ``strip_abstract_label``.

* ``<meta name="description">`` exists but is auto-truncated to ~260
  chars ending in ``...`` — it is only accepted as a fallback when it
  does NOT carry the truncation marker, otherwise a poisoned snippet
  would pass the ``MIN_ABSTRACT_CHARS`` gate (same failure mode the
  ISCA fetcher avoids).

Path guard: ``link.springer.com`` also serves journal landing pages,
search pages and TOCs. Only paths whose segments include ``chapter``,
``article`` or ``referenceworkentry`` are fetched; anything else
short-circuits *before* the network call so we neither burn HTTP quota
nor pollute the ``empty_abstract`` diagnostic bucket.
"""

from __future__ import annotations

from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .base import AbstractResult, Fetcher, MIN_ABSTRACT_CHARS
from ._http import clean_text, http_get, strip_abstract_label

_ARTICLE_SEGMENTS = ("chapter", "article", "referenceworkentry")

# link.springer.com sits behind F5 Distributed Cloud Bot Defense, which
# serves a 3 KB JavaScript challenge page to browser-like User-Agents
# (verified 2026-10-19: requests with the shared Chrome UA ALWAYS
# receives the challenge; any non-browser UA — ``curl/*``,
# ``python-requests/*`` or an honest self-identifying bot UA — receives
# the full 250 KB HTML). We therefore identify ourselves as a bot for
# this host instead of impersonating a browser.
_SPRINGER_HEADERS = {
    "User-Agent": "PaperVault-AbstractBackfill/1.0 (+https://github.com/youngfish42/PaperVault)"
}


def _looks_like_article(url: str) -> bool:
    """Return True iff the URL path looks like a Springer content page."""
    path = (urlparse(url).path or "").lower()
    segments = [seg for seg in path.split("/") if seg]
    return any(seg in _ARTICLE_SEGMENTS for seg in segments)


def _is_truncated(text: str) -> bool:
    """Springer truncates description metas with an ellipsis marker."""
    return text.endswith("...") or text.endswith("…")


class _SpringerFetcher(Fetcher):
    allowed_hosts = ("link.springer.com",)
    source = "springer"

    def fetch(self, url: str) -> AbstractResult:
        if not _looks_like_article(url):
            return AbstractResult(
                ok=False, url=url, source=self.source,
                reason="no_abstract_available",
            )

        resp, err = http_get(url, headers=_SPRINGER_HEADERS)
        if err is not None:
            return err
        soup = BeautifulSoup(resp.text, "html.parser")

        text = ""
        section = soup.find("section", attrs={"aria-labelledby": "Abs1"})
        if section is None:
            # Actual page anatomy: the Abs1 id lives on the inner
            # ``<h2 id="Abs1">`` title and on the wrapper
            # ``<div class="c-article-section" id="Abs1-section">``
            # (a bare ``section#Abs1`` never occurs on real pages).
            section = soup.find("div", id="Abs1-section")
        if section is not None:
            strip_abstract_label(section)
            text = clean_text(section.get_text(" "))

        if len(text) < MIN_ABSTRACT_CHARS:
            meta = soup.find("meta", attrs={"name": "description"})
            if meta is not None:
                candidate = clean_text(meta.get("content"))
                if candidate and not _is_truncated(candidate):
                    text = candidate

        if not text or len(text) < MIN_ABSTRACT_CHARS:
            return AbstractResult(
                ok=False, url=url, source=self.source,
                reason="empty_abstract", http_status=resp.status_code,
            )
        return AbstractResult(
            ok=True, url=url, abstract=text, source=self.source,
            http_status=resp.status_code,
        )


SPRINGER_FETCHER = _SpringerFetcher()

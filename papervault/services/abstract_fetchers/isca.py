"""ISCA Archive (``isca-archive.org``) abstract fetcher.

Verified against real pages on 2026-07-18 (e.g.
``https://www.isca-archive.org/interspeech_2023/gong23c_interspeech.html``):

* Modern Interspeech / ISCA archive pages render the abstract inside::

      <div id="abstract">
        <p>... full abstract prose ...</p>
      </div>

  This layout is stable across Interspeech 2019-2025 and the newer
  workshop archives.

* Older pages that pre-date the ``isca-archive.org`` rehost (still
  linked via ``isca-speech.org/archive/...``) either 404 or return a
  bare menu HTML with no ``<div id="abstract">``; those are handled
  by the DOI-based fallback chain.

We accept ``isca-archive.org`` as the canonical host. The dispatcher
already strips a leading ``www.``, so both bare and www-prefixed URLs
route to this fetcher.

Design note: we intentionally do NOT fall back to ``<meta
name="description">`` -- ISCA description metas are auto-truncated to
~150-200 chars and would silently pass the ``MIN_ABSTRACT_CHARS`` gate,
poisoning the cache with snippets while marking the record ``ok=True``.
When ``<div id="abstract">`` is missing we return ``empty_abstract`` so
the outer pipeline can degrade to the DOI-based fallback chain instead.
"""

from __future__ import annotations

from bs4 import BeautifulSoup

from .base import AbstractResult, Fetcher, MIN_ABSTRACT_CHARS
from ._http import clean_text, http_get, strip_abstract_label


class _IscaFetcher(Fetcher):
    allowed_hosts = ("isca-archive.org",)
    source = "isca"

    def fetch(self, url: str) -> AbstractResult:
        resp, err = http_get(url)
        if err is not None:
            return err
        soup = BeautifulSoup(resp.text, "html.parser")

        text = ""

        node = soup.find("div", id="abstract")
        if node is not None:
            # ISCA pages ship exactly one "Abstract" label inside the
            # container, so first-match semantics are enough.
            strip_abstract_label(node)
            text = clean_text(node.get_text(" "))

        if not text or len(text) < MIN_ABSTRACT_CHARS:
            return AbstractResult(
                ok=False, url=url, source=self.source,
                reason="empty_abstract", http_status=resp.status_code,
            )
        return AbstractResult(
            ok=True, url=url, abstract=text, source=self.source,
            http_status=resp.status_code,
        )


ISCA_FETCHER = _IscaFetcher()

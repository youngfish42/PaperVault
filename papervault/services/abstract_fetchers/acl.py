"""ACL Anthology (``aclanthology.org``) abstract fetcher.

Verified against real pages on 2026-07-12:

* Modern pages (2019+, e.g. ``2023.acl-long.1`` / ``P19-1001``) render::

      <div class="card-body acl-abstract">
        <h5 class="card-title">Abstract</h5>
        <span>… full abstract prose …</span>
      </div>

  The heading is ``<h5>`` -- **not** ``<strong>`` -- and the anthology's
  Hugo template does *not* emit any ``<meta name="citation_abstract">``
  on these pages (only ``citation_abstract_html_url`` exists).

* Legacy pages (pre-2019, e.g. ``P05-1001``) frequently have neither the
  ``acl-abstract`` div nor a ``citation_abstract`` meta tag -- the caller
  degrades to CrossRef/S2/arXiv/OpenAlex in that case.

We still probe both candidates (div + meta) and keep the longer one so
edge-case revamps of the template continue to work, but the heading
stripper now covers ``<strong>`` **and** ``<h1>-<h6>`` labels.
"""

from __future__ import annotations

from bs4 import BeautifulSoup

from .base import AbstractResult, Fetcher, MIN_ABSTRACT_CHARS
from ._http import clean_text, http_get


class _ACLFetcher(Fetcher):
    allowed_hosts = ("aclanthology.org",)
    source = "acl"

    def fetch(self, url: str) -> AbstractResult:
        resp, err = http_get(url)
        if err is not None:
            return err
        soup = BeautifulSoup(resp.text, "html.parser")

        candidates = []
        div = soup.find("div", class_="acl-abstract")
        if div is not None:
            # Real modern ACL pages label the block with
            # ``<h5 class="card-title">Abstract</h5>`` (2019+), while
            # older / fixture markup sometimes uses ``<strong>``. Strip
            # both so the returned text starts at the actual prose.
            #
            # ``startswith`` (rather than strict equality) keeps us in
            # lockstep with :mod:`.aaai` and :mod:`.mlr` and tolerates
            # future template variants such as ``Abstract:`` /
            # ``Abstract .``.
            for label in div.find_all(["strong", "h1", "h2", "h3", "h4", "h5", "h6"]):
                if label.get_text(strip=True).lower().startswith("abstract"):
                    label.extract()
                    break
            candidates.append(clean_text(div.get_text(" ")))

        meta = soup.find("meta", attrs={"name": "citation_abstract"})
        if meta is not None:
            candidates.append(clean_text(meta.get("content", "")))

        candidates = [c for c in candidates if c]
        if not candidates:
            return AbstractResult(
                ok=False, url=url, source=self.source,
                reason="empty_abstract", http_status=resp.status_code,
            )

        abstract = max(candidates, key=len)
        if len(abstract) < MIN_ABSTRACT_CHARS:
            return AbstractResult(
                ok=False, url=url, source=self.source,
                reason="empty_abstract", http_status=resp.status_code,
            )
        return AbstractResult(
            ok=True, url=url, abstract=abstract, source=self.source,
            http_status=resp.status_code,
        )


ACL_FETCHER = _ACLFetcher()

"""ACL Anthology (``aclanthology.org``) abstract fetcher.

Modern paper pages (2019+) expose the abstract in either:
  * ``<div class="acl-abstract">…</div>`` (semantic block), or
  * ``<meta name="citation_abstract" content="…">`` (highwire metadata),
whichever the Hugo template happens to render for that year.

We probe both and take the longer non-empty candidate. Older pages
(pre-2019 ACL / EMNLP / NAACL) frequently have neither node — the caller
degrades to CrossRef/S2/arXiv/OpenAlex in that case.
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
            # Skip a possible leading ``<strong>Abstract</strong>`` label
            for strong in div.find_all("strong"):
                if strong.get_text(strip=True).lower() == "abstract":
                    strong.extract()
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

"""AAAI OJS (``ojs.aaai.org``) abstract fetcher.

The OJS 3 theme AAAI switched to in 2020 renders abstracts inside a
``<section class="item abstract">`` element that also contains a
leading ``<h2>Abstract</h2>`` heading. We strip the heading and return
whatever prose is left.
"""

from __future__ import annotations

from bs4 import BeautifulSoup

from .base import AbstractResult, Fetcher, MIN_ABSTRACT_CHARS
from ._http import clean_text, http_get


class _AAAIFetcher(Fetcher):
    allowed_hosts = ("ojs.aaai.org",)
    source = "aaai"

    def fetch(self, url: str) -> AbstractResult:
        resp, err = http_get(url)
        if err is not None:
            return err
        soup = BeautifulSoup(resp.text, "html.parser")

        section = soup.find("section", class_=lambda c: c and "abstract" in c.split())
        if section is None:
            section = soup.find("div", class_=lambda c: c and "abstract" in c.split())
        if section is None:
            return AbstractResult(
                ok=False, url=url, source=self.source,
                reason="empty_abstract", http_status=resp.status_code,
            )

        for h in section.find_all(["h1", "h2", "h3", "h4"]):
            if h.get_text(strip=True).lower().startswith("abstract"):
                h.extract()

        text = clean_text(section.get_text(" "))
        if not text or len(text) < MIN_ABSTRACT_CHARS:
            return AbstractResult(
                ok=False, url=url, source=self.source,
                reason="empty_abstract", http_status=resp.status_code,
            )
        return AbstractResult(
            ok=True, url=url, abstract=text, source=self.source,
            http_status=resp.status_code,
        )


AAAI_FETCHER = _AAAIFetcher()

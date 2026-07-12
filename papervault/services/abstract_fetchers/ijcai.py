"""IJCAI (``ijcai.org/proceedings/...``) abstract fetcher.

The IJCAI proceedings pages carry the abstract inside a
``<div class="col-md-12">`` block (the same class as the surrounding
grid — historically consistent since 2017). The heuristic we use:
find the *first* ``<div class="col-md-12">`` whose text starts with
"Abstract" and return the remainder.
"""

from __future__ import annotations

from bs4 import BeautifulSoup

from .base import AbstractResult, Fetcher, MIN_ABSTRACT_CHARS
from ._http import clean_text, http_get


class _IJCAIFetcher(Fetcher):
    allowed_hosts = ("ijcai.org",)
    source = "ijcai"

    def fetch(self, url: str) -> AbstractResult:
        resp, err = http_get(url)
        if err is not None:
            return err
        soup = BeautifulSoup(resp.text, "html.parser")

        text = ""
        for div in soup.find_all("div", class_="col-md-12"):
            raw = clean_text(div.get_text(" "))
            if raw.lower().startswith("abstract"):
                # Drop the ``Abstract`` label but keep everything after it
                text = raw[len("Abstract"):].strip(" :.-")
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


IJCAI_FETCHER = _IJCAIFetcher()

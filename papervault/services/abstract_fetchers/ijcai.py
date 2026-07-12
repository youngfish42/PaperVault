"""IJCAI (``ijcai.org/proceedings/...``) abstract fetcher.

The IJCAI proceedings pages carry the abstract inside a
``<div class="col-md-12">`` block (the same class as the surrounding
grid — historically consistent since 2017).

Heuristic (review issue #8: previous version blindly matched any block
whose text *started* with "Abstract", which would misfire on a page
where an ``<h2>Abstract</h2>`` heading sits alone inside its own
``col-md-12``):
  1. First, look for a heading tag (``h1``/``h2``/``h3``) whose text
     equals "Abstract" and return the concatenated text of its
     following siblings (paragraphs).
  2. Fall back to the historical "col-md-12 startswith Abstract"
     match, but require the *remainder* (after stripping the label) to
     be at least ``MIN_ABSTRACT_CHARS`` chars — otherwise we're
     staring at a bare heading.
"""

from __future__ import annotations

from bs4 import BeautifulSoup

from .base import AbstractResult, Fetcher, MIN_ABSTRACT_CHARS
from ._http import clean_text, http_get


def _abstract_from_heading(soup: BeautifulSoup) -> str:
    for heading in soup.find_all(["h1", "h2", "h3"]):
        label = clean_text(heading.get_text(" "))
        if label.lower().strip(" :.-") != "abstract":
            continue
        parts = []
        for sibling in heading.find_next_siblings():
            piece = clean_text(sibling.get_text(" "))
            if piece:
                parts.append(piece)
        text = " ".join(parts).strip()
        if text:
            return text
    return ""


class _IJCAIFetcher(Fetcher):
    allowed_hosts = ("ijcai.org",)
    source = "ijcai"

    def fetch(self, url: str) -> AbstractResult:
        resp, err = http_get(url)
        if err is not None:
            return err
        soup = BeautifulSoup(resp.text, "html.parser")

        text = _abstract_from_heading(soup)

        if not text:
            for div in soup.find_all("div", class_="col-md-12"):
                raw = clean_text(div.get_text(" "))
                if not raw.lower().startswith("abstract"):
                    continue
                remainder = raw[len("Abstract"):].strip(" :.-")
                # Guard against ``<div><h2>Abstract</h2></div>`` where
                # the div's text is just the label with no body.
                if len(remainder) >= MIN_ABSTRACT_CHARS:
                    text = remainder
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

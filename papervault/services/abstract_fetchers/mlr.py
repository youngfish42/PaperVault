"""MLR / PMLR (``proceedings.mlr.press``) abstract fetcher.

PMLR HTML has been remarkably stable: the abstract sits inside either
  * ``<div id="abstract">…</div>`` (current templates), or
  * ``<p><b>Abstract</b> …</p>`` (legacy templates from ICML pre-2016).

We accept whichever exists.
"""

from __future__ import annotations

from bs4 import BeautifulSoup

from .base import AbstractResult, Fetcher, MIN_ABSTRACT_CHARS
from ._http import clean_text, http_get


class _MLRFetcher(Fetcher):
    allowed_hosts = ("proceedings.mlr.press",)
    source = "mlr"

    def fetch(self, url: str) -> AbstractResult:
        resp, err = http_get(url)
        if err is not None:
            return err
        soup = BeautifulSoup(resp.text, "html.parser")

        text = ""
        node = soup.find("div", id="abstract")
        if node is not None:
            text = clean_text(node.get_text(" "))
        else:
            for p in soup.find_all("p"):
                b = p.find("b")
                if b is not None and b.get_text(strip=True).lower().startswith("abstract"):
                    # Drop the leading ``<b>Abstract</b>`` label from the extracted text
                    b.extract()
                    text = clean_text(p.get_text(" "))
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


MLR_FETCHER = _MLRFetcher()

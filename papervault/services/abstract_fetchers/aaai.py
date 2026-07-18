"""AAAI OJS (``ojs.aaai.org`` + legacy ``aaai.org``) abstract fetcher.

The OJS 3 theme AAAI switched to in 2020 renders abstracts inside a
``<section class="item abstract">`` element that also contains a
leading ``<h2>Abstract</h2>`` heading. We strip the heading and return
whatever prose is left.

Legacy URLs under ``aaai.org/ojs/index.php/AAAI/article/view/<id>``
(collected for AAAI 2019 and earlier) 301-redirect to the corresponding
``ojs.aaai.org/index.php/AAAI/article/view/<id>`` page — verified live
on 2026-07-18 against ``view/4093``. Because ``requests.Session``
follows 3xx redirects by default, listing ``aaai.org`` in
``allowed_hosts`` is enough to route those legacy URLs through the same
selector without any extra HTTP plumbing.

Path guard: ``aaai.org`` also serves conference landing pages, news,
and other non-article content that share the host. To avoid burning
one HTTP request per non-article URL (and polluting the ``empty_abstract``
reason bucket in the diagnostic dashboard), we short-circuit before the
network call when the path does not look like an OJS article route.
The ``ojs.aaai.org`` host is trusted wholesale because it exclusively
hosts the OJS instance.
"""

from __future__ import annotations

from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .base import AbstractResult, Fetcher, MIN_ABSTRACT_CHARS
from ._http import clean_text, http_get


def _looks_like_ojs_article(url: str) -> bool:
    """Return True iff ``url`` is (or 301-redirects to) an OJS article page.

    Both the modern ``ojs.aaai.org/index.php/AAAI/article/view/<id>`` and
    the legacy ``aaai.org/ojs/index.php/AAAI/article/view/<id>`` shapes
    contain the ``/article/view/`` suffix, so a single substring check
    is enough. We intentionally do NOT match on ``/ojs/`` alone because
    that also covers OJS admin / feed pages which have no abstract.
    """
    path = (urlparse(url).path or "").lower()
    return "/article/view/" in path


class _AAAIFetcher(Fetcher):
    allowed_hosts = ("ojs.aaai.org", "aaai.org")
    source = "aaai"

    def fetch(self, url: str) -> AbstractResult:
        host = (urlparse(url).netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]
        if host == "aaai.org" and not _looks_like_ojs_article(url):
            return AbstractResult(
                ok=False, url=url, source=self.source,
                reason="no_abstract_available",
            )

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

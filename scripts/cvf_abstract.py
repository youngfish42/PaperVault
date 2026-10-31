"""Side-effect-free helper for scraping CVF Open Access abstracts.

This module intentionally contains **only** the three pure-ish helpers that
both the collector (`collector.search_from_thecvf`) and the CLI backfill
script (`scripts.fetch_cvf_abstracts`) need:

    - is_cvf_paper_url(url)     -- URL sniff test
    - _clean_abstract(text)     -- whitespace / hyphenation cleanup
    - fetch_cvf_abstract(url)   -- primary + fallback scrape

Design constraints:

    * NO ``sys.path.insert``            -- do not mutate global import path.
    * NO ``sys.stdout.reconfigure``     -- do not mutate global stdio.
    * NO ``tqdm`` / ``argparse`` imports -- keep runtime dependencies light.
    * NO module-level network I/O       -- import must be cheap and safe.

The heavier CLI-oriented module :mod:`scripts.fetch_cvf_abstracts` re-exports
these helpers so that existing ``from scripts.fetch_cvf_abstracts import
fetch_cvf_abstract`` call sites keep working, but the collector should import
from *this* module instead to avoid pulling in CLI side effects during a
production paper-collection run.
"""

from __future__ import annotations

import re
import threading
from typing import Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from collector import HEADERS as COLLECTOR_HEADERS
from collector import search_abs_from_thecvf

__all__ = ["is_cvf_paper_url", "fetch_cvf_abstract", "_clean_abstract"]


_CVF_HOST = "openaccess.thecvf.com"

_thread_local = threading.local()


def _get_session() -> requests.Session:
    if not hasattr(_thread_local, "session"):
        s = requests.Session()
        s.trust_env = False
        _thread_local.session = s
    return _thread_local.session


def _clean_abstract(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", "", text)
    text = text.strip()
    text = re.sub(r"-\r?\n\s*", "", text)
    text = re.sub(r"\r?\n\s*([a-z0-9])", r" \1", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def is_cvf_paper_url(url: str) -> bool:
    if not url:
        return False
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    host = (parsed.netloc or "").lower()
    if host != _CVF_HOST and not host.endswith("." + _CVF_HOST):
        return False
    path = parsed.path or ""
    return "/html/" in path and path.endswith(".html")


def fetch_cvf_abstract(url: str, *, timeout: int = 20) -> str:
    """Fetch and clean the abstract of a single CVF Open Access paper page.

    Primary strategy: reuse ``collector.search_abs_from_thecvf`` which parses
    the canonical ``<div id="abstract">`` element.

    Fallback: perform a second GET and look for ``<meta name="description">``
    or ``<meta property="og:description">``. This handles the rare pages
    whose abstract markup has drifted.

    On network errors / non-200 / structural changes the primary strategy is
    swallowed (returns ``None`` internally) and the fallback GET is attempted.
    If the fallback itself raises, the exception propagates so the caller can
    log ``tried``/``failed`` accounting.
    """
    primary: Optional[str] = None
    try:
        primary = search_abs_from_thecvf(url)
    except Exception:
        primary = None

    if primary:
        cleaned = _clean_abstract(primary)
        if cleaned:
            return cleaned

    session = _get_session()
    resp = session.get(url, headers=COLLECTOR_HEADERS, timeout=timeout)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    node = soup.find(id="abstract")
    if node is not None:
        text = node.get_text(" ", strip=True)
        cleaned = _clean_abstract(text)
        if cleaned:
            return cleaned

    for selector in (
        {"name": "meta", "attrs": {"name": "description"}},
        {"name": "meta", "attrs": {"property": "og:description"}},
    ):
        tag = soup.find(**selector)
        if tag is not None:
            content = tag.get("content", "") or ""
            cleaned = _clean_abstract(content)
            if cleaned and len(cleaned) >= 40:
                return cleaned

    return ""

"""Shared HTTP + error-normalisation helpers for abstract fetchers.

Every domain fetcher only cares about *its* selectors — the mechanical
"fetch a page and turn requests.exceptions into REASON_ENUM values"
lives here so the domain modules stay ~40 lines each.
"""

from __future__ import annotations

from typing import Optional, Tuple

import requests

from collector.http import HEADERS, SESSION

from .base import AbstractResult


def http_get(url: str, timeout: float = 20.0) -> Tuple[Optional[requests.Response], Optional[AbstractResult]]:
    """Perform a GET and translate any exception into a fetcher-shaped ``AbstractResult``.

    Returns ``(response, None)`` on 2xx, otherwise ``(None, AbstractResult(ok=False, ...))``.
    The 4xx/5xx bodies are still surfaced via :attr:`AbstractResult.http_status`
    so downstream code (progress record + analyse script) can distinguish
    ``rate_limited`` (429) from generic ``network``.
    """
    try:
        resp = SESSION.get(url, headers=HEADERS, timeout=timeout)
    except requests.exceptions.Timeout:
        return None, AbstractResult(ok=False, url=url, reason="timeout")
    except requests.exceptions.RequestException:
        return None, AbstractResult(ok=False, url=url, reason="network")

    status = resp.status_code
    if 200 <= status < 300:
        return resp, None
    if 300 <= status < 400:
        # Review issue #7: SESSION should already follow redirects, so
        # a bare 3xx surfacing here means the server pointed us at a
        # non-fetchable resource (an interstitial, a login wall, etc.).
        # Treat it as "no abstract available" instead of hiding it in
        # the generic ``network`` bucket -- otherwise the analyse
        # dashboard cannot distinguish a genuine outage from a soft
        # deny.
        return None, AbstractResult(
            ok=False, url=url, reason="no_abstract_available", http_status=status
        )
    if status == 429:
        return None, AbstractResult(ok=False, url=url, reason="rate_limited", http_status=status)
    if status in (500, 502, 503, 504):
        return None, AbstractResult(ok=False, url=url, reason="network", http_status=status)
    if status == 404:
        return None, AbstractResult(ok=False, url=url, reason="doi_not_found", http_status=status)
    return None, AbstractResult(ok=False, url=url, reason="network", http_status=status)


def clean_text(raw: Optional[str]) -> str:
    """Collapse whitespace + strip. Empty/None → ""."""
    if not raw:
        return ""
    return " ".join(raw.split()).strip()


_DEFAULT_LABEL_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6", "strong")


def strip_abstract_label(
    node,
    *,
    tags=_DEFAULT_LABEL_TAGS,
    all_matches: bool = False,
) -> None:
    """Remove leading "Abstract"-style heading elements from ``node`` in place.

    Both the ISCA and AAAI fetchers wrap the abstract body in a container
    whose first child is a heading whose text starts with the literal
    word ``Abstract`` (case-insensitive). Extracting the surrounding text
    with ``get_text`` would otherwise leak that label into the returned
    prose. Sharing the traversal here keeps the two fetchers in lockstep
    and matches the same case-insensitive ``startswith("abstract")`` rule.

    Parameters
    ----------
    node:
        A BeautifulSoup element. Passing ``None`` is a no-op.
    tags:
        Which tag names count as label candidates. Defaults to the six
        HTML headings plus ``<strong>`` (ISCA's older markup used the
        latter).
    all_matches:
        If ``False`` (the ISCA convention) stop after the first heading
        that matches. If ``True`` (the AAAI convention) strip every
        matching heading in the container — some AAAI OJS templates
        emit both an ``<h2>Abstract</h2>`` and an in-body ``<strong>``
        repeat.
    """
    if node is None:
        return
    for candidate in list(node.find_all(list(tags))):
        if candidate.get_text(strip=True).lower().startswith("abstract"):
            candidate.extract()
            if not all_matches:
                return

"""Domain-specific abstract fetchers + dispatcher.

Task 5 of the abstract-backfill-repair spec introduces a *first-line*
fetcher layer that scrapes the conference site directly (e.g.
``aclanthology.org``, ``proceedings.mlr.press``) instead of relying on
the DOI-based fallback chain (CrossRef / S2 / arXiv / OpenAlex). The
main pipeline in :mod:`scripts.fetch_abstracts` calls :func:`dispatch`
first; on ``ok=False`` it degrades to the legacy multi-source chain.

Design contract
---------------
* Every domain fetcher lives in its own module and exports a
  :class:`Fetcher` instance keyed by an ``allowed_hosts`` tuple.
* :data:`FETCHER_REGISTRY` maps a canonical host (already lower-cased
  and stripped of a leading ``www.``) to the fetcher instance.
* :func:`dispatch` normalises the URL host, looks up a fetcher and
  returns an :class:`AbstractResult`. When no domain matches, it returns
  ``AbstractResult(ok=False, reason="no_abstract_available", ...)``
  without performing any HTTP call.

The PDF-only sites (``vldb`` / ``ceur``) intentionally short-circuit to
``ok=False, reason="no_abstract_available"``. PDF extraction is
explicitly out of scope for this iteration — see
[spec.md](file:///d:/git/youngfish/PaperVault/.trae/specs/abstract-backfill-repair/spec.md#L21).
"""

from __future__ import annotations

from urllib.parse import urlparse

from .base import AbstractResult, Fetcher
from .acl import ACL_FETCHER
from .mlr import MLR_FETCHER
from .aaai import AAAI_FETCHER
from .ijcai import IJCAI_FETCHER
from .vldb import VLDB_FETCHER
from .ceur import CEUR_FETCHER

__all__ = [
    "AbstractResult",
    "Fetcher",
    "FETCHER_REGISTRY",
    "dispatch",
]


def _register(*fetchers: Fetcher) -> dict:
    reg: dict = {}
    for f in fetchers:
        for host in f.allowed_hosts:
            reg[host] = f
    return reg


FETCHER_REGISTRY: dict = _register(
    ACL_FETCHER,
    MLR_FETCHER,
    AAAI_FETCHER,
    IJCAI_FETCHER,
    VLDB_FETCHER,
    CEUR_FETCHER,
)


def _canonical_host(url: str) -> str:
    if not url or not isinstance(url, str):
        return ""
    host = (urlparse(url).netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def dispatch(url: str) -> AbstractResult:
    """Route ``url`` to the appropriate domain fetcher.

    Returns ``AbstractResult(ok=False, reason="no_abstract_available")``
    when no fetcher claims the URL. This means the caller can uniformly
    treat "no domain matched" and "domain matched but page yields no
    abstract" the same way (both degrade to the legacy DOI/title chain
    in ``fetch_abstract_for_paper``).
    """
    if not url:
        return AbstractResult(ok=False, url=url, reason="no_abstract_available")
    host = _canonical_host(url)
    fetcher = FETCHER_REGISTRY.get(host)
    if fetcher is None:
        return AbstractResult(ok=False, url=url, reason="no_abstract_available")
    return fetcher.fetch(url)

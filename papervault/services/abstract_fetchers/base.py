"""Common base types for the abstract-fetcher family.

Kept intentionally tiny so individual fetchers stay <60 lines each.
The Fetcher contract is:

* ``allowed_hosts`` — a tuple of canonical, lower-cased hostnames
  (no leading ``www.``) claimed by this fetcher.
* ``source`` — a short identifier written into :attr:`AbstractResult.source`
  and later into ``progress[url]["source"]`` when the fetcher succeeds.
* ``fetch(url)`` — perform (mocked in tests) HTTP + parsing and return
  a fully-populated :class:`AbstractResult`.

Every fetcher **must** be defensive: network errors, missing DOM nodes,
and empty extracted strings all degrade to ``ok=False`` with a
:data:`REASON_ENUM`-compatible ``reason``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


# Minimum char count for a claim of a "real" abstract. Anything shorter
# is treated as scraping noise (menu blurbs, tag lists, ...).
MIN_ABSTRACT_CHARS = 60


@dataclass
class AbstractResult:
    """Return value of every :meth:`Fetcher.fetch` call and of :func:`dispatch`."""

    ok: bool
    url: str = ""
    abstract: Optional[str] = None
    source: str = ""
    reason: str = ""
    http_status: Optional[int] = None

    def __bool__(self) -> bool:  # convenience: ``if dispatch(url): ...``
        return bool(self.ok)


class Fetcher:
    """Base class for domain-specific abstract fetchers.

    Subclasses (or single instances used as attribute containers) declare
    ``allowed_hosts`` + ``source`` + implement :meth:`fetch`. This class
    itself is not abstract in the ``abc.ABC`` sense — we prefer plain
    duck typing so individual fetchers can be one-liners.
    """

    allowed_hosts: Tuple[str, ...] = ()
    source: str = ""

    def fetch(self, url: str) -> AbstractResult:  # pragma: no cover - overridden
        raise NotImplementedError

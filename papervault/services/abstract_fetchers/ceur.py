"""CEUR-WS (``ceur-ws.org``) abstract fetcher — PDF-only site.

Same story as :mod:`.vldb`: CEUR-WS index pages only link to a per-paper
PDF, no HTML abstract. We short-circuit to
``ok=False, reason="no_abstract_available"`` and stay clear of PDF /
OCR dependencies (spec Non-Goals L21).
"""

from __future__ import annotations

from .base import AbstractResult, Fetcher


class _CEURFetcher(Fetcher):
    allowed_hosts = ("ceur-ws.org",)
    source = "ceur"

    def fetch(self, url: str) -> AbstractResult:
        return AbstractResult(
            ok=False, url=url, source=self.source, reason="no_abstract_available",
        )


CEUR_FETCHER = _CEURFetcher()

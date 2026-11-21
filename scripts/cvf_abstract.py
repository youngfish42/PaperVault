"""Shared CVF abstract-fetching helpers with no CLI side effects."""

from __future__ import annotations

import threading

import requests

from collector.http import _create_session
from collector.sources.thecvf import (
    clean_cvf_abstract,
    is_cvf_paper_url,
    search_abs_from_thecvf,
)

__all__ = ["is_cvf_paper_url", "fetch_cvf_abstract", "_clean_abstract"]

_thread_local = threading.local()


def _get_session() -> requests.Session:
    if not hasattr(_thread_local, "session"):
        session = _create_session()
        session.trust_env = False
        _thread_local.session = session
    return _thread_local.session


def _clean_abstract(text: str) -> str:
    return clean_cvf_abstract(text)


def fetch_cvf_abstract(url: str, *, timeout: int = 20) -> str:
    return search_abs_from_thecvf(url, timeout=timeout, session=_get_session())

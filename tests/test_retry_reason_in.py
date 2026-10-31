"""Task 7: ``--reason-in`` whitelist filter for the retry-failed path.

Verifies that :func:`scripts.fetch_abstracts._process_targets`:

* When ``reason_in`` is set and ``retry_failed=True``, only papers whose
  previous failure ``reason`` is in the whitelist (or that predate v4
  and lack a ``reason`` field entirely) get retried.
* When ``reason_in`` is ``None`` the retry-failed behaviour is exactly
  what it was before Task 7 (regression guard).
"""

from __future__ import annotations

from unittest.mock import patch

from scripts import fetch_abstracts as fa


def _paper(url: str, name: str = "T") -> dict:
    return {"paper_url": url, "paper_name": name, "conf": "TEST"}


def test_reason_in_filters_retry_targets():
    """Only ``rate_limited`` records should reach the fetcher when whitelisted."""
    papers = [
        _paper("https://example.org/a"),  # rate_limited -> should retry
        _paper("https://example.org/b"),  # no_doi       -> should NOT retry
        _paper("https://example.org/c"),  # timeout      -> should retry
        _paper("https://example.org/d"),  # legacy v3 (no reason) -> should retry
    ]
    progress = {
        "https://example.org/a": {"status": "failed", "attempts": 1, "reason": "rate_limited"},
        "https://example.org/b": {"status": "failed", "attempts": 1, "reason": "no_doi"},
        "https://example.org/c": {"status": "failed", "attempts": 2, "reason": "timeout"},
        "https://example.org/d": {"status": "failed", "attempts": 1},  # legacy
    }

    fetched_urls = []

    def _spy(paper, last_time, **_kw):
        fetched_urls.append(paper["paper_url"])
        return None, last_time, "", {"reason": "network", "last_source": "arxiv"}

    with patch.object(fa, "fetch_abstract_for_paper", side_effect=_spy), \
         patch.object(fa, "save_progress"):
        fa._process_targets(
            targets=papers,
            all_papers=papers,
            chunk_size=100,
            retry_failed=True,
            retry_partial=False,
            query_doi_by_title=False,
            max_failed_attempts=5,
            progress=progress,
            reason_in={"rate_limited", "timeout", "network", "empty_abstract"},
        )
    # rate_limited (a) + timeout (c) + legacy without reason (d).
    # b (no_doi) is filtered out.
    assert set(fetched_urls) == {
        "https://example.org/a",
        "https://example.org/c",
        "https://example.org/d",
    }


def test_reason_in_none_matches_prior_behaviour():
    """When ``reason_in`` is None every failed<attempts URL is retried (regression guard)."""
    papers = [
        _paper("https://example.org/a"),
        _paper("https://example.org/b"),
    ]
    progress = {
        "https://example.org/a": {"status": "failed", "attempts": 1, "reason": "no_doi"},
        "https://example.org/b": {"status": "failed", "attempts": 1, "reason": "rate_limited"},
    }
    fetched_urls = []

    def _spy(paper, last_time, **_kw):
        fetched_urls.append(paper["paper_url"])
        return None, last_time, "", {"reason": "network", "last_source": "arxiv"}

    with patch.object(fa, "fetch_abstract_for_paper", side_effect=_spy), \
         patch.object(fa, "save_progress"):
        fa._process_targets(
            targets=papers,
            all_papers=papers,
            chunk_size=100,
            retry_failed=True,
            retry_partial=False,
            query_doi_by_title=False,
            max_failed_attempts=5,
            progress=progress,
            reason_in=None,
        )
    assert set(fetched_urls) == {"https://example.org/a", "https://example.org/b"}


def test_reason_in_normalises_input_tokens():
    """Free-form reason strings get funneled through :func:`normalize_reason`."""
    papers = [_paper("https://example.org/a")]
    progress = {
        "https://example.org/a": {"status": "failed", "attempts": 1, "reason": "rate limit hit"},
    }
    fetched_urls = []

    def _spy(paper, last_time, **_kw):
        fetched_urls.append(paper["paper_url"])
        return None, last_time, "", {"reason": "network", "last_source": ""}

    with patch.object(fa, "fetch_abstract_for_paper", side_effect=_spy), \
         patch.object(fa, "save_progress"):
        fa._process_targets(
            targets=papers,
            all_papers=papers,
            chunk_size=100,
            retry_failed=True,
            retry_partial=False,
            query_doi_by_title=False,
            max_failed_attempts=5,
            progress=progress,
            # Contains a raw phrase that :func:`normalize_reason` maps
            # onto ``rate_limited``. The stored ``reason`` string
            # "rate limit hit" itself will also normalise the same way.
            reason_in={"rate limit hit"},
        )
    assert fetched_urls == ["https://example.org/a"]

"""Unit tests for the in-batch / cache-merge dedupe added by PR A.

These cover the three duplication paths that the audit on ``cache.jsonl.gz``
flagged as common sources of repeated rows:

* OpenReview returning the same forum_id across pagination boundaries
  (or across the v1->v2 fallback inside ``search_from_iclr_openreview``).
* ACL multi-volume parsing where the same paper URL surfaces from two
  ``_parse_acl_volume`` invocations within one collect run.
* ``_merge_with_cache`` folding cache rows into a freshly recollected
  conf without ever dropping a non-empty abstract.

All tests are fully offline: ``SESSION.get`` is monkey-patched and
``_parse_acl_volume`` is exercised against an in-memory HTML fixture, so
no network access is required.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

import collector


# ---------------------------------------------------------------------------
# OpenReview pagination / v1->v2 fallback dedupe
# ---------------------------------------------------------------------------


class _FakeResponse:
    """Minimal stand-in for ``requests.Response`` that only needs ``.json()``."""

    def __init__(self, payload: Dict[str, Any]):
        self._payload = payload

    def json(self) -> Dict[str, Any]:
        return self._payload


def _make_note(forum_id: str, title: str, abstract: str, venue: str = "ICLR 2024 Poster") -> Dict[str, Any]:
    return {
        "id": forum_id,
        "content": {
            "title": title,
            "authors": ["Alice"],
            "abstract": abstract,
            "venue": venue,
        },
    }


def test_openreview_dedupes_repeated_forum_id(monkeypatch: pytest.MonkeyPatch):
    """A forum_id that already exists in ``res[name]`` (e.g. from a previous
    OpenReview URL in the same collect run, or from the v1->v2 fallback path)
    must not be appended a second time. The longer abstract from the second
    occurrence must win."""

    page = {
        "notes": [
            # Duplicate of an entry already in res[name], now with a real
            # abstract — should merge into the prior record, not append.
            _make_note("aaa", "Paper A", "Long abstract for A"),
            _make_note("bbb", "Paper B", "Short B"),
        ]
    }

    pages = iter([page, {"notes": []}])

    def fake_get(url, headers=None, timeout=None):  # noqa: ARG001
        try:
            return _FakeResponse(next(pages))
        except StopIteration:
            return _FakeResponse({"notes": []})

    monkeypatch.setattr(collector.SESSION, "get", fake_get)

    # Pre-seed with an existing record for forum_id 'aaa' that has an empty
    # abstract — emulates the v1 endpoint having appended a placeholder before
    # the v2 fallback re-serves the same paper with a real abstract.
    res: Dict[str, List[dict]] = {
        "ICLR 2024": [
            {
                "paper_name": "Paper A",
                "paper_url": "https://openreview.net/pdf?id=aaa",
                "paper_authors": ["Alice"],
                "paper_abstract": "",
                "paper_code": "#",
            }
        ]
    }
    collector.search_from_iclr_openreview(
        "https://api.openreview.net/notes?content.venue=ICLR%202024%20Poster",
        "ICLR 2024",
        res,
    )

    papers = res["ICLR 2024"]
    forum_ids = [p["paper_url"].rsplit("=", 1)[-1] for p in papers]
    assert forum_ids.count("aaa") == 1, "duplicate forum_id must collapse"
    assert sorted(forum_ids) == ["aaa", "bbb"]

    # The merged record should carry the *longer* abstract that arrived second.
    paper_a = next(p for p in papers if p["paper_url"].endswith("=aaa"))
    assert paper_a["paper_abstract"] == "Long abstract for A"


def test_openreview_idempotent_across_reruns(monkeypatch: pytest.MonkeyPatch):
    """Calling search_from_iclr_openreview twice with the same data must not
    double the result list — the second pass should merge into existing
    records keyed by forum_id."""

    base_page = {
        "notes": [
            _make_note("aaa", "Paper A", "abs A"),
        ]
    }

    def fake_get_factory():
        pages = iter([base_page, {"notes": []}])

        def _fake_get(url, headers=None, timeout=None):  # noqa: ARG001
            try:
                return _FakeResponse(next(pages))
            except StopIteration:
                return _FakeResponse({"notes": []})

        return _fake_get

    monkeypatch.setattr(collector.SESSION, "get", fake_get_factory())

    res: Dict[str, List[dict]] = {}
    collector.search_from_iclr_openreview("https://api.openreview.net/notes?x=1", "ICLR 2024", res)
    first_count = len(res["ICLR 2024"])

    # Second pass with a fresh fake iterator: same data again.
    monkeypatch.setattr(collector.SESSION, "get", fake_get_factory())
    collector.search_from_iclr_openreview("https://api.openreview.net/notes?x=1", "ICLR 2024", res)

    assert len(res["ICLR 2024"]) == first_count == 1


# ---------------------------------------------------------------------------
# ACL volume dedupe
# ---------------------------------------------------------------------------


_ACL_VOLUME_HTML = """
<html><body>
<p>
  <strong><a href="/2023.acl-long.1/">Paper One</a></strong>
  <a href="/people/a/alice/">Alice</a>
</p>
<div id="abstract-2023--acl-long--1">Long-form abstract one.</div>

<p>
  <strong><a href="/2023.acl-long.2/">Paper Two</a></strong>
  <a href="/people/b/bob/">Bob</a>
</p>
<div id="abstract-2023--acl-long--2">Long-form abstract two.</div>
</body></html>
"""


def _patch_acl_get(monkeypatch: pytest.MonkeyPatch, html: str) -> None:
    monkeypatch.setattr(
        collector.SESSION,
        "get",
        lambda url, headers=None: SimpleNamespace(text=html),
    )


def test_parse_acl_volume_dedupes_repeated_invocations(monkeypatch: pytest.MonkeyPatch):
    """Calling _parse_acl_volume twice for the same volume URL must not
    yield duplicate paper rows; merge logic must still update abstracts."""

    _patch_acl_get(monkeypatch, _ACL_VOLUME_HTML)

    res: Dict[str, List[dict]] = {}
    collector._parse_acl_volume(
        "https://aclanthology.org/volumes/2023.acl-long/", "^/2023.acl*", "ACL 2023", res
    )
    first = len(res["ACL 2023"])
    assert first == 2

    collector._parse_acl_volume(
        "https://aclanthology.org/volumes/2023.acl-long/", "^/2023.acl*", "ACL 2023", res
    )
    assert len(res["ACL 2023"]) == first, "second invocation must not add duplicates"


# ---------------------------------------------------------------------------
# search_from_acl: volume-entry vs events-entry routing
# ---------------------------------------------------------------------------


_ACL_FINDINGS_VOLUME_HTML = """
<html><body>
<p>
  <strong><a href="/2026.findings-acl.1/">Findings Paper One</a></strong>
  <a href="/people/a/alice/">Alice</a>
</p>
<div id="abstract-2026--findings-acl--1">Findings abstract one.</div>

<p>
  <strong><a href="/2026.findings-acl.2/">Findings Paper Two</a></strong>
  <a href="/people/b/bob/">Bob</a>
</p>
<div id="abstract-2026--findings-acl--2">Findings abstract two.</div>

<a href="/volumes/2026.findings-acl.bib">BibTeX</a>
<a href="/volumes/2026.findings-acl.xml">XML</a>
<a href="/volumes/2026.findings-acl.enw">Endnote</a>
</body></html>
"""


def test_search_from_acl_handles_volume_entry(monkeypatch: pytest.MonkeyPatch):
    """Regression: when the conf entry URL is already a volume page (as ACL
    findings entries are configured), ``search_from_acl`` must parse the page
    itself instead of enumerating the ``.bib`` / ``.enw`` / ``.xml`` metadata
    links it finds inside. Previously that mis-routing produced
    ``empty result for tag='^YYYY.findings*'``."""

    _patch_acl_get(monkeypatch, _ACL_FINDINGS_VOLUME_HTML)

    res: Dict[str, List[dict]] = {}
    collector.search_from_acl(
        "https://aclanthology.org/volumes/2026.findings-acl/",
        "^2026.findings*",
        "ACL2026",
        res,
    )

    assert "ACL2026" in res
    urls = sorted(p["paper_url"] for p in res["ACL2026"])
    assert urls == [
        "https://aclanthology.org/2026.findings-acl.1/",
        "https://aclanthology.org/2026.findings-acl.2/",
    ]


def test_is_acl_volume_entry_predicate():
    """Whitebox: the volume-entry classifier must accept /volumes/XXX/ but
    reject the events root and the metadata download URLs that Anthology
    exposes alongside a volume page."""

    assert collector._is_acl_volume_entry(
        "https://aclanthology.org/volumes/2026.findings-acl/"
    )
    assert collector._is_acl_volume_entry(
        "https://aclanthology.org/volumes/2023.acl-long/"
    )
    assert not collector._is_acl_volume_entry(
        "https://aclanthology.org/events/acl-2026/"
    )
    assert not collector._is_acl_volume_entry(
        "https://aclanthology.org/volumes/2026.findings-acl.bib"
    )
    assert not collector._is_acl_volume_entry(
        "https://aclanthology.org/volumes/2026.findings-acl.xml"
    )
    # Trailing-slash policy: the predicate must require the path to end with
    # "/"; hrefs without it (e.g. some Anthology templates strip it) should
    # not silently be accepted as volume entries.
    assert not collector._is_acl_volume_entry(
        "https://aclanthology.org/volumes/2026.findings-acl"
    )
    # Query strings / fragments must not affect the routing decision: they
    # are peeled off by urlparse and the .path still ends with "/".
    assert collector._is_acl_volume_entry(
        "https://aclanthology.org/volumes/2026.findings-acl/?utm=foo"
    )
    assert collector._is_acl_volume_entry(
        "https://aclanthology.org/volumes/2026.findings-acl/#top"
    )
    # Host whitelist: reject any non-Anthology domain even if the path
    # happens to match the volume shape.
    assert not collector._is_acl_volume_entry(
        "https://example.com/volumes/2026.findings-acl/"
    )
    # www subdomain is a valid Anthology alias and must be accepted.
    assert collector._is_acl_volume_entry(
        "https://www.aclanthology.org/volumes/2026.findings-acl/"
    )


# ---------------------------------------------------------------------------
# _merge_with_cache: URL dedupe + abstract merge across all confs
# ---------------------------------------------------------------------------


def test_merge_with_cache_preserves_cache_abstract_on_empty_recollect():
    """The freshly recollected record has an empty abstract; the cached one
    has the real abstract. The merged result must keep the cache abstract."""

    new_res = {
        "ICLR 2024": [
            {
                "paper_name": "X",
                "paper_url": "https://openreview.net/pdf?id=xxx",
                "paper_authors": ["Alice"],
                "paper_abstract": "",
                "paper_code": "#",
            }
        ]
    }
    cache_res = {
        "ICLR 2024": [
            {
                "paper_name": "X",
                "paper_url": "https://openreview.net/pdf?id=xxx",
                "paper_authors": ["Alice"],
                "paper_abstract": "Cached abstract.",
                "paper_code": "https://github.com/foo/bar",
            }
        ]
    }

    merged = collector._merge_with_cache(new_res, cache_res, set(), set())
    papers = merged["ICLR 2024"]
    assert len(papers) == 1
    assert papers[0]["paper_abstract"] == "Cached abstract."
    assert papers[0]["paper_code"] == "https://github.com/foo/bar"


def test_merge_with_cache_dedupes_non_dblp_conf():
    """Pre-PR-A this branch only deduped multi-volume DBLP confs; verify
    other confs also dedupe by URL now."""

    new_res = {
        "NeurIPS 2023": [
            {
                "paper_name": "Y",
                "paper_url": "https://proceedings.neurips.cc/y",
                "paper_authors": ["A"],
                "paper_abstract": "fresh abs",
                "paper_code": "#",
            }
        ]
    }
    cache_res = {
        "NeurIPS 2023": [
            {
                "paper_name": "Y",
                "paper_url": "https://proceedings.neurips.cc/y",
                "paper_authors": ["A"],
                "paper_abstract": "cached abs that is longer than the fresh one",
                "paper_code": "#",
            },
            {
                "paper_name": "Z",
                "paper_url": "https://proceedings.neurips.cc/z",
                "paper_authors": ["B"],
                "paper_abstract": "",
                "paper_code": "#",
            },
        ]
    }

    merged = collector._merge_with_cache(new_res, cache_res, set(), set())
    papers = merged["NeurIPS 2023"]
    urls = [p["paper_url"] for p in papers]
    # "y" must appear exactly once (URL-deduped), and the longer (cached)
    # abstract must win because of the field-merge in _merge_paper_record.
    assert urls.count("https://proceedings.neurips.cc/y") == 1
    paper_y = next(p for p in papers if p["paper_url"].endswith("/y"))
    assert paper_y["paper_abstract"] == "cached abs that is longer than the fresh one"
    # "z" was only in cache, must be carried over.
    assert "https://proceedings.neurips.cc/z" in urls


def test_merge_with_cache_keeps_untouched_confs():
    """If a conf is in cache but not in new_res, it should be passed through
    unchanged (the legacy ``conf_name not in result`` branch)."""

    new_res: Dict[str, List[dict]] = {}
    cache_res = {
        "CVPR 2024": [
            {
                "paper_name": "P",
                "paper_url": "https://openaccess.thecvf.com/p",
                "paper_authors": [],
                "paper_abstract": "cached",
                "paper_code": "#",
            }
        ]
    }
    merged = collector._merge_with_cache(new_res, cache_res, set(), set())
    assert merged["CVPR 2024"] == cache_res["CVPR 2024"]

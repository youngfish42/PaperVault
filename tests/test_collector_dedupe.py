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


# ---------------------------------------------------------------------------
# Multi-volume ACL name legacy-skip exemption
# ---------------------------------------------------------------------------


_ACL_MAIN_VOLUME_HTML = """
<html><body>
<p>
  <strong><a href="/2026.acl-long.1/">Main Paper One</a></strong>
  <a href="/people/a/alice/">Alice</a>
</p>
<div id="abstract-2026--acl-long--1">Main abstract one.</div>
</body></html>
"""


def test_collect_does_not_legacy_skip_findings_when_main_already_in_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    """Regression for the ACL2026 findings mis-skip: when acl_conf.json ships
    two entries with the same ``name`` (main conf on ``/events/`` and findings
    on ``/volumes/``), the second entry must NOT be swallowed by the legacy
    ``name in cache_conf`` branch of ``_should_skip`` just because the first
    entry has already populated the cache.

    Before the fix: the events entry ran, cache_conf gained ``ACL2026``, then
    the findings-volume entry hit ``name in cache_conf`` and was marked
    ``legacy: True`` in ``progress`` without ever fetching the URL. That is
    exactly the behaviour Actions run #34 reproduced on origin/main.
    """


    # Two ACL confs sharing the same ``name``, pointing at distinct URLs.
    acl_conf = [
        {
            "name": "ACL2026",
            "tag": "^2026.acl*",
            "url": "https://aclanthology.org/events/acl-2026/",
        },
        {
            "name": "ACL2026",
            "tag": "^2026.findings*",
            "url": "https://aclanthology.org/volumes/2026.findings-acl/",
        },
    ]
    # Pre-seed cache with the events-side result so ``name in cache_conf``
    # is True by the time _should_skip evaluates the second entry.
    cache_res = {
        "ACL2026": [
            {
                "paper_name": "Main Paper One",
                "paper_url": "https://aclanthology.org/2026.acl-long.1/",
                "paper_authors": ["Alice"],
                "paper_abstract": "Main abstract one.",
                "paper_code": "#",
            }
        ]
    }


    def fake_json_load(fp, *args, **kwargs):  # noqa: ARG001
        name = getattr(fp, "name", "")
        if name.endswith("acl_conf.json"):
            return acl_conf
        return []

    monkeypatch.setattr("collector.json.load", fake_json_load)

    # Route every URL fetch to a fixture: findings URL returns 2 papers, the
    # events URL returns an events page that itself points to a single
    # sub-volume (the ``_ACL_MAIN_VOLUME_HTML`` above).
    findings_html = _ACL_FINDINGS_VOLUME_HTML
    main_events_html = (
        '<html><body>'
        '<a href="/volumes/2026.acl-long/">Main Volume</a>'
        '</body></html>'
    )
    main_volume_html = _ACL_MAIN_VOLUME_HTML

    def fake_get(url, headers=None):  # noqa: ARG001
        if url.endswith("/volumes/2026.findings-acl/"):
            return SimpleNamespace(text=findings_html)
        if url.endswith("/events/acl-2026/"):
            return SimpleNamespace(text=main_events_html)
        if url.endswith("/volumes/2026.acl-long/"):
            return SimpleNamespace(text=main_volume_html)
        return SimpleNamespace(text="")

    monkeypatch.setattr(collector.SESSION, "get", fake_get)

    # Short-circuit cache load + HF sync + progress persistence.
    monkeypatch.setattr(collector, "load_cache", lambda _p: cache_res)
    monkeypatch.setattr(collector, "load_collect_progress", lambda: {})
    monkeypatch.setattr(collector, "save_collect_progress", lambda _p: None)

    def _stub_save_cache(path, _payload):
        open(path, "wb").close()

    monkeypatch.setattr(collector, "save_cache", _stub_save_cache)
    monkeypatch.setattr(collector, "ensure_cache_local", lambda *a, **k: None)
    # Redirect the failures file to tmp so we don't touch the repo copy.
    monkeypatch.setattr(collector, "COLLECT_FAILURES_FILE", str(tmp_path / "failures.json"))

    # Only exercise the ACL loop: neuter the other collectors.
    monkeypatch.setattr(collector, "search_from_iclr", lambda url, name, res: res)
    monkeypatch.setattr(collector, "search_from_thecvf", lambda url, name, res: res)
    monkeypatch.setattr(collector, "search_from_nips", lambda url, name, res: res)
    monkeypatch.setattr(collector, "search_from_dblp", lambda url, name, res: res)

    # Provide a real gz path so ``collect`` takes the "cache exists" branch
    # that populates cache_conf; the file itself doesn't need to be usable
    # because we've replaced ``load_cache`` above.
    fake_gz = tmp_path / "cache.jsonl.gz"
    fake_gz.write_bytes(b"")

    res = collector.collect(cache_file=str(fake_gz), force=False)

    urls = {p["paper_url"] for p in res.get("ACL2026", [])}
    assert "https://aclanthology.org/2026.findings-acl.1/" in urls, (
        "findings volume entry must be collected even though ACL2026 was "
        "already present in the pre-run cache (regression: legacy-skip)."
    )
    assert "https://aclanthology.org/2026.findings-acl.2/" in urls


def test_collect_multi_volume_acl_names_heals_legacy_progress_entry(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    """Second half of the fix: if a previous run has already written a
    legacy-skip marker into ``progress`` for the findings-volume URL of a
    multi-entry ACL name, the next run must scrub that marker and re-collect
    instead of honouring it forever."""


    acl_conf = [
        {
            "name": "ACL2026",
            "tag": "^2026.acl*",
            "url": "https://aclanthology.org/events/acl-2026/",
        },
        {
            "name": "ACL2026",
            "tag": "^2026.findings*",
            "url": "https://aclanthology.org/volumes/2026.findings-acl/",
        },
    ]
    cache_res = {"ACL2026": [
        {
            "paper_name": "Main Paper One",
            "paper_url": "https://aclanthology.org/2026.acl-long.1/",
            "paper_authors": ["Alice"],
            "paper_abstract": "",
            "paper_code": "#",
        }
    ]}
    # The stale marker that previous versions of collector.py would have
    # planted on the findings-volume URL.
    stale_progress = {
        "ACL::https://aclanthology.org/volumes/2026.findings-acl/": {
            "name": "ACL2026",
            "ts": "2026-07-01T00:00:00",
            "legacy": True,
        }
    }

    def fake_json_load(fp, *args, **kwargs):  # noqa: ARG001
        name = getattr(fp, "name", "")
        if name.endswith("acl_conf.json"):
            return acl_conf
        return []

    monkeypatch.setattr("collector.json.load", fake_json_load)
    monkeypatch.setattr(collector, "load_cache", lambda _p: cache_res)
    monkeypatch.setattr(collector, "load_collect_progress", lambda: dict(stale_progress))
    saved_progress_snapshots: List[Dict[str, Any]] = []

    def _capture_progress(p):
        saved_progress_snapshots.append({k: (v.copy() if isinstance(v, dict) else v) for k, v in p.items()})

    monkeypatch.setattr(collector, "save_collect_progress", _capture_progress)

    def _stub_save_cache(path, _payload):
        # Real ``save_cache`` writes gzip so ``os.replace`` can move it into
        # place. Since we don't care about the on-disk artefact here, just
        # touch the tmp path so ``os.replace`` inside ``_save_state`` does
        # not raise WinError 2 and abort the ACL loop early.
        open(path, "wb").close()

    monkeypatch.setattr(collector, "save_cache", _stub_save_cache)
    monkeypatch.setattr(collector, "ensure_cache_local", lambda *a, **k: None)
    # Bypass the 5s throttle inside ``_save_state`` so every conf iteration
    # actually persists progress. Without this, only the first save fires
    # and we cannot observe the post-fix state of the findings URL entry.
    _fake_clock = {"t": 0.0}

    def _tick():
        _fake_clock["t"] += 10.0
        return _fake_clock["t"]

    monkeypatch.setattr(collector.time, "time", _tick)
    monkeypatch.setattr(collector, "COLLECT_FAILURES_FILE", str(tmp_path / "failures.json"))
    monkeypatch.setattr(collector, "search_from_iclr", lambda url, name, res: res)
    monkeypatch.setattr(collector, "search_from_thecvf", lambda url, name, res: res)
    monkeypatch.setattr(collector, "search_from_nips", lambda url, name, res: res)
    monkeypatch.setattr(collector, "search_from_dblp", lambda url, name, res: res)

    def fake_get(url, headers=None):  # noqa: ARG001
        if url.endswith("/volumes/2026.findings-acl/"):
            return SimpleNamespace(text=_ACL_FINDINGS_VOLUME_HTML)
        return SimpleNamespace(text="")

    monkeypatch.setattr(collector.SESSION, "get", fake_get)

    fake_gz = tmp_path / "cache.jsonl.gz"
    fake_gz.write_bytes(b"")

    res = collector.collect(cache_file=str(fake_gz), force=False)

    urls = {p["paper_url"] for p in res.get("ACL2026", [])}
    assert "https://aclanthology.org/2026.findings-acl.1/" in urls, (
        "stale ``legacy: True`` progress entry for a multi-entry ACL name "
        "must be scrubbed and the URL re-collected on the next run."
    )

    # Directly observe the self-heal: after ``collect()`` finishes, the stale
    # ``legacy: True`` marker for the findings URL must be gone from the
    # persisted progress. It is fine for intermediate snapshots to still
    # carry the marker (``_save_state`` fires between the events entry and
    # the findings entry, i.e. before ``_should_skip`` sees the findings
    # URL and pops the stale key). What matters is that the FINAL persisted
    # state no longer honours the legacy marker, so the next workflow run
    # will re-collect. This pins the pop() branch in ``_should_skip`` — a
    # future regression that keeps the guard-return but drops the pop()
    # would still be caught here because the final snapshot would then
    # retain the ``legacy: True`` entry unchanged.
    findings_key = "ACL::https://aclanthology.org/volumes/2026.findings-acl/"
    assert saved_progress_snapshots, "collect() must call save_collect_progress at least once"
    final_snap = saved_progress_snapshots[-1]
    final_entry = final_snap.get(findings_key)
    assert not (isinstance(final_entry, dict) and final_entry.get("legacy")), (
        f"stale legacy marker persisted after collect(): {final_entry!r}"
    )

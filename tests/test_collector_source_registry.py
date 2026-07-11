"""Regression tests for the SOURCE_REGISTRY refactor of collector.collect().

The 5 per-source for-loops (ACL, ICLR, thecvf, NeurIPS, DBLP) were merged
into a single loop driven by ``collector.sources.SOURCE_REGISTRY``. These
tests pin the two source-specific behaviors that the merged loop must
preserve byte-for-byte:

* TR-8.4: ACL's zero-result soft-fail branch appends to the failures log
  and does NOT write a progress key.
* TR-8.5: DBLP's post-run hook adds the collected name to the set that is
  later passed as the 4th argument to ``_merge_with_cache``.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List

import pytest

import collector
import collector.pipeline as pipeline


def _install_stub_confs(monkeypatch: pytest.MonkeyPatch, confs_by_file: Dict[str, list]) -> None:
    """Route ``json.load`` inside collector.pipeline to canned per-conf lists.

    ``collector.pipeline`` opens each of the 5 conf files at the top of
    ``collect()``; we intercept the ``json.load`` call and dispatch on the
    file-object's ``.name`` (the path passed to ``open()``).
    """

    def _fake_load(fp, *args, **kwargs):  # noqa: ARG001
        name = getattr(fp, "name", "")
        for suffix, value in confs_by_file.items():
            if name.endswith(suffix):
                return value
        return []

    monkeypatch.setattr(pipeline.json, "load", _fake_load)


def _neuter_pkg_side_effects(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    progress_sink: List[Dict[str, Any]],
) -> None:
    """Short-circuit cache load / HF sync / failure-file so tests stay offline."""

    monkeypatch.setenv("PAPERVAULT_OFFLINE", "1")
    monkeypatch.setattr(collector, "load_cache", lambda _p: {})
    monkeypatch.setattr(collector, "save_cache", lambda path, _payload: open(path, "wb").close())
    monkeypatch.setattr(collector, "load_collect_progress", lambda: {})
    monkeypatch.setattr(
        collector,
        "save_collect_progress",
        lambda p: progress_sink.append({k: (v.copy() if isinstance(v, dict) else v) for k, v in p.items()}),
    )
    monkeypatch.setattr(collector, "ensure_cache_local", lambda *a, **k: None)
    monkeypatch.setattr(collector, "sync_cache_artifacts", lambda *a, **k: None)
    monkeypatch.setattr(collector, "COLLECT_FAILURES_FILE", str(tmp_path / "failures.json"))


def test_acl_empty_result_soft_fails_and_does_not_write_progress(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    """TR-8.4: The ACL soft-fail branch must NOT write progress[ACL::url]
    (writing would cause ``_should_skip`` to permanently swallow the URL on
    the next run) and MUST record a failure entry pointing at the empty tag."""

    acl_confs = [{"name": "ACLtest25", "url": "https://example/acl", "tag": "^X-nope"}]
    _install_stub_confs(
        monkeypatch,
        {
            "acl_conf.json": acl_confs,
            "iclr_conf.json": [],
            "thecvf_conf.json": [],
            "nips_conf.json": [],
            "dblp_conf.json": [],
        },
    )

    progress_sink: List[Dict[str, Any]] = []
    _neuter_pkg_side_effects(monkeypatch, tmp_path, progress_sink)

    # ACL search stub: return res unchanged so len(res.get(name, [])) stays 0
    # both before and after the call, triggering the empty-result branch.
    monkeypatch.setattr(collector, "search_from_acl", lambda url, tag, name, res: res)
    monkeypatch.setattr(collector, "search_from_iclr", lambda url, name, res: res)
    monkeypatch.setattr(collector, "search_from_thecvf", lambda url, name, res: res)
    monkeypatch.setattr(collector, "search_from_nips", lambda url, name, res: res)
    monkeypatch.setattr(collector, "search_from_dblp", lambda url, name, res: res)

    collector.collect(cache_file=None, force=True)

    # No snapshot may ever contain the ACL progress key for this URL.
    for snap in progress_sink:
        assert "ACL::https://example/acl" not in snap, (
            f"soft-fail branch must not write progress key, got: {snap!r}"
        )

    failures_path = tmp_path / "failures.json"
    assert failures_path.exists(), "collect() must write a failures.json"
    failures = json.loads(failures_path.read_text(encoding="utf-8"))
    acl_failures = [f for f in failures if f.get("source") == "ACL"]
    assert acl_failures, f"expected an ACL failure entry, got: {failures!r}"
    assert acl_failures[0]["error"].startswith("empty result for tag="), (
        f"failure entry error text must pin the soft-fail message, got: {acl_failures[0]!r}"
    )


def test_dblp_post_run_hook_tracks_collected_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    """TR-8.5: after a successful DBLP search, the name must be added to the
    ``collected_dblp_names`` set that is passed as the 4th positional arg to
    ``_merge_with_cache``. This set gates the multi-volume DBLP cache merge
    scoping, so losing it would silently regress dedupe behaviour."""

    dblp_confs = [{"name": "DBLPtest25", "url": "https://example/dblp"}]
    _install_stub_confs(
        monkeypatch,
        {
            "acl_conf.json": [],
            "iclr_conf.json": [],
            "thecvf_conf.json": [],
            "nips_conf.json": [],
            "dblp_conf.json": dblp_confs,
        },
    )

    progress_sink: List[Dict[str, Any]] = []
    _neuter_pkg_side_effects(monkeypatch, tmp_path, progress_sink)

    # DBLP stub: append 1 paper so we take the non-zero result path and the
    # post-run hook actually fires.
    def _stub_dblp(url, name, res):
        res.setdefault(name, []).append(
            {
                "paper_name": "DBLP Paper 1",
                "paper_url": "https://example/dblp/p1",
                "paper_authors": ["A"],
                "paper_abstract": "abs",
                "paper_code": "#",
            }
        )
        return res

    monkeypatch.setattr(collector, "search_from_dblp", _stub_dblp)
    monkeypatch.setattr(collector, "search_from_acl", lambda url, tag, name, res: res)
    monkeypatch.setattr(collector, "search_from_iclr", lambda url, name, res: res)
    monkeypatch.setattr(collector, "search_from_thecvf", lambda url, name, res: res)
    monkeypatch.setattr(collector, "search_from_nips", lambda url, name, res: res)

    captured: Dict[str, Any] = {}

    def _spy_merge(new_res, cache_res, multi_volume_dblp_names, collected_dblp_names):
        captured["collected_dblp_names"] = set(collected_dblp_names)
        return new_res

    monkeypatch.setattr(pipeline, "_merge_with_cache", _spy_merge)

    collector.collect(cache_file=None, force=True)

    assert "collected_dblp_names" in captured, "_merge_with_cache spy was never called"
    assert "DBLPtest25" in captured["collected_dblp_names"], (
        f"DBLP post-run hook must add the name to collected_dblp_names, "
        f"got: {captured['collected_dblp_names']!r}"
    )

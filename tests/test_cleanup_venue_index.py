"""Regression tests for :mod:`scripts.cleanup_venue_index` (Task 10)."""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import cleanup_venue_index as cvi  # noqa: E402
from scripts.fetch_abstracts import _load_jsonl_gz  # noqa: E402


def _write_progress(tmp_path: Path, records: list[dict]) -> Path:
    path = tmp_path / "progress.jsonl.gz"
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write(json.dumps({"_meta": True, "schema": "abstract_backfill_progress/v4"}) + "\n")
        for rec in records:
            f.write(json.dumps(rec) + "\n")
    return path


# The three canonical "venue-index" URLs the classifier already pins down.
_VENUE_INDEX = [
    "https://aclanthology.org/2021.acl-long.0/",                # ACL front matter
    "https://openaccess.thecvf.com/CVPR2020",                   # CVF landing page
    "https://openreview.net/group?id=ICLR.cc/2021/Conference",  # OpenReview root
]
# Confirmed single-paper URLs — these must never be purged.
_PAPER_URLS = [
    "https://aclanthology.org/2023.acl-long.42/",
    "https://openaccess.thecvf.com/content/CVPR2024/html/Foo_CVPR_2024_paper.html",
    "https://openreview.net/forum?id=abcXYZ",
]


def test_find_venue_index_urls_picks_only_landing_pages():
    progress = {u: {"status": "failed", "reason": "empty_abstract"} for u in _VENUE_INDEX + _PAPER_URLS}
    victims = cvi.find_venue_index_urls(progress)
    assert set(victims) == set(_VENUE_INDEX)


def test_main_dry_run_does_not_touch_file(tmp_path, capsys):
    path = _write_progress(tmp_path, [
        {"url": u, "status": "failed", "reason": "empty_abstract"} for u in _VENUE_INDEX + _PAPER_URLS
    ])
    before = path.stat().st_mtime_ns
    rc = cvi.main(["--progress", str(path)])
    assert rc == 0
    assert path.stat().st_mtime_ns == before  # dry-run must not rewrite
    out = capsys.readouterr().out
    assert "DRY-RUN" in out
    assert "venue-index URLs found: 3" in out


def test_main_apply_purges_venue_index_urls(tmp_path):
    path = _write_progress(tmp_path, [
        {"url": u, "status": "failed", "reason": "empty_abstract"} for u in _VENUE_INDEX + _PAPER_URLS
    ])
    rc = cvi.main(["--progress", str(path), "--apply"])
    assert rc == 0

    progress, _ = _load_jsonl_gz(path)
    # Venue-index URLs gone, paper URLs preserved.
    for u in _VENUE_INDEX:
        assert u not in progress
    for u in _PAPER_URLS:
        assert u in progress


def test_main_apply_with_no_hits_leaves_file_untouched(tmp_path):
    path = _write_progress(tmp_path, [
        {"url": u, "status": "success", "chars": 500} for u in _PAPER_URLS
    ])
    before = path.stat().st_mtime_ns
    rc = cvi.main(["--progress", str(path), "--apply"])
    assert rc == 0
    assert path.stat().st_mtime_ns == before


def test_main_upload_invokes_upload_progress_only(tmp_path, monkeypatch):
    path = _write_progress(tmp_path, [
        {"url": _VENUE_INDEX[0], "status": "failed", "reason": "empty_abstract"},
    ])
    called = {}

    import data_artifacts

    def _fake(path_arg, commit_message="x"):
        called["path"] = Path(path_arg)
        called["commit_message"] = commit_message
        return ["cache/abstract_backfill_progress.jsonl.gz"]

    monkeypatch.setattr(data_artifacts, "upload_progress_only", _fake)
    # R1 guardrail: cleanup_venue_index now refuses to invoke the
    # uploader unless ``--progress`` matches ``DEFAULT_PROGRESS_PATH``.
    # Redirect the canonical anchor to the tmp file so the happy path
    # can still be exercised end-to-end here.
    monkeypatch.setattr(data_artifacts, "DEFAULT_PROGRESS_PATH", path)

    def _boom(*a, **kw):
        raise AssertionError("cleanup_venue_index must NEVER upload cache.jsonl.gz")

    monkeypatch.setattr(data_artifacts, "sync_cache_artifacts", _boom, raising=False)
    monkeypatch.setattr(data_artifacts, "upload_to_huggingface", _boom, raising=False)

    rc = cvi.main(["--progress", str(path), "--upload"])
    assert rc == 0
    assert called["path"] == path
    assert "Cleanup" in called["commit_message"]


def test_main_missing_file_returns_nonzero(tmp_path):
    missing = tmp_path / "nope.jsonl.gz"
    rc = cvi.main(["--progress", str(missing), "--apply"])
    assert rc == 1


def test_cleanup_upload_rejects_non_canonical_progress(tmp_path, monkeypatch, capsys):
    """R1 regression: --upload with a non-canonical --progress path must
    fail fast with a friendly stderr message and rc == 2, without ever
    invoking ``upload_progress_only`` (which would otherwise surface a
    raw ``RuntimeError`` from the whitelist)."""
    path = _write_progress(tmp_path, [
        {"url": _VENUE_INDEX[0], "status": "failed", "reason": "empty_abstract"},
    ])

    import data_artifacts

    def _forbidden(*a, **kw):
        raise AssertionError(
            "R1: cleanup_venue_index must NOT call upload_progress_only "
            "when --progress is not the canonical path."
        )

    monkeypatch.setattr(data_artifacts, "upload_progress_only", _forbidden)
    monkeypatch.setattr(
        data_artifacts,
        "DEFAULT_PROGRESS_PATH",
        tmp_path / "somewhere_else" / "abstract_backfill_progress.jsonl.gz",
    )

    rc = cvi.main(["--progress", str(path), "--upload"])
    assert rc == 2
    captured = capsys.readouterr()
    assert "canonical" in captured.err.lower()
    assert "expected:" in captured.err
    assert "got:" in captured.err

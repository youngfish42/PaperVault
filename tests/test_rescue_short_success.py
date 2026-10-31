"""Regression tests for :mod:`scripts.rescue_short_success` (Task 9).

Coverage:
  * ``find_short_successes`` selection semantics.
  * ``demote_records`` mutates in-place with correct fields.
  * ``main --dry-run`` produces a report and leaves the file untouched.
  * ``main --apply`` rewrites the progress file with demoted records.
  * ``main --upload`` reaches :func:`data_artifacts.upload_progress_only`
    and refuses to upload any other file (verifies the whitelist).
"""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from papervault.services.abstract_fetchers.base import MIN_ABSTRACT_CHARS  # noqa: E402
from scripts import rescue_short_success as rss  # noqa: E402
from scripts.fetch_abstracts import _load_jsonl_gz  # noqa: E402


def _write_progress(tmp_path: Path, records: list[dict]) -> Path:
    path = tmp_path / "progress.jsonl.gz"
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write(json.dumps({"_meta": True, "schema": "abstract_backfill_progress/v4"}) + "\n")
        for rec in records:
            f.write(json.dumps(rec) + "\n")
    return path


def test_find_short_successes_selects_only_short_successful_numeric():
    progress = {
        "https://a": {"status": "success", "chars": 42},         # short   -> hit
        "https://b": {"status": "success", "chars": 400},        # long    -> skip
        "https://c": {"status": "success"},                      # missing -> skip
        "https://d": {"status": "failed", "chars": 10, "reason": "empty_abstract"},  # not success -> skip
        "https://e": {"status": "success", "chars": MIN_ABSTRACT_CHARS},  # exact boundary -> skip (not <)
        "https://f": {"status": "success", "chars": MIN_ABSTRACT_CHARS - 1},  # short -> hit
    }
    hits = rss.find_short_successes(progress, MIN_ABSTRACT_CHARS)
    urls = [u for u, _c in hits]
    # Sorted by chars ASC.
    assert urls == ["https://a", "https://f"]


def test_demote_records_sets_failed_and_reason():
    progress = {
        "https://a": {"status": "success", "chars": 12, "attempts": 5, "source": "arxiv"},
    }
    rss.demote_records(progress, [("https://a", 12)])
    meta = progress["https://a"]
    assert meta["status"] == "failed"
    assert meta["reason"] == "empty_abstract"
    assert meta["rescued"] is True
    assert meta["attempts"] == 0
    assert meta["chars"] == 12
    # non-standard field preserved
    assert meta["source"] == "arxiv"


def test_main_dry_run_does_not_write(tmp_path, capsys):
    path = _write_progress(tmp_path, [
        {"url": "https://a", "status": "success", "chars": 10},
        {"url": "https://b", "status": "success", "chars": 500},
    ])
    before_mtime = path.stat().st_mtime_ns

    rc = rss.main(["--progress", str(path)])  # dry-run is default
    assert rc == 0

    after_mtime = path.stat().st_mtime_ns
    assert before_mtime == after_mtime, "dry-run must not rewrite the file"

    out = capsys.readouterr().out
    assert "DRY-RUN" in out
    assert "short-success candidates: 1" in out


def test_main_apply_rewrites_progress(tmp_path):
    path = _write_progress(tmp_path, [
        {"url": "https://a", "status": "success", "chars": 10},
        {"url": "https://b", "status": "success", "chars": 500},
        {"url": "https://c", "status": "failed", "reason": "network", "attempts": 1},
    ])
    rc = rss.main(["--progress", str(path), "--apply"])
    assert rc == 0

    progress, _physical = _load_jsonl_gz(path)
    # a: demoted; b: untouched success; c: still failed.
    assert progress["https://a"]["status"] == "failed"
    assert progress["https://a"]["reason"] == "empty_abstract"
    assert progress["https://a"]["rescued"] is True
    assert progress["https://a"]["attempts"] == 0
    assert progress["https://b"]["status"] == "success"
    assert progress["https://c"]["status"] == "failed"
    assert progress["https://c"]["reason"] == "network"


def test_main_upload_invokes_upload_progress_only(tmp_path, monkeypatch):
    path = _write_progress(tmp_path, [
        {"url": "https://a", "status": "success", "chars": 5},
    ])
    called_with = {}

    import data_artifacts

    def _fake_upload(path_arg, commit_message="x"):
        called_with["path"] = Path(path_arg)
        called_with["commit_message"] = commit_message
        return ["cache/abstract_backfill_progress.jsonl.gz"]

    monkeypatch.setattr(data_artifacts, "upload_progress_only", _fake_upload)
    # R1 guardrail: rescue_short_success now refuses to invoke the
    # uploader unless ``--progress`` matches ``DEFAULT_PROGRESS_PATH``.
    # Redirect the canonical anchor to our tmp file so the happy path
    # can still be exercised end-to-end here.
    monkeypatch.setattr(data_artifacts, "DEFAULT_PROGRESS_PATH", path)
    # Explicitly forbid any other uploader.
    def _boom(*a, **kw):
        raise AssertionError("rescue_short_success must NEVER upload cache.jsonl.gz")

    monkeypatch.setattr(data_artifacts, "sync_cache_artifacts", _boom, raising=False)
    monkeypatch.setattr(data_artifacts, "upload_to_huggingface", _boom, raising=False)

    rc = rss.main(["--progress", str(path), "--upload"])
    assert rc == 0
    assert "path" in called_with
    assert called_with["path"] == path
    assert "Rescue" in called_with["commit_message"]


def test_main_apply_with_no_hits_leaves_file_untouched(tmp_path):
    path = _write_progress(tmp_path, [
        {"url": "https://a", "status": "success", "chars": 500},
        {"url": "https://b", "status": "failed", "reason": "network"},
    ])
    before_mtime = path.stat().st_mtime_ns
    rc = rss.main(["--progress", str(path), "--apply"])
    assert rc == 0
    after_mtime = path.stat().st_mtime_ns
    assert before_mtime == after_mtime


def test_main_missing_file_returns_nonzero(tmp_path, capsys):
    missing = tmp_path / "nope.jsonl.gz"
    rc = rss.main(["--progress", str(missing), "--apply"])
    assert rc == 1


def test_upload_only_accepts_the_progress_file(tmp_path):
    """Sanity-check the whitelist directly: cache.jsonl.gz path is rejected."""
    from data_artifacts import upload_progress_only

    other = tmp_path / "cache.jsonl.gz"
    other.write_bytes(b"gz")
    with pytest.raises(RuntimeError, match="refuses to upload"):
        upload_progress_only(other)


def test_rescue_upload_rejects_non_canonical_progress(tmp_path, monkeypatch, capsys):
    """R1 regression: --upload with a non-canonical --progress path must
    fail fast with a friendly stderr message and rc == 2, without ever
    invoking ``upload_progress_only`` (which would surface a raw
    ``RuntimeError`` from the whitelist)."""
    path = _write_progress(tmp_path, [
        {"url": "https://a", "status": "success", "chars": 5},
    ])

    import data_artifacts

    def _forbidden_upload(*a, **kw):
        raise AssertionError(
            "R1: rescue_short_success must NOT call upload_progress_only "
            "when --progress is not the canonical path."
        )

    monkeypatch.setattr(data_artifacts, "upload_progress_only", _forbidden_upload)
    # Deliberately point the canonical anchor somewhere *else* so the
    # tmp_path passed via --progress does not match.
    monkeypatch.setattr(
        data_artifacts,
        "DEFAULT_PROGRESS_PATH",
        tmp_path / "somewhere_else" / "abstract_backfill_progress.jsonl.gz",
    )

    rc = rss.main(["--progress", str(path), "--upload"])
    assert rc == 2
    captured = capsys.readouterr()
    assert "canonical" in captured.err.lower()
    assert "expected:" in captured.err
    assert "got:" in captured.err

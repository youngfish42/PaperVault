"""Regression tests for :mod:`scripts.analyze_backfill` (Task 8).

Guarantees:
  * Uses fixture-only I/O — never touches the live progress file.
  * Emits all four canonical sections in the expected order.
  * Correctly bucketises status / host / reason / month counts.
  * Never imports or invokes ``data_artifacts.sync_cache_artifacts`` or
    :func:`data_artifacts.upload_to_huggingface` (read-only guarantee).
"""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import analyze_backfill as ab  # noqa: E402


def _make_progress_file(tmp_path: Path, records: list[dict]) -> Path:
    path = tmp_path / "progress.jsonl.gz"
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write(json.dumps({"_meta": True, "schema": "abstract_backfill_progress/v4"}) + "\n")
        for rec in records:
            f.write(json.dumps(rec) + "\n")
    return path


def test_build_report_sections_and_status_counts():
    progress = {
        "https://aclanthology.org/2024.acl-long.1/": {"status": "success", "ts": "2024-05-01T00:00:00"},
        "https://aclanthology.org/2024.acl-long.2/": {
            "status": "failed", "attempts": 1, "reason": "rate_limited",
            "ts": "2024-05-02T00:00:00",
        },
        "https://ojs.aaai.org/index.php/AAAI/article/view/1": {
            "status": "failed", "attempts": 2, "reason": "network",
            "ts": "2024-06-01T00:00:00",
        },
        "https://ojs.aaai.org/index.php/AAAI/article/view/2": {
            "status": "failed", "attempts": 1, "reason": "network",
            "ts": "2024-06-15T00:00:00",
        },
    }
    md = ab.build_report(progress)
    # All four canonical section headings must be present, in this order.
    for header in (
        ab.SECTION_STATUS,
        ab.SECTION_DOMAINS,
        ab.SECTION_REASONS,
        ab.SECTION_MONTHLY,
    ):
        assert header in md
    assert md.index(ab.SECTION_STATUS) \
        < md.index(ab.SECTION_DOMAINS) \
        < md.index(ab.SECTION_REASONS) \
        < md.index(ab.SECTION_MONTHLY)

    # Status distribution: 1 success + 3 failed.
    assert "| `success` | 1 |" in md
    assert "| `failed` | 3 |" in md
    assert "**Total** | **4**" in md

    # Domain aggregation strips ``www.`` and buckets by host.
    assert "| `ojs.aaai.org` | 2 |" in md
    assert "| `aclanthology.org` | 1 |" in md

    # Reason breakdown honours ``normalize_reason`` results.
    assert "| `network` | 2 |" in md
    assert "| `rate_limited` | 1 |" in md

    # Monthly bucketisation on the ``ts`` prefix.
    assert "| `2024-05` | 2 |" in md
    assert "| `2024-06` | 2 |" in md


def test_build_report_handles_empty_progress():
    md = ab.build_report({})
    assert "| **Total** | **0** |" in md
    assert "_(no failures)_" in md
    assert "_(no failures with reason)_" in md
    assert "_(no timestamped records)_" in md


def test_build_report_treats_legacy_v3_records():
    """v3 records lack ``reason``; the analyser surfaces them as ``(unspecified)``."""
    progress = {
        "https://example.org/x": {"status": "failed", "attempts": 1, "ts": "2023-11-11T00:00:00"},
    }
    md = ab.build_report(progress)
    assert "| `(unspecified)` | 1 |" in md


def test_main_writes_markdown_and_does_not_touch_hf(tmp_path, monkeypatch, capsys):
    """End-to-end: main() consumes a fixture progress file and writes markdown.

    Explicitly guards against accidentally importing / invoking the HF
    upload helpers.
    """
    progress_path = _make_progress_file(
        tmp_path,
        records=[
            {"url": "https://aclanthology.org/x", "status": "success", "ts": "2024-01-01T00:00:00"},
            {
                "url": "https://ojs.aaai.org/y", "status": "failed", "attempts": 1,
                "reason": "empty_abstract", "ts": "2024-01-02T00:00:00",
            },
        ],
    )
    output_path = tmp_path / "report.md"

    # Hard-fail if the analyser ever tries to upload — proves the
    # "never touches HF" guarantee at runtime.
    import data_artifacts

    def _boom(*args, **kwargs):
        raise AssertionError("analyze_backfill must not upload to HF")

    monkeypatch.setattr(data_artifacts, "upload_to_huggingface", _boom, raising=False)
    monkeypatch.setattr(data_artifacts, "sync_cache_artifacts", _boom, raising=False)
    monkeypatch.setattr(data_artifacts, "upload_progress_only", _boom, raising=False)

    written_path, markdown = ab.main([
        "--progress", str(progress_path),
        "--output", str(output_path),
    ])
    assert written_path == output_path
    assert output_path.exists()
    on_disk = output_path.read_text(encoding="utf-8")
    assert on_disk == markdown
    assert "# Abstract backfill diagnostic report" in on_disk

    banner = capsys.readouterr().out
    assert "READ-ONLY" in banner


def test_main_prints_read_only_banner(capsys, tmp_path):
    """Sanity: even against a missing progress file the banner still prints."""
    missing = tmp_path / "no-such.jsonl.gz"
    output = tmp_path / "report.md"
    ab.main(["--progress", str(missing), "--output", str(output)])
    banner = capsys.readouterr().out
    assert "READ-ONLY" in banner
    assert output.exists()  # empty-progress report still written

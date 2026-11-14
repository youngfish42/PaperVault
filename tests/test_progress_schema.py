"""Tests for the schema-v4 upgrade of ``scripts/fetch_abstracts.py``.

Covers spec ``AC-8`` / TR-2.1..2.3:
  * ``PROGRESS_SCHEMA_VERSION == 4`` in the meta line;
  * a v3 fixture can be loaded and rewritten without losing any legacy
    field (``status`` / ``source`` / ``chars`` / ``ts`` / ``attempts``);
  * ``REASON_ENUM`` at minimum contains the 10 canonical reasons named in
    the spec, and ``normalize_reason`` maps common human-readable failure
    blurbs to the right enum member.
"""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

from scripts import fetch_abstracts as fa


def _write_v3_fixture(path: Path) -> None:
    lines = [
        {
            "_meta": True,
            "schema": "abstract_backfill_progress/v3",
            "version": 3,
            "generated_at": "2024-01-01T00:00:00",
        },
        {
            "url": "https://doi.org/10.0/success",
            "status": "success",
            "source": "openalex",
            "chars": 1234,
            "ts": "2024-01-01T00:00:01",
        },
        {
            "url": "https://doi.org/10.0/fail",
            "status": "failed",
            "attempts": 3,
            "ts": "2024-01-01T00:00:02",
        },
    ]
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        for obj in lines:
            fh.write(json.dumps(obj) + "\n")


def test_progress_schema_version_bumped_to_v4():
    meta = fa._meta_line()
    assert meta["version"] == 4
    assert meta["schema"] == "abstract_backfill_progress/v4"
    assert meta["_meta"] is True


def test_reason_enum_covers_spec_vocabulary():
    required = {
        "no_doi",
        "doi_not_found",
        "empty_abstract",
        "rate_limited",
        "timeout",
        "network",
        "title_mismatch",
        "venue_index",
        "delegated_to_specialised_fetcher",
        "no_abstract_available",
    }
    assert required.issubset(fa.REASON_ENUM)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("HTTP 429 Too Many Requests", "rate_limited"),
        ("read timeout after 30s", "timeout"),
        ("no DOI in record", "no_doi"),
        ("404 not found", "doi_not_found"),
        ("empty abstract from openalex", "empty_abstract"),
        ("title mismatch: 'A' vs 'B'", "title_mismatch"),
        ("venue index page detected", "venue_index"),
        ("delegated to specialised fetcher", "delegated_to_specialised_fetcher"),
        ("something totally new", "network"),
        (None, "network"),
        ("", "network"),
        ("rate_limited", "rate_limited"),
        # Regression guard for review issue #1: a message that *starts*
        # by declaring the DOI is missing but also contains "not found"
        # must classify as ``no_doi`` — not ``doi_not_found``.
        ("missing doi info: not found upstream", "no_doi"),
        ("nodoi", "no_doi"),
        # Regression guard for review issue R2 (second-pass review):
        # the free-text phrase "no abstract available" must classify
        # as ``no_abstract_available`` (a distinct bucket added to
        # REASON_ENUM in the R#7 3xx fix) — NOT the generic
        # ``empty_abstract`` bucket. Ordering inside normalize_reason
        # must probe this specific phrase before the generic
        # ``"no abstract"`` / ``"empty"`` fallback.
        ("no abstract available", "no_abstract_available"),
        ("Crossref returned no abstract available for this DOI", "no_abstract_available"),
        # The generic bucket must still catch plain "empty abstract"
        # phrasing so upstream code that already writes that message
        # (e.g. Semantic Scholar 200 with empty body) is unaffected.
        ("empty abstract", "empty_abstract"),
    ],
)
def test_normalize_reason_maps_known_blurbs(raw, expected):
    assert fa.normalize_reason(raw) == expected


def test_v3_fixture_loads_without_field_loss(tmp_path, monkeypatch):
    fixture = tmp_path / "abstract_backfill_progress.jsonl.gz"
    _write_v3_fixture(fixture)

    monkeypatch.setattr(fa, "PROGRESS_FILE", fixture)
    monkeypatch.setattr(fa, "LEGACY_PROGRESS_FILE", tmp_path / "abstract_backfill_progress.json")
    fa._last_saved_snapshot.clear()
    fa._progress_runtime["physical_lines"] = 0

    loaded = fa.load_progress()

    # Both v3 records must survive verbatim.
    assert "https://doi.org/10.0/success" in loaded
    assert "https://doi.org/10.0/fail" in loaded
    ok = loaded["https://doi.org/10.0/success"]
    assert ok["status"] == "success"
    assert ok["source"] == "openalex"
    assert ok["chars"] == 1234
    assert ok["ts"] == "2024-01-01T00:00:01"

    bad = loaded["https://doi.org/10.0/fail"]
    assert bad["status"] == "failed"
    assert bad["attempts"] == 3
    assert bad["ts"] == "2024-01-01T00:00:02"
    # v3 records do NOT carry `reason`; we must not fabricate one on read.
    assert "reason" not in bad


def test_include_legacy_boolean_optional_action():
    """R4 regression: ``--include-legacy`` / ``--no-include-legacy`` are
    now driven by :class:`argparse.BooleanOptionalAction` (Python 3.9+).

    We invoke the script with ``--help`` in a subprocess to:
      * confirm both flag surfaces are still advertised (backwards-compat
        with any operator muscle memory / GitHub Actions invocations);
      * verify the parser accepts the module import without raising,
        i.e. ``BooleanOptionalAction`` is available on the CI Python
        (3.10, per ``.github/workflows/ci.yml``).
    """
    import subprocess

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "fetch_abstracts.py"), "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    # ``--help`` must succeed and both flag forms must appear.
    assert result.returncode == 0, result.stderr
    combined = result.stdout + result.stderr
    assert "--include-legacy" in combined
    assert "--no-include-legacy" in combined

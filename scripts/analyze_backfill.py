"""Analyse ``cache/abstract_backfill_progress.jsonl.gz`` and emit a markdown report.

Read-only diagnostic — the script:
  * does **NOT** write to :data:`data_artifacts.CACHE_FILE`;
  * does **NOT** call :func:`data_artifacts.sync_cache_artifacts`;
  * does **NOT** call :func:`data_artifacts.upload_to_huggingface`;
  * does **NOT** call :func:`data_artifacts.upload_progress_only`.

The output markdown lands at
[docs/abstract_backfill_report.md](file:///d:/git/youngfish/PaperVault/docs/abstract_backfill_report.md)
by default; override with ``--output``.

Sections rendered (spec AC-9):

  1. **Status Distribution** — success / failed / other counts.
  2. **Top Failure Domains** — hosts most contributing to failures.
  3. **Reason Breakdown** — histogram over :data:`REASON_ENUM` values.
  4. **Monthly Progress** — record-write timestamps bucketed by month.

Corresponds to Task 8 of the abstract-backfill-repair spec.

TODO(review #10, fourth pass): this report is currently only regenerated
on demand (locally, or manually via ``workflow_dispatch``). Consider
wiring it into ``backfill_abstracts.yml`` as a final read-only step so
every 6-hourly run refreshes ``docs/abstract_backfill_report.md``
alongside the progress ledger. Not doing it today because the report is
already 100 % deterministic from the progress file (which *is* pushed to
HF) so any operator can regenerate it locally in seconds.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# We deliberately import ``load_progress`` and ``normalize_reason`` from
# the main fetch script so this analyser stays in lock-step with the
# schema owner. It's a read-only import: nothing in that module runs
# when we don't call ``run()``.
from scripts.fetch_abstracts import (  # noqa: E402
    PROGRESS_FILE,
    _load_jsonl_gz,
    normalize_reason,
)


# The four canonical section headings the test suite pins.
SECTION_STATUS = "## Status Distribution"
SECTION_DOMAINS = "## Top Failure Domains"
SECTION_REASONS = "## Reason Breakdown"
SECTION_MONTHLY = "## Monthly Progress"


def _host_of(url: str) -> str:
    if not url:
        return "(empty-url)"
    host = (urlparse(url).netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host or "(no-host)"


def _month_of(ts: str) -> str:
    """Return the ``YYYY-MM`` prefix of an ISO-8601 timestamp, or ``(no-ts)``."""
    if not ts or len(ts) < 7:
        return "(no-ts)"
    return ts[:7]


def _render_status(counts: Counter) -> str:
    lines = [SECTION_STATUS, "", "| Status | Count |", "|---|---|"]
    for status, count in counts.most_common():
        lines.append(f"| `{status}` | {count} |")
    lines.append(f"| **Total** | **{sum(counts.values())}** |")
    return "\n".join(lines)


def _render_domains(counts: Counter, top: int = 10) -> str:
    lines = [SECTION_DOMAINS, "", f"Top {top} hosts by failure count.", "",
             "| Host | Failed count |", "|---|---|"]
    if not counts:
        lines.append("| _(no failures)_ | 0 |")
    else:
        for host, count in counts.most_common(top):
            lines.append(f"| `{host}` | {count} |")
    return "\n".join(lines)


def _render_reasons(counts: Counter) -> str:
    lines = [SECTION_REASONS, "", "| Reason | Count |", "|---|---|"]
    if not counts:
        lines.append("| _(no failures with reason)_ | 0 |")
    else:
        for reason, count in counts.most_common():
            lines.append(f"| `{reason}` | {count} |")
    return "\n".join(lines)


def _render_monthly(counts: Counter) -> str:
    lines = [SECTION_MONTHLY, "", "| Month | Records touched |", "|---|---|"]
    if not counts:
        lines.append("| _(no timestamped records)_ | 0 |")
    else:
        for month in sorted(counts):
            lines.append(f"| `{month}` | {counts[month]} |")
    return "\n".join(lines)


def build_report(progress: Dict[str, dict]) -> str:
    """Turn a loaded progress dict into the markdown report body."""
    status_counts: Counter = Counter()
    domain_failures: Counter = Counter()
    reason_counts: Counter = Counter()
    monthly_counts: Counter = Counter()

    for url, meta in progress.items():
        status = meta.get("status") or "unknown"
        status_counts[status] += 1

        if status == "failed":
            domain_failures[_host_of(url)] += 1
            raw_reason = meta.get("reason")
            if raw_reason:
                reason_counts[normalize_reason(raw_reason)] += 1
            else:
                # v3 legacy record — surface the gap so operators can see
                # how much of the ledger predates schema v4.
                reason_counts["(unspecified)"] += 1

        monthly_counts[_month_of(meta.get("ts", ""))] += 1

    body = [
        "# Abstract backfill diagnostic report",
        "",
        f"* Total tracked URLs: **{sum(status_counts.values())}**",
        "* Source: `cache/abstract_backfill_progress.jsonl.gz`",
        "* Generated by [scripts/analyze_backfill.py](file:///d:/git/youngfish/PaperVault/scripts/analyze_backfill.py) (read-only; never touches HF).",
        "",
        _render_status(status_counts),
        "",
        _render_domains(domain_failures),
        "",
        _render_reasons(reason_counts),
        "",
        _render_monthly(monthly_counts),
        "",
    ]
    return "\n".join(body)


def _print_banner() -> None:
    print("=" * 60)
    print("[analyze_backfill] READ-ONLY diagnostic run.")
    print("[analyze_backfill] 本机绝不上传 HF (no sync_cache_artifacts / no upload_to_huggingface).")
    print("=" * 60)


def main(argv: Optional[Iterable[str]] = None) -> Tuple[Path, str]:
    parser = argparse.ArgumentParser(
        description="Analyse abstract backfill progress and emit a markdown report.",
    )
    parser.add_argument(
        "--progress",
        type=Path,
        default=PROGRESS_FILE,
        help="Path to the progress .jsonl.gz file (default: cache/abstract_backfill_progress.jsonl.gz).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "docs" / "abstract_backfill_report.md",
        help="Output markdown path (default: docs/abstract_backfill_report.md).",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    _print_banner()

    if not args.progress.exists():
        print(f"[analyze_backfill] Progress file not found: {args.progress}", file=sys.stderr)
        progress: Dict[str, dict] = {}
    else:
        progress, _physical = _load_jsonl_gz(args.progress)
    markdown = build_report(progress)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(markdown, encoding="utf-8")
    print(f"[analyze_backfill] Wrote report to {args.output}")
    return args.output, markdown


if __name__ == "__main__":
    main()

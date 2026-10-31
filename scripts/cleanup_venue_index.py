"""Purge venue-index URLs from the abstract backfill progress ledger.

**Problem.** A venue-index URL (e.g. ``https://openaccess.thecvf.com/CVPR2024``,
``https://aclanthology.org/events/acl-2023/``) is a *landing page*, not a
paper detail page. It has no abstract to backfill. Historic runs
occasionally recorded such URLs in the progress ledger — those entries
eat retry budget forever and dilute the analyser numbers.

**Fix.** Drop every progress record whose URL classifies as
``venue-index`` (via :func:`collector.url_types.is_venue_index`). The
new source modules already guard the collector against writing such
URLs; this script cleans up the legacy debt.

Three modes, mirroring [scripts/rescue_short_success.py](file:///d:/git/youngfish/PaperVault/scripts/rescue_short_success.py):

  * ``--dry-run`` (default): report only.
  * ``--apply``: rewrite the *local* progress file atomically.
  * ``--upload``: after ``--apply``, push progress.jsonl.gz to HF via
    :func:`data_artifacts.upload_progress_only`. ``cache.jsonl.gz`` is
    **never** uploaded.

Corresponds to Task 10 of the abstract-backfill-repair spec (AC-12).
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, Iterable, List, Optional
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from collector.url_types import is_venue_index  # noqa: E402
from scripts.fetch_abstracts import (  # noqa: E402
    PROGRESS_FILE,
    _compact_progress,
    _load_jsonl_gz,
)


def find_venue_index_urls(progress: Dict[str, dict]) -> List[str]:
    """Return the subset of URLs classified as ``venue-index`` (sorted)."""
    return sorted(url for url in progress if is_venue_index(url))


def _host_of(url: str) -> str:
    host = (urlparse(url).netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host or "(no-host)"


def _print_banner(mode: str) -> None:
    print("=" * 60)
    print(f"[cleanup_venue_index] Mode: {mode}")
    if mode == "dry-run":
        print("[cleanup_venue_index] DRY-RUN — no file writes, no HF upload.")
    elif mode == "apply":
        print("[cleanup_venue_index] APPLY — rewriting local progress file only. No HF upload.")
    else:
        print("[cleanup_venue_index] UPLOAD — will push progress .jsonl.gz to HF after apply.")
    print("[cleanup_venue_index] cache.jsonl.gz WILL NOT be touched.")
    print("=" * 60)


def _print_report(urls: List[str]) -> None:
    print(f"[cleanup_venue_index] venue-index URLs found: {len(urls)}")
    if not urls:
        return
    hosts = Counter(_host_of(u) for u in urls)
    print("[cleanup_venue_index] By host:")
    for host, count in hosts.most_common():
        print(f"  - {host}: {count}")
    print("[cleanup_venue_index] first up to 20 candidates:")
    for u in urls[:20]:
        print(f"  - {u}")


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Purge venue-index URLs from the abstract backfill progress ledger.",
    )
    parser.add_argument("--progress", type=Path, default=PROGRESS_FILE,
                        help="Progress file path (default: cache/abstract_backfill_progress.jsonl.gz).")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", dest="dry_run", default=True,
                      help="Report only; do not write. (Default.)")
    mode.add_argument("--apply", action="store_true", dest="apply_changes",
                      help="Drop venue-index URLs from the local progress file.")
    mode.add_argument("--upload", action="store_true", dest="upload_changes",
                      help="After --apply, push progress.jsonl.gz to Hugging Face.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.upload_changes:
        current_mode = "upload"
    elif args.apply_changes:
        current_mode = "apply"
    else:
        current_mode = "dry-run"
    _print_banner(current_mode)

    if not args.progress.exists():
        print(f"[cleanup_venue_index] Progress file not found: {args.progress}", file=sys.stderr)
        return 1

    progress, _physical = _load_jsonl_gz(args.progress)
    victims = find_venue_index_urls(progress)
    _print_report(victims)

    if current_mode == "dry-run":
        return 0
    if not victims:
        print("[cleanup_venue_index] Nothing to purge; leaving the file alone.")
        return 0

    for url in victims:
        progress.pop(url, None)

    # Review issue #6: same fix as rescue_short_success -- use the
    # explicit ``path=`` kwarg to compact into the caller-selected
    # file, avoiding a race-prone monkey-patch of ``fa.PROGRESS_FILE``.
    _compact_progress(progress, path=args.progress)
    print(f"[cleanup_venue_index] Rewrote {args.progress} — removed {len(victims)} venue-index records.")

    if current_mode == "upload":
        # Guardrail (review issue R1): ``upload_progress_only`` is a
        # strict whitelist that accepts *only* the canonical
        # ``ROOT/cache/abstract_backfill_progress.jsonl.gz``. Trying to
        # upload a custom ``--progress`` path would blow up with a bare
        # ``RuntimeError`` at the very last step. Fail fast with a
        # friendly message so the operator can adjust the invocation.
        from data_artifacts import DEFAULT_PROGRESS_PATH

        canonical = Path(DEFAULT_PROGRESS_PATH).resolve()
        if args.progress.resolve() != canonical:
            print(
                "[cleanup_venue_index] --upload requires the canonical "
                "progress file location:\n"
                f"    expected: {canonical}\n"
                f"    got:      {args.progress.resolve()}\n"
                "Run this script against the default --progress path, or "
                "drop --upload and push via the GitHub Actions workflow.",
                file=sys.stderr,
            )
            return 2

        from data_artifacts import upload_progress_only

        upload_progress_only(
            args.progress,
            commit_message=f"Cleanup {len(victims)} venue-index URLs from backfill progress",
        )
        print("[cleanup_venue_index] Upload complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

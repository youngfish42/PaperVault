"""Rescue ``status=success`` progress records with implausibly short abstracts.

**Problem.** Historic backfill runs (schema v3) occasionally marked entries
as ``status=success`` even when the recovered abstract was empty or a few
characters long (e.g. an HTML fragment leaked into the extractor). Those
records will never be retried because :func:`_process_targets` only
considers ``status=failed`` URLs.

**Fix.** Demote each such record back to ``failed`` with the canonical
reason ``empty_abstract`` — the next backfill run will pick them up as
usual (subject to ``--reason-in``).

The tool has three modes:

  * ``--dry-run`` (default): report only, no writes, no uploads.
  * ``--apply``: rewrite the *local* progress file atomically.
  * ``--upload``: after ``--apply``, push the progress file to Hugging
    Face via :func:`data_artifacts.upload_progress_only` — the only
    sanctioned local upload path. ``cache/cache.jsonl.gz`` is **never**
    uploaded.

The abstract length is read from ``meta["chars"]`` when present (the
schema v4 field written by ``normalise_meta``). When ``chars`` is
missing (v3 legacy records), the analyser conservatively skips the URL
— we don't want to demote records without evidence.

Corresponds to Task 9 of the abstract-backfill-repair spec (AC-11).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from papervault.services.abstract_fetchers.base import MIN_ABSTRACT_CHARS  # noqa: E402
from scripts.fetch_abstracts import (  # noqa: E402
    PROGRESS_FILE,
    _compact_progress,
    _load_jsonl_gz,
)


def find_short_successes(
    progress: Dict[str, dict],
    threshold: int,
) -> List[Tuple[str, int]]:
    """Return ``[(url, chars), ...]`` for successes whose abstract is too short.

    Records without a numeric ``chars`` field are *not* rescued — we won't
    fabricate evidence. Callers can spot how many were skipped via
    :func:`_count_unknown_chars`.
    """
    hits: List[Tuple[str, int]] = []
    for url, meta in progress.items():
        if meta.get("status") != "success":
            continue
        chars = meta.get("chars")
        if not isinstance(chars, int):
            continue
        if chars < threshold:
            hits.append((url, chars))
    hits.sort(key=lambda item: item[1])
    return hits


def _count_unknown_chars(progress: Dict[str, dict]) -> int:
    return sum(
        1 for meta in progress.values()
        if meta.get("status") == "success" and not isinstance(meta.get("chars"), int)
    )


def demote_records(
    progress: Dict[str, dict],
    hits: Iterable[Tuple[str, int]],
) -> Dict[str, dict]:
    """Mutate ``progress`` in-place: success -> failed / reason=empty_abstract.

    Attempts counter is preserved (adds ``rescued=True`` for auditability).
    Returns the mutated dict so callers can chain.
    """
    for url, chars in hits:
        meta = dict(progress[url])
        meta["status"] = "failed"
        meta["reason"] = "empty_abstract"
        meta["rescued"] = True
        # Force the next run to attempt this URL: ``attempts`` <=
        # max_failed_attempts. If the record had somehow been
        # incremented past the retry ceiling, reset it to 0 -- the
        # rescue is by definition a fresh chance.
        meta["attempts"] = 0
        # Retain historical ``chars`` for observability; the new
        # attempt will overwrite it on success.
        meta["chars"] = chars
        progress[url] = meta
    return progress


def _print_banner(mode: str) -> None:
    print("=" * 60)
    print(f"[rescue_short_success] Mode: {mode}")
    if mode == "dry-run":
        print("[rescue_short_success] DRY-RUN — no file writes, no HF upload.")
    elif mode == "apply":
        print("[rescue_short_success] APPLY — rewriting local progress file only. No HF upload.")
    else:  # upload
        print("[rescue_short_success] UPLOAD — will push progress .jsonl.gz to HF after apply.")
    print("[rescue_short_success] cache.jsonl.gz WILL NOT be touched.")
    print("=" * 60)


def _print_report(
    hits: List[Tuple[str, int]],
    unknown_chars_count: int,
    threshold: int,
) -> None:
    print(f"[rescue_short_success] threshold = {threshold} chars")
    print(f"[rescue_short_success] short-success candidates: {len(hits)}")
    print(f"[rescue_short_success] success records without numeric 'chars' (skipped): {unknown_chars_count}")
    if hits:
        print("[rescue_short_success] first up to 20 candidates (chars, url):")
        for url, chars in hits[:20]:
            print(f"  - {chars:>4} chars  {url}")


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Demote short-abstract success records to failed/empty_abstract.",
    )
    parser.add_argument("--progress", type=Path, default=PROGRESS_FILE,
                        help="Progress file path (default: cache/abstract_backfill_progress.jsonl.gz).")
    parser.add_argument("--threshold", type=int, default=MIN_ABSTRACT_CHARS,
                        help=f"Minimum abstract length to keep as success (default: {MIN_ABSTRACT_CHARS}).")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", dest="dry_run", default=True,
                      help="Report only; do not write. (Default.)")
    mode.add_argument("--apply", action="store_true", dest="apply_changes",
                      help="Rewrite the local progress file with demoted records.")
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
        print(f"[rescue_short_success] Progress file not found: {args.progress}", file=sys.stderr)
        return 1

    progress, _physical = _load_jsonl_gz(args.progress)
    hits = find_short_successes(progress, args.threshold)
    unknown_chars_count = _count_unknown_chars(progress)
    _print_report(hits, unknown_chars_count, args.threshold)

    if current_mode == "dry-run":
        return 0

    if not hits:
        print("[rescue_short_success] Nothing to rescue; leaving the file alone.")
        return 0

    demote_records(progress, hits)
    # Review issue #6: pass ``path=`` through explicitly instead of the
    # historical monkey-patch of ``fa.PROGRESS_FILE`` -- the latter is
    # not thread-safe if another importer of ``fetch_abstracts`` is
    # active, and it silently mutated global state on failure.
    _compact_progress(progress, path=args.progress)
    print(f"[rescue_short_success] Rewrote {args.progress} with {len(hits)} demoted records.")

    if current_mode == "upload":
        # Guardrail (review issue R1): ``data_artifacts.upload_progress_only``
        # accepts *only* the canonical repo path
        # (``ROOT/cache/abstract_backfill_progress.jsonl.gz``). Piping a
        # custom ``--progress`` path straight into it would surface as a
        # raw ``RuntimeError`` at the very last step. Fail fast here with
        # a user-friendly message so the operator understands the CLI
        # contract instead of chasing a stack trace.
        from data_artifacts import DEFAULT_PROGRESS_PATH

        canonical = Path(DEFAULT_PROGRESS_PATH).resolve()
        if args.progress.resolve() != canonical:
            print(
                "[rescue_short_success] --upload requires the canonical "
                "progress file location:\n"
                f"    expected: {canonical}\n"
                f"    got:      {args.progress.resolve()}\n"
                "Run this script against the default --progress path, or "
                "drop --upload and push via the GitHub Actions workflow.",
                file=sys.stderr,
            )
            return 2

        # Deferred import: keep dry-run / apply modes free of any HF
        # side-effect risk (never even import the uploader).
        from data_artifacts import upload_progress_only

        upload_progress_only(
            args.progress,
            commit_message=f"Rescue {len(hits)} short-success records back to failed/empty_abstract",
        )
        print("[rescue_short_success] Upload complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

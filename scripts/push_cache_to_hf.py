"""One-shot uploader: push local cache/cache.jsonl.gz to Hugging Face.

Why a dedicated script?
-----------------------

The reusable path in ``data_artifacts`` is ``sync_cache_artifacts``, which is
designed to be called at the tail of a *data-mutating* pipeline (collector,
abstract backfill, ...). In our current situation the local cache has already
been rebuilt out-of-band (paperswithcode cleanup + GitHub link re-scan), so
we need a push that:

  1. Does **not** download anything from HF first (that would clobber our
     freshly rebuilt local file).
  2. Still primes the ``parent_commit`` optimistic-lock so the upload is
     rejected safely if another writer raced us.
  3. Verifies the local file is a well-formed gzip JSONL before pushing.
  4. Prints a clear commit-sha / commit-URL receipt from Hugging Face so we
     know the write actually landed.

Usage
-----

  # 1) Configure credentials (PowerShell)
  $env:HF_TOKEN = "hf_xxx..."
  $env:PAPERVAULT_HF_REPO_ID = "youngfish42/papervault-cache"

  # 2) Dry-run: sanity-check the local file and show what we WOULD push
  python scripts/push_cache_to_hf.py --dry-run

  # 3) Actually push (uses the default commit message)
  python scripts/push_cache_to_hf.py

  # 4) Push with a specific commit message
  python scripts/push_cache_to_hf.py --message "Reset 1577 pwc entries and backfill 207 GitHub links"

Exit codes
----------
  0  Upload succeeded (or dry-run finished cleanly).
  1  Pre-flight sanity check failed (bad gzip / row count mismatch / repo
     misconfigured).
  2  HF upload failed after all retries.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# NOTE: data_artifacts loads .env via python-dotenv on import; do the import
# before we start peeking at env vars so a repo-local .env can seed HF_TOKEN /
# PAPERVAULT_HF_REPO_ID for us.
from data_artifacts import (  # noqa: E402
    DEFAULT_CACHE_PATH,
    DEFAULT_PROGRESS_PATH,
    _HF_PARENT_COMMITS,
    _get_hf_api,
    _get_remote_commit,
    _hf_repo_id,
    _hf_repo_type,
    _hf_token,
    _path_in_repo,
    ensure_cache_local,
    upload_to_huggingface,
)


DEFAULT_MESSAGE = "Update PaperVault cache artifacts"


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def preflight(cache_path: Path, *, sample_head: int = 3) -> Tuple[int, list]:
    """Verify the local file is a well-formed gzip JSONL before we push.

    Returns (row_count, sample_first_records).  Raises RuntimeError on any
    problem so we bail out **before** contacting Hugging Face.
    """
    if not cache_path.exists():
        raise RuntimeError(f"cache file does not exist: {cache_path}")

    size = cache_path.stat().st_size
    if size < 1024:
        raise RuntimeError(
            f"cache file suspiciously small ({size} bytes) — refusing to push."
        )

    # gzip magic 0x1f 0x8b
    with cache_path.open("rb") as fh:
        magic = fh.read(2)
    if magic != b"\x1f\x8b":
        raise RuntimeError(
            f"cache file is not gzip (magic={magic!r}) — refusing to push."
        )

    rows = 0
    sample: list = []
    parse_errors = 0
    with gzip.open(cache_path, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                parse_errors += 1
                if parse_errors > 3:
                    raise RuntimeError(
                        f"cache file has >3 unparseable JSONL lines "
                        f"(row={rows}); refusing to push."
                    )
                continue
            rows += 1
            if len(sample) < sample_head:
                sample.append(obj)

    if rows == 0:
        raise RuntimeError("cache file has zero valid rows — refusing to push.")

    return rows, sample


def prime_parent_commit(cache_path: Path) -> Optional[str]:
    """Populate ``_HF_PARENT_COMMITS[<path>]`` without touching the local file.

    We call ``ensure_cache_local(refresh=False)`` which is documented to only
    fetch the remote commit sha and NOT overwrite the local file — the local
    cache is our freshly rebuilt source of truth and must stay untouched.
    """
    _, commit = ensure_cache_local(cache_path=cache_path, refresh=False)
    return commit


def _sync_progress_ok(progress_path: Path) -> bool:
    """Return True if we should include the progress file in this push."""
    if not progress_path.exists():
        return False
    # Gzip sanity only — the progress file is JSONL.gz with a metadata header.
    try:
        with progress_path.open("rb") as fh:
            return fh.read(2) == b"\x1f\x8b"
    except OSError:
        return False


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Push the local PaperVault cache to Hugging Face.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--cache",
        type=Path,
        default=DEFAULT_CACHE_PATH,
        help=f"local cache file to push (default: {DEFAULT_CACHE_PATH})",
    )
    p.add_argument(
        "--progress",
        type=Path,
        default=DEFAULT_PROGRESS_PATH,
        help=(
            "abstract-backfill progress file to co-push if it exists "
            f"(default: {DEFAULT_PROGRESS_PATH}). Pass '' to skip."
        ),
    )
    p.add_argument(
        "-m",
        "--message",
        default=DEFAULT_MESSAGE,
        help="HF commit message (default: %(default)r)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "run every pre-flight check and print the plan, but do NOT contact "
            "Hugging Face for the actual upload."
        ),
    )
    p.add_argument(
        "--no-progress",
        action="store_true",
        help="never include the progress file, even if it exists.",
    )
    return p.parse_args()


def _tune_hf_client_for_large_uploads() -> None:
    """Force the huggingface_hub client into a slow-network-friendly profile.

    Rationale: the default (Xet-based) upload path in ``huggingface_hub`` has
    been observed to stall on 100MB+ single-file uploads over consumer
    residential networks (tqdm freezes mid-transfer, connection never times
    out). The CI workflow already opts out of Xet — see
    ``.github/workflows/backfill_abstracts.yml`` which sets
    ``HF_HUB_DISABLE_XET: "1"``. We mirror the same environment locally, and
    additionally opt-in to ``hf_transfer`` (Rust) which supports parallel
    chunked uploads and is much more robust for large files.

    All settings use ``setdefault`` so the caller can still override any of
    them from the shell before invoking the script.
    """
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    os.environ.setdefault("HF_HUB_ENABLE_HF_TRANSFER", "1")
    os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "60")
    os.environ.setdefault("PAPERVAULT_HF_UPLOAD_MAX_ATTEMPTS", "5")
    os.environ.setdefault("PAPERVAULT_HF_UPLOAD_RETRY_BACKOFF", "10")

    if os.environ.get("HF_HUB_ENABLE_HF_TRANSFER") == "1":
        try:
            import hf_transfer  # noqa: F401
        except ImportError:
            print(
                "[!] HF_HUB_ENABLE_HF_TRANSFER=1 but the ``hf_transfer`` "
                "package is not installed. Falling back to the pure-Python "
                "client. For faster large-file uploads run:  pip install hf_transfer"
            )
            os.environ.pop("HF_HUB_ENABLE_HF_TRANSFER", None)


def main() -> int:
    args = parse_args()

    if os.getenv("PAPERVAULT_OFFLINE", "").lower() in ("1", "true", "yes"):
        print(
            "[!] PAPERVAULT_OFFLINE=1 is set; unset it before running this script "
            "or Hugging Face upload will be skipped by data_artifacts."
        )
        return 1

    _tune_hf_client_for_large_uploads()
    print(
        f"[*] HF client tuning: DISABLE_XET={os.environ.get('HF_HUB_DISABLE_XET')} "
        f"HF_TRANSFER={os.environ.get('HF_HUB_ENABLE_HF_TRANSFER') or 'off'} "
        f"MAX_ATTEMPTS={os.environ.get('PAPERVAULT_HF_UPLOAD_MAX_ATTEMPTS')} "
        f"BACKOFF={os.environ.get('PAPERVAULT_HF_UPLOAD_RETRY_BACKOFF')}s"
    )

    cache_path = args.cache.resolve()
    print(f"[*] Local cache : {cache_path}")

    # -----------------------------------------------------------------
    # Step 1: pre-flight the local file
    # -----------------------------------------------------------------
    print("[*] Pre-flight: gzip magic + JSONL row count ...")
    t0 = time.time()
    try:
        rows, sample = preflight(cache_path)
    except RuntimeError as exc:
        print(f"[!] Pre-flight failed: {exc}")
        return 1
    dt = time.time() - t0
    print(
        f"[+] Pre-flight OK: {rows} rows, "
        f"{_human_size(cache_path.stat().st_size)} on disk, {dt:.1f}s to scan."
    )
    if sample:
        keys = sorted(sample[0].keys())
        print(f"    First record has {len(keys)} fields: {keys[:8]}{'...' if len(keys) > 8 else ''}")

    # Optional progress file
    progress_targets: list = []
    progress_path = args.progress if args.progress else None
    if progress_path and not args.no_progress:
        progress_path = progress_path.resolve()
        if _sync_progress_ok(progress_path):
            progress_targets.append(progress_path)
            print(
                f"[*] Progress file: {progress_path} "
                f"({_human_size(progress_path.stat().st_size)}) — will co-push."
            )
        else:
            print(f"[*] Progress file: {progress_path} (missing or not gzip) — skipping.")

    # -----------------------------------------------------------------
    # Step 2: repo config sanity
    # -----------------------------------------------------------------
    repo_id = _hf_repo_id()
    if not repo_id:
        print("[!] PAPERVAULT_HF_REPO_ID is not set; nothing to push to.")
        return 1
    if not _hf_token():
        print("[!] HF_TOKEN is not set; upload will fail with 401.")
        return 1
    print(f"[*] Target repo : {repo_id} ({_hf_repo_type()})")

    api = _get_hf_api()
    if api is None:
        print("[!] huggingface_hub is unavailable; cannot proceed.")
        return 1

    # -----------------------------------------------------------------
    # Step 3: prime parent_commit WITHOUT clobbering the local file
    # -----------------------------------------------------------------
    print("[*] Priming parent_commit (no local overwrite) ...")
    try:
        parent = prime_parent_commit(cache_path)
    except Exception as exc:  # noqa: BLE001
        # The upload path below can still run without a parent_commit; log and
        # continue rather than aborting.
        print(f"[!] Could not prime parent_commit ({exc}); upload will proceed unlocked.")
        parent = None
    if parent:
        print(f"[+] parent_commit primed: {parent}")
    else:
        print("[*] parent_commit unavailable (first-ever push, or repo empty).")

    # Also prime for the progress file so both uploads move atomically.
    for pp in progress_targets:
        try:
            ensure_cache_local(cache_path=pp, refresh=False)
        except Exception as exc:  # noqa: BLE001
            print(f"[!] Could not prime parent_commit for {pp.name} ({exc}); ignored.")

    # -----------------------------------------------------------------
    # Step 4: upload (or dry-run)
    # -----------------------------------------------------------------
    targets = [cache_path, *progress_targets]
    print(f"[*] Commit message: {args.message!r}")
    print(f"[*] Files to push ({len(targets)}):")
    for p in targets:
        print(f"      - {_path_in_repo(p)}  ({_human_size(p.stat().st_size)})")

    if args.dry_run:
        print("[*] --dry-run: stopping here. No HF API call was made.")
        return 0

    print("[*] Uploading to Hugging Face ...")
    t0 = time.time()
    uploaded = upload_to_huggingface(targets, commit_message=args.message)
    dt = time.time() - t0

    if not uploaded:
        print(f"[!] Upload finished with 0 files pushed after {dt:.1f}s.")
        return 2

    print(f"[+] Upload finished in {dt:.1f}s; pushed: {uploaded}")

    # -----------------------------------------------------------------
    # Step 5: HF-side receipt (fresh head sha)
    # -----------------------------------------------------------------
    try:
        new_head = _get_remote_commit(api, repo_id, _path_in_repo(cache_path))
    except Exception:  # noqa: BLE001
        new_head = None
    if new_head:
        print(f"[+] New head commit on {repo_id}: {new_head}")
        print(
            f"    View commit: https://huggingface.co/datasets/{repo_id}/commit/{new_head}"
        )
        if parent and new_head == parent:
            print(
                "[!] WARNING: new_head == parent_commit; the upload did not "
                "advance the branch. Inspect Hugging Face manually before "
                "assuming success."
            )
            return 2
    else:
        print("[!] Could not fetch new head commit; upload response was accepted anyway.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Synchronise IJCAI main proceedings from official JSON/HTML snapshots, never PDFs."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from collector.io import load_cache, save_cache  # noqa: E402
from collector.merge import _merge_with_cache  # noqa: E402
from collector.sources.ijcai import (  # noqa: E402
    IJCAICollectionError,
    collect_ijcai_papers,
    normalize_ijcai_title,
)
from data_artifacts import ensure_cache_local, sync_cache_artifacts  # noqa: E402

CACHE_FILE = ROOT / "cache" / "cache.jsonl.gz"
BACKUP_FILE = ROOT / "cache" / "cache.jsonl.gz.ijcai.bak"
CONF_FILE = ROOT / "conf" / "ijcai_conf.json"
SNAPSHOT_DIR = ROOT / ".cache" / "ijcai"


def _coverage(records: list[dict]) -> dict:
    return {
        "total": len(records),
        "abstracts": sum(bool((record.get("paper_abstract") or "").strip()) for record in records),
    }


def _save_atomic(cache_path: Path, data: dict) -> None:
    tmp_path = Path(str(cache_path) + ".tmp")
    try:
        save_cache(str(tmp_path), data)
        verified = load_cache(str(tmp_path))
        expected = sum(len(records) for records in data.values())
        actual = sum(len(records) for records in verified.values())
        if actual != expected:
            raise RuntimeError(f"cache verification failed: expected {expected} rows, got {actual}")
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    if not BACKUP_FILE.exists():
        shutil.copy2(cache_path, BACKUP_FILE)
    os.replace(tmp_path, cache_path)


def run(args) -> int:
    if not (args.offline or args.dry_run or args.replay):
        ensure_cache_local(CACHE_FILE, refresh=True)
    if not CACHE_FILE.exists():
        print(f"[!] Cache file not found: {CACHE_FILE}", file=sys.stderr)
        return 1
    with CONF_FILE.open("r", encoding="utf-8") as handle:
        entries = json.load(handle)
    if args.year:
        years = {str(year) for year in args.year}
        entries = [entry for entry in entries if re.search(r"(\d{4})$", entry["name"]).group(1) in years]
    if not entries:
        print("[!] No IJCAI proceedings selected.", file=sys.stderr)
        return 1

    merged = load_cache(str(CACHE_FILE))
    if args.dry_run:
        for entry in entries:
            cached = len(merged.get(entry["name"], []))
            print(f"  {entry['name']}: cached={cached}, url={entry['url']}")
        print("[*] Dry run: no HTTP requests, cache writes or Hugging Face sync.")
        return 0

    failures = []
    for entry in entries:
        name = entry["name"]
        before_records = merged.get(name, [])
        before = _coverage(before_records)
        old_title_keys = {
            key for record in before_records
            if (key := normalize_ijcai_title(record.get("paper_name", "")))
        }
        try:
            papers = collect_ijcai_papers(
                entry["url"],
                concurrency=args.concurrency,
                limit=args.limit,
                snapshot_dir=args.snapshot_dir,
                cached_records=before_records,
                refresh=args.refresh,
                allow_network=not args.replay,
            )
        except IJCAICollectionError as exc:
            papers = exc.papers
            failures.extend((name, url, error) for url, error in exc.failures)
            print(f"[!] {name}: {exc}; retaining successful pages for resume", file=sys.stderr)
        except Exception as exc:
            failures.append((name, entry["url"], str(exc)))
            print(f"[!] {name}: {exc}", file=sys.stderr)
            continue

        official_keys = [normalize_ijcai_title(record.get("paper_name", "")) for record in papers]
        matched = sum(bool(key) and key in old_title_keys for key in official_keys)
        new_res = {name: papers}
        merged = _merge_with_cache(
            new_res,
            merged,
            set(),
            set(),
            ijcai_title_merge_records=new_res,
        )
        after = _coverage(merged[name])
        print(
            f"[*] {name} {entry['url']}: official={len(papers)} matched={matched} "
            f"rows={before['total']}→{after['total']} "
            f"abstracts={before['abstracts']}→{after['abstracts']}",
            flush=True,
        )

    _save_atomic(CACHE_FILE, merged)
    print(f"[*] Cache updated atomically; backup: {BACKUP_FILE}")
    if not (args.no_sync or args.offline or args.replay):
        sync_cache_artifacts(
            cache_path=CACHE_FILE,
            progress_path=None,
            commit_message="Enrich IJCAI papers from official proceedings metadata",
        )
    else:
        print("[*] Skipped Hugging Face sync (--no-sync/--offline/--replay).")
    if failures:
        print(f"[!] {len(failures)} IJCAI page(s) failed; rerun to resume from local snapshots.", file=sys.stderr)
        return 2
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, nargs="+", help="Conference year(s)")
    parser.add_argument("--limit", type=int, help="Maximum official papers per proceedings page")
    parser.add_argument("--concurrency", type=int, default=4, help="Detail-page workers (default: 4)")
    parser.add_argument("--snapshot-dir", type=Path, default=SNAPSHOT_DIR)
    parser.add_argument("--refresh", action="store_true", help="Redownload pages even when snapshots exist")
    parser.add_argument("--replay", action="store_true", help="Read snapshots only; fail instead of making HTTP requests")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", dest="dry_run", action="store_true", default=True)
    mode.add_argument("--write", dest="dry_run", action="store_false", help="Write the merged cache")
    parser.add_argument("--offline", action="store_true", help="Use the local cache and skip HF sync")
    parser.add_argument("--no-sync", action="store_true", help="Write locally but skip HF sync")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    if args.concurrency < 1:
        parser.error("--concurrency must be positive")
    return run(args)


if __name__ == "__main__":
    sys.exit(main())

"""Collect MICCAI Open Access abstracts/code links and merge them into the cache."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from collector.io import load_cache, save_cache  # noqa: E402
from collector.merge import _merge_with_cache  # noqa: E402
from collector.sources.miccai import (  # noqa: E402
    MICCAICollectionError,
    collect_miccai_papers,
    normalize_miccai_title,
)
from data_artifacts import ensure_cache_local, sync_cache_artifacts  # noqa: E402

CACHE_FILE = ROOT / "cache" / "cache.jsonl.gz"
BACKUP_FILE = ROOT / "cache" / "cache.jsonl.gz.miccai.bak"
CONF_FILE = ROOT / "conf" / "miccai_conf.json"


def _coverage(records: list[dict]) -> dict:
    return {
        "total": len(records),
        "abstracts": sum(bool((record.get("paper_abstract") or "").strip()) for record in records),
        "code": sum(
            bool((record.get("paper_code") or "").strip())
            and (record.get("paper_code") or "").strip() != "#"
            for record in records
        ),
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
    shutil.copy2(cache_path, BACKUP_FILE)
    os.replace(tmp_path, cache_path)


def run(args) -> int:
    if not args.offline:
        ensure_cache_local(CACHE_FILE, refresh=True)
    if not CACHE_FILE.exists():
        print(f"[!] Cache file not found: {CACHE_FILE}", file=sys.stderr)
        return 1

    with CONF_FILE.open("r", encoding="utf-8") as handle:
        confs = json.load(handle)
    if args.year:
        years = set(args.year)
        confs = [conf for conf in confs if conf["name"][-4:] in {str(year) for year in years}]
    if not confs:
        print("[!] No MICCAI Open Access configurations selected.", file=sys.stderr)
        return 1

    merged = load_cache(str(CACHE_FILE))
    for conf in confs:
        name = conf["name"]
        before_records = merged.get(name, [])
        before = _coverage(before_records)
        old_title_keys = {
            key for record in before_records
            if (key := normalize_miccai_title(record.get("paper_name", "")))
        }
        try:
            papers = collect_miccai_papers(
                conf["url"], concurrency=args.concurrency, limit=args.limit
            )
        except MICCAICollectionError as exc:
            print(f"[!] {name}: {exc}", file=sys.stderr)
            print("[!] Cache untouched; fix the failed detail pages and rerun.", file=sys.stderr)
            return 2

        official_keys = [
            normalize_miccai_title(record.get("paper_name", "")) for record in papers
        ]
        matched = sum(bool(key) and key in old_title_keys for key in official_keys)
        official_new = sum(bool(key) and key not in old_title_keys for key in official_keys)
        new_res = {name: papers}
        merged = _merge_with_cache(
            new_res,
            merged,
            set(),
            set(),
            title_merge_records=new_res,
        )
        after = _coverage(merged[name])
        print(
            f"[*] {name}: official={len(papers)} matched={matched} new={official_new} "
            f"rows={before['total']}→{after['total']} "
            f"abstracts={before['abstracts']}→{after['abstracts']} "
            f"code={before['code']}→{after['code']}"
        )

    if args.dry_run:
        print("[*] Dry run: cache untouched, no Hugging Face sync.")
        return 0

    _save_atomic(CACHE_FILE, merged)
    print(f"[*] Cache updated atomically; backup: {BACKUP_FILE}")
    if not (args.no_sync or args.offline):
        sync_cache_artifacts(
            cache_path=CACHE_FILE,
            progress_path=None,
            commit_message="Enrich MICCAI papers from Open Access",
        )
    else:
        print("[*] Skipped Hugging Face sync (--no-sync/--offline).")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, nargs="+", help="Conference year(s), e.g. 2026 2025")
    parser.add_argument("--limit", type=int, help="Maximum papers per selected conference")
    parser.add_argument("--concurrency", type=int, default=8, help="Detail-page workers (default: 8)")
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

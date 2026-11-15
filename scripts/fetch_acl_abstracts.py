"""Backfill ACL Anthology abstracts from official BibTeX and academic APIs."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Optional

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from collector.code_links import extract_github_link  # noqa: E402
from data_artifacts import (  # noqa: E402
    ensure_cache_local,
    ensure_progress_local,
    sync_cache_artifacts,
)
from scripts.acl_abstracts import (  # noqa: E402
    ACL_BIBTEX_URL,
    canonical_acl_url,
    clean_acl_abstract,
    is_acl_paper_url,
    load_bibtex_metadata,
)
from scripts.fetch_abstracts import (  # noqa: E402
    S2_BATCH_SIZE,
    _evaluate_openalex_title_item,
    _evaluate_s2_title_item,
    _openalex_title_match_fetch,
    _s2_batch_fetch,
    _s2_record_failure,
    _s2_record_success,
    clean_abstract,
    load_progress,
    save_progress,
)
from scripts.fetch_cvf_abstracts import load_cache, save_cache_atomic  # noqa: E402

CACHE_FILE = ROOT / "cache" / "cache.jsonl.gz"
PROGRESS_FILE = ROOT / "cache" / "abstract_backfill_progress.jsonl.gz"
BACKUP_FILE = ROOT / "cache" / "cache.jsonl.gz.acl.bak"
CONF_FILE = ROOT / "conf" / "acl_conf.json"
BIBTEX_FILE = ROOT / ".cache" / "anthology+abstracts.bib.gz"
BIBTEX_TTL_SECONDS = 86400
MIN_ABSTRACT_CHARS = 40


def _download_bibtex(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    for attempt in range(1, 4):
        try:
            session = requests.Session()
            session.trust_env = False
            with session.get(ACL_BIBTEX_URL, stream=True, timeout=(15, 120)) as response:
                response.raise_for_status()
                with tmp.open("wb") as handle:
                    for chunk in response.iter_content(1024 * 1024):
                        if chunk:
                            handle.write(chunk)
            os.replace(tmp, path)
            print(f"[*] Downloaded ACL BibTeX: {path} ({path.stat().st_size} bytes)")
            return
        except Exception as exc:
            print(f"[!] ACL BibTeX download attempt {attempt}/3 failed: {exc}")
            if attempt < 3:
                time.sleep(3 * attempt)
    raise RuntimeError("Unable to download ACL Anthology BibTeX after 3 attempts")


def ensure_bibtex(path: Path = BIBTEX_FILE, refresh: bool = False) -> Path:
    if refresh or not path.exists() or time.time() - path.stat().st_mtime > BIBTEX_TTL_SECONDS:
        _download_bibtex(path)
    return path


def _conf_year(conf: str) -> int:
    match = re.search(r"(\d{4})$", conf or "")
    return int(match.group(1)) if match else 0


def collect_targets(
    records: list[dict],
    progress: dict,
    conf_filter: Optional[set[str]],
    year_filter: Optional[set[int]],
    retry_failed: bool,
) -> list[dict]:
    targets = []
    for record in records:
        url = canonical_acl_url(record.get("paper_url", ""))
        if not is_acl_paper_url(url) or (record.get("paper_abstract") or "").strip():
            continue
        conf = record.get("conf", "")
        if conf_filter is not None and conf not in conf_filter:
            continue
        if year_filter is not None and _conf_year(conf) not in year_filter:
            continue
        if not retry_failed and progress.get(url, {}).get("status") == "failed":
            continue
        targets.append(record)
    return targets


def _fill(progress: dict, record: dict, abstract: str, source: str, stats: dict) -> None:
    _s2_record_success(progress, record, abstract, source)
    if (record.get("paper_code") or "#") == "#":
        code = extract_github_link(abstract)
        if code:
            record["paper_code"] = code
            stats["code"] += 1
    stats[source] = stats.get(source, 0) + 1
    stats["changed"] = True
    stats["dirty"] = True


def _flush(records: list[dict], progress: dict, stats: dict) -> None:
    save_progress(progress)
    if stats.get("dirty"):
        if not BACKUP_FILE.exists():
            shutil.copy2(CACHE_FILE, BACKUP_FILE)
        save_cache_atomic(CACHE_FILE, records)
        stats["dirty"] = False


def run_bibtex_pass(
    records: list[dict], targets: list[dict], progress: dict, metadata: dict, stats: dict
) -> None:
    for record in targets:
        if (record.get("paper_abstract") or "").strip():
            continue
        fields = metadata.get(canonical_acl_url(record["paper_url"]), {})
        abstract = clean_acl_abstract(fields.get("abstract", ""))
        if len(abstract) >= MIN_ABSTRACT_CHARS:
            _fill(progress, record, abstract, "acl_bibtex", stats)


def run_s2_pass(
    records: list[dict], targets: list[dict], progress: dict,
    stats: dict, start_time: float, soft_timeout: Optional[float], dry_run: bool,
) -> None:
    pending = [record for record in targets if not (record.get("paper_abstract") or "").strip()]
    for start in range(0, len(pending), S2_BATCH_SIZE):
        if soft_timeout and time.time() - start_time >= soft_timeout:
            print("[!] Soft timeout reached before S2 batch; progress saved.")
            return
        chunk = pending[start:start + S2_BATCH_SIZE]
        ids = [
            "ACL:" + canonical_acl_url(record["paper_url"]).rstrip("/").rsplit("/", 1)[-1]
            for record in chunk
        ]
        if dry_run:
            print(f"  [dry-run] S2 batch: {len(chunk)} Anthology IDs")
            continue
        results, error = _s2_batch_fetch(ids)
        if results is None:
            for record in chunk:
                _s2_record_failure(progress, record, error or "network", "semanticscholar_batch", count_attempt=False)
            print(f"[!] S2 batch failed: {error}")
            continue
        for record, item in zip(chunk, results):
            abstract, reason = _evaluate_s2_title_item(item, record)
            if abstract:
                _fill(progress, record, abstract, "semanticscholar_batch", stats)
            else:
                _s2_record_failure(progress, record, reason or "empty_abstract", "semanticscholar_batch")
        _flush(records, progress, stats)
        print(f"[*] S2 batch {start // S2_BATCH_SIZE + 1}: processed={min(start + len(chunk), len(pending))}/{len(pending)}, filled={stats.get('semanticscholar_batch', 0)}")


def run_openalex_pass(
    records: list[dict], targets: list[dict], progress: dict, stats: dict,
    start_time: float, soft_timeout: Optional[float], dry_run: bool,
) -> None:
    pending = [record for record in targets if not (record.get("paper_abstract") or "").strip()]
    transient_streak = 0
    for index, record in enumerate(pending, 1):
        if soft_timeout and time.time() - start_time >= soft_timeout:
            print(f"[!] Soft timeout reached at OpenAlex record {index}/{len(pending)}; progress saved.")
            return
        title = record.get("paper_name", "")
        if dry_run:
            if index <= 10:
                print(f"  [dry-run] OpenAlex title: {title}")
            continue
        candidates, error = _openalex_title_match_fetch(title)
        if candidates is None:
            _s2_record_failure(progress, record, error or "network", "openalex_title", count_attempt=error not in {"network", "timeout", "rate_limited"})
            if error in {"network", "timeout", "rate_limited"}:
                transient_streak += 1
                if transient_streak >= 5:
                    _flush(records, progress, stats)
                    print("[!] Stopping after 5 consecutive transient OpenAlex failures; progress saved.")
                    return
            else:
                transient_streak = 0
            continue
        transient_streak = 0
        abstract, reason = _evaluate_openalex_title_item(candidates, record)
        if abstract:
            _fill(progress, record, abstract, "openalex_title", stats)
        else:
            _s2_record_failure(progress, record, reason or "empty_abstract", "openalex_title")
        if index % 100 == 0 or index == len(pending):
            _flush(records, progress, stats)
            print(f"[*] OpenAlex: {index}/{len(pending)}, filled={stats.get('openalex_title', 0)}")


def run(args) -> int:
    if args.conf is not None and not [name for name in args.conf if name.strip()]:
        print("[!] --conf requires at least one non-empty conference name", file=sys.stderr)
        return 2
    if not args.offline:
        ensure_cache_local(CACHE_FILE, refresh=True)
        ensure_progress_local(PROGRESS_FILE, refresh=True)
    if not CACHE_FILE.exists():
        print(f"[!] Cache not found: {CACHE_FILE}")
        return 1
    records = load_cache(CACHE_FILE)
    progress = load_progress()
    conf_filter = {name.strip() for name in args.conf if name.strip()} if args.conf is not None else None
    year_filter = set(args.year) if args.year else None
    targets = collect_targets(records, progress, conf_filter, year_filter, args.retry_failed)
    if args.limit:
        targets = targets[:args.limit]
    print(f"[*] ACL abstract targets: {len(targets)} (cache={len(records)})")
    if args.dry_run:
        print("[*] Dry run: no downloads, API requests or writes.")
        return 0
    if not targets:
        print("[*] No ACL abstracts need backfilling.")
        return 0

    stats = {"changed": False, "code": 0, "dirty": False}
    start_time = time.time()
    if not args.skip_bibtex:
        metadata = load_bibtex_metadata(
            ensure_bibtex(refresh=args.refresh_bibtex),
            {canonical_acl_url(record["paper_url"]) for record in targets},
        )
        print(f"[*] BibTeX metadata matched: {len(metadata)}/{len(targets)}")
        run_bibtex_pass(records, targets, progress, metadata, stats)
        if stats["changed"]:
            _flush(records, progress, stats)
    else:
        metadata = {}
    if not args.skip_s2:
        run_s2_pass(records, targets, progress, stats, start_time, args.soft_timeout, args.dry_run)
    if not args.skip_openalex:
        run_openalex_pass(records, targets, progress, stats, start_time, args.soft_timeout, args.dry_run)

    remaining = [record for record in targets if not (record.get("paper_abstract") or "").strip()]
    save_progress(progress)
    if stats["changed"]:
        _flush(records, progress, stats)
    print(f"[*] ACL backfill summary: {stats}; remaining={len(remaining)}")
    if not args.no_sync and not args.offline and stats["changed"]:
        sync_cache_artifacts(
            cache_path=CACHE_FILE,
            progress_path=PROGRESS_FILE,
            commit_message=f"Backfill ACL abstracts (+{sum(value for key, value in stats.items() if key not in {'changed', 'code'})})",
        )
    return 0


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--conf", nargs="*", default=None)
    parser.add_argument("--year", nargs="*", type=int, default=None)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--skip-bibtex", action="store_true")
    parser.add_argument("--skip-s2", action="store_true")
    parser.add_argument("--skip-openalex", action="store_true")
    parser.add_argument("--refresh-bibtex", action="store_true")
    parser.add_argument("--soft-timeout", type=float, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--no-sync", action="store_true")
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(run(parse_args()))

"""
一次性迁移：cache/abstract_backfill_progress.json -> cache/abstract_backfill_progress.jsonl.gz

设计要点：
- 输出首行为 meta（含 schema 版本与生成时间），其余每行一条 record。
- record 字段：url, status, source?, chars?, attempts?, ts?。url 作为业务主键。
- 旧 JSON 字典天然去重；本脚本只做一次"格式翻译"，不去除任何信息。
- 写入采用 .tmp -> os.replace 的原子替换，避免半成品文件污染下游加载。
- 验证：迁移完成后回读 .jsonl.gz，比对 (条目数, status 分布) 与原 JSON 一致。

用法:
    python scripts/migrate_progress_to_jsonl.py
    python scripts/migrate_progress_to_jsonl.py --source cache/abstract_backfill_progress.json \
        --target cache/abstract_backfill_progress.jsonl.gz
    python scripts/migrate_progress_to_jsonl.py --dry-run
"""

import argparse
import gzip
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Dict

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DEFAULT_SOURCE = ROOT / "cache" / "abstract_backfill_progress.json"
DEFAULT_TARGET = ROOT / "cache" / "abstract_backfill_progress.jsonl.gz"

SCHEMA_NAME = "abstract_backfill_progress/v3"
SCHEMA_VERSION = 3


def _to_record(url: str, meta: dict) -> dict:
    """把旧字典格式 (url -> meta) 翻译成 JSONL record。

    主键统一为 'url'。仅保留 source/chars/attempts/ts 等业务字段，未识别字段
    透传以避免信息丢失。
    """
    rec = {"url": url, "status": meta.get("status", "unknown")}
    for key in ("source", "chars", "attempts", "ts"):
        if key in meta:
            rec[key] = meta[key]
    for key, value in meta.items():
        if key not in rec and key != "status":
            rec[key] = value
    return rec


def migrate(source: Path, target: Path, dry_run: bool = False) -> Dict[str, int]:
    if not source.exists():
        raise FileNotFoundError(f"Source progress file not found: {source}")

    with source.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if "processed" in data:
        processed = data["processed"]
    elif "processed_urls" in data:
        processed = {url: {"status": "unknown"} for url in data["processed_urls"]}
    else:
        processed = data

    if not isinstance(processed, dict):
        raise ValueError(
            f"Unexpected progress structure: top-level 'processed' should be dict, got {type(processed)}"
        )

    total = len(processed)
    status_counter: Counter = Counter()
    for meta in processed.values():
        status_counter[meta.get("status", "unknown")] += 1

    meta_line = {
        "_meta": True,
        "schema": SCHEMA_NAME,
        "version": SCHEMA_VERSION,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": str(source.relative_to(ROOT) if source.is_absolute() else source).replace("\\", "/"),
        "record_count": total,
        "status_breakdown": dict(status_counter),
    }

    print(f"[*] Source: {source}")
    print(f"[*] Target: {target}")
    print(f"[*] Records: {total}")
    print(f"[*] Status breakdown: {dict(status_counter)}")

    if dry_run:
        print("[*] --dry-run set; no file written.")
        return {"records": total, **status_counter}

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    try:
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            f.write(json.dumps(meta_line, ensure_ascii=False) + "\n")
            for url, rec_meta in processed.items():
                rec = _to_record(url, rec_meta)
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass

    size_bytes = target.stat().st_size
    print(f"[+] Wrote {target} ({size_bytes:,} bytes, {size_bytes / 1024 / 1024:.2f} MB)")

    verify_counter: Counter = Counter()
    verify_records = 0
    seen_urls = set()
    with gzip.open(target, "rt", encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if i == 0 and obj.get("_meta"):
                continue
            verify_records += 1
            verify_counter[obj.get("status", "unknown")] += 1
            seen_urls.add(obj.get("url"))

    print(f"[*] Verify: read back {verify_records} records, {len(seen_urls)} unique urls.")
    print(f"[*] Verify status breakdown: {dict(verify_counter)}")

    if verify_records != total:
        raise RuntimeError(
            f"Verification failed: record count mismatch (orig={total}, jsonl={verify_records})"
        )
    if dict(verify_counter) != dict(status_counter):
        raise RuntimeError(
            f"Verification failed: status breakdown mismatch "
            f"(orig={dict(status_counter)}, jsonl={dict(verify_counter)})"
        )
    if len(seen_urls) != total:
        print(
            f"[!] Warning: unique url count ({len(seen_urls)}) differs from record count ({total}); "
            "this should not happen for a freshly migrated file."
        )

    print("[+] Migration verified OK.")
    return {"records": verify_records, **verify_counter}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE,
                        help="Path to legacy JSON progress file")
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET,
                        help="Output JSONL.gz path")
    parser.add_argument("--dry-run", action="store_true",
                        help="Parse and report, but do not write the target file")
    args = parser.parse_args()
    migrate(args.source.resolve(), args.target.resolve(), dry_run=args.dry_run)


if __name__ == "__main__":
    main()

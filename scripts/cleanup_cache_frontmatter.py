"""一次性缓存清洗：移除 front-matter 类记录（会议卷首页 / 论文集封面条目）。

背景
----
全库约 2,033 条记录不是论文而是 front-matter：``paper_name`` 以
"Proceedings of" / "Front Matter" / "Table of Contents" / "Preface" /
"Findings of the Association" 等开头（如 ICSE2026 的
``doi.org/10.1145/3786581``、IJCAI2025 的 ``/proceedings/2025/`` 落地页、
ICML2025 workshop 的 ``proceedings.mlr.press/v292/`` 卷首页）。

判定（保守，双重条件——标题命中正则 *且* 满足任一佐证）：
  1. 标题命中 :data:`FRONT_MATTER_RE`（大小写不敏感）；
  2. ``classify_paper_url(url) == "venue-index"``，或（无摘要 *且* 无真实
     代码链接——``paper_code`` 为空或 ``"#"`` 占位）。

设计参照 ``cleanup_cache_dedupe.py``：流式读取，``.tmp`` + 回读校验 +
``os.replace`` 原子重写，默认保留 ``.bak`` 备份。进度文件
（abstract_backfill_progress.jsonl.gz）中的对应记录**不动**——它们是
历史账本，不是缓存索引。

用法
----
    python scripts/cleanup_cache_frontmatter.py                  # dry-run 报告（默认）
    python scripts/cleanup_cache_frontmatter.py --review         # dry-run + 完整待删清单
    python scripts/cleanup_cache_frontmatter.py --write          # 真正删除
    python scripts/cleanup_cache_frontmatter.py --write --no-sync
"""

import argparse
import gzip
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from collector.url_types import classify_paper_url  # noqa: E402
from data_artifacts import ensure_cache_local, sync_cache_artifacts  # noqa: E402

DEFAULT_CACHE = ROOT / "cache" / "cache.jsonl.gz"

FRONT_MATTER_RE = re.compile(
    r"^(proceedings\b|front matter|frontmatter|table of contents|preface\b|findings of the association)",
    re.IGNORECASE,
)


def is_frontmatter_record(rec: dict) -> bool:
    """判定一条记录是否为 front-matter（保守双重条件，见模块 docstring）。"""
    if not isinstance(rec, dict):
        return False
    title = (rec.get("paper_name") or "").strip()
    if not FRONT_MATTER_RE.match(title):
        return False
    if classify_paper_url(rec.get("paper_url", "")) == "venue-index":
        return True
    has_abstract = bool((rec.get("paper_abstract") or "").strip())
    code = (rec.get("paper_code") or "").strip()
    has_code = bool(code and code != "#")
    return not has_abstract and not has_code


def cleanup(
    source: Path,
    *,
    dry_run: bool = True,
    backup: bool = True,
) -> dict:
    """读取 ``source``，移除 front-matter 记录，（除非 dry-run）原子写回。

    返回统计字典；``matched`` 是完整待删清单（``--review`` 打印用）。
    删除按谓词确定性过滤——等价于按 ``(conf, paper_url)`` 匹配，天然幂等。
    """
    if not source.exists():
        raise FileNotFoundError(f"cache file not found: {source}")

    total = 0
    parse_errors = 0
    matched = []  # (conf, title, url)
    kept_records = []  # 仅 write 模式需要：保留行在内存中重写

    with gzip.open(source, "rt", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except Exception:
                parse_errors += 1
                continue
            total += 1
            if is_frontmatter_record(rec):
                matched.append(
                    (rec.get("conf", ""), (rec.get("paper_name") or "").strip(),
                     rec.get("paper_url", ""))
                )
            else:
                kept_records.append(rec)

    by_conf: dict = {}
    for conf, _t, _u in matched:
        by_conf[conf] = by_conf.get(conf, 0) + 1

    stats = {
        "source": str(source),
        "total_rows": total,
        "parse_errors": parse_errors,
        "matched_frontmatter": len(matched),
        "kept": total - len(matched),
        "by_conf": dict(sorted(by_conf.items(), key=lambda kv: -kv[1])),
        "matched": matched,
        "dry_run": dry_run,
    }

    if dry_run or not matched:
        return stats

    tmp = source.with_suffix(source.suffix + ".tmp")
    if tmp.exists():
        tmp.unlink()
    written = 0
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        for rec in kept_records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            written += 1

    # 回读校验：行数必须等于 kept；任意一行 JSON 解析失败 → 回滚。
    verify_rows = 0
    try:
        with gzip.open(tmp, "rt", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                json.loads(line)
                verify_rows += 1
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"verification read failed: {exc}") from exc
    if verify_rows != len(kept_records) or written != len(kept_records):
        tmp.unlink(missing_ok=True)
        raise RuntimeError(
            f"verification mismatch: written={written} verify_rows={verify_rows} kept={len(kept_records)}"
        )

    if backup:
        bak = source.with_suffix(source.suffix + ".bak")
        if bak.exists():
            bak.unlink()
        os.replace(source, bak)
        os.replace(tmp, source)
        stats["backup"] = str(bak)
    else:
        os.replace(tmp, source)
        stats["backup"] = None

    stats["written_rows"] = written
    return stats


def _print_report(stats: dict, review: bool) -> None:
    print(f"\n===== front-matter cleanup report [{'DRY-RUN' if stats['dry_run'] else 'WRITE'}] =====")
    print(f"total rows scanned : {stats['total_rows']}")
    print(f"parse errors       : {stats['parse_errors']}")
    print(f"front-matter matched: {stats['matched_frontmatter']}")
    print(f"kept               : {stats['kept']}")
    if stats["by_conf"]:
        print("top confs          :")
        for conf, n in list(stats["by_conf"].items())[:15]:
            print(f"    {conf:20} {n}")
    show = stats["matched"] if review else stats["matched"][:30]
    if show:
        print(f"{'full ' if review else 'sample '}list ({len(show)} of {stats['matched_frontmatter']}):")
        for conf, title, url in show:
            print(f"    [{conf}] {title[:70]}")
            print(f"        {url}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--source", type=Path, default=DEFAULT_CACHE,
                        help="cache jsonl.gz 路径，默认 cache/cache.jsonl.gz")
    g = parser.add_mutually_exclusive_group()
    g.add_argument("--dry-run", dest="dry_run", action="store_true", default=True,
                   help="只统计与生成报告，不写回（默认）")
    g.add_argument("--write", dest="dry_run", action="store_false",
                   help="真正从缓存删除匹配记录")
    parser.add_argument("--no-backup", action="store_true",
                        help="写入时不保留 .bak 备份（不推荐）")
    parser.add_argument("--review", action="store_true",
                        help="打印完整待删清单（conf/title/url）供人工过目")
    parser.add_argument("--no-sync", action="store_true",
                        help="跳过结束时 sync_cache_artifacts 的 HF 推送")
    parser.add_argument("--offline", action="store_true",
                        help="跳过 ensure_cache_local 并且不推送 HF")
    args = parser.parse_args(argv)

    if not args.offline:
        ensure_cache_local(args.source, refresh=True)

    try:
        stats = cleanup(
            args.source,
            dry_run=args.dry_run,
            backup=not args.no_backup,
        )
    except FileNotFoundError as exc:
        print(f"[cleanup_frontmatter] ERROR: {exc}", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(f"[cleanup_frontmatter] VERIFY FAILED: {exc}", file=sys.stderr)
        return 2

    _print_report(stats, args.review)

    if args.dry_run:
        print("[*] Dry run: cache untouched, no Hugging Face sync.")
        return 0
    removed = stats["matched_frontmatter"]
    if removed > 0 and not (args.no_sync or args.offline):
        sync_cache_artifacts(
            cache_path=args.source,
            commit_message="Remove front-matter (venue index / proceedings cover) records",
        )
    elif removed > 0:
        print(
            "[*] --no-sync/--offline set; skipped Hugging Face push. "
            "Local cache has been updated; remember to sync before any other "
            "workflow touches HF."
        )
    print(f"[cleanup_frontmatter] done, removed {removed} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())

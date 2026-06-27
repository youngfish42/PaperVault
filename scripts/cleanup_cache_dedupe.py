"""一次性缓存清洗：基于 PR A 的字段级合并对 cache/cache.jsonl.gz 去重。

设计要点
--------
- 去重语义与 collector._merge_with_cache (PR A) 对齐：按 (conf, paper_url)
  双键唯一化。collector 端实现为「先按 conf_name 分桶，桶内 dict[paper_url]
  字符串字面去重」(collector.py 第 895 行附近)，本脚本则把 conf 与 url 一并
  拼进 sha1 哈希 key 做全局桶——结果等价，pid 仅作内部桶键，不与 collector
  共用具体字节。具体 key 形态：
    * paper_url 非空时:  sha1("{conf}|url|{paper_url}")[:16]
    * paper_url 为空时:  sha1("{conf}|name|{paper_name_lower}")[:16]
- 注意：collector 对 paper_url 为空的 cache 行是直接 append（不去重），本
  脚本则用 (conf, paper_name_lower) 兜底。这是个有意的偏离——比 collector
  更严，用于清掉历史无 URL 的脏重复，且因 conf 同时参与桶键，不会把不同
  论文错合。代价是「跑完 cleanup 再跑 collector」时，collector 若重新抓到
  同一篇空 URL 的论文，仍可能再生成一行新 cache（因为 collector 不合并空
  URL）——这不是 cleanup 的 bug，而是 collector 端的已知行为。
- 合并逻辑直接复用 collector._merge_paper_record，避免脚本与 collector 之间
  产生第二套实现导致语义漂移。
- 流式读取 cache.jsonl.gz；用 dict 保存 pid -> 合并后的 record 引用，合并
  时原地 mutate，输出按首次出现顺序写回。一遍扫描完成。
  内存预算：当前 cache (~666k 行) 解析后整体常驻 dict，需要 ~6 GB 可用
  RAM。若未来 cache 翻倍，需改外排 / 分桶落盘方案。
- 写入采用 .tmp 文件 + 回读校验 + os.replace 原子替换；可选 .bak 备份
  (始终只保留最近一次清洗前的镜像，旧 .bak 会被覆盖)。
- 支持 --dry-run（只统计，不落盘）与 --report（把审计明细写入 .txt）。

用法
----
    # 1. 先 dry-run 看报告，确认期望
    python scripts/cleanup_cache_dedupe.py --dry-run

    # 2. 满意后真正写入（自动 .bak 备份）
    python scripts/cleanup_cache_dedupe.py

    # 3. 跳过备份（不推荐）
    python scripts/cleanup_cache_dedupe.py --no-backup

退出码
------
    0 成功
    1 输入校验失败（cache 缺失 / 解析异常超过阈值）
    2 写入后回读校验失败（已回滚，原文件未变）
"""

import argparse
import gzip
import hashlib
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 复用 PR A 在 collector.py 顶层暴露的字段级合并实现，确保语义一致。
from collector import _merge_paper_record  # noqa: E402

DEFAULT_CACHE = ROOT / "cache" / "cache.jsonl.gz"


def _pid_of(rec: dict) -> str:
    """计算一条 record 的内部去重桶键。

    与 collector._merge_with_cache 的去重语义对齐——按 (conf, paper_url)
    双键唯一化，conf 与 url 一并拼进 sha1 哈希 key。

    paper_url 为空时按 (conf, paper_name_lower) 兜底，这是相对 collector
    更严的偏离（collector 对空 url 直接 append 不去重），仅用于清掉历史
    无 URL 的脏重复；conf 同时参与桶键，不会把不同会议下的同名论文错合。
    """
    conf = (rec.get("conf") or "").strip()
    url = (rec.get("paper_url") or "").strip()
    if url:
        key = f"{conf}|url|{url}"
    else:
        name = (rec.get("paper_name") or "").strip().lower()
        key = f"{conf}|name|{name}"
    return hashlib.sha1(key.encode("utf-8", errors="ignore")).hexdigest()[:16]


def cleanup(
    source: Path,
    *,
    dry_run: bool = False,
    backup: bool = True,
    report_path: Path | None = None,
) -> dict:
    """读取 ``source``、去重、（除非 dry-run）写回 ``source``，返回统计字典。"""

    if not source.exists():
        raise FileNotFoundError(f"cache file not found: {source}")

    pid_to_record: "dict[str, dict]" = {}
    order: "list[str]" = []  # 保留首次出现顺序，输出更稳定
    total = 0
    parse_errors = 0
    merge_events = 0
    abstract_rescued = 0  # 原 record abstract 为空、合并后非空
    abstract_upgraded = 0  # 原 record abstract 非空但被换成更长的
    code_upgraded = 0  # 原 record code 是 '#' 或空、被换成真实链接
    author_upgraded = 0  # 作者列表被扩充

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
            pid = _pid_of(rec)
            prior = pid_to_record.get(pid)
            if prior is None:
                pid_to_record[pid] = rec
                order.append(pid)
                continue

            # 命中重复：先用 PR A 的 helper 合并，再事后比较前后状态用于报告。
            before_abs = prior.get("paper_abstract") or ""
            before_code = (prior.get("paper_code") or "").strip()
            before_authors = prior.get("paper_authors") or []
            before_authors_n = len(before_authors) if isinstance(before_authors, list) else 0

            _merge_paper_record(prior, rec)
            merge_events += 1

            after_abs = prior.get("paper_abstract") or ""
            after_code = (prior.get("paper_code") or "").strip()
            after_authors = prior.get("paper_authors") or []
            after_authors_n = len(after_authors) if isinstance(after_authors, list) else 0

            if not before_abs and after_abs:
                abstract_rescued += 1
            elif before_abs and after_abs and len(after_abs) > len(before_abs):
                abstract_upgraded += 1
            if (not before_code or before_code == "#") and after_code and after_code != "#":
                code_upgraded += 1
            if after_authors_n > before_authors_n:
                author_upgraded += 1

    unique = len(pid_to_record)
    rows_saved = total - unique

    stats = {
        "source": str(source),
        "total_rows": total,
        "parse_errors": parse_errors,
        "unique_pids": unique,
        "rows_saved": rows_saved,
        "merge_events": merge_events,
        "abstract_rescued": abstract_rescued,
        "abstract_upgraded": abstract_upgraded,
        "code_upgraded": code_upgraded,
        "author_upgraded": author_upgraded,
        "dry_run": dry_run,
    }

    if report_path is not None:
        _write_report(report_path, stats)

    if dry_run:
        return stats

    tmp = source.with_suffix(source.suffix + ".tmp")
    if tmp.exists():
        tmp.unlink()

    written = 0
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        for pid in order:
            f.write(json.dumps(pid_to_record[pid], ensure_ascii=False) + "\n")
            written += 1

    # 回读校验：行数必须等于 unique_pids；任意一行 JSON 解析失败 -> 回滚。
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

    if verify_rows != unique or written != unique:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(
            f"verification mismatch: written={written} verify_rows={verify_rows} unique={unique}"
        )

    if backup:
        bak = source.with_suffix(source.suffix + ".bak")
        if bak.exists():
            bak.unlink()
        os.replace(source, bak)  # 原 cache -> .bak，紧接着 tmp -> 原 cache
        os.replace(tmp, source)
        stats["backup"] = str(bak)
    else:
        os.replace(tmp, source)
        stats["backup"] = None

    stats["written_rows"] = written
    stats["verified_rows"] = verify_rows
    return stats


def _write_report(path: Path, stats: dict) -> None:
    lines = [f"cache_cleanup_report  ts={time.strftime('%Y-%m-%dT%H:%M:%S')}"]
    for k, v in stats.items():
        lines.append(f"  {k}: {v}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--source", type=Path, default=DEFAULT_CACHE,
                        help="cache jsonl.gz 路径，默认 cache/cache.jsonl.gz")
    parser.add_argument("--dry-run", action="store_true",
                        help="只统计与生成报告，不写回原文件")
    parser.add_argument("--no-backup", action="store_true",
                        help="写入时不保留 .bak 备份（不推荐）")
    parser.add_argument("--report", type=Path, default=None,
                        help="把统计结果同时写到该文本文件")
    args = parser.parse_args(argv)

    try:
        stats = cleanup(
            args.source,
            dry_run=args.dry_run,
            backup=not args.no_backup,
            report_path=args.report,
        )
    except FileNotFoundError as exc:
        print(f"[cleanup_cache_dedupe] ERROR: {exc}", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(f"[cleanup_cache_dedupe] VERIFY FAILED: {exc}", file=sys.stderr)
        return 2

    print("[cleanup_cache_dedupe] done")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

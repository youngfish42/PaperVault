"""
针对 OpenReview 渠道（ICLR / NeurIPS 等）的论文，在本机批量回填缺失的 abstract。

不同于通用的 scripts/fetch_abstracts.py（依赖 Crossref/S2/arXiv/OpenAlex），
本脚本走 OpenReview 自身的 API（v2 优先，v1 兜底），命中率最高、字段最权威。

工作流程：
    1. 扫描 cache/cache.jsonl.gz，挑出 paper_url 含 openreview.net 且 abstract 为空、
       且可从 URL 中抽出 forum id 的记录；
    2. 调用 collector._batch_fetch_openreview_abstracts：v2 批量端点
       (api2.openreview.net/notes?ids=A,B,...) 每批最多 N 个；空的再走 v1 兜底；
    3. 增量写回 cache.jsonl.gz（原子替换，先写 tmp 再 rename），并产出统计/进度文件。

用法：
    # 全量回填（默认）
    python scripts/fetch_openreview_abstracts.py

    # 只跑指定会议年份
    python scripts/fetch_openreview_abstracts.py --conf ICLR2024
    python scripts/fetch_openreview_abstracts.py --year 2024 2025

    # 限制本次处理论文数
    python scripts/fetch_openreview_abstracts.py --limit 500

    # 调小批量大小（默认 100，OpenReview v2 接受较大但 URL 长度有限）
    python scripts/fetch_openreview_abstracts.py --chunk-size 50

    # 演练（不写文件）
    python scripts/fetch_openreview_abstracts.py --dry-run
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 复用 collector.py 中已经实现的 OpenReview v2→v1 批量回填逻辑
from collector import (  # noqa: E402
    _batch_fetch_openreview_abstracts,
    _extract_forum_id,
)
from data_artifacts import ensure_cache_local, sync_cache_artifacts  # noqa: E402

# tqdm 进度条；若环境缺失则降级为简单打印
try:
    from tqdm import tqdm  # type: ignore
    _HAS_TQDM = True
except Exception:
    _HAS_TQDM = False

    class tqdm:  # type: ignore
        """tqdm 缺失时的最简 fallback：仅在每次 update 时打印一行。"""
        def __init__(self, total=None, desc="", unit="it", **kwargs):
            self.total = total or 0
            self.n = 0
            self.desc = desc
            self.unit = unit
            self._postfix = ""

        def update(self, n=1):
            self.n += n
            print(f"[{self.desc}] {self.n}/{self.total} {self._postfix}")

        def set_postfix(self, **kwargs):
            self._postfix = " ".join(f"{k}={v}" for k, v in kwargs.items())

        @staticmethod
        def write(msg):
            print(msg)

        def close(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.close()

# Windows 控制台 UTF-8 修复（与 fetch_abstracts.py 保持一致）
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

CACHE_DIR = ROOT / "cache"
CACHE_FILE = CACHE_DIR / "cache.jsonl.gz"
BACKUP_FILE = CACHE_DIR / "cache.jsonl.gz.openreview.bak"
PROGRESS_FILE = CACHE_DIR / "openreview_backfill_progress.json"


# ---------- I/O ----------

def load_cache(path: Path) -> List[dict]:
    """读取 gz 缓存为内存列表，保留顺序（写回需要原顺序以减少 diff）。"""
    if not path.exists():
        raise FileNotFoundError(f"Cache file not found: {path}")
    records: List[dict] = []
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line_num, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise ValueError(f"Malformed JSON on line {line_num}: {e}")
    return records


def save_cache_atomic(path: Path, records: List[dict]) -> None:
    """原子写回：写到临时 .tmp 后 rename，避免中途崩溃损坏原文件。"""
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(tmp_path, "wt", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    os.replace(tmp_path, path)


def load_progress() -> dict:
    if not PROGRESS_FILE.exists():
        return {"filled": [], "tried": []}
    try:
        with PROGRESS_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("filled", [])
        data.setdefault("tried", [])
        return data
    except Exception:
        return {"filled": [], "tried": []}


def save_progress(filled: set, tried: set) -> None:
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with PROGRESS_FILE.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "version": 1,
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "filled": sorted(filled),
                "tried": sorted(tried),
            },
            f,
            ensure_ascii=False,
            indent=2,
        )


# ---------- 任务集筛选 ----------

_YEAR_RE = re.compile(r"(\d{4})$")


def conf_year(conf_name: str) -> int:
    """从 'ICLR2024' 之类的 conf 名提取年份；不可解析返回 0。"""
    if not conf_name:
        return 0
    m = _YEAR_RE.search(conf_name)
    return int(m.group(1)) if m else 0


def build_task(records: List[dict], *, conf_filter, year_filter, retry_failed: bool,
               progress: dict) -> List[Tuple[int, str]]:
    """从全量 records 中挑出待回填条目，返回 [(record_index, forum_id), ...]。"""
    filled_set = set(progress.get("filled", []))
    tried_set = set(progress.get("tried", []))

    tasks: List[Tuple[int, str]] = []
    for idx, rec in enumerate(records):
        url = (rec.get("paper_url") or "")
        if "openreview.net" not in url:
            continue
        abstract = (rec.get("paper_abstract") or "").strip()
        if abstract:
            continue
        conf_name = rec.get("conf") or ""
        if conf_filter and conf_name not in conf_filter:
            continue
        if year_filter and conf_year(conf_name) not in year_filter:
            continue
        fid = _extract_forum_id(url)
        if not fid:
            # group?id= 等无 forum id 的条目，无法本机回填
            continue
        if fid in filled_set:
            # 上次已成功过（理论上 abstract 应非空，此处兜底跳过）
            continue
        if not retry_failed and fid in tried_set:
            continue
        tasks.append((idx, fid))
    return tasks


# ---------- 主流程 ----------

def run(args) -> int:
    # Pull the latest cache from Hugging Face before touching it locally; this
    # is necessary because another workflow (e.g. collect_papers) may have
    # pushed a newer cache while this script was queued.
    ensure_cache_local(CACHE_FILE, refresh=True)
    if not CACHE_FILE.exists():
        print(f"[!] Cache file not found: {CACHE_FILE}")
        return 1

    print(f"[*] Loading cache: {CACHE_FILE}")
    records = load_cache(CACHE_FILE)
    print(f"    Loaded {len(records)} records")

    progress = load_progress()
    conf_filter = set(args.conf) if args.conf else None
    year_filter = set(args.year) if args.year else None

    tasks = build_task(
        records,
        conf_filter=conf_filter,
        year_filter=year_filter,
        retry_failed=args.retry_failed,
        progress=progress,
    )

    # 任务分布概览
    per_conf = Counter(records[i].get("conf", "?") for i, _ in tasks)
    print(f"[*] Pending OpenReview papers: {len(tasks)}")
    for conf_name, cnt in sorted(per_conf.items()):
        print(f"      - {conf_name}: {cnt}")

    if args.limit and len(tasks) > args.limit:
        print(f"[*] --limit {args.limit} applied; truncating tasks")
        tasks = tasks[: args.limit]

    if args.dry_run:
        print("[*] --dry-run: no requests sent, no file written.")
        # 仅在 dry-run 中展示前若干条 forum_id 样本，便于核对
        for i, fid in tasks[:10]:
            print(f"      sample: idx={i}  conf={records[i].get('conf')}  forum_id={fid}")
        return 0

    if not tasks:
        print("[*] Nothing to do.")
        return 0

    # 备份原文件（仅在第一次或文件不存在时；用户保留多次运行也只保留首个原版）
    if not BACKUP_FILE.exists():
        print(f"[*] Backing up original cache to {BACKUP_FILE}")
        import shutil
        shutil.copy2(CACHE_FILE, BACKUP_FILE)

    filled_set = set(progress.get("filled", []))
    tried_set = set(progress.get("tried", []))

    # 分批处理：每个 chunk 调一次 collector._batch_fetch_openreview_abstracts，
    # 拿回后立即写回 records（内存）；累计每达到 flush_every 个成功就 flush 一次磁盘。
    chunk_size = max(1, args.chunk_size)
    flush_every = max(1, args.flush_every)

    total_filled = 0
    total_tried = 0
    pending_writeback = 0
    t0 = time.time()

    if not _HAS_TQDM:
        print("[!] tqdm not installed; falling back to plain text progress. "
              "Install with: pip install tqdm")

    pbar = tqdm(
        total=len(tasks),
        desc="OpenReview",
        unit="paper",
        dynamic_ncols=True,
        mininterval=0.5,
        smoothing=0.1,
    )

    def _refresh_postfix():
        hit = (total_filled / total_tried * 100.0) if total_tried else 0.0
        elapsed = time.time() - t0
        rate = total_tried / elapsed if elapsed > 0 else 0.0
        pbar.set_postfix(
            filled=total_filled,
            tried=total_tried,
            hit=f"{hit:.1f}%",
            rate=f"{rate:.1f}/s",
            pending=pending_writeback,
        )

    try:
        for start in range(0, len(tasks), chunk_size):
            chunk = tasks[start : start + chunk_size]
            fids = [fid for _, fid in chunk]
            try:
                abs_map = _batch_fetch_openreview_abstracts(fids)
            except Exception as e:
                tqdm.write(f"[!] Chunk {start}-{start+len(chunk)} failed: {e}")
                tried_set.update(fids)
                total_tried += len(fids)
                pbar.update(len(chunk))
                _refresh_postfix()
                continue

            chunk_filled = 0
            for idx, fid in chunk:
                tried_set.add(fid)
                abs_ = (abs_map.get(fid) or "").strip()
                if abs_:
                    records[idx]["paper_abstract"] = abs_
                    filled_set.add(fid)
                    chunk_filled += 1
            total_filled += chunk_filled
            total_tried += len(chunk)
            pending_writeback += chunk_filled
            pbar.update(len(chunk))
            _refresh_postfix()

            # 增量落盘
            if pending_writeback >= flush_every:
                save_cache_atomic(CACHE_FILE, records)
                save_progress(filled_set, tried_set)
                tqdm.write(f"    [*] Flushed cache (filled+={pending_writeback}, "
                           f"total filled={total_filled})")
                pending_writeback = 0
                _refresh_postfix()

            # 全局节流（chunk 内部 collector 已带 0.5s 节流，这里只做兜底）
            if args.sleep > 0:
                time.sleep(args.sleep)
    except KeyboardInterrupt:
        tqdm.write("\n[!] KeyboardInterrupt detected; flushing in-memory progress...")
        raise
    finally:
        # 收尾刷一次（任何退出路径都会落盘，避免内存增量丢失）
        if pending_writeback > 0 or args.always_flush:
            save_cache_atomic(CACHE_FILE, records)
            tqdm.write(f"    [*] Final flush (filled+={pending_writeback}, "
                       f"total filled={total_filled})")
        save_progress(filled_set, tried_set)
        pbar.close()

    elapsed = time.time() - t0
    print(
        f"\n[*] Done. Filled {total_filled}/{total_tried} abstracts in {elapsed:.0f}s "
        f"(cache: {CACHE_FILE})"
    )
    if total_filled < total_tried:
        miss = total_tried - total_filled
        print(
            f"    {miss} paper(s) still missing abstract on OpenReview "
            f"(可能是被作者删除或仅 PDF 可见，可后续走 scripts/fetch_abstracts.py)"
        )

    # Push the updated cache to Hugging Face so other workflows / the live app
    # see the new abstracts. parent_commit optimistic locking inside
    # data_artifacts will reject the push if another writer landed first.
    if total_filled > 0 and not args.dry_run:
        try:
            sync_cache_artifacts(
                cache_path=CACHE_FILE,
                commit_message=f"Backfill OpenReview abstracts (+{total_filled})",
            )
        except Exception as exc:
            print(f"[!] sync_cache_artifacts failed (non-fatal): {exc}")
    return 0


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--conf", nargs="*", default=None,
                   help="只处理这些 conf 名（如 ICLR2024 NIPS2023）")
    p.add_argument("--year", nargs="*", type=int, default=None,
                   help="只处理这些年份（如 --year 2024 2025）")
    p.add_argument("--limit", type=int, default=0,
                   help="本次最多处理多少条（0 表示不限）")
    p.add_argument("--chunk-size", type=int, default=100,
                   help="每次 OpenReview v2 批量请求的 id 数（默认 100）")
    p.add_argument("--flush-every", type=int, default=500,
                   help="累计回填多少条后落盘一次（默认 500）")
    p.add_argument("--sleep", type=float, default=0.0,
                   help="每个 chunk 之间额外 sleep 秒数（默认 0；底层已有 0.5s 节流）")
    p.add_argument("--dry-run", action="store_true",
                   help="只打印任务集，不发起请求、不写文件")
    p.add_argument("--retry-failed", action="store_true",
                   help="重试之前 tried 但未拿到 abstract 的 forum id")
    p.add_argument("--always-flush", action="store_true",
                   help="即使本轮无新增，也写一次 cache（默认仅在有新增时写）")
    return p.parse_args(argv)


if __name__ == "__main__":
    sys.exit(run(parse_args()))

"""
针对 CVF Open Access 渠道（CVPR / ICCV / WACV 等）的论文，在本机批量回填缺失的 abstract。

不同于通用的 scripts/fetch_abstracts.py（依赖 Crossref/S2/arXiv/OpenAlex 且强依赖 DOI），
本脚本直接抓取 https://openaccess.thecvf.com/content/.../*_paper.html 详情页里
`<div id="abstract">` 段的纯文本，命中率极高。

工作流程：
    1. `ensure_cache_local(refresh=True)` 从 Hugging Face 拉最新 cache.jsonl.gz；
    2. 扫描 cache 里所有 paper_url 指向 openaccess.thecvf.com 且 abstract 为空的记录；
    3. 使用 ThreadPoolExecutor 并发抓取详情页；复用 collector.search_abs_from_thecvf
       作为主选择器，未命中时回退到 <meta name="description"> / og:description；
    4. 增量写回 cache.jsonl.gz（.tmp + os.replace 原子替换），并维护独立进度文件；
    5. 结束时 `sync_cache_artifacts` 推 HF（可 --no-sync 关闭）。

用法：
    # 全量回填（默认）
    python scripts/fetch_cvf_abstracts.py

    # 只跑指定会议
    python scripts/fetch_cvf_abstracts.py --conf CVPR2024
    python scripts/fetch_cvf_abstracts.py --conf CVPR2024 ICCV2023

    # 只跑指定年份
    python scripts/fetch_cvf_abstracts.py --year 2023 2024

    # 限制本次处理论文数（小样本试跑）
    python scripts/fetch_cvf_abstracts.py --conf CVPR2024 --limit 20 --no-sync

    # 演练（不请求、不写文件）
    python scripts/fetch_cvf_abstracts.py --dry-run

    # 重试之前 tried 但未拿到 abstract 的 URL
    python scripts/fetch_cvf_abstracts.py --retry-failed
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# The three CVF scraping primitives live in a dedicated side-effect-free
# helper module so that `collector.py` can import them without inheriting
# this file's CLI-only side effects (sys.path mutation, stdout reconfigure,
# tqdm, argparse, ...). We keep re-exporting them from this module for
# backward compatibility with any external caller.
from scripts.cvf_abstract import (  # noqa: E402
    _clean_abstract,
    fetch_cvf_abstract,
    is_cvf_paper_url,
)
from data_artifacts import (  # noqa: E402
    ensure_cache_local,
    ensure_progress_local,
    sync_cache_artifacts,
    upload_to_huggingface,
)

try:
    from tqdm import tqdm  # type: ignore
    _HAS_TQDM = True
except Exception:
    _HAS_TQDM = False

    class tqdm:  # type: ignore
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


if sys.platform == "win32":
    try:
        # 优先用 reconfigure（3.7+），保留原 stream 对象，避免旧包装对象 GC 时
        # 把底层 buffer 关掉导致后续所有 print 静默丢失。
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


CACHE_DIR = ROOT / "cache"
CACHE_FILE = CACHE_DIR / "cache.jsonl.gz"
BACKUP_FILE = CACHE_DIR / "cache.jsonl.gz.cvf.bak"
PROGRESS_FILE = CACHE_DIR / "cvf_backfill_progress.json"


def load_cache(path: Path) -> List[dict]:
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
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(tmp_path, "wt", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    os.replace(tmp_path, path)


def load_progress() -> dict:
    if not PROGRESS_FILE.exists():
        return {"filled": [], "tried": [], "failed": {}}
    try:
        with PROGRESS_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("filled", [])
        data.setdefault("tried", [])
        data.setdefault("failed", {})
        return data
    except Exception:
        return {"filled": [], "tried": [], "failed": {}}


def save_progress(filled: Set[str], tried: Set[str], failed: Dict[str, int]) -> None:
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = PROGRESS_FILE.with_suffix(PROGRESS_FILE.suffix + ".tmp")
    payload = {
        "version": 1,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "filled": sorted(filled),
        "tried": sorted(tried),
        "failed": failed,
    }
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PROGRESS_FILE)


_YEAR_RE = re.compile(r"(\d{4})$")


def conf_year(conf_name: str) -> int:
    if not conf_name:
        return 0
    m = _YEAR_RE.search(conf_name)
    return int(m.group(1)) if m else 0


def build_task(
    records: List[dict],
    *,
    conf_filter: Optional[Set[str]],
    year_filter: Optional[Set[int]],
    retry_failed: bool,
    progress: dict,
) -> List[Tuple[int, str]]:
    filled_set = set(progress.get("filled", []))
    tried_set = set(progress.get("tried", []))

    tasks: List[Tuple[int, str]] = []
    for idx, rec in enumerate(records):
        url = (rec.get("paper_url") or "").strip()
        if not is_cvf_paper_url(url):
            continue
        if (rec.get("paper_abstract") or "").strip():
            continue
        conf_name = rec.get("conf") or ""
        if conf_filter and conf_name not in conf_filter:
            continue
        if year_filter and conf_year(conf_name) not in year_filter:
            continue
        if url in filled_set:
            continue
        if not retry_failed and url in tried_set:
            continue
        tasks.append((idx, url))
    return tasks


def run(args) -> int:
    if not args.offline:
        ensure_cache_local(CACHE_FILE, refresh=True)
        # Best-effort pull the CVF progress ledger from Hugging Face so that
        # `filled`/`tried`/`failed` sets survive across workflow runs. A missing
        # remote copy (first ever run) is fine -- ensure_progress_local swallows
        # 404s and we start with an empty local file.
        try:
            ensure_progress_local(PROGRESS_FILE, refresh=True)
        except Exception as exc:
            print(f"[!] cvf progress pull skipped: {exc}")
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

    per_conf: Dict[str, int] = {}
    for i, _ in tasks:
        c = records[i].get("conf", "?")
        per_conf[c] = per_conf.get(c, 0) + 1
    print(f"[*] Pending CVF papers: {len(tasks)}")
    for conf_name, cnt in sorted(per_conf.items()):
        print(f"      - {conf_name}: {cnt}")

    if args.limit and len(tasks) > args.limit:
        print(f"[*] --limit {args.limit} applied; truncating tasks")
        tasks = tasks[: args.limit]

    if args.dry_run:
        print("[*] --dry-run: no requests sent, no file written.")
        for i, url in tasks[:10]:
            print(f"      sample: idx={i}  conf={records[i].get('conf')}  url={url}")
        return 0

    if not tasks:
        print("[*] Nothing to do.")
        return 0

    if not BACKUP_FILE.exists():
        print(f"[*] Backing up original cache to {BACKUP_FILE}")
        import shutil
        shutil.copy2(CACHE_FILE, BACKUP_FILE)

    filled_set: Set[str] = set(progress.get("filled", []))
    tried_set: Set[str] = set(progress.get("tried", []))
    failed_map: Dict[str, int] = dict(progress.get("failed", {}))

    concurrency = max(1, args.concurrency)
    chunk_size = max(concurrency, args.chunk_size)
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
        desc="CVF",
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

    def _one(idx_url: Tuple[int, str]) -> Tuple[int, str, Optional[str], Optional[str]]:
        idx, url = idx_url
        try:
            abs_ = fetch_cvf_abstract(url)
            return idx, url, abs_, None
        except Exception as e:
            return idx, url, None, str(e)

    soft_budget = args.soft_timeout if args.soft_timeout and args.soft_timeout > 0 else None
    if soft_budget is not None:
        print(f"[*] Soft timeout: {soft_budget:.0f}s ({soft_budget/60:.1f}min)")

    soft_timeout_hit = False

    try:
        for start in range(0, len(tasks), chunk_size):
            chunk = tasks[start : start + chunk_size]
            with ThreadPoolExecutor(max_workers=concurrency) as ex:
                futures = [ex.submit(_one, t) for t in chunk]
                for fut in as_completed(futures):
                    idx, url, abs_, err = fut.result()
                    tried_set.add(url)
                    total_tried += 1
                    if abs_ and abs_.strip():
                        records[idx]["paper_abstract"] = abs_.strip()
                        filled_set.add(url)
                        failed_map.pop(url, None)
                        total_filled += 1
                        pending_writeback += 1
                    else:
                        failed_map[url] = failed_map.get(url, 0) + 1
                        if err:
                            tqdm.write(f"    [!] {url} -> {err}")
                    pbar.update(1)
                    _refresh_postfix()

            if pending_writeback >= flush_every:
                save_cache_atomic(CACHE_FILE, records)
                save_progress(filled_set, tried_set, failed_map)
                tqdm.write(
                    f"    [*] Flushed cache (filled+={pending_writeback}, "
                    f"total filled={total_filled})"
                )
                pending_writeback = 0
                _refresh_postfix()

            # Soft-timeout check is placed at chunk boundary so that in-flight
            # futures always finish and are properly counted; the final flush
            # in the `finally:` block then persists everything we have.
            if soft_budget is not None and (time.time() - t0) >= soft_budget:
                remaining = max(0, len(tasks) - (start + len(chunk)))
                tqdm.write(
                    f"[!] Soft timeout ({soft_budget:.0f}s) reached; "
                    f"stopping gracefully with {remaining} task(s) left for next run."
                )
                soft_timeout_hit = True
                break

            if args.sleep > 0:
                time.sleep(args.sleep)
    except KeyboardInterrupt:
        tqdm.write("\n[!] KeyboardInterrupt detected; flushing in-memory progress...")
        raise
    finally:
        if pending_writeback > 0 or args.always_flush:
            save_cache_atomic(CACHE_FILE, records)
            tqdm.write(
                f"    [*] Final flush (filled+={pending_writeback}, "
                f"total filled={total_filled})"
            )
        save_progress(filled_set, tried_set, failed_map)
        pbar.close()

    elapsed = time.time() - t0
    if soft_timeout_hit:
        print(
            f"\n[*] Soft-timeout exit. Filled {total_filled}/{total_tried} abstracts "
            f"in {elapsed:.0f}s (cache: {CACHE_FILE}). Remaining tasks will be "
            f"picked up on the next run."
        )
    else:
        print(
            f"\n[*] Done. Filled {total_filled}/{total_tried} abstracts in {elapsed:.0f}s "
            f"(cache: {CACHE_FILE})"
        )
    if total_filled < total_tried:
        miss = total_tried - total_filled
        print(
            f"    {miss} paper(s) still missing abstract "
            f"(网络抖动/页面结构变化，可稍后 --retry-failed 再跑一次)"
        )

    sync_failed = False
    if not args.no_sync and not args.offline:
        # We push the CVF progress ledger even when total_filled == 0 so that
        # freshly-recorded `tried`/`failed` counts don't get lost across runs.
        # cache.jsonl.gz is only pushed when new abstracts landed to avoid
        # wasting HF bandwidth on no-op ghost commits.
        if total_filled > 0:
            try:
                sync_cache_artifacts(
                    cache_path=CACHE_FILE,
                    commit_message=f"Backfill CVF abstracts (+{total_filled})",
                    progress_path=None,
                )
            except Exception as exc:
                sync_failed = True
                print(f"[!] sync_cache_artifacts failed: {exc}")
                print(
                    "    Local cache has been updated but Hugging Face was NOT "
                    "synced. Re-run sync_cache_artifacts manually before "
                    "launching any other workflow that touches the cache."
                )
        # Always try to push the CVF progress ledger (even on zero-fill runs)
        # so tried/failed counters survive across GitHub Actions runs.
        # If cache sync failed after filling abstracts, skip pushing progress to
        # avoid marking URLs as "filled" remotely when the cache update didn't land.
        try:
            if (total_filled == 0 or not sync_failed) and PROGRESS_FILE.exists():
                upload_to_huggingface(
                    [PROGRESS_FILE],
                    commit_message=(
                        f"Update CVF backfill progress (filled+={total_filled}, "
                        f"tried+={total_tried})"
                    ),
                )
        except Exception as exc:
            print(f"[!] cvf progress push skipped: {exc}")
    return 1 if sync_failed else 0


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--conf", nargs="*", default=None,
                   help="只处理这些 conf 名（如 CVPR2024 ICCV2023）")
    p.add_argument("--year", nargs="*", type=int, default=None,
                   help="只处理这些年份（如 --year 2023 2024）")
    p.add_argument("--limit", type=int, default=0,
                   help="本次最多处理多少条（0 表示不限）")
    p.add_argument("--concurrency", type=int, default=4,
                   help="并发线程数（默认 4，CVF 是静态 Apache 可支持更高）")
    p.add_argument("--chunk-size", type=int, default=64,
                   help="每批多少条落盘一次（默认 64；会被 concurrency 抬到不低于其值）")
    p.add_argument("--flush-every", type=int, default=500,
                   help="累计回填多少条后落盘一次（默认 500）")
    p.add_argument("--sleep", type=float, default=0.3,
                   help="每个 chunk 之间额外 sleep 秒数（默认 0.3）")
    p.add_argument("--dry-run", action="store_true",
                   help="只打印任务集，不发起请求、不写文件")
    p.add_argument("--retry-failed", action="store_true",
                   help="重试之前 tried 但未拿到 abstract 的 URL")
    p.add_argument("--always-flush", action="store_true",
                   help="即使本轮无新增，也写一次 cache（默认仅在有新增时写）")
    p.add_argument("--no-sync", action="store_true",
                   help="结束后不推 Hugging Face")
    p.add_argument("--offline", action="store_true",
                   help="跳过 ensure_cache_local 与 sync_cache_artifacts（air-gapped 场景）")
    p.add_argument("--soft-timeout", type=float, default=None,
                   help="秒。到点后在 chunk 边界优雅退出（flush + exit 0），"
                        "供 GitHub Actions 保底不超 timeout-minutes")
    return p.parse_args(argv)


if __name__ == "__main__":
    sys.exit(run(parse_args()))

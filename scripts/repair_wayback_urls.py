"""修复 web.archive.org 包装的死论文链接（AAAI 2000-2015 / UAI 2000-2014 等）。

缓存中约 4.4k 条记录的 ``paper_url`` 形如
``https://web.archive.org/web/<ts>/<原始 URL>``，原始站已死。本脚本用标题
查 OpenAlex（门控与 ``--openalex-title`` 一致：标题 ratio ≥0.85 + 年份 ±1），
把链接改写为 ``https://doi.org/<doi>``（优先）或
``primary_location.landing_page_url``（http/https 且非 wayback/原死链 host）。

顺带回填：OpenAlex 返回摘要（inverted-index 重建后 ≥60 字符）且记录摘要为空时
一并填上（progress source 记 ``openalex_title``）。

冲突处理：``(conf, new_url)`` 已存在时用 ``collector._merge_paper_record``
把 wayback 记录合入既有记录并删除前者；否则原地改写 ``paper_url``。

progress / 审计：
  * 新 URL 复制旧 URL 的进度记录（保持 status）；摘要顺带填上的记 success。
  * REASON_ENUM 没有 ``migrated`` 成员，往 progress 里塞新值会破坏分析器，
    因此迁移痕迹写入独立日志 ``cache/wayback_repair_log.jsonl``（每条一行：
    ts/conf/old_url/new_url/action/abstract_filled），旧 URL 的 progress
    记录保持不动。

默认 ``--dry-run``（只输出报告）；``--write`` 才改缓存。缓存写入走与主链
一致的 ``.tmp + os.replace`` 原子方式；结束在**同一进程内**做带
parent_commit 乐观锁的验证式上传 ``_sync_or_die``（除非
--no-sync/--offline/--dry-run），上传失败会以非零退出。

用法::

    python scripts/repair_wayback_urls.py                      # dry-run 报告
    python scripts/repair_wayback_urls.py --dry-run --max-papers 10
    python scripts/repair_wayback_urls.py --write --no-sync    # 真正改写，本地不同步
"""

import argparse
import gzip
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from collector import _merge_paper_record  # noqa: E402
from data_artifacts import (  # noqa: E402
    ensure_cache_local,
    ensure_progress_local,
    upload_to_huggingface,
)
from scripts.fetch_abstracts import (  # noqa: E402
    CACHE_FILE,
    CONTACT_EMAIL,
    HEADERS,
    OPENALEX_MIN_INTERVAL,
    PROGRESS_FILE,
    _install_utf8_console,
    _openalex_rate_state,
    _reconstruct_openalex_abstract,
    _s2_request,
    _s2_status_reason,
    _title_gate,
    _write_cache_atomic,
    _year_gate,
    clean_abstract,
    load_progress,
    save_progress,
)
from papervault.services.abstract_fetchers.base import MIN_ABSTRACT_CHARS  # noqa: E402

WAYBACK_RE = re.compile(r"web\.archive\.org/web/[^/]+/(https?://.+)$")
WAYBACK_REPAIR_LOG = Path("cache") / "wayback_repair_log.jsonl"

OPENALEX_REPAIR_SELECT = (
    "title,abstract_inverted_index,publication_year,doi,primary_location"
)


def unwrap_wayback(url: str) -> Optional[str]:
    """解包 wayback 包装 URL；不是 wayback 包装或解不出时返回 None。"""
    if not url:
        return None
    m = WAYBACK_RE.search(url)
    return m.group(1) if m else None


def _host_of(url: str) -> str:
    host = (urlparse(url or "").netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _openalex_repair_fetch(title: str) -> Tuple[Optional[dict], str]:
    """GET /works?search=<title>（repair 专用 select，含 doi/primary_location）。

    复用 fetch_abstracts 的限速请求设施：OpenAlex 独立 rate_state（3 rps）、
    普通 HEADERS（不带 S2 key）。返回 (首个候选 work dict, error_reason)。
    """
    params = {"search": title, "per-page": 3, "select": OPENALEX_REPAIR_SELECT}
    if CONTACT_EMAIL:
        params["mailto"] = CONTACT_EMAIL
    # OpenAlex 匿名配额按出口 IP 共享且会耗尽（Retry-After 可达次日）；
    # 有 key 时走 api_key 参数（与 ?mailto 可叠加）。
    api_key = os.getenv("OPENALEX_API_KEY", "").strip()
    if api_key:
        params["api_key"] = api_key
    resp, err = _s2_request(
        "get",
        "https://api.openalex.org/works",
        params=params,
        timeout=30,
        min_interval=OPENALEX_MIN_INTERVAL,
        headers=HEADERS,
        rate_state=_openalex_rate_state,
    )
    if err or resp is None:
        return None, err or "network"
    if resp.status_code != 200:
        return None, _s2_status_reason(resp.status_code)
    try:
        data = resp.json()
    except ValueError:
        return None, "network"
    results = data.get("results") if isinstance(data, dict) else None
    if not results:
        return None, "empty_abstract"
    first = results[0]
    if not isinstance(first, dict):
        return None, "empty_abstract"
    return first, ""


def choose_new_url(work: dict, orig_url: str) -> Optional[str]:
    """新 URL 优先级：doi.org/<doi> > primary_location.landing_page_url
    （须 http/https 且不是 wayback / 原死链 host）> None（不修改）。"""
    doi = (work.get("doi") or "").strip()
    if doi:
        if doi.lower().startswith("https://doi.org/"):
            return doi
        if doi.lower().startswith("http://doi.org/"):
            return "https://doi.org/" + doi[len("http://doi.org/"):]
    landing = (
        (work.get("primary_location") or {}).get("landing_page_url") or ""
    ).strip()
    if not landing.lower().startswith(("http://", "https://")):
        return None
    landing_host = _host_of(landing)
    if landing_host == "web.archive.org":
        return None
    if landing_host and landing_host == _host_of(orig_url):
        return None  # 原站已死，指回去没有意义
    return landing or None


def evaluate_repair(work: Optional[dict], paper: dict) -> Tuple[Optional[str], Optional[str], str]:
    """门控 + 新 URL 选择 + 顺带摘要。

    返回 (new_url, abstract_or_None, reason)。reason 为空串表示通过门控；
    new_url 为 None 表示不改链接（无 DOI/可用 landing page）。
    """
    if not work:
        return None, None, "empty_abstract"
    title = (paper.get("paper_name") or "").strip()
    if not _title_gate(work.get("title") or "", title):
        return None, None, "title_mismatch"
    if not _year_gate(paper.get("conf", ""), work.get("publication_year")):
        return None, None, "title_mismatch"
    new_url = choose_new_url(work, unwrap_wayback(paper.get("paper_url", "")) or paper.get("paper_url", ""))
    abstract = clean_abstract(
        _reconstruct_openalex_abstract(work.get("abstract_inverted_index") or {}) or ""
    )
    if len(abstract) < MIN_ABSTRACT_CHARS:
        abstract = None
    return new_url, abstract, ""


def _repair_pass(
    all_papers: List[dict],
    progress: Dict[str, dict],
    *,
    max_papers: Optional[int],
    dry_run: bool,
    fetch_fn=_openalex_repair_fetch,
) -> dict:
    """核心 pass。dry-run 下不改任何数据。返回统计字典。"""
    index: Dict[Tuple[str, str], dict] = {
        (p.get("conf", ""), p.get("paper_url", "")): p
        for p in all_papers
        if p.get("paper_url")
    }
    wayback = [p for p in all_papers if "web.archive.org/web/" in p.get("paper_url", "")]
    unwrapped_ok = []
    unpack_fail = 0
    for p in wayback:
        if unwrap_wayback(p["paper_url"]):
            unwrapped_ok.append(p)
        else:
            unpack_fail += 1
    targets = unwrapped_ok[:max_papers] if max_papers is not None else unwrapped_ok

    stats = {
        "total_wayback": len(wayback),
        "unpack_failed": unpack_fail,
        "processed": 0,
        "rewrite": 0,
        "merge": 0,
        "unchanged": 0,
        "abstract_filled": 0,
        "orig_hosts": {},
        "new_hosts": {},
        "reasons": {},
        "samples": [],
    }
    log_lines: List[dict] = []
    drop_ids = set()

    for i, paper in enumerate(targets, 1):
        old_url = paper["paper_url"]
        orig = unwrap_wayback(old_url)
        orig_host = _host_of(orig)
        stats["orig_hosts"][orig_host] = stats["orig_hosts"].get(orig_host, 0) + 1
        title = (paper.get("paper_name") or "").strip()
        print(f"[{i}/{len(targets)}] [{paper.get('conf')}] {title[:60]}")
        work, err = fetch_fn(title)
        if not work:
            # 请求级失败（429/网络/无候选）：只记一次 reason，不再进评估门控
            reason = err or "empty_abstract"
            stats["reasons"][reason] = stats["reasons"].get(reason, 0) + 1
            stats["unchanged"] += 1
            continue
        new_url, abstract, reason = evaluate_repair(work, paper)
        if reason:
            stats["reasons"][reason] = stats["reasons"].get(reason, 0) + 1
            stats["unchanged"] += 1
            continue
        if not new_url:
            stats["reasons"]["no_usable_url"] = stats["reasons"].get("no_usable_url", 0) + 1
            stats["unchanged"] += 1
            continue

        stats["new_hosts"][_host_of(new_url)] = stats["new_hosts"].get(_host_of(new_url), 0) + 1
        abstract_filled = bool(abstract) and not (paper.get("paper_abstract") or "").strip()
        existing = index.get((paper.get("conf", ""), new_url))
        action = "merge" if (existing is not None and existing is not paper) else "rewrite"

        if len(stats["samples"]) < 12:
            stats["samples"].append((old_url, new_url, action, abstract_filled))

        stats["processed"] += 1
        stats[action] += 1
        if abstract_filled:
            stats["abstract_filled"] += 1
        if dry_run:
            continue

        # ---- 真正改写 ----
        if abstract_filled:
            paper["paper_abstract"] = abstract
        if action == "merge":
            _merge_paper_record(existing, paper)
            drop_ids.add(id(paper))
        else:
            index.pop((paper.get("conf", ""), old_url), None)
            paper["paper_url"] = new_url
            index[(paper.get("conf", ""), new_url)] = paper

        # progress：新 URL 复制旧记录（保持 status）；摘要顺带填上的记 success
        if abstract_filled:
            progress[new_url] = {
                "status": "success",
                "source": "openalex_title",
                "chars": len(abstract),
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
        elif old_url in progress:
            progress[new_url] = dict(progress[old_url])
        # 旧 URL 的 progress 记录保持不动；迁移痕迹进独立日志（REASON_ENUM
        # 没有 migrated，塞新值会破坏分析器）。
        log_lines.append({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "conf": paper.get("conf", ""),
            "old_url": old_url,
            "new_url": new_url,
            "action": action,
            "abstract_filled": abstract_filled,
        })

    if not dry_run and log_lines:
        # 真正落盘：cache 原子重写 + progress + 迁移日志
        remaining = [p for p in all_papers if id(p) not in drop_ids]
        all_papers[:] = remaining
        _write_cache_atomic(all_papers)
        save_progress(progress)
        WAYBACK_REPAIR_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(WAYBACK_REPAIR_LOG, "a", encoding="utf-8") as f:
            for line in log_lines:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
    return stats


def _print_report(stats: dict, dry_run: bool) -> None:
    mode = "DRY-RUN (no writes)" if dry_run else "WRITE"
    print(f"\n===== wayback repair report [{mode}] =====")
    print(f"wayback records in cache : {stats['total_wayback']}")
    print(f"unpack failed            : {stats['unpack_failed']}")
    print(f"processed this run       : {stats['processed']}")
    print(f"  rewrite (in place)     : {stats['rewrite']}")
    print(f"  merge (dup (conf,url)) : {stats['merge']}")
    print(f"  unchanged              : {stats['unchanged']}")
    print(f"  abstracts backfilled   : {stats['abstract_filled']}")
    print("orig host distribution   :")
    for host, n in sorted(stats["orig_hosts"].items(), key=lambda kv: -kv[1])[:10]:
        print(f"    {host:35} {n}")
    print("new host distribution    :")
    for host, n in sorted(stats["new_hosts"].items(), key=lambda kv: -kv[1])[:10]:
        print(f"    {host:35} {n}")
    if stats["reasons"]:
        print("unchanged reasons        :")
        for r, n in sorted(stats["reasons"].items(), key=lambda kv: -kv[1]):
            print(f"    {r:35} {n}")
    if stats["samples"]:
        print("samples                  :")
        for old, new, action, filled in stats["samples"]:
            mark = " +abstract" if filled else ""
            print(f"    [{action}]{mark}\n      - {old}\n      + {new}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Repair web.archive.org-wrapped dead paper URLs via OpenAlex")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", dest="dry_run", action="store_true", default=True,
                   help="Report only (default)")
    g.add_argument("--write", dest="dry_run", action="store_false",
                   help="Actually rewrite the cache/progress")
    ap.add_argument("--max-papers", "-n", type=int, default=None)
    ap.add_argument("--no-sync", action="store_true", help="Skip the final Hugging Face push")
    ap.add_argument("--offline", action="store_true",
                    help="Skip ensure_cache_local / ensure_progress_local AND the final sync")
    args = ap.parse_args()
    _install_utf8_console()

    t0 = time.time()
    if not args.offline:
        ensure_cache_local(CACHE_FILE, refresh=True)
        ensure_progress_local(PROGRESS_FILE, refresh=True)

    all_papers: List[dict] = []
    with gzip.open(CACHE_FILE, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                all_papers.append(json.loads(line))
    print(f"[*] Total papers in cache: {len(all_papers)}")
    progress = load_progress()

    stats = _repair_pass(
        all_papers, progress,
        max_papers=args.max_papers, dry_run=args.dry_run,
    )
    _print_report(stats, args.dry_run)
    print(f"[*] Done in {time.time() - t0:.0f}s")

    if args.dry_run:
        print("[*] Dry run: no cache/progress writes, no Hugging Face sync.")
        return
    if stats["processed"] > 0 and not (args.no_sync or args.offline):
        _sync_or_die()
    elif stats["processed"] > 0:
        print(
            "[*] --no-sync/--offline set; skipped Hugging Face push. "
            "Local cache has been updated; remember to sync before any other "
            "workflow touches HF."
        )


def _sync_or_die() -> None:
    """Verified in-process upload of cache + progress to Hugging Face.

    Two deliberate deviations from calling ``sync_cache_artifacts`` here or
    from a follow-up ``python -c ...`` CI step (PR #221 review):

    1. **Same process**: ``ensure_cache_local`` at startup registered the
       ``parent_commit`` for the optimistic lock in this process; a separate
       push process would upload WITHOUT any parent_commit and could
       silently clobber a concurrent writer (no 412 retry).
    2. **Verified**: ``sync_cache_artifacts`` prints-and-continues on upload
       failure (by design for the 6h backfill), but here the push *is* the
       deliverable — a silent failure would leave the migration-log PR
       mergeable while HF still serves the unrepaired cache. We therefore
       upload directly and exit non-zero when the cache file is not
       confirmed uploaded.
    """
    targets = [CACHE_FILE]
    progress_included = PROGRESS_FILE.exists()
    if progress_included:
        targets.append(PROGRESS_FILE)
    uploaded = upload_to_huggingface(
        targets,
        commit_message="Repair web.archive.org-wrapped dead paper URLs",
    )
    if "cache/cache.jsonl.gz" not in uploaded:
        print("[!] FATAL: cache.jsonl.gz was NOT confirmed uploaded to Hugging Face.")
        sys.exit(1)
    if progress_included and "cache/abstract_backfill_progress.jsonl.gz" not in uploaded:
        # The progress ledger is event-sourced and re-derived on the next
        # run; a missed upload is worth a loud warning, not a job failure.
        print("[!] WARNING: progress file was not confirmed uploaded (cache is safe).")


if __name__ == "__main__":
    main()

"""审计（可选回滚）wayback 修复写入的数据 —— PR #228 污染事故的后续。

背景：wayback 修复前几轮用宽松门控（包含匹配 + 候选缺年份放行）写入并推
HF 的错误链接（AAAI 2010-2014 → 无关 2024 期刊 DOI、UAI2001 → PeerJ 表格页、
同一 arxiv id 分给两篇不同论文等）。本脚本逐条审计迁移日志
（本地 ``cache/wayback_repair_log.jsonl`` + ``origin/auto-wayback-repair``
分支上 CI 产出的同名日志），拿 new_url 的真实标题与本地记录标题比对。

判定（与收紧后的修复门控同口径）：归一化（去非字母数字、小写）后纯
SequenceMatcher ratio ≥ ``AUDIT_MIN_RATIO``（0.90，**无包含捷径**）→ PASS，
否则 FAIL；new_url host 不是 doi.org / arxiv.org（或远端取不到）→
``unverifiable`` 不联网。同一 new_url 对应多条不同标题的（碰撞组）逐条按
ratio 各自判定。

用法::

    python -u scripts/audit_wayback_repair.py            # 审计报告（默认，不改数据）
    python -u scripts/audit_wayback_repair.py --revert   # 回滚 FAIL 条目（仍默认 dry-run）
    python -u scripts/audit_wayback_repair.py --revert --write   # 真正回滚 + 验证式 HF 上传
"""

import argparse
import gzip
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

# 显式路径：裸 load_dotenv() 在 stdin/嵌入式场景下定位不到 .env（已知 bug）。
load_dotenv(ROOT / ".env")

from scripts.fetch_abstracts import (  # noqa: E402
    CACHE_FILE,
    CONTACT_EMAIL,
    HEADERS,
    PROGRESS_FILE,
    _install_utf8_console,
    _s2_request,
    _write_cache_atomic,
    load_progress,
    save_progress,
)
from scripts.repair_wayback_urls import (  # noqa: E402
    _norm_title,
    _title_ratio,
    _host_of,
    _sync_or_die,
    _crossref_rate_state,
    CROSSREF_MIN_INTERVAL,
    ARXIV_MIN_INTERVAL,
    _arxiv_rate_state,
    WAYBACK_REPAIR_LOG,
)

# 与修复门控同口径：纯 ratio ≥ 0.90，无包含捷径。
AUDIT_MIN_RATIO = 0.90
# 首轮修复推送时间：取早于它的最新 HF commit 作为"修复前"缓存。
PREREPAIR_BEFORE = "2026-10-25T03:30:00Z"
AUDIT_REPORT = ROOT / "docs" / "wayback_repair_audit.md"
ARXIV_BATCH = 100


# ---------- 输入：日志 / 缓存版本 -------------------------------------------------


def load_log_entries(paths: List[Path]) -> List[dict]:
    """读迁移日志（可多份），按 (old_url, new_url) 去重（本地与 CI 日志可能重叠）。"""
    seen = set()
    entries = []
    for path in paths:
        if not path or not Path(path).exists():
            continue
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                key = (rec.get("old_url"), rec.get("new_url"))
                if key in seen:
                    continue
                seen.add(key)
                entries.append(rec)
    return entries


def fetch_ci_log(tmpdir: Path) -> Optional[Path]:
    """取 origin/auto-wayback-repair 分支上的 CI 迁移日志；取不到返回 None。"""
    try:
        subprocess.run(
            ["git", "fetch", "origin", "auto-wayback-repair"],
            cwd=ROOT, check=True, capture_output=True, timeout=120,
        )
        out = subprocess.run(
            ["git", "show", "origin/auto-wayback-repair:cache/wayback_repair_log.jsonl"],
            cwd=ROOT, check=True, capture_output=True, timeout=120,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
        print(f"[!] CI log unavailable ({exc}); auditing local log only.")
        return None
    path = Path(tmpdir) / "wayback_repair_log.ci.jsonl"
    path.write_bytes(out.stdout)
    return path


def load_cache_records(path: Path) -> List[dict]:
    records = []
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def by_conf_url(records: List[dict]) -> Dict[Tuple[str, str], dict]:
    return {
        (p.get("conf", ""), p.get("paper_url", "")): p
        for p in records
        if p.get("paper_url")
    }


def fetch_prerepair_cache(tmpdir: Path) -> Optional[Path]:
    """从 HF 拉修复前版本（早于 PREREPAIR_BEFORE 的最新 commit）的 cache.jsonl.gz。"""
    repo_id = os.getenv("PAPERVAULT_HF_REPO_ID", "").strip()
    token = os.getenv("HF_TOKEN", "").strip()
    if not repo_id:
        print("[!] PAPERVAULT_HF_REPO_ID not set; merge entries cannot be audited "
              "against a pre-repair cache.")
        return None
    from huggingface_hub import hf_hub_download, list_repo_commits

    commits = list_repo_commits(repo_id, repo_type="dataset", token=token or None)
    cutoff = PREREPAIR_BEFORE
    prior = [c for c in commits if c.created_at.strftime("%Y-%m-%dT%H:%M:%SZ") < cutoff]
    if not prior:
        print("[!] No HF commit predates the first repair push; cannot audit merges.")
        return None
    prior.sort(key=lambda c: c.created_at)
    base = prior[-1]
    print(f"[*] Pre-repair cache revision: {base.commit_id[:10]} ({base.created_at})")
    path = hf_hub_download(
        repo_id, "cache/cache.jsonl.gz", repo_type="dataset",
        revision=base.commit_id, token=token or None,
        cache_dir=str(Path(tmpdir) / "hf"),
    )
    return Path(path)


# ---------- 远端真实标题 -----------------------------------------------------------


def _crossref_title(doi_url: str) -> Optional[str]:
    """doi.org/<doi> → Crossref 直查标题（无配额问题）。"""
    doi = doi_url.split("doi.org/", 1)[-1]
    if not doi:
        return None
    # 注意：/works/<doi> 单条路由不支持 select 参数（400 parameter-not-allowed），
    # 只有 /works 列表路由支持——直查必须取完整 message 再挑 title。
    params = {"mailto": CONTACT_EMAIL} if CONTACT_EMAIL else {}
    resp, err = _s2_request(
        "get",
        f"https://api.crossref.org/works/{doi}",
        params=params,
        timeout=30,
        min_interval=CROSSREF_MIN_INTERVAL,
        headers=HEADERS,
        rate_state=_crossref_rate_state,
        max_retries=2,
    )
    if err or resp is None or resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    title = (data.get("message") or {}).get("title")
    if isinstance(title, list) and title:
        return str(title[0]).strip()
    return None


def _arxiv_titles_batch(arxiv_ids: List[str]) -> Dict[str, Optional[str]]:
    """arXiv id_list 批量查标题（≤100/批，批间隔 ARXIV_MIN_INTERVAL 秒）。"""
    import xml.etree.ElementTree as ET

    import requests as _requests

    out: Dict[str, Optional[str]] = {}
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    for start in range(0, len(arxiv_ids), ARXIV_BATCH):
        batch = arxiv_ids[start:start + ARXIV_BATCH]
        url = ("https://export.arxiv.org/api/query?id_list="
               + _requests.utils.quote(",".join(batch))
               # id_list 的 max_results 默认只有 10——不显式放大到批大小，
               # 每批 100 个 id 只回前 10 条（2026-10-26 审计第二轮实测
               # 852 个 id 只解析出 80 条的根因）。
               + f"&max_results={len(batch)}")
        resp, err = _s2_request(
            "get", url, timeout=90,
            min_interval=max(ARXIV_MIN_INTERVAL, 5.0), headers=HEADERS,
            rate_state=_arxiv_rate_state, max_retries=3,
        )
        if err or resp is None or resp.status_code != 200:
            for i in batch:
                out[i] = None
            continue
        try:
            root = ET.fromstring(resp.text)
        except ET.ParseError:
            for i in batch:
                out[i] = None
            continue
        for entry in root.findall("atom:entry", ns):
            id_elem = entry.find("atom:id", ns)
            title_elem = entry.find("atom:title", ns)
            if id_elem is None or not id_elem.text:
                continue
            # 规范化：去掉 "…/abs/" 前缀（保留老式分类前缀 cs/0003013）
            # 再去版本后缀（1301.3857v1 → 1301.3857）
            import re as _re
            eid = _re.sub(r"v\d+$", "", id_elem.text.strip().split("/abs/", 1)[-1])
            out[eid] = (title_elem.text or "").strip() if title_elem is not None else None
        for i in batch:
            out.setdefault(i, None)
    return out


def resolve_remote_titles(
    urls: List[str],
    *,
    crossref_fn=_crossref_title,
    arxiv_batch_fn=_arxiv_titles_batch,
) -> Dict[str, Optional[str]]:
    """doi.org → Crossref 直查；arxiv.org → 批量 API；其它 host 不联网（None）。"""
    result: Dict[str, Optional[str]] = {}
    arxiv_ids = []
    arxiv_urls = {}
    for url in sorted(set(urls)):
        host = _host_of(url)
        if host == "doi.org":
            result[url] = crossref_fn(url)
        elif host == "arxiv.org":
            # 老式 arXiv id 带分类前缀（cs/0003013）——只取末段会把 id 砍成
            # "0003013"，使整批 id_list 查询被 arXiv 400 拒绝。
            arxiv_id = url.split("/abs/", 1)[-1].strip("/")
            arxiv_ids.append(arxiv_id)
            arxiv_urls[arxiv_id] = url
        else:
            result[url] = None
    if arxiv_ids:
        titles = arxiv_batch_fn(arxiv_ids)
        for arxiv_id, url in arxiv_urls.items():
            result[url] = titles.get(arxiv_id)
    return result


# ---------- 审计判定（纯逻辑，可测） -------------------------------------------------


def audit_entries(
    entries: List[dict],
    current_by_key: Dict[Tuple[str, str], dict],
    prerepair_by_key: Optional[Dict[Tuple[str, str], dict]],
    remote_titles: Dict[str, Optional[str]],
) -> List[dict]:
    """逐条审计。返回行列表：{entry, local_title, remote_title, ratio, verdict}。

    * rewrite 条目的本地标题取自**当前缓存** (conf, new_url)；merge 条目取自
      **修复前缓存** (conf, old_url)（日志行可能缺 old_title——评审问题 6 修复
      前的存量行；缺时退而求其次用日志里的 old_title 字段）。
    * host 非 doi.org/arxiv.org 或远端取不到标题 → ``unverifiable``。
    * 纯 ratio（无包含捷径）≥ AUDIT_MIN_RATIO → PASS，否则 FAIL。同一 new_url
      多条不同标题（碰撞组）逐条独立判定，达标保留、不达标 FAIL。
    """
    rows = []
    for entry in entries:
        conf = entry.get("conf", "")
        old_url = entry.get("old_url", "")
        new_url = entry.get("new_url", "")
        action = entry.get("action", "")
        local_title = ""
        if action == "merge":
            rec = (prerepair_by_key or {}).get((conf, old_url))
            if rec is not None:
                local_title = (rec.get("paper_name") or "").strip()
            if not local_title:
                local_title = (entry.get("old_title") or "").strip()
        else:
            rec = current_by_key.get((conf, new_url))
            if rec is not None:
                local_title = (rec.get("paper_name") or "").strip()
        host = _host_of(new_url)
        remote = remote_titles.get(new_url)
        if host not in ("doi.org", "arxiv.org") or not remote:
            verdict = "unverifiable"
            ratio = None
        else:
            ratio = _title_ratio(local_title, remote)
            verdict = "PASS" if ratio >= AUDIT_MIN_RATIO else "FAIL"
        rows.append({
            "entry": entry,
            "local_title": local_title,
            "remote_title": remote,
            "ratio": ratio,
            "verdict": verdict,
        })
    return rows


# ---------- 回滚（纯逻辑，可测） ----------------------------------------------------


def revert_failed(
    failed_rows: List[dict],
    all_papers: List[dict],
    prerepair_by_key: Optional[Dict[Tuple[str, str], dict]],
    progress: Dict[str, dict],
    *,
    dry_run: bool = True,
) -> dict:
    """回滚 FAIL 条目。dry_run=True 时只统计不落任何数据。返回统计字典。"""
    current_by_key = by_conf_url(all_papers)
    stats = {"rewrite_reverted": 0, "merge_reverted": 0, "target_restored": 0,
             "missing_current": 0, "missing_prerepair": 0, "progress_marked": 0}
    for row in failed_rows:
        entry = row["entry"]
        conf = entry.get("conf", "")
        old_url = entry.get("old_url", "")
        new_url = entry.get("new_url", "")
        action = entry.get("action", "")

        if action == "merge":
            prerepair = prerepair_by_key or {}
            orig = prerepair.get((conf, old_url))
            if orig is None:
                stats["missing_prerepair"] += 1
            else:
                stats["merge_reverted"] += 1
                if not dry_run:
                    all_papers.append(json.loads(json.dumps(orig)))  # 深拷贝恢复整条
            # 目标记录：修复前就存在 → 可能被并入了错误摘要/代码，恢复字段
            target_pre = prerepair.get((conf, new_url))
            target_now = current_by_key.get((conf, new_url))
            if target_pre is not None and target_now is not None:
                stats["target_restored"] += 1
                if not dry_run:
                    target_now["paper_abstract"] = target_pre.get("paper_abstract", "")
                    target_now["paper_code"] = target_pre.get("paper_code", "#")
            # 目标在修复前不存在（它是本轮某条 rewrite 创建的）→ 不管，
            # 那条 rewrite 条目自己会按 rewrite 规则处理。
        else:  # rewrite
            rec = current_by_key.get((conf, new_url))
            if rec is None:
                stats["missing_current"] += 1
                continue
            stats["rewrite_reverted"] += 1
            if not dry_run:
                rec["paper_url"] = old_url
                current_by_key.pop((conf, new_url), None)
                current_by_key[(conf, old_url)] = rec
                if entry.get("abstract_filled"):
                    rec["paper_abstract"] = ""  # 那是错误论文的摘要

        # progress：回滚的 new_url 追加 failed/title_mismatch（让后续回填重试），
        # old_url 的既有记录不动。
        stats["progress_marked"] += 1
        if not dry_run:
            old_attempts = progress.get(new_url, {}).get("attempts", 0)
            progress[new_url] = {
                "status": "failed",
                "attempts": old_attempts + 1,
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "reason": "title_mismatch",
                "last_source": "wayback_repair_audit",
            }
    return stats


# ---------- 报告 --------------------------------------------------------------------


def write_report(rows: List[dict], path: Path) -> dict:
    counts = {"PASS": 0, "FAIL": 0, "unverifiable": 0}
    for r in rows:
        counts[r["verdict"]] += 1
    lines = [
        "# Wayback repair audit",
        "",
        f"- date: {time.strftime('%Y-%m-%d %H:%M')}",
        f"- entries: {len(rows)} (PASS {counts['PASS']} / FAIL {counts['FAIL']} "
        f"/ unverifiable {counts['unverifiable']})",
        f"- verdict rule: normalised pure ratio >= {AUDIT_MIN_RATIO} (no containment shortcut)",
        "",
    ]
    fails = [r for r in rows if r["verdict"] == "FAIL"]
    if fails:
        lines += [
            "## FAIL details",
            "",
            "| conf | action | old_url | new_url | ratio | remote title | local title |",
            "|------|--------|---------|---------|-------|--------------|-------------|",
        ]
        for r in fails:
            e = r["entry"]
            ratio = f"{r['ratio']:.2f}" if r["ratio"] is not None else "-"
            lines.append(
                f"| {e.get('conf','')} | {e.get('action','')} | {e.get('old_url','')} "
                f"| {e.get('new_url','')} | {ratio} | {(r['remote_title'] or '')[:60]} "
                f"| {(r['local_title'] or '')[:60]} |"
            )
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return counts


# ---------- 主流程 --------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--revert", action="store_true",
                    help="Revert FAIL entries (still dry-run unless --write)")
    ap.add_argument("--write", dest="write", action="store_true",
                    help="With --revert: actually mutate cache/progress and push to HF")
    ap.add_argument("--no-sync", action="store_true")
    ap.add_argument("--offline", action="store_true",
                    help="Skip HF pre-repair download and any HF push (merge rows "
                         "become unverifiable-for-restore without the pre-repair cache)")
    ap.add_argument("--output", type=Path, default=AUDIT_REPORT)
    args = ap.parse_args()
    _install_utf8_console()

    t0 = time.time()
    tmpdir = Path(tempfile.mkdtemp(prefix="wayback_audit_"))
    print(f"[*] tmpdir: {tmpdir}")

    # 1. 输入
    log_paths = [ROOT / WAYBACK_REPAIR_LOG]
    ci_log = fetch_ci_log(tmpdir)
    if ci_log:
        log_paths.append(ci_log)
    entries = load_log_entries(log_paths)
    print(f"[*] log entries (deduped): {len(entries)}")

    current_papers = load_cache_records(ROOT / CACHE_FILE)
    current_by_key = by_conf_url(current_papers)
    print(f"[*] current cache records: {len(current_papers)}")

    prerepair_by_key = None
    if not args.offline:
        prerepair_path = fetch_prerepair_cache(tmpdir)
        if prerepair_path:
            prerepair_by_key = by_conf_url(load_cache_records(prerepair_path))
            print(f"[*] pre-repair cache records: {len(prerepair_by_key)}")

    # 2. 远端标题
    urls = [e.get("new_url", "") for e in entries]
    doi_urls = [u for u in set(urls) if _host_of(u) == "doi.org"]
    arxiv_urls = [u for u in set(urls) if _host_of(u) == "arxiv.org"]
    print(f"[*] remote lookups: {len(doi_urls)} doi.org + {len(arxiv_urls)} arxiv.org "
          f"(+ {len(set(urls)) - len(doi_urls) - len(arxiv_urls)} unverifiable hosts)")
    remote_titles = resolve_remote_titles(urls)
    resolved = sum(1 for u in set(urls) if remote_titles.get(u))
    print(f"[*] remote titles resolved: {resolved}/{len(set(urls))} unique urls "
          f"(doi.org {sum(1 for u in doi_urls if remote_titles.get(u))}/{len(doi_urls)}, "
          f"arxiv.org {sum(1 for u in arxiv_urls if remote_titles.get(u))}/{len(arxiv_urls)})")

    # 3. 审计
    rows = audit_entries(entries, current_by_key, prerepair_by_key, remote_titles)
    counts = write_report(rows, args.output)
    print(f"[*] audit: PASS={counts['PASS']} FAIL={counts['FAIL']} "
          f"unverifiable={counts['unverifiable']} (report: {args.output})")

    # 4. 可选回滚
    if args.revert:
        failed = [r for r in rows if r["verdict"] == "FAIL"]
        progress = load_progress()
        stats = revert_failed(
            failed, current_papers, prerepair_by_key, progress,
            dry_run=not args.write,
        )
        print(f"[*] revert stats: {stats}")
        if not args.write:
            print("[*] Dry run: cache/progress untouched, no Hugging Face sync.")
        else:
            _write_cache_atomic(current_papers)
            save_progress(progress)
            if not (args.no_sync or args.offline):
                _sync_or_die()
            else:
                print("[*] --no-sync/--offline set; skipped Hugging Face push.")
    print(f"[*] Done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()

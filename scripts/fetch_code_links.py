"""
为 cache/cache.jsonl 中的论文扫描 abstract 中的 GitHub 代码链接。

参考 FL-paper-update-tracker 的方案：从已获取的 abstract 中
使用正则表达式提取 GitHub 仓库链接，保留首个匹配结果。

用法:
    python scripts/fetch_code_links.py              # 默认处理当前年度
    python scripts/fetch_code_links.py --year all   # 处理所有年份
    python scripts/fetch_code_links.py --retry-failed # 重试之前未找到的
"""

import argparse
import gzip
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data_artifacts import ensure_cache_local, sync_cache_artifacts

CACHE_FILE = Path("cache/cache.jsonl.gz")
BACKUP_FILE = Path("cache/cache.jsonl.gz.bak")

# 匹配 GitHub 仓库链接（标准 user/repo 格式）
GITHUB_RE = re.compile(
    r"https?://github\.com/[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+(?:/[^\s\)\]\}>\"'`]*)?"
)


def extract_github_link(text: str) -> str:
    """从文本中提取第一个 GitHub 仓库链接，清理尾部标点。"""
    if not text:
        return ""
    matches = GITHUB_RE.findall(text)
    if not matches:
        return ""
    url = matches[0]
    # 清理尾部常见标点
    url = url.rstrip(".,;:'\")]}>")
    return url


def run(year: str = None, retry_failed: bool = False) -> None:
    if year is None:
        year = str(datetime.now().year)

    # Pull the latest cache from Hugging Face before scanning so we don't
    # overwrite a newer cache produced by a concurrent abstract-backfill run.
    ensure_cache_local(CACHE_FILE, refresh=True)

    # 读取缓存
    papers = []
    if CACHE_FILE.exists():
        with gzip.open(CACHE_FILE, "rt", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                papers.append(json.loads(line))

    targets = []
    for p in papers:
        year_match = re.search(r"\d{4}", str(p.get("conf", "")))
        paper_year = year_match.group(0) if year_match else ""
        if year != "all" and paper_year != year:
            continue
        code = p.get("paper_code", "#")
        abstract = (p.get("paper_abstract") or "").strip()
        if not abstract:
            continue
        if not retry_failed:
            # 默认只处理尚未被新逻辑扫描过的条目
            if code != "#":
                continue
        else:
            # 重试模式：处理所有 code 为空或 "#" 的条目
            if code and code != "#":
                continue
        targets.append(p)

    print(f"[*] Total papers to scan: {len(targets)}")
    if not targets:
        print("[!] No papers need scanning. Exiting.")
        return

    found = 0
    unchanged = 0
    for i, p in enumerate(targets, 1):
        title = (p.get("paper_name") or "").strip()
        abstract = p["paper_abstract"]
        new_code = extract_github_link(abstract)
        if new_code:
            p["paper_code"] = new_code
            found += 1
            print(f"[{i}/{len(targets)}] FOUND: {title[:60]}... -> {new_code}")
        else:
            p["paper_code"] = "#"
            unchanged += 1
            if i % 1000 == 0:
                print(f"[{i}/{len(targets)}] scanned, no link yet...")

    print(f"[*] Scan done. Found: {found}, Not found: {unchanged}")

    # 备份并写回
    if CACHE_FILE.exists():
        shutil.copy2(CACHE_FILE, BACKUP_FILE)
    # NOTE: use ``.with_name`` rather than ``.with_suffix`` — the latter only
    # replaces the last suffix (``.gz``) and would yield the buggy path
    # ``cache.jsonl.jsonl.gz.tmp`` that then fails the atomic ``os.replace``.
    tmp_file = CACHE_FILE.with_name(CACHE_FILE.name + ".tmp")
    with gzip.open(tmp_file, "wt", encoding="utf-8") as f:
        for p in papers:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    os.replace(str(tmp_file), str(CACHE_FILE))
    print("[*] Cache saved.")
    sync_cache_artifacts(
        cache_path=CACHE_FILE,
        commit_message="Update PaperVault data artifacts after code link scan",
    )


if __name__ == "__main__":
    default_year = str(datetime.now().year)
    parser = argparse.ArgumentParser(description="Extract GitHub links from abstracts")
    parser.add_argument(
        "--year",
        type=str,
        default=default_year,
        help=f"Year to process (default: {default_year}, use 'all' for all years)",
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Retry previously failed entries (re-scan for empty code links)",
    )
    args = parser.parse_args()
    run(year=args.year, retry_failed=args.retry_failed)

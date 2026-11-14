"""用 PwC archive 快照离线补全缓存中的空摘要与空代码链接。

数据来源（Hugging Face，2025-07 末快照，已停止更新）：
    - pwc-archive/papers-with-abstracts        (576k 行，parquet)
    - pwc-archive/links-between-paper-and-code (300k 行，parquet)

匹配策略（保守：全部为规范化后的确定性精确匹配，无模糊打分）：
    1. arXiv ID（去 vN 版本后缀）
    2. OpenReview ID（forum/pdf 的 id= 参数）
    3. 规范化 URL 精确相等（协议统一 https、host 小写去 www.、
       已知大小写不敏感 host 的 path 小写化，其余 host path 保持原样）
    4. 规范化标题精确相等 + PwC 侧全局唯一 + 年份守卫（兜底 doi.org 条目）

填充规则：
    - 仅填空条目（paper_abstract == "" / paper_code 为 "" 或 "#"），已有值不动。
    - 摘要需清洗后 >= MIN_ABSTRACT_CHARS 字符。
    - 代码仅接受 github.com 链接；is_official 优先，其次 mentioned_in_paper，
      其余不补；同优先级按 URL 字典序取第一个，保证确定性。

用法:
    python scripts/fetch_pwc_backfill.py --dry-run --report cache/pwc_report.json
    python scripts/fetch_pwc_backfill.py                  # 正式运行（备份+上传 HF）
    python scripts/fetch_pwc_backfill.py --no-upload      # 只写本地
    python scripts/fetch_pwc_backfill.py --conf ICML2024 --conf ACL2023
"""

import argparse
import gzip
import json
import os
import re
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data_artifacts import ensure_cache_local, sync_cache_artifacts  # noqa: E402

CACHE_FILE = Path("cache/cache.jsonl.gz")
BACKUP_FILE = Path("cache/cache.jsonl.gz.bak")

PWC_ABSTRACTS_REPO = "pwc-archive/papers-with-abstracts"
PWC_LINKS_REPO = "pwc-archive/links-between-paper-and-code"

ABSTRACTS_FILES = [f"data/train-0000{i}-of-00004.parquet" for i in range(4)]
LINKS_FILES = ["data/train-00000-of-00001.parquet"]

ABSTRACTS_COLUMNS = [
    "title", "abstract", "arxiv_id", "openreview_id",
    "url_abs", "conference_url_abs", "proceeding", "conference", "date",
]
LINKS_COLUMNS = [
    "paper_title", "paper_arxiv_id", "paper_url_abs",
    "repo_url", "is_official", "mentioned_in_paper",
]

# 对齐 papervault/services/abstract_fetchers/base.py 的长度门槛
MIN_ABSTRACT_CHARS = 60

ARXIV_RE = re.compile(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5}|[a-z\-]+/\d{7})", re.IGNORECASE)
OPENREVIEW_RE = re.compile(r"openreview\.net/(?:forum|pdf)\?.*?\bid=([\w\-]+)", re.IGNORECASE)
YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")
GITHUB_REPO_RE = re.compile(
    r"^https?://github\.com/[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+(?:/[^\s\)\]\}>\"'`]*)?$"
)

# 这些 host 的 URL path 大小写不敏感，规范化为小写以提高命中率；
# aclanthology.org 等不在其中（ID 大小写敏感，保持原样以避免错配）。
_CASE_INSENSITIVE_PATH_HOSTS = {
    "openaccess.thecvf.com",
    "papers.nips.cc",
    "proceedings.mlr.press",
    "arxiv.org",
    "openreview.net",
    "jmlr.org",
}


# ---------- 规范化与键提取 ----------
def normalize_url(url: str) -> str:
    """规范化 URL 用于精确相等匹配。空/非法输入返回 ""."""
    if not url:
        return ""
    url = url.strip()
    if not url.lower().startswith(("http://", "https://")):
        return ""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = parsed.path
    if host in _CASE_INSENSITIVE_PATH_HOSTS:
        path = path.lower()
    path = path.rstrip("/")
    if not host or not path:
        return ""
    return f"https://{host}{path}"


def normalize_title(title: str) -> str:
    """小写并去除所有非字母数字字符，用于标题精确匹配。"""
    if not title:
        return ""
    return re.sub(r"[^\w]+", "", title.strip().lower(), flags=re.UNICODE)


def extract_arxiv_id(url: str) -> str:
    """从 arxiv.org URL 提取去版本号的 arXiv ID。"""
    if not url:
        return ""
    m = ARXIV_RE.search(url)
    if not m:
        return ""
    return re.sub(r"v\d+$", "", m.group(1), flags=re.IGNORECASE)


def extract_openreview_id(url: str) -> str:
    """从 openreview.net forum/pdf URL 提取 id 参数。"""
    if not url:
        return ""
    m = OPENREVIEW_RE.search(url)
    return m.group(1) if m else ""


def _years_in_text(text: str) -> set:
    if not text:
        return set()
    return {int(y) for y in YEAR_RE.findall(str(text))}


def _conf_year(conf: str) -> Optional[int]:
    # conf 名形如 "NIPS2018" / "SP2026"，年份紧贴字母、无词边界，
    # 与 fetch_code_links.py 一致直接用 \d{4} 搜索。
    m = re.search(r"\d{4}", conf or "")
    return int(m.group(0)) if m else None


def _pwc_years(row: dict) -> set:
    """PwC 行的年份证据：优先 proceeding/conference 文本，退化为 date 年份。"""
    years = _years_in_text(row.get("proceeding")) | _years_in_text(row.get("conference"))
    if years:
        return years
    date = row.get("date")
    if date is None:
        return set()
    year = getattr(date, "year", None)
    if year is None:
        m = YEAR_RE.search(str(date))
        year = int(m.group(1)) if m else None
    return {year} if year else set()


def year_guard_ok(conf: str, pwc_row: dict) -> bool:
    """年份守卫：双方年份都存在且完全不相交时拒绝（防会议/期刊版同标题错配）。

    proceeding/conference 提供的年份要求精确命中 conf 年份；
    仅有 date（多为 arXiv 提交年份）时允许 ±1 年（预印本早于会议一年很常见）。
    """
    conf_year = _conf_year(conf)
    if conf_year is None:
        return True
    venue_years = _years_in_text(pwc_row.get("proceeding")) | _years_in_text(
        pwc_row.get("conference")
    )
    if venue_years:
        return conf_year in venue_years
    date_years = _pwc_years(pwc_row)
    if date_years:
        return any(abs(y - conf_year) <= 1 for y in date_years)
    return True


def clean_abstract(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", "", str(text))
    return re.sub(r"\s+", " ", text).strip()


def clean_github_url(url: str) -> str:
    """校验并清理 GitHub 仓库链接；不合格返回 ""."""
    if not url:
        return ""
    url = str(url).strip().rstrip(".,;:'\")]}>").rstrip("/")
    if not GITHUB_REPO_RE.match(url):
        return ""
    return url


# ---------- PwC 索引构建 ----------
class _IndexBuilder:
    """key -> row 索引；同一 key 映射到内容不一致的多行时整键弃用。"""

    def __init__(self, fingerprint):
        self._fingerprint = fingerprint
        self._index: Dict[str, dict] = {}
        self._poisoned: set = set()

    def add(self, key: str, row: dict) -> None:
        if not key or key in self._poisoned:
            return
        prior = self._index.get(key)
        if prior is None:
            self._index[key] = row
        elif self._fingerprint(prior) != self._fingerprint(row):
            del self._index[key]
            self._poisoned.add(key)

    def get(self, key: str) -> Optional[dict]:
        return self._index.get(key) if key else None


def _abstract_fingerprint(row: dict) -> str:
    return clean_abstract(row.get("abstract"))


def build_abstract_index(rows: Iterable[dict]) -> dict:
    """从 PwC papers-with-abstracts 行构建四个匹配索引。"""
    by_arxiv = _IndexBuilder(_abstract_fingerprint)
    by_openreview = _IndexBuilder(_abstract_fingerprint)
    by_url = _IndexBuilder(_abstract_fingerprint)
    by_title = _IndexBuilder(_abstract_fingerprint)
    for row in rows:
        arxiv_id = (row.get("arxiv_id") or "").strip()
        if arxiv_id:
            by_arxiv.add(re.sub(r"v\d+$", "", arxiv_id, flags=re.IGNORECASE), row)
        openreview_id = (row.get("openreview_id") or "").strip()
        if openreview_id:
            by_openreview.add(openreview_id, row)
        for url_key in (row.get("url_abs"), row.get("conference_url_abs")):
            by_url.add(normalize_url(url_key or ""), row)
        by_title.add(normalize_title(row.get("title") or ""), row)
    return {
        "arxiv": by_arxiv,
        "openreview": by_openreview,
        "url": by_url,
        "title": by_title,
    }


def _paper_identity(row: dict) -> str:
    return (
        (row.get("paper_arxiv_id") or "").strip()
        or normalize_url(row.get("paper_url_abs") or "")
        or (row.get("paper_url") or "").strip()
        or normalize_title(row.get("paper_title") or "")
    )


def select_repo(repo_rows: List[dict]) -> str:
    """从同一篇 paper 的若干链接行中确定性选出最优 GitHub 仓库。

    is_official 优先，其次 mentioned_in_paper，其余不采用；
    同优先级按 URL 字典序取第一个。
    """
    candidates: List[Tuple[int, str]] = []
    for row in repo_rows:
        url = clean_github_url(row.get("repo_url") or "")
        if not url:
            continue
        if row.get("is_official"):
            priority = 0
        elif row.get("mentioned_in_paper"):
            priority = 1
        else:
            continue
        candidates.append((priority, url))
    if not candidates:
        return ""
    candidates.sort(key=lambda t: (t[0], t[1]))
    return candidates[0][1]


def build_code_index(rows: Iterable[dict]) -> dict:
    """从 PwC links-between-paper-and-code 行构建 code 匹配索引。

    键的消歧：同一键若对应多个不同的 paper 身份则整键弃用；
    同一 paper 的多个 repo 行经 select_repo 收敛为一个链接。
    """
    key_to_papers: Dict[str, Dict[str, List[dict]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        identity = _paper_identity(row)
        if not identity:
            continue
        keys = set()
        arxiv_id = (row.get("paper_arxiv_id") or "").strip()
        if arxiv_id:
            keys.add(("arxiv", re.sub(r"v\d+$", "", arxiv_id, flags=re.IGNORECASE)))
        url_key = normalize_url(row.get("paper_url_abs") or "")
        if url_key:
            keys.add(("url", url_key))
        title_key = normalize_title(row.get("paper_title") or "")
        if title_key:
            keys.add(("title", title_key))
        for kind, key in keys:
            key_to_papers[(kind, key)][identity].append(row)

    index: Dict[str, Dict[str, str]] = {"arxiv": {}, "url": {}, "title": {}}
    for (kind, key), papers in key_to_papers.items():
        if len(papers) != 1:
            continue  # 同一键对应多篇 paper，保守弃用
        repo_rows = next(iter(papers.values()))
        repo = select_repo(repo_rows)
        if repo:
            index[kind][key] = repo
    return index


# ---------- 匹配与回填 ----------
def match_abstract(index: dict, paper: dict) -> Tuple[Optional[dict], str]:
    """按 arxiv → openreview → url → title 的优先级查找 PwC 行。"""
    url = paper.get("paper_url") or ""
    row = index["arxiv"].get(extract_arxiv_id(url))
    if row is not None:
        return row, "arxiv_id"
    row = index["openreview"].get(extract_openreview_id(url))
    if row is not None:
        return row, "openreview_id"
    row = index["url"].get(normalize_url(url))
    if row is not None:
        return row, "url"
    title_key = normalize_title(paper.get("paper_name") or "")
    if title_key:
        row = index["title"].get(title_key)
        if row is not None and year_guard_ok(paper.get("conf") or "", row):
            return row, "title"
    return None, ""


def match_repo(code_index: dict, paper: dict) -> Tuple[str, str]:
    """按 arxiv → url → title 的优先级查找 GitHub 仓库链接。"""
    url = paper.get("paper_url") or ""
    repo = code_index["arxiv"].get(extract_arxiv_id(url))
    if repo:
        return repo, "arxiv_id"
    repo = code_index["url"].get(normalize_url(url))
    if repo:
        return repo, "url"
    repo = code_index["title"].get(normalize_title(paper.get("paper_name") or ""))
    if repo:
        return repo, "title"
    return "", ""


def backfill_records(
    papers: List[dict],
    abstract_index: dict,
    code_index: dict,
    conf_filter: Optional[set] = None,
) -> dict:
    """原地回填 papers 列表中的空字段，返回统计字典。"""
    stats = {
        "scanned": 0,
        "abstract_filled": 0,
        "code_filled": 0,
        "abstract_by_method": Counter(),
        "code_by_method": Counter(),
        "abstract_by_conf": Counter(),
        "code_by_conf": Counter(),
        "samples_abstract": [],
        "samples_code": [],
    }
    for paper in papers:
        conf = paper.get("conf") or ""
        if conf_filter and conf not in conf_filter:
            continue
        stats["scanned"] += 1

        if not (paper.get("paper_abstract") or "").strip():
            row, method = match_abstract(abstract_index, paper)
            if row is not None:
                abstract = clean_abstract(row.get("abstract"))
                if len(abstract) >= MIN_ABSTRACT_CHARS:
                    paper["paper_abstract"] = abstract
                    stats["abstract_filled"] += 1
                    stats["abstract_by_method"][method] += 1
                    stats["abstract_by_conf"][conf] += 1
                    if len(stats["samples_abstract"]) < 20:
                        stats["samples_abstract"].append(
                            {
                                "conf": conf,
                                "paper_name": paper.get("paper_name"),
                                "paper_url": paper.get("paper_url"),
                                "method": method,
                                "abstract_head": abstract[:120],
                            }
                        )

        code = (paper.get("paper_code") or "").strip()
        if code in ("", "#"):
            repo, method = match_repo(code_index, paper)
            if repo:
                paper["paper_code"] = repo
                stats["code_filled"] += 1
                stats["code_by_method"][method] += 1
                stats["code_by_conf"][conf] += 1
                if len(stats["samples_code"]) < 20:
                    stats["samples_code"].append(
                        {
                            "conf": conf,
                            "paper_name": paper.get("paper_name"),
                            "paper_url": paper.get("paper_url"),
                            "method": method,
                            "repo_url": repo,
                        }
                    )
    return stats


# ---------- PwC 数据加载 ----------
def _load_parquet_rows(repo_id: str, filenames: List[str], columns: List[str]) -> Iterable[dict]:
    from huggingface_hub import hf_hub_download
    import pyarrow.parquet as pq

    for filename in filenames:
        local = hf_hub_download(repo_id=repo_id, repo_type="dataset", filename=filename)
        table = pq.read_table(local, columns=columns)
        for batch in table.to_batches(max_chunksize=10000):
            for row in batch.to_pylist():
                yield row


def load_pwc_abstract_rows() -> Iterable[dict]:
    return _load_parquet_rows(PWC_ABSTRACTS_REPO, ABSTRACTS_FILES, ABSTRACTS_COLUMNS)


def load_pwc_link_rows() -> Iterable[dict]:
    return _load_parquet_rows(PWC_LINKS_REPO, LINKS_FILES, LINKS_COLUMNS)


# ---------- 缓存读写 ----------
def load_papers(cache_path: Path) -> List[dict]:
    papers = []
    with gzip.open(cache_path, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                papers.append(json.loads(line))
    return papers


def write_papers_atomic(cache_path: Path, papers: List[dict]) -> None:
    tmp_file = cache_path.with_name(cache_path.name + ".tmp")
    with gzip.open(tmp_file, "wt", encoding="utf-8") as f:
        for p in papers:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    # 回读校验：行数一致且每行可解析
    verify = 0
    with gzip.open(tmp_file, "rt", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                json.loads(line)
                verify += 1
    if verify != len(papers):
        tmp_file.unlink(missing_ok=True)
        raise RuntimeError(
            f"verification mismatch: written={len(papers)} verified={verify}"
        )
    os.replace(str(tmp_file), str(cache_path))


def _stats_to_jsonable(stats: dict) -> dict:
    out = dict(stats)
    for key in ("abstract_by_method", "code_by_method", "abstract_by_conf", "code_by_conf"):
        out[key] = dict(out[key])
    return out


def run(
    dry_run: bool = False,
    report_path: Optional[Path] = None,
    conf_filter: Optional[set] = None,
    upload: bool = True,
) -> dict:
    ensure_cache_local(CACHE_FILE, refresh=True)

    print("[*] Loading PwC papers-with-abstracts ...")
    abstract_index = build_abstract_index(load_pwc_abstract_rows())
    print("[*] Loading PwC links-between-paper-and-code ...")
    code_index = build_code_index(load_pwc_link_rows())

    papers = load_papers(CACHE_FILE)
    print(f"[*] Cache papers: {len(papers)}")

    stats = backfill_records(papers, abstract_index, code_index, conf_filter=conf_filter)
    stats["dry_run"] = dry_run

    print(f"[*] Scanned: {stats['scanned']}")
    print(f"[*] Abstract filled: {stats['abstract_filled']}  by_method={dict(stats['abstract_by_method'])}")
    print(f"[*] Code filled: {stats['code_filled']}  by_method={dict(stats['code_by_method'])}")

    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(_stats_to_jsonable(stats), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"[*] Report written to {report_path}")

    if dry_run:
        print("[*] Dry-run: cache not modified.")
        return stats

    if stats["abstract_filled"] == 0 and stats["code_filled"] == 0:
        print("[!] Nothing to fill. Cache untouched.")
        return stats

    if CACHE_FILE.exists():
        shutil.copy2(CACHE_FILE, BACKUP_FILE)
        print(f"[*] Backup written to {BACKUP_FILE}")
    write_papers_atomic(CACHE_FILE, papers)
    print("[*] Cache saved.")

    if upload:
        sync_cache_artifacts(
            cache_path=CACHE_FILE,
            commit_message="Backfill abstracts and code links from PwC archive datasets",
            progress_path=None,
        )
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill abstracts/code from PwC archive")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不写回缓存")
    parser.add_argument("--report", type=Path, default=None, help="输出 JSON 报告路径")
    parser.add_argument(
        "--conf",
        action="append",
        default=None,
        help="只处理指定 conf（可重复传入），默认处理全部",
    )
    parser.add_argument("--no-upload", action="store_true", help="跳过 HF 上传")
    args = parser.parse_args()
    run(
        dry_run=args.dry_run,
        report_path=args.report,
        conf_filter=set(args.conf) if args.conf else None,
        upload=not args.no_upload,
    )

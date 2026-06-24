"""
为 cache/cache.jsonl 中的论文批量补充 abstract。

参考 FL-paper-update-tracker 的多源策略：
    Crossref (DOI) → Semantic Scholar (DOI) → arXiv (title) → OpenAlex (DOI)

用法:
    python scripts/fetch_abstracts.py              # 默认处理 DOI 论文 (Phase 1)
    python scripts/fetch_abstracts.py --phase all  # 处理所有空 abstract
    python scripts/fetch_abstracts.py --phase 2    # 处理核心会议非 DOI 论文
    python scripts/fetch_abstracts.py --phase 3    # 处理剩余非 DOI 论文
    python scripts/fetch_abstracts.py --conf AAAI2020 --chunk-size 500
    python scripts/fetch_abstracts.py --phase 1 --retry-failed
"""

import argparse
import gzip
import json
import os
import random
import re
import sys
import threading
import time
import difflib
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse
from typing import Dict, List, Optional, Set, Tuple

import requests
from requests.adapters import HTTPAdapter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data_artifacts import sync_cache_artifacts, ensure_cache_local, ensure_progress_local

# Windows 控制台 UTF-8 编码修复
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

# ---------- 配置 ----------
CONTACT_EMAIL = os.getenv("CONTACT_EMAIL", "im.young@foxmail.com")

CROSSREF_AGENT = (
    f"PaperVault-AbstractBackfill/1.0 (mailto:{CONTACT_EMAIL})"
    if CONTACT_EMAIL
    else "PaperVault-AbstractBackfill/1.0"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36 " + CROSSREF_AGENT
    )
}

CACHE_DIR = Path("cache")
CACHE_FILE = CACHE_DIR / "cache.jsonl.gz"
PROGRESS_FILE = CACHE_DIR / "abstract_backfill_progress.jsonl.gz"
LEGACY_PROGRESS_FILE = CACHE_DIR / "abstract_backfill_progress.json"
BACKUP_FILE = CACHE_DIR / "cache.jsonl.gz.bak"

PROGRESS_SCHEMA = "abstract_backfill_progress/v3"
PROGRESS_SCHEMA_VERSION = 3
# 当物理行数 > 活跃 url 数 * COMPACTION_RATIO 时，触发整文件 compaction
PROGRESS_COMPACTION_RATIO = 2.0
# 用于 append 写入时跟踪"自上次落盘以来的脏行数"，配合活跃 key 数判断 compaction 时机
_progress_runtime: Dict[str, int] = {"physical_lines": 0}
# 进程内缓存的"磁盘上 progress 文件最新一次反映出来的快照"，用于增量 save diff
_last_saved_snapshot: Dict[str, dict] = {}
# 保护 _progress_runtime / _last_saved_snapshot / PROGRESS_FILE 写入的串行锁。
# fetch_abstracts.py 内有 ThreadPoolExecutor 并发改写 processed，且 save_progress
# 可能在主线程周期性触发；加锁防止：
#   1. dict diff 时对 processed 的迭代与 worker 写入冲突；
#   2. _progress_runtime 计数与 append/compact 的非原子叠加；
#   3. 同一进程内并行 _append_progress 写 gzip 流。
_progress_lock = threading.Lock()

# 核心会议（用于 Phase 2/3 划分）
CORE_CONFS = {
    "NIPS", "ICML", "ICLR", "CVPR", "ICCV", "ECCV",
    "ACL", "EMNLP", "NAACL", "COLING",
    "AAAI", "IJCAI", "KDD", "SIGIR", "WWW", "MM",
}

# ---------- 会议/期刊优先度分解（Tier） ----------
# 数值越小优先级越高。用于 list_pending_confs / batch 模式排序。
# 匹配规则：去掉年份后的 conf prefix，取最长匹配项。
CONF_PRIORITY: Dict[str, int] = {
    # Tier 1 — 顶级会议 (ML三大 + CV三大 + NLP三大)
    "NIPS": 1, "ICML": 1, "ICLR": 1,
    "CVPR": 1, "ICCV": 1, "ECCV": 1,
    "ACL": 1, "EMNLP": 1, "NAACL": 1, "COLING": 1,
    # Tier 2 — 重要会议
    "AAAI": 2, "IJCAI": 2,
    "KDD": 2, "SIGIR": 2, "WWW": 2, "WSDM": 2, "CIKM": 2,
    "MM": 2, "ICASSP": 2, "INTERSPEECH": 2, "MICCAI": 2,
    "BMVC": 2, "AISTATS": 2, "COLT": 2,
    "VLDB": 2, "SIGMOD": 2, "ICDE": 2, "ICDM": 2,
    # Tier 3 — 期刊 (Journal)
    "TPAMI": 3, "TNNLS": 3, "TIP": 3, "TKDE": 3, "TASLP": 3,
    "TOIS": 3, "IJCV": 3, "JMLR": 3, "TMM": 3, "TCYB": 3,
    "TCSVT": 3, "TIST": 3, "TKDD": 3, "TWEB": 3,
    # Tier 4 — 其他 (默认)
}


def sync_artifacts_after_cache_update(commit_message: str):
    sync_cache_artifacts(
        cache_path=CACHE_FILE,
        commit_message=commit_message,
    )


def _get_conf_tier(conf: str) -> int:
    """获取会议/期刊的优先级 tier（1-4），数值越小越优先。"""
    prefix = re.sub(r"\d{4}$", "", conf).upper()
    # 优先返回最长匹配项，避免短前缀误匹配
    matches = [k for k in CONF_PRIORITY if prefix.startswith(k)]
    if not matches:
        return 4
    best = max(matches, key=len)
    return CONF_PRIORITY[best]


_thread_local = threading.local()


def _get_session() -> requests.Session:
    if not hasattr(_thread_local, "session"):
        _thread_local.session = requests.Session()
        _thread_local.session.trust_env = False
        _thread_local.session.mount("https://", HTTPAdapter(max_retries=2))
    return _thread_local.session


def _rate_limited_request(
    url: str,
    last_time: float,
    min_interval: float = 1.5,
    timeout: int = 15,
    **kwargs,
) -> Tuple[requests.Response, float]:
    wait = max(0.0, min_interval - (time.time() - last_time))
    if wait > 0:
        time.sleep(wait + random.uniform(0.0, 0.3))
    session = _get_session()
    req_headers = kwargs.pop("headers", HEADERS)
    resp = session.get(url, timeout=timeout, headers=req_headers, **kwargs)
    return resp, time.time()


# ---------- Abstract 清洗 ----------
def clean_abstract(text: str) -> str:
    if not text:
        return ""
    # 去除 XML 标签
    text = re.sub(r"<jats:p>(.*?)</jats:p>", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.strip()
    # 处理连字符换行
    text = re.sub(r"-\n\s*", "", text)
    text = re.sub(r"-\r\n\s*", "", text)
    # 合并句子内硬换行
    text = re.sub(r"\n\s*([a-z0-9])", r" \1", text)
    text = re.sub(r"\r\n\s*([a-z0-9])", r" \1", text)
    # 压缩空白
    text = re.sub(r"[\s\t]+", " ", text)
    return text.strip()


# ---------- 标题匹配 ----------
def is_title_match(api_title: str, paper_title: str, threshold: float = 0.70) -> bool:
    if not api_title or not paper_title:
        return False
    norm = lambda t: re.sub(r"[^\w]+", "", t.strip().lower(), flags=re.UNICODE)
    n_api = norm(api_title)
    n_paper = norm(paper_title)
    if not n_api or not n_paper:
        return False
    if n_api in n_paper or n_paper in n_api:
        return True
    ratio = difflib.SequenceMatcher(None, n_api, n_paper).ratio()
    return ratio >= threshold


# ---------- DOI 提取 ----------
def extract_doi(paper_url: str) -> Optional[str]:
    parsed = urlparse(paper_url.strip())
    host = (parsed.netloc or "").lower()
    if host == "doi.org" or host.endswith(".doi.org"):
        doi = parsed.path.strip("/")
        return doi or None
    return None


def query_doi_by_title(title: str, last_time: float, min_interval: float = 2.0) -> Tuple[Optional[str], float]:
    """通过 Crossref 用标题查询 DOI（保守策略：严格限流，仅返回高置信度结果）。"""
    if not title or len(title) < 5:
        return None, last_time
    encoded = requests.utils.quote(title)
    url = f"https://api.crossref.org/works?query.title={encoded}&rows=1"
    headers = {"User-Agent": CROSSREF_AGENT}
    try:
        resp, last_time = _rate_limited_request(url, last_time, min_interval=min_interval, headers=headers)
        if resp.status_code != 200:
            return None, last_time
        data = resp.json()
        items = data.get("message", {}).get("items", [])
        if not items:
            return None, last_time
        item = items[0]
        api_title = item.get("title", [""])[0] if isinstance(item.get("title"), list) else item.get("title", "")
        if not is_title_match(api_title, title, threshold=0.85):
            return None, last_time
        doi = item.get("DOI")
        return doi, last_time
    except Exception:
        return None, last_time


# ---------- API 查询 ----------
def fetch_crossref_abstract(
    doi: str, last_time: float, min_interval: float = 1.5, max_retries: int = 3
) -> Tuple[Optional[str], Optional[str], float]:
    url = f"https://api.crossref.org/works/{doi}"
    headers = {"User-Agent": CROSSREF_AGENT}
    for attempt in range(1, max_retries + 1):
        try:
            resp, last_time = _rate_limited_request(
                url, last_time, min_interval=min_interval, headers=headers
            )
            if resp.status_code in (404, 403):
                return None, None, last_time
            if resp.status_code == 429:
                time.sleep(min(60, 2 ** attempt))
                continue
            resp.raise_for_status()
            data = resp.json()
            item = data.get("message", {})
            raw_title = item.get("title")
            if isinstance(raw_title, list) and raw_title:
                api_title = str(raw_title[0]).strip() or None
            elif raw_title:
                api_title = str(raw_title).strip() or None
            else:
                api_title = None
            abstract = item.get("abstract")
            if abstract and isinstance(abstract, str):
                cleaned = clean_abstract(abstract)
                if cleaned:
                    return cleaned, api_title, last_time
            return None, api_title, last_time
        except requests.exceptions.Timeout:
            if attempt < max_retries:
                time.sleep(2 ** attempt)
        except Exception:
            if attempt < max_retries:
                time.sleep(2 ** attempt)
    return None, None, last_time


def fetch_semantic_scholar_abstract(
    doi: str, last_time: float, min_interval: float = 1.5, max_retries: int = 3
) -> Tuple[Optional[str], Optional[str], float]:
    url = f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}"
    params = {"fields": "abstract,title"}
    for attempt in range(1, max_retries + 1):
        try:
            resp, last_time = _rate_limited_request(
                url, last_time, min_interval=min_interval, params=params
            )
            if resp.status_code in (404, 403):
                return None, None, last_time
            if resp.status_code == 429:
                time.sleep(min(60, 2 ** attempt))
                continue
            resp.raise_for_status()
            data = resp.json()
            api_title = data.get("title")
            if api_title:
                api_title = str(api_title).strip() or None
            abstract = data.get("abstract")
            if abstract and abstract.strip():
                return clean_abstract(abstract), api_title, last_time
            return None, api_title, last_time
        except requests.exceptions.Timeout:
            if attempt < max_retries:
                time.sleep(2 ** attempt)
        except Exception:
            if attempt < max_retries:
                time.sleep(2 ** attempt)
    return None, None, last_time


def fetch_arxiv_abstract(
    title: str, last_time: float, min_interval: float = 1.5, max_retries: int = 3
) -> Tuple[Optional[str], Optional[str], float]:
    import xml.etree.ElementTree as ET

    encoded_title = requests.utils.quote(title)
    url = f"http://export.arxiv.org/api/query?search_query=ti:{encoded_title}&max_results=1"
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    for attempt in range(1, max_retries + 1):
        try:
            resp, last_time = _rate_limited_request(
                url, last_time, min_interval=min_interval
            )
            if resp.status_code in (404, 403):
                return None, None, last_time
            if resp.status_code == 429:
                time.sleep(min(60, 2 ** attempt))
                continue
            resp.raise_for_status()
            root = ET.fromstring(resp.text)
            entry = root.find("atom:entry", ns)
            if entry is None:
                return None, None, last_time
            title_elem = entry.find("atom:title", ns)
            api_title = title_elem.text.strip() if title_elem is not None and title_elem.text else None
            summary_elem = entry.find("atom:summary", ns)
            abstract = None
            if summary_elem is not None and summary_elem.text:
                cleaned = clean_abstract(summary_elem.text)
                if cleaned:
                    abstract = cleaned
            return abstract, api_title, last_time
        except requests.exceptions.Timeout:
            if attempt < max_retries:
                time.sleep(2 ** attempt)
        except Exception:
            if attempt < max_retries:
                time.sleep(2 ** attempt)
    return None, None, last_time


def _reconstruct_openalex_abstract(inverted_index: dict) -> Optional[str]:
    if not isinstance(inverted_index, dict) or not inverted_index:
        return None
    try:
        max_pos = max(max(positions) for positions in inverted_index.values() if positions)
        words = [""] * (max_pos + 1)
        for word, positions in inverted_index.items():
            for pos in positions:
                if 0 <= pos <= max_pos:
                    words[pos] = word
        abstract = " ".join(words)
        return abstract if abstract.strip() else None
    except Exception:
        return None


def fetch_openalex_abstract(
    doi: str, last_time: float, min_interval: float = 1.5, max_retries: int = 3
) -> Tuple[Optional[str], Optional[str], float]:
    mailto = f"&mailto={requests.utils.quote(CONTACT_EMAIL)}" if CONTACT_EMAIL else ""
    url = f"https://api.openalex.org/works/doi:{doi}?select=display_name,abstract_inverted_index{mailto}"
    for attempt in range(1, max_retries + 1):
        try:
            resp, last_time = _rate_limited_request(
                url, last_time, min_interval=min_interval
            )
            if resp.status_code in (404, 403):
                return None, None, last_time
            if resp.status_code == 429:
                time.sleep(min(60, 2 ** attempt))
                continue
            resp.raise_for_status()
            data = resp.json()
            api_title = data.get("display_name")
            if api_title:
                api_title = str(api_title).strip() or None
            inverted = data.get("abstract_inverted_index")
            abstract = None
            if inverted:
                reconstructed = _reconstruct_openalex_abstract(inverted)
                if reconstructed:
                    cleaned = clean_abstract(reconstructed)
                    if cleaned:
                        abstract = cleaned
            return abstract, api_title, last_time
        except requests.exceptions.Timeout:
            if attempt < max_retries:
                time.sleep(2 ** attempt)
        except Exception:
            if attempt < max_retries:
                time.sleep(2 ** attempt)
    return None, None, last_time


def _fetch_doi_sources_concurrent(
    doi: str, title: str, last_time: dict, sleep_sec: float = 1.5, max_retries: int = 3
) -> Tuple[Optional[str], Optional[str], str, dict]:
    """并发查询 Crossref、Semantic Scholar、OpenAlex，返回按优先级第一个成功且标题匹配的结果。"""
    results: Dict[str, Tuple[Optional[str], Optional[str], float]] = {}

    def query_crossref():
        return fetch_crossref_abstract(doi, last_time["crossref"], min_interval=sleep_sec, max_retries=max_retries)

    def query_semanticscholar():
        return fetch_semantic_scholar_abstract(doi, last_time["semanticscholar"], min_interval=sleep_sec, max_retries=max_retries)

    def query_openalex():
        return fetch_openalex_abstract(doi, last_time["openalex"], min_interval=sleep_sec, max_retries=max_retries)

    executor = ThreadPoolExecutor(max_workers=3)
    futures = {
        executor.submit(query_crossref): "crossref",
        executor.submit(query_semanticscholar): "semanticscholar",
        executor.submit(query_openalex): "openalex",
    }
    try:
        for future in as_completed(futures):
            source = futures[future]
            try:
                abstract, api_title, new_time = future.result()
                results[source] = (abstract, api_title, new_time)
                # 一旦某个源返回成功且标题匹配，立即取消其余任务并提前返回
                if abstract and api_title and is_title_match(api_title, title):
                    last_time[source] = new_time
                    now = time.time()
                    for pending_source in ("crossref", "semanticscholar", "openalex"):
                        if pending_source != source and pending_source not in results:
                            last_time[pending_source] = max(last_time.get(pending_source, 0.0), now)
                    return abstract, api_title, source, last_time
            except Exception:
                pass
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    # 按优先级检查，并同步更新 last_time
    for source in ["crossref", "semanticscholar", "openalex"]:
        if source not in results:
            continue
        abstract, api_title, new_time = results[source]
        last_time[source] = new_time
        if abstract and api_title and not is_title_match(api_title, title):
            print(f"    [!] Title mismatch ({source}): api='{api_title[:80]}' vs local='{title[:80]}'")
            continue
        if abstract:
            return abstract, api_title, source, last_time

    return None, None, "", last_time


def fetch_abstract_for_paper(
    paper: dict, last_time: dict, sleep_sec: float = 1.5, max_retries: int = 3,
    query_doi_by_title_enabled: bool = False,
) -> Tuple[Optional[str], dict, str]:
    """
    返回: (abstract, last_time, source)
    source 取值: "crossref", "semanticscholar", "arxiv", "openalex", ""
    """
    doi = extract_doi(paper.get("paper_url", ""))
    title = (paper.get("paper_name") or "").strip()
    abstract = None
    source = ""

    # 可选：对非 DOI 论文尝试用标题查询 DOI
    if not doi and query_doi_by_title_enabled and title:
        queried_doi, last_time["crossref"] = query_doi_by_title(title, last_time["crossref"])
        if queried_doi:
            doi = queried_doi

    if doi:
        abstract, api_title, source, last_time = _fetch_doi_sources_concurrent(
            doi, title, last_time, sleep_sec=sleep_sec, max_retries=max_retries
        )

    if not abstract and title:
        abstract, api_title, last_time["arxiv"] = fetch_arxiv_abstract(
            title, last_time["arxiv"], min_interval=sleep_sec, max_retries=max_retries
        )
        if abstract and api_title and not is_title_match(api_title, title):
            print(f"    [!] Title mismatch (arxiv): api='{api_title[:80]}' vs local='{title[:80]}'")
            abstract = None
        if abstract:
            source = "arxiv"

    return abstract, last_time, source


# ---------- 进度管理（v3 JSONL.gz 格式，兼容旧 JSON / 旧 v2 格式） ----------
#
# 新格式：cache/abstract_backfill_progress.jsonl.gz
#   - 首行：meta，例如 {"_meta": true, "schema": "abstract_backfill_progress/v3",
#                       "version": 3, "generated_at": "..."}
#   - 其余每行：record，例如
#       {"url": "...", "status": "success", "source": "openalex", "chars": 1764, "ts": "..."}
#       {"url": "...", "status": "failed", "attempts": 2, "ts": "..."}
#   - 同一 url 后写覆盖前写（event-sourcing 风格）。加载时归并到 {url: latest_record}。
#
# 写入策略：
#   - 增量 save 走 append（避免每次重写 ~1 MB 文件）。
#   - 物理行数膨胀到 live_keys * COMPACTION_RATIO 时，自动 compact 整文件。
#   - compact 使用 .tmp + os.replace，原子替换防止半成品损坏。
#
# 兼容回退：
#   - 若 .jsonl.gz 不存在但旧 .json 存在，自动迁移一次（或单次读取兼容）。


def _meta_line() -> dict:
    return {
        "_meta": True,
        "schema": PROGRESS_SCHEMA,
        "version": PROGRESS_SCHEMA_VERSION,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


def _record_to_meta(rec: dict) -> dict:
    """从 jsonl record 提取出 load_progress 期望的 meta dict（去掉 url 主键）。"""
    return {k: v for k, v in rec.items() if k != "url"}


def _meta_to_record(url: str, meta: dict) -> dict:
    """把 (url, meta) 装回 jsonl record（添加 url 字段）。

    业务不变量：``meta`` 不应携带 ``url`` 字段——url 是外层主键。如果调用方
    误把 url 也塞进了 meta，这里给出一次性告警（避免静默丢失数据）；当
    meta 内的 url 与外层 url 不一致时，以外层为准并打印 WARNING。
    """
    if "url" in meta:
        inner = meta.get("url")
        if inner != url:
            print(
                f"[!] _meta_to_record: meta carries url={inner!r} but outer url={url!r}; "
                "outer wins, inner value will be dropped."
            )
        else:
            # 通常意味着上游构造时把主键也复制进了 meta；功能上无害，仅提示。
            print(
                f"[!] _meta_to_record: meta unexpectedly contains 'url' field for {url!r}; "
                "dropping it to keep schema clean."
            )
    rec = {"url": url}
    rec.update({k: v for k, v in meta.items() if k != "url"})
    return rec


def _load_legacy_json(path: Path) -> Dict[str, dict]:
    """兼容加载旧 abstract_backfill_progress.json（v1 列表式 / v2 字典式）。"""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if "processed_urls" in data:
        return {url: {"status": "unknown", "ts": ""} for url in data["processed_urls"]}
    return data.get("processed", {}) if isinstance(data, dict) else {}


def _load_jsonl_gz(path: Path) -> Tuple[Dict[str, dict], int]:
    """读取 jsonl.gz 进度文件，返回 ({url: latest_meta}, physical_record_lines)。

    physical_record_lines 不包含 meta 行，仅统计 record 行数；用于估算
    compaction 时机（与 live url 数比较）。

    若 gzip 流损坏（例如上一次 append 被强杀，写入了不完整的 gzip 段），
    则把损坏文件改名为 ``<path>.broken-<ts>`` 备份后返回 ({}, 0)，让上层
    回退到 legacy 或空状态而不是直接抛异常拒绝启动。
    """
    progress: Dict[str, dict] = {}
    physical = 0
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for i, raw in enumerate(f):
                line = raw.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    print(f"[!] Skipping malformed progress line {i + 1} in {path.name}")
                    continue
                if obj.get("_meta"):
                    continue
                url = obj.get("url")
                if not url:
                    continue
                physical += 1
                # 后写覆盖前写：天然实现事件溯源式去重
                progress[url] = _record_to_meta(obj)
    except (OSError, EOFError, gzip.BadGzipFile) as exc:
        backup = path.with_name(f"{path.name}.broken-{int(time.time())}")
        try:
            os.replace(path, backup)
            print(
                f"[!] Progress file appears corrupted ({exc!r}); "
                f"moved to {backup.name} and starting from empty state."
            )
        except OSError as move_exc:
            print(
                f"[!] Progress file appears corrupted ({exc!r}) and could not be "
                f"renamed for backup ({move_exc!r}); starting from empty state."
            )
        return {}, 0
    return progress, physical


def load_progress() -> Dict[str, dict]:
    """加载进度文件，返回 {url: {"status": ..., ...}} 字典。

    优先级：新 .jsonl.gz > 旧 .json。两者都不存在时返回空。
    同时初始化 _last_saved_snapshot，使后续 save 的 diff 计算可参照磁盘真实状态。
    """
    global _last_saved_snapshot
    if PROGRESS_FILE.exists():
        progress, physical = _load_jsonl_gz(PROGRESS_FILE)
        _progress_runtime["physical_lines"] = physical
        _last_saved_snapshot = {k: dict(v) for k, v in progress.items()}
        return progress
    if LEGACY_PROGRESS_FILE.exists():
        print(
            f"[*] Legacy progress file detected ({LEGACY_PROGRESS_FILE.name}); "
            f"loading and will write back to {PROGRESS_FILE.name} on next save."
        )
        legacy = _load_legacy_json(LEGACY_PROGRESS_FILE)
        # PROGRESS_FILE 还不存在；snapshot 留空，首次 save 会触发 compact 把全量写入
        _progress_runtime["physical_lines"] = 0
        _last_saved_snapshot = {}
        return legacy
    _progress_runtime["physical_lines"] = 0
    _last_saved_snapshot = {}
    return {}


def _compact_progress(progress: Dict[str, dict]) -> None:
    """整文件原子重写：丢弃历史 append，只保留每个 url 的最新状态。"""
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = PROGRESS_FILE.with_suffix(PROGRESS_FILE.suffix + ".tmp")
    try:
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            meta = _meta_line()
            meta["record_count"] = len(progress)
            f.write(json.dumps(meta, ensure_ascii=False) + "\n")
            for url, rec_meta in progress.items():
                rec = _meta_to_record(url, rec_meta)
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        os.replace(tmp, PROGRESS_FILE)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    _progress_runtime["physical_lines"] = len(progress)


def _append_progress(records: List[dict]) -> None:
    """把若干 record append 到 jsonl.gz 末尾。gzip 模块支持多流串联，read 端透明可读。

    特殊情况：若 PROGRESS_FILE 还不存在，则改走 ``_compact_progress`` 的原子
    路径（``.tmp + os.replace``）而不是直接 ``gzip.open("ab")``——后者在写到
    一半被打断时会留下一个 *只含半段 gzip 流* 的文件，导致下次启动加载失败。
    """
    if not records:
        return
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not PROGRESS_FILE.exists():
        # 首文件改走 compact 路径，享受原子替换语义。
        seed: Dict[str, dict] = {}
        for rec in records:
            url = rec.get("url")
            if not url:
                continue
            seed[url] = _record_to_meta(rec)
        _compact_progress(seed)
        return
    with gzip.open(PROGRESS_FILE, "ab") as raw:
        # gzip.open ab 不直接接受 text；自己手工编码以保证可控
        buf_lines = []
        for rec in records:
            buf_lines.append(json.dumps(rec, ensure_ascii=False))
        payload = ("\n".join(buf_lines) + "\n").encode("utf-8")
        raw.write(payload)
        try:
            raw.flush()
        except Exception:
            pass
    _progress_runtime["physical_lines"] += len(records)


def save_progress(processed: Dict[str, dict]) -> None:
    """保存进度。

    策略：
      - 首次调用或文件不存在：整文件 compact 写入（含 meta 行）。
      - 增量调用：append 自上次 save 以来发生变化（新增或 meta 变化）的 url。
      - 当物理行数膨胀至 live_keys * COMPACTION_RATIO 时，自动 compact。

    并发：整个临界区受 ``_progress_lock`` 保护；入口处先对 ``processed`` 拍
    一个浅快照（``list(processed.items())``），避免 diff 阶段被 worker 线程
    并发改写 dict 时触发 ``RuntimeError: dictionary changed size``。
    """
    global _last_saved_snapshot

    with _progress_lock:
        # 浅快照：dict 顶层 keys/values 在锁内不再变化；meta dict 是只读消费，
        # worker 线程后续写入只会"替换 processed[url]"而不会原地改这里捕获到的 meta 对象。
        snapshot_items = list(processed.items())

        if not PROGRESS_FILE.exists():
            seed_dict = {url: meta for url, meta in snapshot_items}
            _compact_progress(seed_dict)
            _last_saved_snapshot = {k: dict(v) for k, v in seed_dict.items()}
            return

        # 计算 diff（新增 + 修改）。删除场景在本工作流中不存在，无需处理。
        dirty: List[dict] = []
        for url, meta in snapshot_items:
            snap = _last_saved_snapshot.get(url)
            if snap is None or snap != meta:
                dirty.append(_meta_to_record(url, meta))

        if not dirty:
            return

        _append_progress(dirty)

        physical = _progress_runtime.get("physical_lines", 0)
        live = len(snapshot_items)
        if live > 0 and physical > live * PROGRESS_COMPACTION_RATIO:
            print(
                f"[*] Compacting progress file: physical_lines={physical}, live_keys={live} "
                f"(ratio={physical / live:.2f} > {PROGRESS_COMPACTION_RATIO})"
            )
            _compact_progress({url: meta for url, meta in snapshot_items})

        _last_saved_snapshot = {url: dict(meta) for url, meta in snapshot_items}


# ---------- 预检统计 ----------
def preflight_check(papers: List[dict]):
    """运行前统计，帮助用户确认目标和预期。"""
    total = len(papers)
    empty = [p for p in papers if not (p.get("paper_abstract") or "").strip()]
    empty_count = len(empty)

    doi_empty = 0
    non_doi_empty = 0
    host_counts = {}
    year_counts = {}
    conf_counts = {}

    for p in empty:
        url = p.get("paper_url", "")
        host = urlparse(url).netloc.lower()
        conf = p.get("conf", "UNKNOWN")
        year_match = re.search(r"\d{4}", conf)
        year = year_match.group(0) if year_match else "UNKNOWN"

        host_counts[host] = host_counts.get(host, 0) + 1
        year_counts[year] = year_counts.get(year, 0) + 1
        conf_counts[conf] = conf_counts.get(conf, 0) + 1

        if host in ("doi.org",) or host.endswith(".doi.org"):
            doi_empty += 1
        else:
            non_doi_empty += 1

    print("=" * 60)
    print("[*] Preflight Check")
    print("=" * 60)
    print(f"    Total papers in cache      : {total}")
    print(f"    Papers with abstract       : {total - empty_count}")
    empty_ratio = (empty_count / total * 100) if total else 0.0
    print(f"    Papers with EMPTY abstract : {empty_count} ({empty_ratio:.1f}%)")
    print(f"")
    print(f"    Empty abstract by URL type:")
    print(f"      DOI (doi.org)            : {doi_empty}")
    print(f"      Non-DOI                  : {non_doi_empty}")
    print(f"")
    print(f"    Empty abstract by year:")
    for y in sorted(year_counts.keys())[:10]:
        print(f"      {y}: {year_counts[y]}")
    print(f"")
    print(f"    Top 10 conferences with empty abstract:")
    for conf, n in sorted(conf_counts.items(), key=lambda x: x[1], reverse=True)[:10]:
        print(f"      {conf}: {n}")
    print("=" * 60)
    print("")


# ---------- Conf 粒度辅助函数 ----------
def list_pending_confs(papers: List[dict]) -> List[Tuple[str, dict]]:
    """扫描所有论文，返回按优先级排序的待处理 conf 列表。

    排序规则（优先级从高到低）：
        1. Tier 越小越优先（顶级会议 > 重要会议 > 期刊 > 其他）
        2. DOI empty 比例越高越优先（API 获取成功率高）
        3. Empty 总数越多越优先（ROI 高）
    """
    conf_stats: Dict[str, dict] = {}
    for p in papers:
        conf = p.get("conf", "UNKNOWN")
        has_abs = bool((p.get("paper_abstract") or "").strip())
        url = p.get("paper_url", "")
        host = urlparse(url).netloc.lower()
        is_doi = host == "doi.org" or host.endswith(".doi.org")

        if conf not in conf_stats:
            conf_stats[conf] = {"total": 0, "has": 0, "empty": 0, "doi_empty": 0}
        conf_stats[conf]["total"] += 1
        if has_abs:
            conf_stats[conf]["has"] += 1
        else:
            conf_stats[conf]["empty"] += 1
            if is_doi:
                conf_stats[conf]["doi_empty"] += 1

    empty_confs = {k: v for k, v in conf_stats.items() if v["empty"] > 0}
    return sorted(
        empty_confs.items(),
        key=lambda x: (
            _get_conf_tier(x[0]),
            -(x[1]["doi_empty"] / x[1]["empty"] if x[1]["empty"] > 0 else 0),
            -x[1]["empty"],
        ),
    )


def update_conf_progress_md(conf: str, total: int, success: int, failed: int, elapsed_sec: float):
    """更新 docs/abstract_backfill_progress.md，将指定 conf 从'待处理'移到'已完成'。"""
    md_path = Path("docs/abstract_backfill_progress.md")
    if not md_path.exists():
        return

    with open(md_path, "r", encoding="utf-8") as f:
        content = f.read()

    lines = content.splitlines()
    new_lines = []
    found_pending = False

    for line in lines:
        if not found_pending and line.startswith("|") and conf in line and "待处理" in line:
            found_pending = True
            continue
        new_lines.append(line)

    if found_pending:
        done_header = "| Conf | 总数 | 成功 | 失败 | 完成时间 | 耗时 |"
        elapsed_str = "~{:.0f}min".format(elapsed_sec / 60) if elapsed_sec < 3600 else "~{:.1f}h".format(elapsed_sec / 3600)
        done_line = "| {} | {} | {} | {} | {} | {} |".format(
            conf, total, success, failed,
            time.strftime("%Y-%m-%d %H:%M"),
            elapsed_str
        )
        for i, line in enumerate(new_lines):
            if line.strip() == done_header:
                new_lines.insert(i + 2, done_line)
                break

        log_line = "- {}: 完成 {} (成功 {}/{}，耗时 {:.0f}s)".format(
            time.strftime("%Y-%m-%d %H:%M"), conf, success, total, elapsed_sec
        )
        for i, line in enumerate(new_lines):
            if line.startswith("## 执行日志"):
                new_lines.insert(i + 2, log_line)
                break

        with open(md_path, "w", encoding="utf-8") as f:
            f.write("\n".join(new_lines) + "\n")


def match_conf_pattern(pattern: str, confs: Set[str]) -> List[str]:
    """支持通配符匹配 conf 名称。pattern 如 'AAAI*'、'IC*2022'。"""
    if "*" not in pattern:
        return [pattern] if pattern in confs else []
    regex = pattern.replace("*", ".*")
    return sorted([c for c in confs if re.fullmatch(regex, c)])


# ---------- 主流程 ----------
def filter_papers_by_phase(papers: List[dict], phase: str) -> List[dict]:
    if phase == "all":
        return papers

    results = []
    for p in papers:
        url = p.get("paper_url", "")
        host = urlparse(url).netloc.lower()
        is_doi = host == "doi.org" or host.endswith(".doi.org")
        conf = p.get("conf", "")
        conf_name = re.sub(r"\d{4}$", "", conf)

        if phase == "1":
            if is_doi:
                results.append(p)
        elif phase == "2":
            if not is_doi and conf_name in CORE_CONFS:
                results.append(p)
        elif phase == "3":
            if not is_doi and conf_name not in CORE_CONFS:
                results.append(p)
    return results


def _process_targets(
    targets: List[dict],
    all_papers: List[dict],
    chunk_size: int,
    retry_failed: bool,
    retry_partial: bool,
    query_doi_by_title: bool,
    start_time: float = None,
    soft_timeout: float = None,
    max_failed_attempts: int = 3,
    progress: Dict[str, dict] = None,
) -> Tuple[int, int, bool]:
    """处理一组目标论文，返回 (success_count, failed_count, timed_out)。"""
    if progress is None:
        progress = load_progress()
    if not retry_failed and not retry_partial:
        targets = [p for p in targets if p.get("paper_url") not in progress]
    elif retry_partial:
        cache_has_abs = {p.get("paper_url", ""): (p.get("paper_abstract") or "").strip() for p in all_papers}
        partial_urls = {
            url for url, meta in progress.items()
            if meta.get("status") != "success" or not cache_has_abs.get(url, "")
        }
        targets = [p for p in targets if p.get("paper_url") in partial_urls]
        print(f"[*] Retry partial mode: {len(targets)} papers need retry")
    else:
        failed_urls = {
            url for url, meta in progress.items()
            if meta.get("status") == "failed" and meta.get("attempts", 0) < max_failed_attempts
        }
        targets = [p for p in targets if p.get("paper_url") in failed_urls]
        print(f"[*] Retry failed mode: {len(targets)} failed papers to retry (max_attempts={max_failed_attempts})")

    if not targets:
        print("[!] All target papers already processed. Exiting.")
        return 0, 0, False

    last_time = {"crossref": 0.0, "semanticscholar": 0.0, "arxiv": 0.0, "openalex": 0.0}
    success = 0
    failed = 0
    chunk_success = 0
    cache_dirty = False

    for i, paper in enumerate(targets, 1):
        # 软超时检查：达到时限后保存当前进度并优雅退出
        if soft_timeout and start_time is not None and (time.time() - start_time) >= soft_timeout:
            print(f"[!] Soft timeout ({soft_timeout}s) reached at paper {i}/{len(targets)}. Saving progress and exiting.")
            save_progress(progress)
            if cache_dirty:
                tmp_file = CACHE_FILE.with_suffix(".jsonl.gz.tmp")
                with gzip.open(tmp_file, "wt", encoding="utf-8") as f:
                    for p in all_papers:
                        f.write(json.dumps(p, ensure_ascii=False) + "\n")
                os.replace(str(tmp_file), str(CACHE_FILE))
            print(f"[*] Graceful exit. Total success: {success}, failed: {failed}")
            return success, failed, True

        title = (paper.get("paper_name") or "").strip()
        url = paper.get("paper_url", "")
        print(f"[{i}/{len(targets)}] {title[:60]}...")

        abstract, last_time, source = fetch_abstract_for_paper(
            paper, last_time, query_doi_by_title_enabled=query_doi_by_title
        )

        if abstract and len(abstract.strip()) >= 5:
            paper["paper_abstract"] = abstract
            success += 1
            chunk_success += 1
            cache_dirty = True
            print(f"  -> OK [{source}] ({len(abstract)} chars)")
            progress[url] = {
                "status": "success",
                "source": source,
                "chars": len(abstract),
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
        else:
            paper["paper_abstract"] = ""
            failed += 1
            print("  -> Failed")
            old_attempts = progress.get(url, {}).get("attempts", 0)
            progress[url] = {
                "status": "failed",
                "attempts": old_attempts + 1,
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }

        if i % chunk_size == 0 or i == len(targets):
            print(f"[*] Saving progress... (chunk success: {chunk_success}, total success: {success}, failed: {failed})")
            save_progress(progress)
            if cache_dirty:
                tmp_file = CACHE_FILE.with_suffix(".jsonl.gz.tmp")
                with gzip.open(tmp_file, "wt", encoding="utf-8") as f:
                    for p in all_papers:
                        f.write(json.dumps(p, ensure_ascii=False) + "\n")
                os.replace(str(tmp_file), str(CACHE_FILE))
                cache_dirty = False
            chunk_success = 0

    print(f"[*] Done. Success: {success}, Failed: {failed}")
    return success, failed, False


def run(
    phase: str = "1",
    target_conf: Optional[str] = None,
    chunk_size: int = 500,
    retry_failed: bool = False,
    retry_partial: bool = False,
    query_doi_by_title: bool = False,
    list_mode: bool = False,
    batch: bool = False,
    top_n: Optional[int] = None,
    max_papers: Optional[int] = None,
    soft_timeout: float = None,
    max_failed_attempts: int = 3,
) -> None:
    global_start = time.time()
    # Pull the latest cache from Hugging Face before reading/writing anything.
    # This is the canonical source of truth across all workflows.
    ensure_cache_local(CACHE_FILE, refresh=True)
    # Progress file lives on the same HF dataset. Pull it too so multi-machine
    # / multi-day runs share a single coherent progress ledger.
    ensure_progress_local(PROGRESS_FILE, refresh=True)
    print(f"[*] Phase: {phase}, conf: {target_conf or 'all'}, chunk_size: {chunk_size}, max_papers: {max_papers or 'unlimited'}")
    if query_doi_by_title:
        print("[*] DOI query by title: ENABLED (slower, use with caution)")
    if soft_timeout:
        print(f"[*] Soft timeout: {soft_timeout}s ({soft_timeout/3600:.1f}h)")
    print(f"[*] Max failed attempts before permanently skipping: {max_failed_attempts}")

    # 1. 读取所有论文
    all_papers = []
    with gzip.open(CACHE_FILE, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            all_papers.append(json.loads(line))
    print(f"[*] Total papers in cache: {len(all_papers)}")

    # 2. 预检统计
    preflight_check(all_papers)

    empty_papers = [p for p in all_papers if not (p.get("paper_abstract") or "").strip()]
    print(f"[*] Papers with empty abstract: {len(empty_papers)}")

    # batch 模式下预先过滤掉已成功或已永久失败的论文，避免反复选中
    if batch:
        progress = load_progress()
        cache_has_abs = {
            p.get("paper_url", ""): (p.get("paper_abstract") or "").strip()
            for p in all_papers
        }
        skip_urls = set()
        for url, meta in progress.items():
            if meta.get("status") == "success" and cache_has_abs.get(url, ""):
                skip_urls.add(url)
            elif (
                not retry_partial
                and meta.get("status") == "failed"
                and meta.get("attempts", 0) >= max_failed_attempts
            ):
                skip_urls.add(url)
        if skip_urls:
            before = len(empty_papers)
            empty_papers = [p for p in empty_papers if p.get("paper_url") not in skip_urls]
            print(f"[*] Filtered {before - len(empty_papers)} already-success/permanently-failed papers from batch queue")
            print(f"[*] Papers with empty abstract after filtering: {len(empty_papers)}")

    # --- list_mode: 只输出待处理 conf 列表 ---
    if list_mode:
        pending = list_pending_confs(all_papers)
        print("\n[*] Pending conferences (sorted by priority, Tier > DOI-first):")
        hdr = "{:<6} {:<6} {:<20} {:>6} {:>6} {:>6} {:>8}".format("Rank", "Tier", "Conf", "Total", "Empty", "DOI_E", "Pct")
        print(hdr)
        print("-" * 66)
        for i, (conf, s) in enumerate(pending, 1):
            pct = s["doi_empty"] / s["empty"] * 100 if s["empty"] > 0 else 0
            tier = _get_conf_tier(conf)
            print("{:<6} {:<6} {:<20} {:>6} {:>6} {:>6} {:>7.0f}%".format(
                i, f"T{tier}", conf, s["total"], s["empty"], s["doi_empty"], pct
            ))
        print(f"\n[*] Total: {len(pending)} conferences, {sum(s['empty'] for _, s in pending)} papers")
        return

    # --- 确定要处理的 conf 列表 ---
    all_empty_confs = sorted(set(p.get("conf") for p in empty_papers))

    if target_conf:
        matched = match_conf_pattern(target_conf, set(all_empty_confs))
        if not matched:
            print(f"[!] No conference matches pattern: {target_conf}")
            return
        target_confs = matched
        print(f"[*] Matched conferences: {', '.join(target_confs)}")
    elif batch:
        pending = list_pending_confs(all_papers)
        target_confs = [c for c, _ in pending]
        if top_n:
            target_confs = target_confs[:top_n]
        # 预过滤：提前剔除所有论文都已在 progress 中的会议，避免空转
        if not retry_failed and not retry_partial:
            progress = load_progress()
            target_confs = [
                conf for conf in target_confs
                if any(
                    p.get("paper_url") not in progress
                    for p in empty_papers if p.get("conf") == conf
                )
            ]
            print(f"[*] Batch mode: {len(target_confs)} conferences have real pending papers after pre-filter")
        else:
            print(f"[*] Batch mode: will process {len(target_confs)} conferences")
    else:
        # phase 模式（原有逻辑）
        targets = filter_papers_by_phase(empty_papers, phase)
        if max_papers is not None:
            targets = targets[:max_papers]
            print(f"[*] Targets after phase filter & max_papers limit: {len(targets)}")
        else:
            print(f"[*] Targets after phase filter: {len(targets)}")
        if not targets:
            print("[!] No papers to process. Exiting.")
            return
        progress = load_progress()
        success, failed, timed_out = _process_targets(
            targets,
            all_papers,
            chunk_size,
            retry_failed,
            retry_partial,
            query_doi_by_title,
            start_time=global_start,
            soft_timeout=soft_timeout,
            max_failed_attempts=max_failed_attempts,
            progress=progress,
        )
        if success > 0:
            sync_artifacts_after_cache_update("Update PaperVault data artifacts after abstract backfill")
        return

    # --- 逐个 conf 处理 ---
    processed_total = 0
    success_total = 0
    progress = load_progress()
    for conf in target_confs:
        # 软超时检查
        if soft_timeout and global_start is not None and (time.time() - global_start) >= soft_timeout:
            print(f"[*] Soft timeout ({soft_timeout}s) reached. Stopping before conf: {conf}")
            break

        conf_papers = [p for p in empty_papers if p.get("conf") == conf]
        if not conf_papers:
            continue
        # 若指定了 max_papers，按全局剩余额度截断
        if max_papers is not None:
            remaining = max_papers - processed_total
            if remaining <= 0:
                print(f"[*] Max papers limit ({max_papers}) reached. Stopping.")
                break
            if len(conf_papers) > remaining:
                conf_papers = conf_papers[:remaining]
                print(f"[*] Truncated to {remaining} papers due to max_papers limit")

        print(f"\n{'='*60}")
        print(f"[*] Processing conf: {conf} ({len(conf_papers)} papers)")
        print(f"{'='*60}")
        start_ts = time.time()
        success, failed, timed_out = _process_targets(
            conf_papers, all_papers, chunk_size, retry_failed, retry_partial, query_doi_by_title,
            start_time=global_start, soft_timeout=soft_timeout, max_failed_attempts=max_failed_attempts,
            progress=progress,
        )
        attempted = success + failed
        processed_total += attempted
        success_total += success
        elapsed = time.time() - start_ts
        if attempted > 0:
            print(f"[*] Conf {conf} summary: Success={success}, Failed={failed}, Time={elapsed:.0f}s")
            if not timed_out:
                update_conf_progress_md(conf, len(conf_papers), success, failed, elapsed)
            else:
                print(f"[*] Conf {conf} interrupted by soft timeout; skip marking as completed.")
                break

    if success_total > 0:
        sync_artifacts_after_cache_update("Update PaperVault data artifacts after abstract backfill")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill paper abstracts from multiple APIs")
    parser.add_argument("--phase", type=str, default="1", choices=["1", "2", "3", "all"],
                        help="Phase: 1=DOI papers (high ROI), 2=core conf non-DOI, 3=remaining non-DOI, all=everything")
    parser.add_argument("--conf", type=str, default=None, help="Process conference(s), e.g. AAAI2020 or 'AAAI*'")
    parser.add_argument("--chunk-size", type=int, default=500, help="Save progress every N papers")
    parser.add_argument("--retry-failed", action="store_true",
                        help="Retry only papers previously marked as failed")
    parser.add_argument("--retry-partial", action="store_true",
                        help="Retry papers that were interrupted (progress exists but cache not updated)")
    parser.add_argument("--query-doi-by-title", action="store_true",
                        help="Query Crossref for DOI using paper title (slower, optional)")
    parser.add_argument("--list", action="store_true", dest="list_mode",
                        help="List pending conferences sorted by priority and exit")
    parser.add_argument("--batch", action="store_true",
                        help="Batch process all pending conferences in priority order")
    parser.add_argument("--top", type=int, default=None, dest="top_n",
                        help="With --batch, only process top N conferences")
    parser.add_argument("--max-papers", "-n", type=int, default=None, dest="max_papers",
                        help="Maximum number of papers to process in this run (global limit)")
    parser.add_argument("--soft-timeout", type=float, default=None,
                        help="Soft timeout in seconds. Save progress and exit gracefully when reached (e.g. 18000 for 5h)")
    parser.add_argument("--max-failed-attempts", type=int, default=3,
                        help="Max retry attempts for failed papers before permanently skipping them (default: 3)")
    args = parser.parse_args()
    run(
        phase=args.phase,
        target_conf=args.conf,
        chunk_size=args.chunk_size,
        retry_failed=args.retry_failed,
        retry_partial=args.retry_partial,
        query_doi_by_title=args.query_doi_by_title,
        list_mode=args.list_mode,
        batch=args.batch,
        top_n=args.top_n,
        max_papers=args.max_papers,
        soft_timeout=args.soft_timeout,
        max_failed_attempts=args.max_failed_attempts,
    )

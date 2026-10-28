"""修复 web.archive.org 包装的死论文链接（AAAI 2000-2015 / UAI 2000-2014 等）。

缓存中约 4.4k 条记录的 ``paper_url`` 形如
``https://web.archive.org/web/<ts>/<原始 URL>``，原始站已死。本脚本按标题做
**逐条兜底链**查询（``--source auto`` 默认）：

1. **OpenAlex** ``/works?search=``（3 rps）——覆盖最全但匿名配额按 IP/日
   发放且极小（实测全量跑 270 条即 429 断供，Retry-After 数万秒）；
2. **Crossref** ``/works?query.bibliographic=``（2 rps，独立 rate_state）——
   OpenAlex rate_limited / 无候选时落这里；命中即得 ``https://doi.org/<DOI>``；
3. **arXiv** ``export.arxiv.org/api/query?search_query=ti:"..."``（3s 间隔，
   独立 rate_state）——最后兜底；新 URL = 去版本号的
   ``https://arxiv.org/abs/<id>``。arXiv **不过年份守卫**（老论文的 arXiv
   年份是挂出年，与会议年无关）。

门控（PR #228 评审后收紧）：纯 SequenceMatcher ratio（**无包含捷径**）
≥ ``REPAIR_MIN_RATIO``（0.90）；ratio ≥ ``REPAIR_EXACT_RATIO``（0.98）视为
精确匹配、免年份守卫；0.90–0.98 的模糊匹配须过 ``_year_gate``（±1）且
候选缺年份一律拒绝；arXiv 源只接受 ratio ≥ ``ARXIV_MIN_RATIO``（0.98）
的精确匹配且不过年份守卫（老论文的 arXiv 年份是挂出年，与会议年无关）。
候选未过完整接受判定（标题或年份）同样落下一源。某源 ``rate_limited``
或无候选立即落下一源，不重试当前源（每日配额型 429 重试无意义）。

顺带回填：源返回摘要（OpenAlex inverted-index 重建 / Crossref JATS 去标签 /
arXiv summary，均需 ≥MIN_ABSTRACT_CHARS）且记录摘要为空时一并填上
（progress source 记 ``<source>_title``）。Crossref 不带摘要就只修 URL。

冲突处理：``(conf, new_url)`` 已存在时用 ``collector._merge_paper_record``
把 wayback 记录合入既有记录并删除前者；否则原地改写 ``paper_url``。

progress / 审计：
  * 新 URL 复制旧 URL 的进度记录（保持 status）；摘要顺带填上的记 success。
  * REASON_ENUM 没有 ``migrated`` 成员，往 progress 里塞新值会破坏分析器，
    因此迁移痕迹写入独立日志 ``cache/wayback_repair_log.jsonl``（每条一行：
    ts/conf/old_url/new_url/action/abstract_filled/source），旧 URL 的
    progress 记录保持不动。

默认 ``--dry-run``（只输出报告）；``--write`` 才改缓存。缓存写入走与主链
一致的 ``.tmp + os.replace`` 原子方式；结束在**同一进程内**做带
parent_commit 乐观锁的验证式上传 ``_sync_or_die``（除非
--no-sync/--offline/--dry-run），上传失败会以非零退出。

``--soft-timeout`` 到期后停止新查询并正常保存、上传。
``cache/wayback_repair_checkpoint.json`` 记录本轮所有已尝试 URL（包括未改写
记录），每 ``REPAIR_FLUSH_EVERY`` 条原子落盘并随 HF 同步；下次从首个未尝试
URL 续跑，完整一轮结束后自动重置。缓存上传失败时不会推进远端 checkpoint。

用法::

    python scripts/repair_wayback_urls.py                      # dry-run 报告（auto 链）
    python scripts/repair_wayback_urls.py --dry-run --max-papers 10 --source crossref
    python scripts/repair_wayback_urls.py --write --no-sync    # 真正改写，本地不同步
    python scripts/repair_wayback_urls.py --write --soft-timeout 18000
"""

import argparse
import gzip
import json
import os
import difflib
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
    _write_cache_atomic,
    _year_gate,
    clean_abstract,
    load_progress,
    save_progress,
)
from papervault.services.abstract_fetchers.base import MIN_ABSTRACT_CHARS  # noqa: E402

WAYBACK_RE = re.compile(r"web\.archive\.org/web/[^/]+/(https?://.+)$")
WAYBACK_REPAIR_LOG = Path("cache") / "wayback_repair_log.jsonl"
WAYBACK_REPAIR_CHECKPOINT = Path("cache") / "wayback_repair_checkpoint.json"
WAYBACK_CHECKPOINT_SCHEMA = "wayback_repair_checkpoint/v1"

# 每处理 N 条目标落盘一次（cache + progress + 迁移日志 + checkpoint）。
# 长跑在系统休眠 / 进程被终止时只损失最近一个 chunk 的进度。
REPAIR_FLUSH_EVERY = 250

OPENALEX_REPAIR_SELECT = (
    "title,abstract_inverted_index,publication_year,doi,primary_location"
)

# 兜底链各源的限速预算与独立 rate_state（互不占额度）。
CROSSREF_MIN_INTERVAL = 0.5  # 2 rps
ARXIV_MIN_INTERVAL = 3.0
_crossref_rate_state = {"last_time": 0.0}
_arxiv_rate_state = {"last_time": 0.0}

SOURCE_CHAIN = ("openalex", "crossref", "arxiv")

# ---- 候选接受阈值（PR #228 评审后收紧） ----
# 评审发现已写入 HF 的真实污染：Crossref 兜底把二十余条 AAAI 2010-2014
# 论文改到两个无关的 2024 期刊 DOI（10.17816/psaic720-406 等）、UAI2001
# 被改到 PeerJ 表格页；arXiv 源把同一 arxiv id 分给 UAI+AAAI 两篇不同
# 论文。根因是门控太松：``_title_gate`` 的**包含捷径**让短标题被无关长
# 标题吞掉，且候选缺年份时年份守卫默认放行。因此：
#   * REPAIR_MIN_RATIO：模糊匹配下限，纯 SequenceMatcher ratio，无包含捷径；
#   * REPAIR_EXACT_RATIO：视为精确匹配（免年份守卫——在线年≠会议年是常态）；
#   * ARXIV_MIN_RATIO：arXiv 源只接受精确匹配（其年份无会议语义，
#     包含匹配曾被证实会把同 id 分给不同论文）。
REPAIR_MIN_RATIO = 0.90
REPAIR_EXACT_RATIO = 0.98
ARXIV_MIN_RATIO = 0.98


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
        max_retries=2,  # 日配额型 429 重试一次即放弃，快速落下一源
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


def _crossref_repair_fetch(title: str) -> Tuple[Optional[dict], str]:
    """GET api.crossref.org/works?query.bibliographic=<title>&rows=1。

    返回 (items[0] 原始 dict, error_reason)；无候选返回 ``empty_abstract``。
    Crossref 的 ``abstract`` 字段是 JATS XML 包裹的，去标签留给
    ``evaluate_repair``（复用 fetch_abstracts 的 ``clean_abstract``）。
    """
    params = {
        "query.bibliographic": title,
        "rows": 1,
        # published 之外补 issued / published-print：很多老会议记录只有后者，
        # 只取 published 会把年份误判为缺失（模糊候选因此被一律拒收）。
        "select": "DOI,title,published,published-print,issued,abstract",
    }
    if CONTACT_EMAIL:
        params["mailto"] = CONTACT_EMAIL
    resp, err = _s2_request(
        "get",
        "https://api.crossref.org/works",
        params=params,
        timeout=30,
        min_interval=CROSSREF_MIN_INTERVAL,
        headers=HEADERS,
        rate_state=_crossref_rate_state,
        max_retries=2,  # 日配额型 429 重试一次即放弃，快速落下一源
    )
    if err or resp is None:
        return None, err or "network"
    if resp.status_code != 200:
        return None, _s2_status_reason(resp.status_code)
    try:
        data = resp.json()
    except ValueError:
        return None, "network"
    items = (data.get("message") or {}).get("items") if isinstance(data, dict) else None
    if not items:
        return None, "empty_abstract"
    first = items[0]
    if not isinstance(first, dict):
        return None, "empty_abstract"
    return first, ""


def _arxiv_repair_fetch(title: str) -> Tuple[Optional[dict], str]:
    """GET export.arxiv.org Atom API（ti:"<title>" 短语查询，max_results=1）。

    返回规范化 dict ``{"title", "abs_url", "summary"}``：``abs_url`` 是去掉
    版本后缀的 ``https://arxiv.org/abs/<id>``。无候选返回 ``empty_abstract``。
    """
    import xml.etree.ElementTree as ET

    import requests as _requests

    encoded = _requests.utils.quote(title)
    url = f'https://export.arxiv.org/api/query?search_query=ti:"{encoded}"&max_results=1'
    resp, err = _s2_request(
        "get",
        url,
        timeout=30,
        min_interval=ARXIV_MIN_INTERVAL,
        headers=HEADERS,
        rate_state=_arxiv_rate_state,
        max_retries=2,  # 日配额型 429 重试一次即放弃，快速落下一源
    )
    if err or resp is None:
        return None, err or "network"
    if resp.status_code != 200:
        return None, _s2_status_reason(resp.status_code)
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError:
        return None, "network"
    entry = root.find("atom:entry", ns)
    if entry is None:
        return None, "empty_abstract"
    id_elem = entry.find("atom:id", ns)
    entry_id = (id_elem.text or "").strip() if id_elem is not None else ""
    if not entry_id:
        return None, "empty_abstract"
    abs_url = re.sub(r"v\d+$", "", entry_id)
    if abs_url.startswith("http://"):
        abs_url = "https://" + abs_url[len("http://"):]
    # 安全校验：entry id 必须指向 https 的 arxiv.org，其它 host 一律丢弃
    #（防上游被劫持/畸形响应把外链写进缓存）。
    if not abs_url.startswith("https://") or _host_of(abs_url) != "arxiv.org":
        return None, "empty_abstract"
    title_elem = entry.find("atom:title", ns)
    summary_elem = entry.find("atom:summary", ns)
    return {
        "title": (title_elem.text or "").strip() if title_elem is not None else "",
        "abs_url": abs_url,
        "summary": (summary_elem.text or "").strip() if summary_elem is not None else "",
    }, ""


def _fetch_by_source(source: str, title: str) -> Tuple[Optional[dict], str]:
    if source == "openalex":
        return _openalex_repair_fetch(title)
    if source == "crossref":
        return _crossref_repair_fetch(title)
    if source == "arxiv":
        return _arxiv_repair_fetch(title)
    raise ValueError(f"unknown repair source: {source}")


def _first_title(raw) -> str:
    """Crossref 的 ``title`` 字段是列表形态；其余源是字符串。统一取首个。"""
    if isinstance(raw, list):
        return (raw[0] if raw else "") or ""
    return raw or ""


def _norm_title(t: str) -> str:
    """与 ``fetch_abstracts._title_gate`` 相同的归一化（去非词字符小写）。"""
    return re.sub(r"[^\w]+", "", (t or "").strip().lower())


def _title_ratio(a: str, b: str) -> float:
    na, nb = _norm_title(a), _norm_title(b)
    if not na or not nb:
        return 0.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def _candidate_year(source: str, work: dict):
    """按源提取候选年份（arxiv 无年份语义，恒 None）。

    Crossref 按 ``published → published-print → issued`` 顺序回退取
    ``date-parts[0][0]``——老会议记录常只有 ``issued``/``published-print``。
    """
    if source == "crossref":
        for field in ("published", "published-print", "issued"):
            parts = (work.get(field) or {}).get("date-parts") or []
            if parts and parts[0] and parts[0][0]:
                return parts[0][0]
        return None
    if source == "openalex":
        return work.get("publication_year")
    return None


def _candidate_accepted(cand_title: str, cand_year, title: str, conf: str, source: str) -> bool:
    """完整的候选接受判定（链路预过滤 + evaluate_repair 防御共用）。

    * arxiv：只接受归一化 ratio ≥ ARXIV_MIN_RATIO 的精确匹配（无年份守卫）；
    * crossref / openalex：纯 ratio ≥ REPAIR_MIN_RATIO（无包含捷径）；
      ratio ≥ REPAIR_EXACT_RATIO 视为精确匹配、免年份守卫；
      其余模糊匹配必须过年份守卫（±1），候选缺年份时一律拒绝
      （缺年份 + 模糊匹配正是 PR #228 污染的根因组合）。
    """
    ratio = _title_ratio(cand_title, title)
    if ratio <= 0.0:
        return False
    if source == "arxiv":
        return ratio >= ARXIV_MIN_RATIO
    if ratio >= REPAIR_EXACT_RATIO:
        return True
    if ratio < REPAIR_MIN_RATIO:
        return False
    if not cand_year:
        return False
    return _year_gate(conf, cand_year)


def make_lookup(source: str = "auto"):
    """构造 per-record 兜底链 ``lookup(title, conf) -> (source, work, err)``。

    ``auto``：OpenAlex → Crossref → arXiv。某源 ``rate_limited`` 或无候选
    （``empty_abstract``）立即落下一源，不重试当前源（每日配额型 429 重试
    无意义）。**候选拿到但完整接受判定（:func:`_candidate_accepted`：标题
    ratio 阈值 + 按源的年份规则）失败同样落下一源**：Crossref 的
    ``query.bibliographic`` 模糊搜索对库外论文几乎总会返回"某个东西"，
    把这种候选当终点既会挡住 arXiv 兜底（2026-10-25 第二轮实测 3,817 条
    因此滞留），也曾把老论文写到无关期刊 DOI（PR #228 评审）。
    attempts 里该情形记 ``title_mismatch``（``year_mismatch`` 不在
    REASON_ENUM，分析器只认枚举成员，故归并入 title_mismatch）。
    单源模式（``--source crossref`` 等）只查对应源。
    """
    chain = list(SOURCE_CHAIN) if source == "auto" else [source]

    def lookup(title: str, conf: str = ""):
        """返回 (source, work, err, attempts)；attempts 是 [(src, err), ...]
        逐源失败记录，供报告按源统计 rate_limited 计数。"""
        attempts = []
        last_err = ""
        saw_title_mismatch = False
        for src in chain:
            work, err = _fetch_by_source(src, title)
            if work is not None:
                if _candidate_accepted(
                    _first_title(work.get("title")),
                    _candidate_year(src, work),
                    title,
                    conf,
                    src,
                ):
                    return src, work, "", attempts
                saw_title_mismatch = True
                attempts.append((src, "title_mismatch"))
                continue
            last_err = err or "empty_abstract"
            attempts.append((src, last_err))
            # 网络类错误（timeout/network）也落下一源，但 Crossref 挂了
            # arXiv 大概率也连不上——仍然尝试，成本是一次快失败。
        if saw_title_mismatch:
            # 有源返回过"像但不对"的候选比"查无此文"信息量更高，优先汇报。
            last_err = "title_mismatch"
        return None, None, last_err, attempts

    return lookup


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


def evaluate_repair(
    work: Optional[dict], paper: dict, source: str = "openalex"
) -> Tuple[Optional[str], Optional[str], str]:
    """门控 + 新 URL 选择 + 顺带摘要。

    返回 (new_url, abstract_or_None, reason)。reason 为空串表示通过门控；
    new_url 为 None 表示不改链接（无 DOI/可用 landing page）。

    ``source`` 感知各源的数据形态与年份语义：
      * ``openalex``：``publication_year`` + inverted-index 摘要；
      * ``crossref``：``published.date-parts[0][0]`` 年份、JATS 摘要（去标签）、
        新 URL 恒为 ``https://doi.org/<DOI>``；
      * ``arxiv``：**不过年份守卫**（老论文的 arXiv 年份是挂出年，与会议年
        无关），新 URL 为去版本号的 abs 页，且只接受归一化
        ratio ≥ ARXIV_MIN_RATIO 的精确标题匹配（无包含捷径）。

    接受判定复用 :func:`_candidate_accepted`（与链路预过滤同一套收紧规则；
    链已预过滤时这里是纯防御，不会冲突）。
    """
    if not work:
        return None, None, "empty_abstract"
    title = (paper.get("paper_name") or "").strip()
    conf = paper.get("conf", "")

    if source == "crossref":
        cand_title = _first_title(work.get("title"))
        year = _candidate_year(source, work)
        doi = (work.get("DOI") or "").strip()
        new_url = f"https://doi.org/{doi}" if doi else None
        abstract = clean_abstract(work.get("abstract") or "")
    elif source == "arxiv":
        cand_title = work.get("title") or ""
        year = None  # arXiv 不过年份守卫
        new_url = work.get("abs_url") or None
        abstract = clean_abstract(work.get("summary") or "")
    else:  # openalex
        cand_title = work.get("title") or ""
        year = work.get("publication_year")
        new_url = choose_new_url(
            work, unwrap_wayback(paper.get("paper_url", "")) or paper.get("paper_url", "")
        )
        abstract = clean_abstract(
            _reconstruct_openalex_abstract(work.get("abstract_inverted_index") or {}) or ""
        )

    if not _candidate_accepted(cand_title, year, title, conf, source):
        return None, None, "title_mismatch"
    if len(abstract) < MIN_ABSTRACT_CHARS:
        abstract = None
    return new_url, abstract, ""


def _empty_repair_checkpoint(source: str) -> dict:
    return {
        "source": source,
        "complete": False,
        "attempted": set(),
        "resumed": 0,
        "dirty": False,
    }


def _load_repair_checkpoint(source: str) -> dict:
    checkpoint = _empty_repair_checkpoint(source)
    if not WAYBACK_REPAIR_CHECKPOINT.exists():
        return checkpoint
    try:
        data = json.loads(WAYBACK_REPAIR_CHECKPOINT.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"[!] Ignoring unreadable wayback repair checkpoint: {exc}")
        return checkpoint
    if (
        not isinstance(data, dict)
        or data.get("schema") != WAYBACK_CHECKPOINT_SCHEMA
        or data.get("source") != source
        or not isinstance(data.get("attempted"), list)
    ):
        print("[!] Wayback repair checkpoint schema/source changed; starting a fresh pass.")
        return checkpoint
    if data.get("complete"):
        print("[*] Previous wayback repair pass completed; starting a fresh pass.")
        return checkpoint
    attempted = {url for url in data["attempted"] if isinstance(url, str) and url}
    checkpoint["attempted"] = attempted
    checkpoint["resumed"] = len(attempted)
    print(f"[*] Resuming wayback repair: {len(attempted)} URLs already attempted.")
    return checkpoint


def _write_repair_checkpoint(checkpoint: dict) -> None:
    payload = {
        "schema": WAYBACK_CHECKPOINT_SCHEMA,
        "source": checkpoint["source"],
        "complete": checkpoint["complete"],
        "attempted": sorted(checkpoint["attempted"]),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    WAYBACK_REPAIR_CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = WAYBACK_REPAIR_CHECKPOINT.with_suffix(
        WAYBACK_REPAIR_CHECKPOINT.suffix + ".tmp"
    )
    try:
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp_path, WAYBACK_REPAIR_CHECKPOINT)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
    checkpoint["dirty"] = False


def _repair_pass(
    all_papers: List[dict],
    progress: Dict[str, dict],
    *,
    max_papers: Optional[int],
    dry_run: bool,
    lookup_fn=None,
    soft_timeout: Optional[float] = None,
    start_time: Optional[float] = None,
    checkpoint: Optional[dict] = None,
) -> dict:
    """核心 pass。dry-run 下不改任何数据。返回统计字典。

    ``lookup_fn(title, conf) -> (source, work, err)`` —— 默认
    :func:`make_lookup()` 的 auto 兜底链（OpenAlex → Crossref → arXiv）。
    """
    if lookup_fn is None:
        lookup_fn = make_lookup("auto")
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
    attempted_before = checkpoint["attempted"] if checkpoint is not None else set()
    pending = [p for p in unwrapped_ok if p["paper_url"] not in attempted_before]
    resume_skipped = len(unwrapped_ok) - len(pending)
    targets = pending[:max_papers] if max_papers is not None else pending

    stats = {
        "total_wayback": len(wayback),
        "unpack_failed": unpack_fail,
        "attempted": 0,
        "processed": 0,
        "rewrite": 0,
        "merge": 0,
        "unchanged": 0,
        "abstract_filled": 0,
        "resumed": checkpoint["resumed"] if checkpoint is not None else 0,
        "resume_skipped": resume_skipped,
        "remaining": len(pending),
        "timed_out": False,
        "orig_hosts": {},
        "new_hosts": {},
        "reasons": {},
        "by_source": {},  # source -> {"rewrite","merge","rate_limited","no_candidate"}
        "samples": [],
    }
    log_lines: List[dict] = []
    drop_ids = set()

    def maybe_flush(i: int) -> None:
        if not dry_run and i % REPAIR_FLUSH_EVERY == 0:
            _flush_repair_writes(
                all_papers, progress, drop_ids, log_lines, checkpoint=checkpoint
            )
            print(f"[*] flushed at record {i}/{len(targets)}")

    for i, paper in enumerate(targets, 1):
        if (
            soft_timeout is not None
            and start_time is not None
            and time.monotonic() - start_time >= soft_timeout
        ):
            print(
                f"[!] Soft timeout ({soft_timeout}s) reached at record "
                f"{i}/{len(targets)}. Saving checkpoint and exiting the lookup loop."
            )
            stats["timed_out"] = True
            break
        old_url = paper["paper_url"]
        orig = unwrap_wayback(old_url)
        orig_host = _host_of(orig)
        stats["orig_hosts"][orig_host] = stats["orig_hosts"].get(orig_host, 0) + 1
        title = (paper.get("paper_name") or "").strip()
        conf = paper.get("conf", "")
        print(f"[{i}/{len(targets)}] [{conf}] {title[:60]}")
        src, work, err, attempts = lookup_fn(title, conf)
        stats["attempted"] += 1
        if checkpoint is not None:
            checkpoint["attempted"].add(old_url)
            checkpoint["dirty"] = True
        # 逐源失败记录入账（每源 rate_limited 计数进报告）。
        for attempt_src, attempt_err in attempts:
            b = stats["by_source"].setdefault(
                attempt_src, {"rewrite": 0, "merge": 0, "rate_limited": 0, "no_candidate": 0}
            )
            if attempt_err == "rate_limited":
                b["rate_limited"] += 1
            else:
                b["no_candidate"] += 1
        src_bucket = stats["by_source"].setdefault(
            src or "none", {"rewrite": 0, "merge": 0, "rate_limited": 0, "no_candidate": 0}
        )
        if not work:
            # 请求级失败（429/网络/无候选）：只记一次 reason，不再进评估门控
            reason = err or "empty_abstract"
            stats["reasons"][reason] = stats["reasons"].get(reason, 0) + 1
            if reason == "rate_limited":
                src_bucket["rate_limited"] += 1
            else:
                src_bucket["no_candidate"] += 1
            stats["unchanged"] += 1
            maybe_flush(i)
            continue
        new_url, abstract, reason = evaluate_repair(work, paper, source=src)
        if reason:
            stats["reasons"][reason] = stats["reasons"].get(reason, 0) + 1
            stats["unchanged"] += 1
            maybe_flush(i)
            continue
        if not new_url:
            stats["reasons"]["no_usable_url"] = stats["reasons"].get("no_usable_url", 0) + 1
            stats["unchanged"] += 1
            maybe_flush(i)
            continue

        stats["new_hosts"][_host_of(new_url)] = stats["new_hosts"].get(_host_of(new_url), 0) + 1
        abstract_filled = bool(abstract) and not (paper.get("paper_abstract") or "").strip()
        existing = index.get((paper.get("conf", ""), new_url))
        action = "merge" if (existing is not None and existing is not paper) else "rewrite"
        src_bucket[action] += 1

        if len(stats["samples"]) < 12:
            stats["samples"].append((old_url, new_url, action, abstract_filled, src))

        stats["processed"] += 1
        stats[action] += 1
        if abstract_filled:
            stats["abstract_filled"] += 1
        if dry_run:
            maybe_flush(i)
            continue

        # ---- 真正改写 ----
        # merge 会删除原 wayback 记录；先把其原始内容捕获进日志（回滚依据）。
        old_payload = {}
        if action == "merge":
            old_payload = {
                "old_title": paper.get("paper_name", ""),
                "old_authors": paper.get("paper_authors") or [],
                "old_abstract": paper.get("paper_abstract") or "",
                # _merge_paper_record 也合并 paper_code；缺了它无法完整重建被删记录。
                "old_code": paper.get("paper_code") or "#",
            }
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
                "source": f"{src}_title",
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
            "source": src,
            **old_payload,
        })
        maybe_flush(i)

    stats["remaining"] = len(pending) - stats["attempted"]
    if checkpoint is not None and not stats["timed_out"] and stats["remaining"] == 0:
        checkpoint["attempted"].clear()
        checkpoint["complete"] = True
        checkpoint["dirty"] = checkpoint["dirty"] or bool(
            stats["attempted"] or stats["resumed"]
        )
    if not dry_run:
        _flush_repair_writes(all_papers, progress, drop_ids, log_lines, checkpoint=checkpoint)
    return stats


def _flush_repair_writes(
    all_papers: List[dict],
    progress: Dict[str, dict],
    drop_ids: set,
    log_lines: List[dict],
    *,
    checkpoint: Optional[dict] = None,
) -> None:
    """原子落盘 cache/checkpoint，并保存 progress 与迁移日志。"""
    checkpoint_dirty = checkpoint is not None and checkpoint["dirty"]
    if not log_lines and not checkpoint_dirty:
        return
    if log_lines:
        if drop_ids:
            all_papers[:] = [p for p in all_papers if id(p) not in drop_ids]
            drop_ids.clear()
        _write_cache_atomic(all_papers)
        save_progress(progress)
        WAYBACK_REPAIR_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(WAYBACK_REPAIR_LOG, "a", encoding="utf-8") as f:
            for line in log_lines:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
        log_lines.clear()
    if checkpoint_dirty:
        _write_repair_checkpoint(checkpoint)


def _print_report(stats: dict, dry_run: bool) -> None:
    mode = "DRY-RUN (no writes)" if dry_run else "WRITE"
    print(f"\n===== wayback repair report [{mode}] =====")
    print(f"wayback records in cache : {stats['total_wayback']}")
    print(f"unpack failed            : {stats['unpack_failed']}")
    print(f"attempted this run       : {stats['attempted']}")
    print(f"resume skipped           : {stats['resume_skipped']}")
    print(f"remaining for next run   : {stats['remaining']}")
    print(f"soft timeout reached     : {'yes' if stats['timed_out'] else 'no'}")
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
    if stats["by_source"]:
        print("by source                :")
        for src, b in sorted(stats["by_source"].items()):
            print(
                f"    {src:15} rewrite={b['rewrite']} merge={b['merge']} "
                f"rate_limited={b['rate_limited']} no_candidate={b['no_candidate']}"
            )
    if stats["reasons"]:
        print("unchanged reasons        :")
        for r, n in sorted(stats["reasons"].items(), key=lambda kv: -kv[1]):
            print(f"    {r:35} {n}")
    if stats["samples"]:
        print("samples                  :")
        for old, new, action, filled, src in stats["samples"]:
            mark = " +abstract" if filled else ""
            print(f"    [{action} via {src}]{mark}\n      - {old}\n      + {new}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Repair web.archive.org-wrapped dead paper URLs via OpenAlex")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", dest="dry_run", action="store_true", default=True,
                   help="Report only (default)")
    g.add_argument("--write", dest="dry_run", action="store_false",
                   help="Actually rewrite the cache/progress")
    ap.add_argument("--max-papers", "-n", type=int, default=None)
    ap.add_argument("--source", choices=["auto", "openalex", "crossref", "arxiv"],
                    default="auto",
                    help="查询源：auto=OpenAlex→Crossref→arXiv 兜底链（默认）；单源则只查对应源")
    ap.add_argument("--soft-timeout", type=float, default=None,
                    help="停止发起新查询的秒数；到期后保存 checkpoint 并继续 HF 同步")
    ap.add_argument("--no-sync", action="store_true", help="Skip the final Hugging Face push")
    ap.add_argument("--offline", action="store_true",
                    help="Skip ensure_cache_local / ensure_progress_local AND the final sync")
    args = ap.parse_args()
    if args.soft_timeout is not None and args.soft_timeout < 0:
        ap.error("--soft-timeout must be non-negative")
    _install_utf8_console()

    t0 = time.monotonic()
    if not args.offline:
        ensure_cache_local(CACHE_FILE, refresh=True)
        ensure_progress_local(PROGRESS_FILE, refresh=True)
        if not args.dry_run:
            ensure_cache_local(WAYBACK_REPAIR_CHECKPOINT, refresh=True)
    if args.soft_timeout is not None:
        print(f"[*] Soft timeout: {args.soft_timeout}s ({args.soft_timeout / 3600:.1f}h)")

    all_papers: List[dict] = []
    with gzip.open(CACHE_FILE, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                all_papers.append(json.loads(line))
    print(f"[*] Total papers in cache: {len(all_papers)}")
    progress = load_progress()
    checkpoint = None if args.dry_run else _load_repair_checkpoint(args.source)

    stats = _repair_pass(
        all_papers, progress,
        max_papers=args.max_papers, dry_run=args.dry_run,
        lookup_fn=make_lookup(args.source),
        soft_timeout=args.soft_timeout, start_time=t0,
        checkpoint=checkpoint,
    )
    _print_report(stats, args.dry_run)
    print(f"[*] Done in {time.monotonic() - t0:.0f}s")

    if args.dry_run:
        print("[*] Dry run: no cache/progress/checkpoint writes, no Hugging Face sync.")
        return
    checkpoint_changed = bool(stats["attempted"] or stats["resumed"])
    if checkpoint_changed and not (args.no_sync or args.offline):
        _sync_or_die(cache_changed=stats["processed"] > 0)
    elif checkpoint_changed:
        print(
            "[*] --no-sync/--offline set; skipped Hugging Face push. "
            "Local cache and repair checkpoint have been updated; remember to sync "
            "before any other workflow touches HF."
        )


def _sync_or_die(cache_changed: bool = True) -> None:
    """Verified in-process upload of repair artifacts to Hugging Face.

    Cache/progress are uploaded and verified before the checkpoint. A failed
    cache upload therefore cannot advance the remote checkpoint past repairs
    that were not persisted. A checkpoint-only run avoids re-uploading the
    large cache file.
    """
    commit_message = "Repair web.archive.org-wrapped dead paper URLs"
    if cache_changed:
        targets = [CACHE_FILE]
        progress_included = PROGRESS_FILE.exists()
        if progress_included:
            targets.append(PROGRESS_FILE)
        uploaded = upload_to_huggingface(targets, commit_message=commit_message)
        if "cache/cache.jsonl.gz" not in uploaded:
            print("[!] FATAL: cache.jsonl.gz was NOT confirmed uploaded to Hugging Face.")
            sys.exit(1)
        if progress_included and "cache/abstract_backfill_progress.jsonl.gz" not in uploaded:
            print("[!] WARNING: progress file was not confirmed uploaded (cache is safe).")

    checkpoint_path = "cache/wayback_repair_checkpoint.json"
    uploaded = upload_to_huggingface(
        [WAYBACK_REPAIR_CHECKPOINT], commit_message=commit_message
    )
    if checkpoint_path not in uploaded:
        print("[!] FATAL: wayback repair checkpoint was NOT confirmed uploaded to Hugging Face.")
        sys.exit(1)


if __name__ == "__main__":
    main()

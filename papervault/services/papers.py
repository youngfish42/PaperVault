"""In-memory paper repository + search service.

The legacy ``app.py`` kept everything as module-level globals; here we wrap
loading and querying behind a small class so it can be cached on the Flask
application, replaced for tests, and reasoned about independently from HTTP.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from collector import load_cache
from data_artifacts import ensure_cache_local

logger = logging.getLogger("papervault.papers")

_YEAR_RE = re.compile(r"\d{4}")
_TRAILING_YEAR_RE = re.compile(r"\d{4}(.*)$")
_WS_RE = re.compile(r"\s+")


@dataclass(slots=True)
class Paper:
    id: str
    conf: str
    year: str
    title: str
    title_format: str
    url: Optional[str]
    authors: List[str]
    abstract: Optional[str]
    code: Optional[str]
    # 以下两个字段在加载期一次性算好，专供 search_papers 内的打分函数
    # 复用，避免每次查询都对每篇论文重新调用 ``str.lower`` / ``join``。
    # 语料规模在万级以上时，把这些 O(语料 * 查询) 的常量开销下沉到
    # O(语料) 是一次显著的吞吐优化（参见服务监控里搜索 P95）。
    #
    # * ``abstract_lower``：``abstract.lower()``；无摘要时为 ``None``，
    #   避免对 ~73% 没有摘要的论文徒占内存。
    # * ``authors_joined_lower``：作者列表小写、去连字符后再 ``" ".join``
    #   的结果，与 ``_normalize`` 的处理对齐，可直接用 ``in`` 子串匹配。
    abstract_lower: Optional[str] = None
    authors_joined_lower: str = ""


@dataclass
class PaperRepository:
    cache_path: Path
    refresh_on_load: bool = True

    _papers: List[Paper] = field(default_factory=list, init=False)
    _by_conf: Dict[str, List[Paper]] = field(default_factory=dict, init=False)
    _loaded: bool = field(default=False, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            self._load()
            self._loaded = True

    def reload(self) -> None:
        with self._lock:
            self._papers = []
            self._by_conf = {}
            self._load()
            self._loaded = True

    def _load(self) -> None:
        ensure_cache_local(str(self.cache_path), refresh=self.refresh_on_load)
        raw = load_cache(str(self.cache_path))

        # cache.jsonl.gz 由 collector 持续追加写入，历史版本从未去重。
        # Paper.id 由 (conf, year, title) 决定，所以重复行会产生相同的 id。
        # 在构建索引前丢弃重复行，避免搜索结果与 UI 出现同一篇论文多次。
        #
        # 注意：本层为运行时**兜底防御**，采用「先到先得」策略——仅按 pid
        # 丢弃后续重复行，**不做字段级合并**（不会用后续行的更长 abstract /
        # 更全 authors / 真实 code 链接升级已记录的 Paper）。
        # 若 cache 中先一行 abstract 为空、后一行同 paper 的 abstract 非空
        # （例如 backfill 追加但未及时压缩），加载期会保留空 abstract、
        # 丢弃真 abstract。
        # 信息完整性应由上游保证：
        #   * collector 端 `_merge_paper_record`（PR #93）做字段级合并；
        #   * `scripts/cleanup_cache_dedupe.py`（PR #94）离线清洗 cache，
        #     已经做完字段级合并。
        # 本层只负责保证「同一 Paper.id 不在索引中出现两次」这一最小契约。
        seen_ids: set = set()
        dropped = 0

        for conf_key, papers in raw.items():
            year_match = _YEAR_RE.search(conf_key)
            if year_match is None:
                logger.warning("Skip conf without year in key: %s", conf_key)
                continue
            year = year_match.group()
            conf_name = _TRAILING_YEAR_RE.sub("", conf_key).strip().upper()

            for paper in papers:
                title = paper.get("paper_name") or ""
                pid = hashlib.sha1(
                    f"{conf_name}|{year}|{title}".encode("utf-8")
                ).hexdigest()[:16]
                if pid in seen_ids:
                    dropped += 1
                    continue
                seen_ids.add(pid)
                authors = paper.get("paper_authors") or []
                if not isinstance(authors, list):
                    authors = [str(authors)]
                authors_list = [str(a) for a in authors]
                normalized_title = _WS_RE.sub(" ", title).strip().lower().replace("-", " ")
                abstract = paper.get("paper_abstract")
                # 在加载期一次性算好供搜索打分使用的派生字段。``abstract_lower``
                # 为 ``None`` 表示原文就没有摘要，保留 ``None`` 而不是 ``""``
                # 以便上游沿用 ``if not abstract`` 的快速判空习惯。
                abstract_lower = abstract.lower() if abstract else None
                authors_joined_lower = " ".join(
                    a.lower().replace("-", " ") for a in authors_list
                )
                rec = Paper(
                    id=pid,
                    conf=conf_name,
                    year=year,
                    title=title,
                    title_format=normalized_title,
                    url=paper.get("paper_url"),
                    authors=authors_list,
                    abstract=abstract,
                    code=paper.get("paper_code"),
                    abstract_lower=abstract_lower,
                    authors_joined_lower=authors_joined_lower,
                )
                self._papers.append(rec)
                self._by_conf.setdefault(conf_name, []).append(rec)

        logger.info(
            "Loaded %d papers across %d conferences (%d duplicate rows dropped)",
            len(self._papers),
            len(self._by_conf),
            dropped,
        )

    def all_papers(self) -> List[Paper]:
        self.ensure_loaded()
        return self._papers

    def confs(self) -> Dict[str, List[Paper]]:
        self.ensure_loaded()
        return self._by_conf

    def support_confs(self) -> List[str]:
        return sorted(self.confs().keys())


@dataclass(slots=True)
class SearchCriteria:
    query: Optional[str]
    field: str  # title | author | any
    confs: List[str]
    since: Optional[int]
    until: Optional[int]
    author: Optional[str]
    sort: str  # -year | year | -title | title | -conf | conf
    page: int
    size: int


def _normalize(s: Optional[str]) -> str:
    if not s:
        return ""
    return _WS_RE.sub(" ", s).strip().lower().replace("-", " ")


# ---------------------------------------------------------------------------
# 打分函数
# ---------------------------------------------------------------------------
#
# 所有 ``_*_score`` 函数都接收已经 *预先切分* 的 ``tokens: List[str]``，
# 调用方在 ``search_papers`` 入口算一次即可——避免对每篇论文都重复
# 执行 ``query.split()``。空 token 列表统一返回 0，调用方据此判空。
#
# 评分语义保持不变：每个 token 独立判断是否作为子串出现在目标字段里，
# 单 token 命中为 1，多 token 取命中数之和（区间 ``[0, len(tokens)]``）。
# 这条规则跨标题 / 摘要 / 作者三个字段统一，便于 ``search_papers`` 的
# per-token AND 判定直接用 ``hits == len(tokens)`` 表达。


def _count_token_hits(haystack: str, tokens: List[str]) -> int:
    """共享的子串命中计数。``haystack`` 已被调用方小写化。"""
    if not tokens or not haystack:
        return 0
    return sum(1 for tok in tokens if tok in haystack)


def _author_score_joined(joined_lower: str, tokens: List[str]) -> int:
    """对已经预算好的作者拼接串计数 token 命中数。

    ``joined_lower`` 应当是 ``Paper.authors_joined_lower``（加载期算好）
    或调用方临时算的等价字符串。这样可以让 ``search_papers`` 在热路径
    上零额外字符串构造。
    """
    return _count_token_hits(joined_lower, tokens)


def _author_score(authors: Iterable[str], needle: str) -> int:
    """兼容旧接口：传入原始作者列表 + 字符串 ``needle``。

    保留供 ``_author_matches`` 兼容 wrapper 以及外部脚本调用。内部热
    路径已改走 ``_author_score_joined``，不再触发这条 ``join``。
    """
    if not needle:
        return 0
    joined = " ".join(a.lower().replace("-", " ") for a in authors)
    return _count_token_hits(joined, needle.split())


def _title_score(paper: Paper, tokens: List[str]) -> int:
    """``paper.title_format`` 已在加载期完成小写与去连字符。"""
    return _count_token_hits(paper.title_format, tokens)


def _abstract_score(paper: Paper, tokens: List[str]) -> int:
    """优先用加载期预算的 ``abstract_lower``；无摘要直接返回 0。"""
    abs_lower = paper.abstract_lower
    if not abs_lower:
        return 0
    return _count_token_hits(abs_lower, tokens)


# 向后兼容的旧谓词。历史外部调用方只关心布尔结果，这里把 _title_matches
# 与 _author_matches 保留为 ``score > 0`` 的薄包装。注意语义已变：原本
# 1/2 token 命中就算 True；现在 per-token AND 在 search_papers 上游强约束
# 必须所有 token 都至少落在一个字段里才入选。这两个谓词保留了较松的
# "任一 token 命中即 True" 行为，避免外部代码意外收紧匹配。
def _author_matches(authors: Iterable[str], needle: str) -> bool:
    return _author_score(authors, needle) > 0


def _title_matches(paper: Paper, query: str) -> bool:
    if not query:
        return False
    return _title_score(paper, query.split()) > 0


def _sort_key(sort: str):
    desc = sort.startswith("-")
    key = sort[1:] if desc else sort

    def _extract(p: Paper):
        if key == "year":
            value: Tuple = (int(p.year),)
        elif key == "conf":
            value = (p.conf,)
        else:
            value = (p.title.lower(),)
        return value

    return _extract, desc


def search_papers(repo: PaperRepository, criteria: SearchCriteria) -> Tuple[List[Paper], int]:
    repo.ensure_loaded()

    confs_filter = {c.upper() for c in criteria.confs} if criteria.confs else None
    author_needle = _normalize(criteria.author) if criteria.author else ""
    query_value = _normalize(criteria.query) if criteria.query else ""
    # ``#`` is the legacy "match-all" sentinel inherited from the original UI.
    # We collapse it to an empty query exactly once here, so every downstream
    # branch (and ``_title_matches``) only ever sees real search tokens.
    if query_value == "#":
        query_value = ""

    use_title = criteria.field in ("title", "any") and query_value != ""
    # 作者字段在 ``field="author"``（仅作者）与 ``field="any"``（标题 +
    # 作者 + 摘要）两种场景下都会被激活；纯标题查询不触碰作者列表。
    use_author = criteria.field in ("author", "any") and query_value != ""
    # 当 ``field`` 是 ``"title"``（默认）或 ``"any"`` 时同时扫描摘要做
    # 模糊加成——单看标题会漏掉很多摘要里讨论该主题的论文。``field="author"``
    # 保持严格：用户既然查作者，摘要内容无关。
    #
    # 摘要的启用条件与标题完全一致（``title`` 与 ``any`` 都开启）。直接
    # 复用 ``use_title`` 而不是再写一遍同样的布尔表达式，避免日后只改
    # 一处导致行为错位。
    use_abstract = use_title

    # 各字段在相关性打分里的权重。标题证据最强、其次作者、最后摘要。
    # 只有当某字段达到 *完整* per-token AND 命中（见下面的 ``has_full``）
    # 时才把它的命中数计入分数；部分命中仍会加分，但不足以单独入选。
    W_TITLE, W_AUTHOR, W_ABSTRACT = 3, 2, 1
    # 把 split 提到循环外：N 篇论文不再重复执行 ``query.split()``。
    # ``author_needle_tokens`` 同理——``criteria.author`` 是一次性输入，
    # 切分一次即可服务于过滤阶段对每篇论文的判定。
    query_tokens = query_value.split() if query_value else []
    author_needle_tokens = author_needle.split() if author_needle else []
    n_query_tokens = len(query_tokens)

    # 累计 ``(paper, score)``。在单次遍历里同时打分，避免后续再走一遍语料。
    scored: List[Tuple[Paper, int]] = []
    for paper in repo.all_papers():
        if confs_filter is not None and paper.conf not in confs_filter:
            continue
        try:
            year_int = int(paper.year)
        except ValueError:
            continue
        if criteria.since is not None and year_int < criteria.since:
            continue
        if criteria.until is not None and year_int > criteria.until:
            continue
        if author_needle_tokens and _author_score_joined(
            paper.authors_joined_lower, author_needle_tokens
        ) == 0:
            continue

        if query_value:
            # 各字段命中数（允许部分命中用于排序，但不用于入选）。三个
            # 字段都消费 *同一份* ``query_tokens``，且作者打分走预算好的
            # ``authors_joined_lower``——本循环内 0 次 ``str.lower`` /
            # ``str.split`` / ``" ".join``。
            t_hits = _title_score(paper, query_tokens) if use_title else 0
            a_hits = _abstract_score(paper, query_tokens) if use_abstract else 0
            auth_hits = (
                _author_score_joined(paper.authors_joined_lower, query_tokens)
                if use_author
                else 0
            )

            # 入选条件：至少一个被搜索字段达到完整 per-token AND。
            # 即上游 UI 说的"per-token AND"——每个 token 都至少落进 *某个*
            # 字段，而不是"每个 token 都必须落进 *每个* 字段"。打分阶段
            # 再用部分命中去打破并列、把多字段命中排在单字段命中之前。
            has_full = (
                (use_title and t_hits == n_query_tokens)
                or (use_abstract and a_hits == n_query_tokens)
                or (use_author and auth_hits == n_query_tokens)
            )
            if not has_full:
                continue

            score = t_hits * W_TITLE + a_hits * W_ABSTRACT + auth_hits * W_AUTHOR
            scored.append((paper, score))
        else:
            # 没有自由文本查询：通过其他筛选的论文全部入选；分数保持 0，
            # 后续排序完全交给 ``criteria.sort``，保持原有行为不变。
            scored.append((paper, 0))

    extractor, desc = _sort_key(criteria.sort)
    if query_value:
        # 主键：相关性（分数越高 = 越多 token 在越多字段里命中）。副键：
        # 调用方指定的排序键，且 *必须* 尊重其 ``desc`` 方向——否则
        # ``sort=year`` (升序) 之类预设在有 query 时会被悄悄反向，
        # 违反 "默认 sort 作为副键保留原有行为" 的契约。
        #
        # 利用 Python sort 的稳定性做两遍排序：先按副键以正确方向稳定排
        # 一遍，再按相关性分数降序稳定排一遍。相同分数的论文会保留第一
        # 遍确定的副键顺序，不同分数的论文按主键排好；这样可以在一行里
        # 同时表达 "主键降序、副键各自方向" 而无需把方向编码进 key。
        scored.sort(key=lambda ps: extractor(ps[0]), reverse=desc)
        scored.sort(key=lambda ps: ps[1], reverse=True)
    else:
        scored.sort(key=lambda ps: extractor(ps[0]), reverse=desc)

    matched = [p for p, _ in scored]
    total = len(matched)
    start = (criteria.page - 1) * criteria.size
    end = start + criteria.size
    return matched[start:end], total

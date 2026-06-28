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
                authors = paper.get("paper_authors") or []
                if not isinstance(authors, list):
                    authors = [str(authors)]
                normalized_title = _WS_RE.sub(" ", title).strip().lower().replace("-", " ")
                rec = Paper(
                    id=pid,
                    conf=conf_name,
                    year=year,
                    title=title,
                    title_format=normalized_title,
                    url=paper.get("paper_url"),
                    authors=[str(a) for a in authors],
                    abstract=paper.get("paper_abstract"),
                    code=paper.get("paper_code"),
                )
                self._papers.append(rec)
                self._by_conf.setdefault(conf_name, []).append(rec)

        logger.info(
            "Loaded %d papers across %d conferences", len(self._papers), len(self._by_conf)
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


def _author_score(authors: Iterable[str], needle: str) -> int:
    """返回 ``needle`` token 在作者列表里命中了多少个。

    得分区间 ``[0, len(needle.split())]``：每个 token 独立判断是否在拼接、
    去连字符、小写化的作者列表里出现，允许任意顺序、允许部分命中（用于
    排序，不用于入选）。
    """
    if not needle:
        return 0
    normalized = [a.lower().replace("-", " ") for a in authors]
    joined = " ".join(normalized)
    tokens = needle.split()
    if len(tokens) == 1:
        return 1 if tokens[0] in joined else 0
    return sum(1 for tok in tokens if tok in joined)


def _title_score(paper: Paper, query: str) -> int:
    """返回 ``query`` token 在 ``paper.title_format`` 里命中了多少个。

    ``title_format`` 在加载时已经做过小写与去连字符处理，可以直接子串匹配。
    Token 顺序无关——例如查询 ``"time series agent"`` 会对标题为
    ``"Agent for Time Series Forecasting"`` 的论文得分 3。
    """
    if not query:
        return 0
    tokens = query.split()
    if len(tokens) == 1:
        return 1 if tokens[0] in paper.title_format else 0
    return sum(1 for tok in tokens if tok in paper.title_format)


def _abstract_score(paper: Paper, query: str) -> int:
    """返回 ``query`` token 在 ``paper.abstract`` 里命中了多少个。

    当论文没有摘要时返回 0（语料中约 73% 的论文没有摘要，按需小写避免
    加载时白白浪费内存）。
    """
    abstract = paper.abstract
    if not query or not abstract:
        return 0
    abs_lower = abstract.lower()
    tokens = query.split()
    if len(tokens) == 1:
        return 1 if tokens[0] in abs_lower else 0
    return sum(1 for tok in tokens if tok in abs_lower)


# 向后兼容的旧谓词。历史外部调用方只关心布尔结果，这里把 _title_matches
# 与 _author_matches 保留为 ``score > 0`` 的薄包装。注意语义已变：原本
# 1/2 token 命中就算 True；现在 per-token AND 在 search_papers 上游强约束
# 必须所有 token 都至少落在一个字段里才入选。这两个谓词保留了较松的
# "任一 token 命中即 True" 行为，避免外部代码意外收紧匹配。
def _author_matches(authors: Iterable[str], needle: str) -> bool:
    return _author_score(authors, needle) > 0


def _title_matches(paper: Paper, query: str) -> bool:
    return _title_score(paper, query) > 0


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
    use_abstract = criteria.field in ("title", "any") and query_value != ""

    # 各字段在相关性打分里的权重。标题证据最强、其次作者、最后摘要。
    # 只有当某字段达到 *完整* per-token AND 命中（见下面的 ``has_full``）
    # 时才把它的命中数计入分数；部分命中仍会加分，但不足以单独入选。
    W_TITLE, W_AUTHOR, W_ABSTRACT = 3, 2, 1
    n_query_tokens = len(query_value.split())

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
        if author_needle and _author_score(paper.authors, author_needle) == 0:
            continue

        if query_value:
            # 各字段命中数（允许部分命中用于排序，但不用于入选）
            t_hits = _title_score(paper, query_value) if use_title else 0
            a_hits = _abstract_score(paper, query_value) if use_abstract else 0
            auth_hits = _author_score(paper.authors, query_value) if use_author else 0

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
        # 调用方指定的排序键。这样 ``sort=-year`` 之类的预设仍然让较新
        # 的论文排在同分桶里靠前，单 token 查询的默认顺序也保持稳定，
        # 只有多 token 查询时分数才会真正改变顺序。
        scored.sort(key=lambda ps: (ps[1], extractor(ps[0])), reverse=True)
    else:
        scored.sort(key=lambda ps: extractor(ps[0]), reverse=desc)

    matched = [p for p, _ in scored]
    total = len(matched)
    start = (criteria.page - 1) * criteria.size
    end = start + criteria.size
    return matched[start:end], total

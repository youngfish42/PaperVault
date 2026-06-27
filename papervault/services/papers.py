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

        # cache.jsonl.gz 由 collector 持续追加写入，历史版本从未去重。
        # Paper.id 由 (conf, year, title) 决定，所以重复行会产生相同的 id。
        # 在构建索引前丢弃重复行，避免搜索结果与 UI 出现同一篇论文多次。
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


def _author_matches(authors: Iterable[str], needle: str) -> bool:
    if not needle:
        return True
    normalized = [a.lower().replace("-", " ") for a in authors]
    joined = " ".join(normalized)
    if len(needle.split(" ")) > 1:
        return needle in normalized
    return needle in joined


def _title_matches(paper: Paper, query: str) -> bool:
    # ``query`` is assumed to be normalised and non-empty by the caller
    # (``search_papers`` filters out the empty/sentinel ``"#"`` token before
    # invoking us). Keeping the function focused on the actual match avoids a
    # second, dead "is this the # sentinel?" branch.
    return query in paper.title_format


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
    use_author_field = criteria.field == "author" and query_value != ""
    use_any_author = criteria.field == "any" and query_value != ""

    matched: List[Paper] = []
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
        if author_needle and not _author_matches(paper.authors, author_needle):
            continue

        if query_value:
            ok = False
            if use_title and _title_matches(paper, query_value):
                ok = True
            elif use_author_field and _author_matches(paper.authors, query_value):
                ok = True
            elif use_any_author:
                ok = _title_matches(paper, query_value) or _author_matches(paper.authors, query_value)
            if not ok:
                continue

        matched.append(paper)

    extractor, desc = _sort_key(criteria.sort)
    matched.sort(key=extractor, reverse=desc)

    total = len(matched)
    start = (criteria.page - 1) * criteria.size
    end = start + criteria.size
    return matched[start:end], total

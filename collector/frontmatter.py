"""Front-matter 与整卷出版物判定的唯一事实来源。

三个消费端共享标题正则和整卷出版物谓词，但保留采集端与清理端的
设计差异：

* 清理端（``scripts/cleanup_cache_frontmatter.py``，
  :func:`is_frontmatter_record`）：处理缓存里的存量记录。普通 front-matter
  要求标题命中正则，且满足 venue-index 或无摘要且无真实代码链接；整卷
  proceedings 则要求标题形态与 DOI/URL 结构双重证据，即使有摘要、代码
  或普通作者列表也可判定为非单篇论文。
* 采集端（``collector/merge.py:_merge_with_cache`` 调用
  :func:`should_drop_on_collect`）：新采集的记录此刻**还没有**摘要和
  代码链接，不能复用清理端的空字段腿。普通 front-matter 要求标题命中且
  （venue-index URL **或** 作者字段为空 / 全是编辑角色标记）；高置信整卷
  出版物则直接过滤。采集端只拦新记录，存量缓存由清理脚本负责。

本模块不判断单篇论文是否属于 workshop。缓存缺少来源页面血缘，标题也不
足以证明这一点；workshop proceedings 整卷会按非论文条目过滤，但其中的
单篇研究论文一律保留。
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from collector.url_types import classify_paper_url

FRONT_MATTER_RE = re.compile(
    r"^(proceedings\b|front matter|frontmatter|table of contents|preface\b|findings of the association)",
    re.IGNORECASE,
)
PROCEEDINGS_TITLE_RE = re.compile(
    r"(?:^(?:(?:adjunct|joint|workshops)\s+)?proceedings\b"
    r"|[,:]\s*proceedings\b|\bproceedings\s*(?:,\s*part\b|$)"
    r"|\bconference proceedings\s*,|\bworkshop proceedings\s*\()",
    re.IGNORECASE,
)
_SPRINGER_BOOK_DOI_RE = re.compile(r"^10\.1007/978-[^_]+$", re.IGNORECASE)
_ACM_DOI_RE = re.compile(r"^10\.1145/\d+$", re.IGNORECASE)
_ACM_LEGACY_DOI_RE = re.compile(r"^(?:doi/)?10\.1145/\d+$", re.IGNORECASE)

_EDITOR_ROLE_RE = re.compile(r"\(\s*eds?\.?\s*\)|\beditors?\b", re.IGNORECASE)


def _title_matches(rec: dict) -> bool:
    if not isinstance(rec, dict):
        return False
    title = (rec.get("paper_name") or "").strip()
    return bool(FRONT_MATTER_RE.match(title))


def is_proceedings_record(rec: dict) -> bool:
    """Return whether structural evidence identifies a whole proceedings volume."""
    if not isinstance(rec, dict):
        return False
    title = (rec.get("paper_name") or "").strip()
    if not PROCEEDINGS_TITLE_RE.search(title):
        return False
    try:
        parsed = urlparse((rec.get("paper_url") or "").strip())
    except (AttributeError, ValueError):
        return False
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = (parsed.path or "").strip("/")

    if host == "doi.org" and _SPRINGER_BOOK_DOI_RE.fullmatch(path):
        return True
    if host == "doi.org" and _ACM_DOI_RE.fullmatch(path):
        return True
    if host.endswith("acm.org"):
        if _ACM_LEGACY_DOI_RE.fullmatch(path):
            return True
        if path == "citation.cfm":
            citation_id = parse_qs(parsed.query).get("id", [""])[0]
            if citation_id.isdigit():
                return True
    return classify_paper_url(rec.get("paper_url", "")) == "venue-index"


def is_frontmatter_record(rec: dict) -> bool:
    """清理端判定：高置信整卷，或标题命中且（venue-index / 空字段）。"""
    if is_proceedings_record(rec):
        return True
    if not _title_matches(rec):
        return False
    if classify_paper_url(rec.get("paper_url", "")) == "venue-index":
        return True
    has_abstract = bool((rec.get("paper_abstract") or "").strip())
    code = (rec.get("paper_code") or "").strip()
    has_code = bool(code and code != "#")
    return not has_abstract and not has_code


def _authors_look_editorial(rec: dict) -> bool:
    """作者字段为空，或所有作者都带编辑角色标记。"""
    authors = rec.get("paper_authors") or []
    if not authors:
        return True
    return all(_EDITOR_ROLE_RE.search(str(a) or "") for a in authors)


def should_drop_on_collect(rec: dict) -> bool:
    """采集端过滤整卷或带 URL/作者佐证的 front-matter 新记录。"""
    if is_proceedings_record(rec):
        return True
    if not _title_matches(rec):
        return False
    if classify_paper_url(rec.get("paper_url", "")) == "venue-index":
        return True
    return _authors_look_editorial(rec)

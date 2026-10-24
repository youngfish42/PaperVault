"""Front-matter 记录（会议卷首页 / 论文集封面）判定的唯一事实来源。

两个消费端共享标题正则 :data:`FRONT_MATTER_RE`，但佐证腿不同——
这是**有意的设计差异**：

* 清理端（``scripts/cleanup_cache_frontmatter.py``，
  :func:`is_frontmatter_record`）：处理缓存里的存量记录。标题命中正则，
  且满足任一佐证：URL 被 ``classify_paper_url`` 判为 venue-index，或
  记录既无摘要也无真实代码链接（``"#"`` 占位不算）。存量记录若有摘要/
  代码链接，说明回填管线曾把它当真论文处理过，保守保留。

* 采集端（``collector/merge.py:_merge_with_cache`` 调用
  :func:`should_drop_on_collect`）：新采集的记录此刻**还没有**摘要和
  代码链接，清理端的"无摘要且无代码"腿在采集时恒真，若直接复用会把
  所有标题以 "Proceedings of" 开头的条目全杀掉（包括还没有机会回填的
  真论文边界形态）。因此采集端两腿同时收紧：标题命中正则，且
  （venue-index URL **或** 作者字段为空 / 全是编辑角色标记
  ``(ed.)``/``(eds.)``/``editor``）。采集端**只拦新记录**，存量缓存里
  已有的 front-matter 由清理脚本负责，collector 不做删除。
"""

from __future__ import annotations

import re

from collector.url_types import classify_paper_url

FRONT_MATTER_RE = re.compile(
    r"^(proceedings\b|front matter|frontmatter|table of contents|preface\b|findings of the association)",
    re.IGNORECASE,
)

# 编辑角色标记：front-matter 条目的"作者"常是 "(Eds.)" / "X (ed.)" 之类。
_EDITOR_ROLE_RE = re.compile(r"\(\s*eds?\.?\s*\)|\beditors?\b", re.IGNORECASE)


def _title_matches(rec: dict) -> bool:
    if not isinstance(rec, dict):
        return False
    title = (rec.get("paper_name") or "").strip()
    return bool(FRONT_MATTER_RE.match(title))


def is_frontmatter_record(rec: dict) -> bool:
    """清理端判定：标题命中正则，且（venue-index URL 或 无摘要且无真实代码链接）。"""
    if not _title_matches(rec):
        return False
    if classify_paper_url(rec.get("paper_url", "")) == "venue-index":
        return True
    has_abstract = bool((rec.get("paper_abstract") or "").strip())
    code = (rec.get("paper_code") or "").strip()
    has_code = bool(code and code != "#")
    return not has_abstract and not has_code


def _authors_look_editorial(rec: dict) -> bool:
    """作者字段为空，或所有作者都带编辑角色标记（front-matter 条目的典型形态）。"""
    authors = rec.get("paper_authors") or []
    if not authors:
        return True
    return all(_EDITOR_ROLE_RE.search(str(a) or "") for a in authors)


def should_drop_on_collect(rec: dict) -> bool:
    """采集端判定：标题命中正则，且（venue-index URL 或 作者为空/编辑角色）。

    只用于过滤**新采集**的记录；存量缓存记录的清理由
    ``scripts/cleanup_cache_frontmatter.py`` 负责。
    """
    if not _title_matches(rec):
        return False
    if classify_paper_url(rec.get("paper_url", "")) == "venue-index":
        return True
    return _authors_look_editorial(rec)

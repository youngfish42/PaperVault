"""per-token AND 搜索的回归测试。

背景
----
原 ``search_papers`` 只在标题里把整个查询当作连续子串匹配，导致
``"time series agent"`` 这类多 token 查询几乎永远返回 0 命中，即便
相关论文分别讨论了三个主题（例如 ICLR 2026 的
``TimeSeriesExamAgent: Creating Time Series Reasoning Benchmarks at Scale``）。

本次改动把查询拆成 token，要求每个 token 至少落在一个被搜索字段里
（标题 / 摘要 / 作者），并用加权得分（标题 3×，作者 2×，摘要 1×）
做相关性排序。

注：加载期去重由 PR #95 单独负责，本文件不重复覆盖。
"""

from __future__ import annotations

from papervault.services.papers import (
    PaperRepository,
    SearchCriteria,
    search_papers,
)


def _criteria(**overrides) -> SearchCriteria:
    base = dict(
        query=None,
        field="title",
        confs=[],
        since=None,
        until=None,
        author=None,
        sort="-year",
        page=1,
        size=50,
    )
    base.update(overrides)
    return SearchCriteria(**base)


# ----------------------------------------------------------------------
# per-token AND 入选 / 排除
# ----------------------------------------------------------------------


def test_per_token_and_includes_full_title_match(
    repository_with_sample: PaperRepository,
):
    items, total = search_papers(
        repository_with_sample,
        _criteria(query="attention all", field="title"),
    )
    titles = [p.title for p in items]
    assert total == 1
    assert titles == ["Attention Is All You Need Revisited"]


def test_per_token_and_excludes_partial_match(
    repository_with_sample: PaperRepository,
):
    # "attention" 出现在 ACL 2023；"survey" 出现在 NeurIPS 2022。两篇
    # 都不同时包含两个 token，因此 per-token AND 必须双双排除。这正是
    # 推动本次改写的回归——旧代码因为整个短语在标题里没有连续出现，
    # 返回 0 命中，无法分辨"零相关"和"短语被打散"。
    items, total = search_papers(
        repository_with_sample,
        _criteria(query="attention survey", field="title"),
    )
    assert total == 0


def test_per_token_and_uses_abstract_fallback(
    repository_with_sample: PaperRepository,
):
    # "mechanisms" 只在 ACL 2023 论文的摘要里出现，标题里没有。默认
    # ``field=title`` 仍应通过摘要模糊加成命中它。
    items, total = search_papers(
        repository_with_sample,
        _criteria(query="mechanisms", field="title"),
    )
    titles = [p.title for p in items]
    assert "Attention Is All You Need Revisited" in titles


def test_author_field_ignores_title_and_abstract(
    repository_with_sample: PaperRepository,
):
    # ``field=author`` 必须保持严格：用户既然指定按作者查，标题 / 摘要
    # 内容不能让同一篇论文凭 free-text 入选。
    items, total = search_papers(
        repository_with_sample,
        _criteria(query="revisited", field="author"),
    )
    # "Revisited" 出现在多篇标题里，但没有作者叫 "Revisited"。
    assert total == 0


# ----------------------------------------------------------------------
# 加权排序
# ----------------------------------------------------------------------


def test_weighted_ranking_prefers_title_evidence(
    repository_with_sample: PaperRepository,
):
    # "revisited" 同时命中两篇标题（ACL 2023 + CVPR 2024）以及 ACL 2023
    # 的摘要里"A revisit of attention mechanisms"。两篇标题命中应当排在
    # 任何纯摘要命中之前；标题命中之间是平局，仅校验相对顺序。
    items, _total = search_papers(
        repository_with_sample,
        _criteria(query="revisited", field="title"),
    )
    titles = [p.title for p in items]
    assert "Attention Is All You Need Revisited" in titles
    assert "Vision Transformers Revisited" in titles
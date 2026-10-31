"""Regression tests for collection-side front-matter filtering.

``collector/frontmatter.py`` is the single source of truth for the
front-matter title regex; the collection-side predicate
(``should_drop_on_collect``) deliberately differs from the cleanup-side
one (``is_frontmatter_record``) because freshly collected records never
have abstracts/code links yet — the cleanup "empty fields" leg would be
vacuously true. The collector therefore requires the title match AND
(venue-index URL OR empty/editorial authors), and only ever filters
*new* records (existing cache rows are left to the cleanup script).

``collector.merge._merge_with_cache`` is the chokepoint where new
records enter the cache; the filter lives there with a printed count.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from collector.frontmatter import (  # noqa: E402
    FRONT_MATTER_RE,
    is_frontmatter_record,
    should_drop_on_collect,
)
from collector.merge import _merge_with_cache  # noqa: E402


def _rec(title, url="https://dl.acm.org/doi/10.1145/123", authors=None,
         abstract="", code="#", conf="ICSE2026"):
    return {
        "paper_name": title,
        "paper_url": url,
        "paper_authors": authors if authors is not None else ["Alice", "Bob"],
        "paper_abstract": abstract,
        "paper_code": code,
        "conf": conf,
    }


# ---------- predicate: collection-side ---------------------------------------

def test_collect_drops_title_match_with_venue_index_url():
    rec = _rec("Proceedings of the Thirty-Fifth International Joint Conference",
               url="https://ijcai.org/proceedings/2026/", authors=["A"])
    assert should_drop_on_collect(rec) is True


def test_collect_drops_title_match_with_empty_authors():
    rec = _rec("Proceedings of the 32nd ACM SIGKDD Conference",
               url="https://doi.org/10.1145/3770855", authors=[])
    assert should_drop_on_collect(rec) is True


def test_collect_drops_title_match_with_editorial_authors():
    rec = _rec("Front Matter", authors=["John Doe (Ed.)", "Jane Smith (eds.)"])
    assert should_drop_on_collect(rec) is True


def test_collect_keeps_title_match_with_real_authors_and_paper_url():
    """新记录没摘要/代码是常态——不能仅凭空字段腿误删（设计差异的核心）。"""
    rec = _rec("Proceedings of Some Workshop", authors=["Alice", "Bob"])
    assert should_drop_on_collect(rec) is False


def test_collect_keeps_real_paper_with_venue_index_url():
    rec = _rec("Attention Is All You Need",
               url="https://aclanthology.org/2021.acl-long.0/", authors=[])
    assert should_drop_on_collect(rec) is False


def test_collect_keeps_real_paper():
    assert should_drop_on_collect(_rec("Deep Residual Learning")) is False


# ---------- predicate: shared regex / cleanup-side unchanged -------------------

def test_shared_regex_drives_both_predicates():
    title = "Preface to the special issue"
    assert FRONT_MATTER_RE.match(title)
    # 清理端：空摘要空代码 → 命中
    assert is_frontmatter_record(_rec(title, authors=[])) is True
    # 采集端：空作者 → 也拦
    assert should_drop_on_collect(_rec(title, authors=[])) is True


def test_cleanup_side_still_keeps_records_with_abstract():
    """清理端行为不变：有摘要的标题命中记录保留（即便 venue-index 之外）。"""
    rec = _rec("Proceedings of Some Conference", abstract="A" * 100)
    assert is_frontmatter_record(rec) is False
    assert should_drop_on_collect(rec) is False  # 有作者 → 采集端也保留


# ---------- _merge_with_cache integration --------------------------------------

def test_merge_with_cache_filters_new_frontmatter_records(capsys):
    new_res = {
        "ICSE2026": [
            _rec("Proceedings of the IEEE/ACM 48th ICSE",
                 url="https://doi.org/10.1145/3786581", authors=[]),
            _rec("A Real Paper", url="https://doi.org/10.1145/real1"),
        ],
    }
    result = _merge_with_cache(new_res, {}, set(), set())
    urls = [p["paper_url"] for p in result["ICSE2026"]]
    assert urls == ["https://doi.org/10.1145/real1"]
    out = capsys.readouterr().out
    assert "filtered 1 front-matter" in out


def test_merge_with_cache_does_not_delete_existing_cache_records():
    """存量缓存里的 front-matter 由清理脚本负责，collector 不删除。"""
    stale = _rec("Proceedings of Old Conf", url="https://doi.org/10.1145/old",
                 authors=[], conf="OLD2020")
    result = _merge_with_cache({}, {"OLD2020": [stale]}, set(), set())
    assert len(result["OLD2020"]) == 1  # 原样保留


def test_merge_with_cache_no_frontmatter_no_log(capsys):
    new_res = {"ICSE2026": [_rec("A Real Paper")]}
    _merge_with_cache(new_res, {}, set(), set())
    assert "front-matter" not in capsys.readouterr().out

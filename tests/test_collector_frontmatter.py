"""Regression tests for collection-side front-matter filtering.

Fresh records cannot use the cleanup-side empty-field evidence because they do
not have abstracts or code links yet. Whole-proceedings records instead require
both a proceedings-shaped title and publisher/venue structural evidence.
Individual workshop papers remain outside this filter.
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
    is_proceedings_record,
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
    rec = _rec("Proceedings of Some Workshop", url="https://example.org/paper")
    assert should_drop_on_collect(rec) is False


def test_collect_keeps_real_paper_with_venue_index_url():
    rec = _rec("Attention Is All You Need",
               url="https://aclanthology.org/2021.acl-long.0/", authors=[])
    assert should_drop_on_collect(rec) is False


def test_collect_keeps_real_paper():
    assert should_drop_on_collect(_rec("Deep Residual Learning")) is False


@pytest.mark.parametrize(
    "url",
    [
        "https://doi.org/10.1007/978-3-032-38062-3",
        "https://doi.org/10.1145/2442796",
        "https://dl.acm.org/doi/10.1145/2442796",
        "http://dl.acm.org/citation.cfm?id=2534190",
        "https://ieeexplore.ieee.org/xpl/conhome/10226/proceeding",
    ],
)
def test_whole_proceedings_structural_evidence_overrides_enriched_fields(url):
    rec = _rec(
        "Proceedings of Some Conference",
        url=url,
        abstract="A" * 100,
        code="https://github.com/example/volume",
    )
    assert is_proceedings_record(rec) is True
    assert is_frontmatter_record(rec) is True
    assert should_drop_on_collect(rec) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://doi.org/10.1007/978-3-032-38062-3_1",
        "https://doi.org/10.1145/2442796.2442800",
        "https://dl.acm.org/doi/10.1145/2442796.2442800",
        "https://dl.acm.org/citation.cfm?id=not-numeric",
    ],
)
def test_chapter_and_paper_dois_are_not_whole_proceedings(url):
    rec = _rec(
        "Proceedings of Some Conference",
        url=url,
        abstract="A" * 100,
        code="https://github.com/example/paper",
    )
    assert is_proceedings_record(rec) is False
    assert is_frontmatter_record(rec) is False
    assert should_drop_on_collect(rec) is False


def test_proceedings_title_shape_requires_structural_evidence():
    rec = _rec(
        "On the Proceedings of Thought: A Study",
        url="https://doi.org/10.1145/2442796",
        abstract="A" * 100,
        code="https://github.com/example/paper",
    )
    assert is_proceedings_record(rec) is False


def test_workshop_research_article_is_preserved():
    rec = _rec(
        "A Novel Method for Geospatial Data Analysis",
        url="https://doi.org/10.1145/2442796.2442800",
        abstract="A" * 100,
        code="https://github.com/example/workshop-paper",
    )
    assert is_proceedings_record(rec) is False
    assert is_frontmatter_record(rec) is False
    assert should_drop_on_collect(rec) is False


def test_shared_regex_drives_both_predicates():
    title = "Preface to the special issue"
    assert FRONT_MATTER_RE.match(title)
    assert is_frontmatter_record(_rec(title, authors=[])) is True
    assert should_drop_on_collect(_rec(title, authors=[])) is True


def test_cleanup_side_still_keeps_records_with_abstract():
    rec = _rec(
        "Proceedings of Some Conference",
        url="https://example.org/paper",
        abstract="A" * 100,
    )
    assert is_frontmatter_record(rec) is False
    assert should_drop_on_collect(rec) is False


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
    stale = _rec("Proceedings of Old Conf", url="https://doi.org/10.1145/old",
                 authors=[], conf="OLD2020")
    result = _merge_with_cache({}, {"OLD2020": [stale]}, set(), set())
    assert len(result["OLD2020"]) == 1


def test_merge_with_cache_no_frontmatter_no_log(capsys):
    new_res = {"ICSE2026": [_rec("A Real Paper")]}
    _merge_with_cache(new_res, {}, set(), set())
    assert "front-matter" not in capsys.readouterr().out

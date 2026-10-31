"""scripts/fetch_pwc_backfill.py 的离线单元测试。

全部使用内存构造的 PwC 行与缓存行，不触网、不下载 parquet。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.fetch_pwc_backfill import (  # noqa: E402
    backfill_records,
    build_abstract_index,
    build_code_index,
    clean_github_url,
    extract_arxiv_id,
    extract_openreview_id,
    match_abstract,
    match_repo,
    normalize_title,
    normalize_url,
    select_repo,
    year_guard_ok,
)

LONG_ABS = "This paper presents a method for testing the backfill pipeline. " * 3


def _abs_row(**overrides):
    row = {
        "title": "Some Paper",
        "abstract": LONG_ABS,
        "arxiv_id": None,
        "openreview_id": None,
        "url_abs": None,
        "conference_url_abs": None,
        "proceeding": None,
        "conference": None,
        "date": None,
    }
    row.update(overrides)
    return row


def _cache_paper(**overrides):
    paper = {
        "paper_name": "Some Paper",
        "paper_url": "",
        "paper_authors": [],
        "paper_abstract": "",
        "paper_code": "#",
        "conf": "ICML2024",
    }
    paper.update(overrides)
    return paper


# ---------- 规范化 / 键提取 ----------
def test_normalize_url_scheme_and_host():
    assert (
        normalize_url("http://www.arxiv.org/abs/1234.56789v2/")
        == "https://arxiv.org/abs/1234.56789v2"
    )


def test_normalize_url_lowercases_cvf_path():
    assert (
        normalize_url("http://openaccess.thecvf.com/content_CVPR_2019/html/Foo_Bar_CVPR_2019_paper.html")
        == "https://openaccess.thecvf.com/content_cvpr_2019/html/foo_bar_cvpr_2019_paper.html"
    )


def test_normalize_url_keeps_aclanthology_path_case():
    assert (
        normalize_url("https://aclanthology.org/D18-1341/")
        == "https://aclanthology.org/D18-1341"
    )


def test_normalize_url_rejects_garbage():
    assert normalize_url("") == ""
    assert normalize_url("not-a-url") == ""
    assert normalize_url("https://") == ""


def test_extract_arxiv_id_strips_version():
    assert extract_arxiv_id("https://arxiv.org/abs/1805.10616v4") == "1805.10616"
    assert extract_arxiv_id("http://arxiv.org/pdf/1805.10616.pdf") == "1805.10616"
    assert extract_arxiv_id("https://doi.org/10.1109/SP.2026") == ""


def test_extract_openreview_id():
    assert extract_openreview_id("https://openreview.net/forum?id=HkGx2sAqFX") == "HkGx2sAqFX"
    assert extract_openreview_id("https://openreview.net/pdf?id=HkGx2sAqFX") == "HkGx2sAqFX"
    assert extract_openreview_id("https://aclanthology.org/D18-1341") == ""


def test_normalize_title():
    assert normalize_title("  PAC-Bayes Bounds: A Survey!  ") == "pacbayesboundsasurvey"


# ---------- 年份守卫 ----------
def test_year_guard_venue_year_match():
    row = _abs_row(proceeding="NeurIPS 2018")
    assert year_guard_ok("NIPS2018", row)
    assert not year_guard_ok("NIPS2019", row)


def test_year_guard_date_fallback_allows_one_year_lag():
    class _D:
        year = 2017

    row = _abs_row(date=_D())
    assert year_guard_ok("NIPS2018", row)  # 预印本早一年，允许
    assert not year_guard_ok("NIPS2020", row)


def test_year_guard_no_conf_year_passes():
    assert year_guard_ok("SOMEWORKSHOP", _abs_row(proceeding="NeurIPS 2018"))


# ---------- 摘要索引与匹配 ----------
def test_match_by_arxiv_id():
    index = build_abstract_index([_abs_row(arxiv_id="1805.10616")])
    paper = _cache_paper(paper_url="https://arxiv.org/abs/1805.10616v2")
    row, method = match_abstract(index, paper)
    assert method == "arxiv_id"
    assert row["abstract"] == LONG_ABS


def test_match_by_openreview_id():
    index = build_abstract_index([_abs_row(openreview_id="HkGx2sAqFX")])
    paper = _cache_paper(paper_url="https://openreview.net/forum?id=HkGx2sAqFX")
    _, method = match_abstract(index, paper)
    assert method == "openreview_id"


def test_match_by_url_exact_after_normalization():
    index = build_abstract_index(
        [_abs_row(url_abs="http://openaccess.thecvf.com/content_cvpr_2017/html/Foo_CVPR_2017_paper.html")]
    )
    paper = _cache_paper(
        paper_url="https://openaccess.thecvf.com/content_CVPR_2017/html/Foo_CVPR_2017_paper.html"
    )
    _, method = match_abstract(index, paper)
    assert method == "url"


def test_match_by_title_with_year_guard():
    index = build_abstract_index([_abs_row(title="Some Paper", proceeding="ICML 2024")])
    ok, method_ok = match_abstract(index, _cache_paper(conf="ICML2024"))
    assert method_ok == "title"
    bad, method_bad = match_abstract(index, _cache_paper(conf="ICML2019"))
    assert bad is None and method_bad == ""


def test_duplicate_title_key_is_dropped():
    rows = [
        _abs_row(title="Dup Title", abstract=LONG_ABS),
        _abs_row(title="Dup Title", abstract="A completely different abstract that is also long enough. " * 2),
    ]
    index = build_abstract_index(rows)
    row, method = match_abstract(index, _cache_paper(paper_name="Dup Title"))
    assert row is None and method == ""


def test_duplicate_title_same_content_kept():
    rows = [_abs_row(title="Dup", abstract=LONG_ABS), _abs_row(title="Dup", abstract=LONG_ABS)]
    index = build_abstract_index(rows)
    row, _ = match_abstract(index, _cache_paper(paper_name="Dup"))
    assert row is not None


# ---------- 代码链接选择 ----------
def test_select_repo_prefers_official():
    rows = [
        {"repo_url": "https://github.com/someone/reimpl", "is_official": False, "mentioned_in_paper": True},
        {"repo_url": "https://github.com/author/official", "is_official": True, "mentioned_in_paper": False},
    ]
    assert select_repo(rows) == "https://github.com/author/official"


def test_select_repo_skips_unmentioned():
    rows = [
        {"repo_url": "https://github.com/random/related", "is_official": False, "mentioned_in_paper": False},
    ]
    assert select_repo(rows) == ""


def test_clean_github_url_rejects_non_github():
    assert clean_github_url("https://gitlab.com/owner/repo") == ""
    assert clean_github_url("https://github.com/owner") == ""
    assert clean_github_url("") == ""
    assert clean_github_url("https://github.com/owner/repo.") == "https://github.com/owner/repo"


def test_code_index_title_ambiguity_dropped():
    rows = [
        {
            "paper_title": "Same Title",
            "paper_arxiv_id": "1111.00001",
            "paper_url_abs": "https://arxiv.org/abs/1111.00001",
            "repo_url": "https://github.com/a/x",
            "is_official": True,
            "mentioned_in_paper": False,
        },
        {
            "paper_title": "Same Title",
            "paper_arxiv_id": "2222.00002",
            "paper_url_abs": "https://arxiv.org/abs/2222.00002",
            "repo_url": "https://github.com/b/y",
            "is_official": True,
            "mentioned_in_paper": False,
        },
    ]
    index = build_code_index(rows)
    repo, method = match_repo(index, _cache_paper(paper_name="Same Title"))
    assert repo == "" and method == ""
    # 但 arxiv 键仍然各自可用
    paper = _cache_paper(paper_url="https://arxiv.org/abs/1111.00001v1")
    repo, method = match_repo(index, paper)
    assert repo == "https://github.com/a/x" and method == "arxiv_id"


# ---------- 回填语义 ----------
def test_backfill_only_fills_empty_fields():
    index = build_abstract_index([_abs_row(title="Some Paper", proceeding="ICML 2024")])
    code_index = build_code_index(
        [
            {
                "paper_title": "Some Paper",
                "paper_arxiv_id": None,
                "paper_url_abs": None,
                "repo_url": "https://github.com/owner/repo",
                "is_official": True,
                "mentioned_in_paper": False,
            }
        ]
    )
    papers = [
        _cache_paper(),  # 空 abstract + 空 code → 都应填
        _cache_paper(paper_abstract="已有摘要，不应被覆盖。", paper_code="https://github.com/exist/ing"),
    ]
    stats = backfill_records(papers, index, code_index)
    assert stats["abstract_filled"] == 1
    assert stats["code_filled"] == 1
    assert papers[0]["paper_abstract"].startswith("This paper presents")
    assert papers[0]["paper_code"] == "https://github.com/owner/repo"
    assert papers[1]["paper_abstract"] == "已有摘要，不应被覆盖。"
    assert papers[1]["paper_code"] == "https://github.com/exist/ing"


def test_backfill_rejects_short_abstract():
    index = build_abstract_index([_abs_row(title="Some Paper", abstract="Too short.")])
    papers = [_cache_paper()]
    stats = backfill_records(papers, index, {"arxiv": {}, "url": {}, "title": {}})
    assert stats["abstract_filled"] == 0
    assert papers[0]["paper_abstract"] == ""


def test_backfill_conf_filter():
    index = build_abstract_index([_abs_row(title="Some Paper", proceeding="ICML 2024")])
    papers = [_cache_paper(conf="ICML2024"), _cache_paper(conf="ACL2023")]
    stats = backfill_records(
        papers, index, {"arxiv": {}, "url": {}, "title": {}}, conf_filter={"ACL2023"}
    )
    assert stats["scanned"] == 1
    # ACL2023 被扫描但年份守卫拦截（proceeding 是 ICML 2024）
    assert stats["abstract_filled"] == 0

"""Tests for ``scripts/repair_wayback_urls.py``.

OpenAlex HTTP is stubbed by injecting ``fetch_fn`` into ``_repair_pass`` —
no network, fully offline. Covers: wayback unwrap regex, new-URL priority,
title/year gates, conflict merge via the real ``collector._merge_paper_record``,
abstract side-fill, and dry-run not touching disk.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from scripts import repair_wayback_urls as rw


WAYBACK_AAAI = (
    "https://web.archive.org/web/2007/http://www.aaai.org/ocs/index.php/AAAI/AAAI06/paper/view/7326"
)
ORIG_AAAI = "http://www.aaai.org/ocs/index.php/AAAI/AAAI06/paper/view/7326"

LONG_ABSTRACT = (
    "We present a sufficiently long synthetic abstract for the wayback "
    "repair tests, comfortably clearing the sixty-character minimum gate."
)


def _paper(url: str, title: str = "Some Old Paper", conf: str = "AAAI2006") -> dict:
    return {
        "paper_url": url,
        "paper_name": title,
        "paper_abstract": "",
        "conf": conf,
    }


def _work(title="Some Old Paper", year=2006, doi=None, landing=None, abstract=True) -> dict:
    return {
        "title": title,
        "publication_year": year,
        "doi": doi,
        "primary_location": {"landing_page_url": landing} if landing else None,
        "abstract_inverted_index": _inverted(LONG_ABSTRACT) if abstract else None,
    }


def _inverted(text: str) -> dict:
    idx: dict = {}
    for pos, word in enumerate(text.split()):
        idx.setdefault(word, []).append(pos)
    return idx


def _no_disk(monkeypatch):
    monkeypatch.setattr(
        rw, "_write_cache_atomic",
        MagicMock(side_effect=AssertionError("cache write must not run")),
    )
    monkeypatch.setattr(
        rw, "save_progress",
        MagicMock(side_effect=AssertionError("progress write must not run")),
    )


# ---------- unwrap regex -------------------------------------------------------

@pytest.mark.parametrize(
    "url, expected",
    [
        (WAYBACK_AAAI, ORIG_AAAI),
        (
            "https://web.archive.org/web/2016/https://dslpitt.org/uai/displayArticleDetails.jsp?mmnu=1&smnu=2&article_id=1&proceeding_id=16",
            "https://dslpitt.org/uai/displayArticleDetails.jsp?mmnu=1&smnu=2&article_id=1&proceeding_id=16",
        ),
        ("https://ojs.aaai.org/index.php/AAAI/article/view/12345", None),
        ("https://web.archive.org/", None),
        ("", None),
        ("https://web.archive.org/web/2007/", None),  # no URL payload
    ],
)
def test_unwrap_wayback(url, expected):
    assert rw.unwrap_wayback(url) == expected


# ---------- new-URL priority -----------------------------------------------------

def test_choose_new_url_prefers_doi():
    work = _work(doi="https://doi.org/10.48550/arxiv.1301.3832",
                 landing="https://arxiv.org/abs/1301.3832")
    assert rw.choose_new_url(work, ORIG_AAAI) == "https://doi.org/10.48550/arxiv.1301.3832"


def test_choose_new_url_landing_fallback_and_rejections():
    # landing page used when no DOI
    work = _work(doi=None, landing="https://ojs.aaai.org/index.php/AAAI/article/view/7326")
    assert rw.choose_new_url(work, ORIG_AAAI) == "https://ojs.aaai.org/index.php/AAAI/article/view/7326"
    # landing pointing back at the dead original host → no change
    work = _work(doi=None, landing="http://www.aaai.org/ocs/index.php/AAAI/AAAI06/paper/view/7326")
    assert rw.choose_new_url(work, ORIG_AAAI) is None
    # landing still on web.archive.org → no change
    work = _work(doi=None, landing="https://web.archive.org/web/2020/https://example.org/x")
    assert rw.choose_new_url(work, ORIG_AAAI) is None
    # non-http landing → no change
    work = _work(doi=None, landing="ftp://example.org/x")
    assert rw.choose_new_url(work, ORIG_AAAI) is None
    # neither → None
    assert rw.choose_new_url(_work(doi=None, landing=None), ORIG_AAAI) is None


# ---------- gates -----------------------------------------------------------------

def test_evaluate_repair_title_gate_rejects():
    paper = _paper(WAYBACK_AAAI, title="The Real Local Title")
    work = _work(title="Completely Different Paper", doi="https://doi.org/10.1/x")
    new_url, abstract, reason = rw.evaluate_repair(work, paper)
    assert new_url is None and abstract is None
    assert reason == "title_mismatch"


def test_evaluate_repair_year_gate_rejects():
    paper = _paper(WAYBACK_AAAI, conf="AAAI2006")
    # 模糊标题（包含关系但 ratio < 0.98）仍受年份守卫约束。
    work = _work(title="Some Old Paper Revisited", year=2020, doi="https://doi.org/10.1/x")
    assert rw.evaluate_repair(work, paper)[2] == "title_mismatch"
    work = _work(title="Some Old Paper Revisited", year=2007, doi="https://doi.org/10.1/x")  # ±1 allowed
    assert rw.evaluate_repair(work, paper)[0] == "https://doi.org/10.1/x"


def test_evaluate_repair_exact_title_skips_year_gate():
    """老会议论文在 OpenAlex 的年份常是 arXiv 挂出年（如 UAI2000 → 2013），
    精确标题匹配（ratio ≥ 0.98）时年份守卫必须跳过，否则这批 wayback 记录
    会被大面积误杀（2026-10-25 CI run 40155700566 的 title_mismatch 主因）。"""
    paper = _paper(WAYBACK_AAAI, conf="UAI2000")
    work = _work(year=2013, doi="https://doi.org/10.48550/arxiv.1301.3857")
    new_url, _abstract, reason = rw.evaluate_repair(work, paper)
    assert reason == ""
    assert new_url == "https://doi.org/10.48550/arxiv.1301.3857"


def test_evaluate_repair_short_abstract_not_filled():
    paper = _paper(WAYBACK_AAAI)
    work = _work(abstract=False)
    new_url, abstract, reason = rw.evaluate_repair(work, paper)
    assert reason == ""
    assert abstract is None  # 无摘要可填，但 URL 仍可修


# ---------- pass: rewrite / merge / dry-run ----------------------------------------

def test_pass_rewrite_in_place_and_fill_abstract(monkeypatch):
    paper = _paper(WAYBACK_AAAI)
    papers = [paper]
    progress = {WAYBACK_AAAI: {"status": "failed", "attempts": 2, "ts": "2026-01-01T00:00:00"}}
    work = _work(doi="https://doi.org/10.1609/aaai.v21i1.7326")
    monkeypatch.setattr(rw, "_write_cache_atomic", MagicMock())
    monkeypatch.setattr(rw, "save_progress", MagicMock())
    # 日志文件重定向到临时路径
    import tempfile, pathlib
    log = pathlib.Path(tempfile.mkdtemp()) / "log.jsonl"
    monkeypatch.setattr(rw, "WAYBACK_REPAIR_LOG", log)

    stats = rw._repair_pass(
        papers, progress, max_papers=None, dry_run=False,
        fetch_fn=lambda _t: (work, ""),
    )

    assert stats["rewrite"] == 1
    assert stats["abstract_filled"] == 1
    assert paper["paper_url"] == "https://doi.org/10.1609/aaai.v21i1.7326"
    assert paper["paper_abstract"] == LONG_ABSTRACT
    # 新 URL 记 success(openalex_title)；旧 URL 记录不动。
    assert progress["https://doi.org/10.1609/aaai.v21i1.7326"]["source"] == "openalex_title"
    assert progress[WAYBACK_AAAI]["status"] == "failed"
    rw._write_cache_atomic.assert_called_once()
    rw.save_progress.assert_called_once()
    # 迁移日志一行，action=rewrite
    lines = log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    import json as _json
    rec = _json.loads(lines[0])
    assert rec["action"] == "rewrite"
    assert rec["old_url"] == WAYBACK_AAAI
    assert rec["new_url"] == paper["paper_url"]


def test_pass_merge_conflict_merges_and_drops(monkeypatch):
    """(conf, new_url) 已存在 → 真实 collector._merge_paper_record 合并，
    wayback 记录从缓存删除。"""
    new_url = "https://doi.org/10.1609/aaai.v21i1.7326"
    existing = _paper(new_url, conf="AAAI2006")
    existing["paper_abstract"] = ""  # 既有记录也没摘要 → 合并后拿到回填摘要
    existing["paper_code"] = "https://github.com/example/repo"
    wayback = _paper(WAYBACK_AAAI, conf="AAAI2006")
    papers = [existing, wayback]
    progress = {}

    import tempfile, pathlib
    log = pathlib.Path(tempfile.mkdtemp()) / "log.jsonl"
    monkeypatch.setattr(rw, "WAYBACK_REPAIR_LOG", log)
    written = {"papers": None}
    monkeypatch.setattr(
        rw, "_write_cache_atomic",
        lambda ps: written.__setitem__("papers", list(ps)),
    )
    monkeypatch.setattr(rw, "save_progress", MagicMock())

    work = _work(doi=new_url)
    stats = rw._repair_pass(
        papers, progress, max_papers=None, dry_run=False,
        fetch_fn=lambda _t: (work, ""),
    )

    assert stats["merge"] == 1 and stats["rewrite"] == 0
    assert len(written["papers"]) == 1
    merged = written["papers"][0]
    assert merged["paper_url"] == new_url
    assert merged["paper_abstract"] == LONG_ABSTRACT  # 合并带入了回填摘要
    assert merged["paper_code"] == "https://github.com/example/repo"  # 非占位 code 保留


def test_pass_unchanged_when_gate_fails(monkeypatch):
    paper = _paper(WAYBACK_AAAI)
    progress = {}
    stats = rw._repair_pass(
        [paper], progress, max_papers=None, dry_run=False,
        fetch_fn=lambda _t: (_work(title="Totally Different"), ""),
    )
    assert stats["unchanged"] == 1
    assert stats["reasons"]["title_mismatch"] == 1
    assert paper["paper_url"] == WAYBACK_AAAI


def test_pass_dry_run_writes_nothing(monkeypatch):
    paper = _paper(WAYBACK_AAAI)
    progress = {WAYBACK_AAAI: {"status": "failed", "attempts": 1}}
    snapshot_url = paper["paper_url"]
    _no_disk(monkeypatch)
    work = _work(doi="https://doi.org/10.1609/aaai.v21i1.7326")

    stats = rw._repair_pass(
        [paper], progress, max_papers=None, dry_run=True,
        fetch_fn=lambda _t: (work, ""),
    )

    assert stats["rewrite"] == 1  # 报告照算
    assert stats["abstract_filled"] == 1
    assert paper["paper_url"] == snapshot_url  # 但未改写
    assert paper["paper_abstract"] == ""
    assert "https://doi.org/10.1609/aaai.v21i1.7326" not in progress
    rw._write_cache_atomic.assert_not_called()
    rw.save_progress.assert_not_called()


def test_pass_max_papers_limit():
    papers = [_paper(f"https://web.archive.org/web/2007/http://www.aaai.org/x/{i}") for i in range(5)]
    stats = rw._repair_pass(
        papers, {}, max_papers=2, dry_run=True,
        fetch_fn=lambda _t: (None, "empty_abstract"),
    )
    # 2 条被处理（都 unchanged），3 条没轮到
    assert stats["unchanged"] == 2
    assert stats["total_wayback"] == 5

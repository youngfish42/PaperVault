"""Tests for ``scripts/repair_wayback_urls.py``.

Lookup is stubbed by injecting ``lookup_fn`` into ``_repair_pass`` —
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
        lookup_fn=lambda _t, _c: ("openalex", work, "", []),
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
        lookup_fn=lambda _t, _c: ("openalex", work, "", []),
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
        lookup_fn=lambda _t, _c: ("openalex", _work(title="Totally Different"), "", []),
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
        lookup_fn=lambda _t, _c: ("openalex", work, "", []),
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
        lookup_fn=lambda _t, _c: (None, None, "empty_abstract", []),
    )
    # 2 条被处理（都 unchanged），3 条没轮到
    assert stats["unchanged"] == 2
    assert stats["total_wayback"] == 5


# ---------- Crossref / arXiv 兜底源（mock HTTP 层在 rw._s2_request） ----------


def _resp(payload=None, status=200, text=""):
    r = MagicMock()
    r.status_code = status
    if payload is not None:
        r.json.return_value = payload
    r.text = text
    r.headers = {}
    return r


CROSSREF_ITEM = {
    "DOI": "10.1609/aaai.v21i1.7326",
    "title": ["Some Old Paper"],
    "published": {"date-parts": [[2006]]},
    "abstract": "<jats:p>We present a sufficiently long synthetic abstract for the wayback repair tests, comfortably clearing the sixty-character minimum gate.</jats:p>",
}

ARXIV_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/1301.3857v1</id>
    <title>Some Old Paper</title>
    <summary>We present a sufficiently long synthetic abstract for the wayback repair tests, comfortably clearing the sixty-character minimum gate.</summary>
  </entry>
</feed>"""


def test_crossref_fetch_parses_first_item(monkeypatch):
    captured = {}
    def fake(method, url, **kw):
        captured["params"] = kw.get("params")
        captured["min_interval"] = kw.get("min_interval")
        captured["rate_state"] = kw.get("rate_state")
        return _resp({"message": {"items": [CROSSREF_ITEM]}}), ""
    monkeypatch.setattr(rw, "_s2_request", fake)
    work, err = rw._crossref_repair_fetch("Some Old Paper")
    assert err == ""
    assert work["DOI"] == "10.1609/aaai.v21i1.7326"
    assert captured["params"]["query.bibliographic"] == "Some Old Paper"
    assert captured["params"]["rows"] == 1
    assert captured["min_interval"] == rw.CROSSREF_MIN_INTERVAL  # 2 rps
    assert captured["rate_state"] is rw._crossref_rate_state   # 独立 rate_state


def test_crossref_fetch_empty_and_rate_limited(monkeypatch):
    monkeypatch.setattr(rw, "_s2_request", lambda *a, **k: (_resp({"message": {"items": []}}), ""))
    work, err = rw._crossref_repair_fetch("nothing")
    assert work is None and err == "empty_abstract"
    monkeypatch.setattr(rw, "_s2_request", lambda *a, **k: (None, "rate_limited"))
    work, err = rw._crossref_repair_fetch("nothing")
    assert work is None and err == "rate_limited"


def test_arxiv_fetch_parses_atom_and_strips_version(monkeypatch):
    captured = {}
    def fake(method, url, **kw):
        captured["url"] = url
        captured["min_interval"] = kw.get("min_interval")
        return _resp(text=ARXIV_XML), ""
    monkeypatch.setattr(rw, "_s2_request", fake)
    work, err = rw._arxiv_repair_fetch("Some Old Paper")
    assert err == ""
    assert work["abs_url"] == "https://arxiv.org/abs/1301.3857"  # 去 v1 + https
    assert work["title"] == "Some Old Paper"
    assert captured["min_interval"] == rw.ARXIV_MIN_INTERVAL     # 3s 间隔
    assert 'ti:"Some%20Old%20Paper"' in captured["url"]


def test_arxiv_fetch_no_entry(monkeypatch):
    xml = '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>'
    monkeypatch.setattr(rw, "_s2_request", lambda *a, **k: (_resp(text=xml), ""))
    work, err = rw._arxiv_repair_fetch("nothing")
    assert work is None and err == "empty_abstract"


# ---------- evaluate_repair：各源语义 ----------


def test_evaluate_crossref_doi_url_and_jats_abstract():
    paper = _paper(WAYBACK_AAAI)
    new_url, abstract, reason = rw.evaluate_repair(CROSSREF_ITEM, paper, source="crossref")
    assert reason == ""
    assert new_url == "https://doi.org/10.1609/aaai.v21i1.7326"
    assert abstract is not None
    assert "<jats:p>" not in abstract  # JATS 标签已去


def test_evaluate_crossref_year_gate_uses_date_parts():
    paper = _paper(WAYBACK_AAAI, conf="AAAI2006")
    bad_year = dict(CROSSREF_ITEM, published={"date-parts": [[1990]]})
    # 标题精确匹配（ratio>=0.98）→ 跳过年份守卫
    new_url, _a, reason = rw.evaluate_repair(bad_year, paper, source="crossref")
    assert reason == "" and new_url is not None
    # 模糊匹配 + 年份超差 → title_mismatch
    fuzzy = dict(bad_year, title=["Some Old Paper With Extra Words And More Padding"])
    _u, _a, reason = rw.evaluate_repair(fuzzy, paper, source="crossref")
    assert reason == "title_mismatch"


def test_evaluate_crossref_short_abstract_only_repairs_url():
    paper = _paper(WAYBACK_AAAI)
    item = dict(CROSSREF_ITEM, abstract="<jats:p>short</jats:p>")
    new_url, abstract, reason = rw.evaluate_repair(item, paper, source="crossref")
    assert reason == ""
    assert new_url is not None
    assert abstract is None  # 未过 MIN_ABSTRACT_CHARS → 只修 URL


def test_evaluate_arxiv_skips_year_gate():
    paper = _paper(WAYBACK_AAAI, conf="AAAI2006")
    work = {"title": "Some Old Paper", "abs_url": "https://arxiv.org/abs/1301.3857",
            "summary": "x" * 100}
    new_url, abstract, reason = rw.evaluate_repair(work, paper, source="arxiv")
    assert reason == ""
    assert new_url == "https://arxiv.org/abs/1301.3857"
    assert abstract == "x" * 100


# ---------- 兜底链顺序 / 单源模式 / rate_limited 直落 ----------


def test_lookup_auto_chain_openalex_first(monkeypatch):
    calls = []
    def fake(src, title):
        calls.append(src)
        return ({"title": title}, "") if src == "openalex" else (None, "empty_abstract")
    monkeypatch.setattr(rw, "_fetch_by_source", fake)
    src, work, err, _att = rw.make_lookup("auto")("Some Old Paper", "AAAI2006")
    assert src == "openalex" and work is not None
    assert calls == ["openalex"]  # 命中即停


def test_lookup_auto_falls_through_on_rate_limited(monkeypatch):
    calls = []
    def fake(src, title):
        calls.append(src)
        if src == "openalex":
            return None, "rate_limited"
        if src == "crossref":
            return None, "empty_abstract"
        return {"title": title}, ""
    monkeypatch.setattr(rw, "_fetch_by_source", fake)
    src, work, err, _att = rw.make_lookup("auto")("Some Old Paper", "AAAI2006")
    assert src == "arxiv"
    assert calls == ["openalex", "crossref", "arxiv"]


def test_lookup_auto_falls_through_on_title_mismatch(monkeypatch):
    """Crossref 的 bibliographic 模糊搜索对库外论文也会返回候选——标题门控
    失败的候选不得挡住 arXiv 兜底（2026-10-25 第二轮 run 3,817 条滞留）。"""
    calls = []
    def fake(src, title):
        calls.append(src)
        if src == "crossref":
            return {"title": "A Completely Different Paper About Something Else"}, ""
        return (None, "rate_limited") if src == "openalex" else ({"title": title}, "")
    monkeypatch.setattr(rw, "_fetch_by_source", fake)
    src, work, err, attempts = rw.make_lookup("auto")("Some Old Paper", "UAI2001")
    assert src == "arxiv"
    assert calls == ["openalex", "crossref", "arxiv"]
    assert ("crossref", "title_mismatch") in attempts


def test_lookup_single_source_only(monkeypatch):
    calls = []
    def fake(src, title):
        calls.append(src)
        return None, "rate_limited"
    monkeypatch.setattr(rw, "_fetch_by_source", fake)
    src, work, err, _att = rw.make_lookup("crossref")("t", "c")
    assert src is None and err == "rate_limited"
    assert calls == ["crossref"]  # 不落其它源


def test_pass_chain_rewrite_by_source_and_progress_source(monkeypatch):
    """auto 链端到端：openalex 429 → crossref 命中；by_source 分桶统计；
    摘要回填的 progress source 记 crossref_title。"""
    paper = _paper(WAYBACK_AAAI)
    progress = {}

    def fake(src, title):
        if src == "openalex":
            return None, "rate_limited"
        return CROSSREF_ITEM, ""
    monkeypatch.setattr(rw, "_fetch_by_source", fake)

    import tempfile, pathlib
    log = pathlib.Path(tempfile.mkdtemp()) / "log.jsonl"
    monkeypatch.setattr(rw, "WAYBACK_REPAIR_LOG", log)
    monkeypatch.setattr(rw, "_write_cache_atomic", MagicMock())
    monkeypatch.setattr(rw, "save_progress", MagicMock())

    stats = rw._repair_pass([paper], progress, max_papers=None, dry_run=False)
    assert stats["rewrite"] == 1
    assert stats["by_source"]["crossref"]["rewrite"] == 1
    assert stats["by_source"]["openalex"]["rate_limited"] == 1
    new_url = "https://doi.org/10.1609/aaai.v21i1.7326"
    assert progress[new_url]["source"] == "crossref_title"
    assert paper["paper_url"] == new_url


def test_pass_flushes_in_chunks(monkeypatch, tmp_path):
    """每 REPAIR_FLUSH_EVERY 条落盘一次：挂机/中断只损失一个 chunk
    （2026-10-26 两次长跑卡死、all-or-nothing 落盘全丢数小时成果的回归）。"""
    monkeypatch.setattr(rw, "REPAIR_FLUSH_EVERY", 2)
    papers = [_paper(WAYBACK_AAAI.replace("7326", str(8000 + i))) for i in range(3)]
    progress = {}

    def fake(src, title):
        return (CROSSREF_ITEM, "") if src == "crossref" else (None, "rate_limited")

    monkeypatch.setattr(rw, "_fetch_by_source", fake)
    monkeypatch.setattr(rw, "WAYBACK_REPAIR_LOG", tmp_path / "log.jsonl")
    write_cache = MagicMock()
    monkeypatch.setattr(rw, "_write_cache_atomic", write_cache)
    monkeypatch.setattr(rw, "save_progress", MagicMock())

    stats = rw._repair_pass(papers, progress, max_papers=None, dry_run=False)

    assert stats["processed"] == 3
    # chunk=2：i=2 触发一次 chunk flush，结尾终刷一次 → 共 2 次写缓存
    assert write_cache.call_count == 2
    lines = (tmp_path / "log.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3

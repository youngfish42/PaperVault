"""Tests for ``scripts/audit_wayback_repair.py`` (PR #228 pollution follow-up).

Fully offline: remote title lookups are injected, caches are synthetic
in-memory records. Covers audit verdicts (PASS / FAIL / unverifiable /
collision groups), rewrite revert (incl. abstract_filled clearing),
merge revert (record restore + target field restore), progress marking,
and dry-run touching nothing.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import audit_wayback_repair as au  # noqa: E402
from scripts import repair_wayback_urls as rw  # noqa: E402


CROSSREF_ITEM = {
    "DOI": "10.1609/aaai.v21i1.7326",
    "title": ["Some Old Paper"],
    "published": {"date-parts": [[2006]]},
    "abstract": "<jats:p>We present a sufficiently long synthetic abstract for the "
    "wayback repair tests, comfortably clearing the sixty-character minimum gate.</jats:p>",
}


def _paper(url, title="Some Old Paper", conf="AAAI2006"):
    return {"paper_url": url, "paper_name": title, "paper_abstract": "",
            "paper_code": "#", "paper_authors": ["A"], "conf": conf}


def _resp(payload=None, status=200, text=""):
    r = MagicMock()
    r.status_code = status
    if payload is not None:
        r.json.return_value = payload
    r.text = text
    r.headers = {}
    return r


def _work(title="Some Old Paper", year=2006, doi=None, abstract=True):
    inv = {}
    if abstract:
        text = ("We present a sufficiently long synthetic abstract for the wayback "
                "repair tests, comfortably clearing the sixty-character minimum gate.")
        for pos, w in enumerate(text.split()):
            inv.setdefault(w, []).append(pos)
    return {"title": title, "publication_year": year, "doi": doi,
            "abstract_inverted_index": inv or None}



OLD_URL = ("https://web.archive.org/web/2015/http://www.aaai.org/ocs/index.php"
           "/AAAI/AAAI12/paper/view/1234")
NEW_DOI = "https://doi.org/10.17816/psaic720-406"
NEW_ARXIV = "https://arxiv.org/abs/1207.4114"


def _rec(url, title="Some Real Paper", conf="AAAI2012", abstract="", code="#"):
    return {
        "paper_url": url, "paper_name": title, "paper_abstract": abstract,
        "paper_code": code, "paper_authors": ["A"], "conf": conf,
    }


def _entry(action="rewrite", old=OLD_URL, new=NEW_DOI, conf="AAAI2012",
           filled=False, **kw):
    e = {"old_url": old, "new_url": new, "conf": conf, "action": action,
         "abstract_filled": filled}
    e.update(kw)
    return e


# ---------- audit verdicts -------------------------------------------------------

def test_audit_rewrite_pass_and_fail():
    cur = au.by_conf_url([_rec(NEW_DOI, title="Some Real Paper")])
    entries = [
        _entry(),  # remote matches → PASS
        _entry(new=NEW_ARXIV),  # remote differs → FAIL
    ]
    remote = {NEW_DOI: "Some Real Paper!", NEW_ARXIV: "Totally Unrelated Journal Article"}
    rows = au.audit_entries(entries, cur, None, remote)
    assert rows[0]["verdict"] == "PASS" and rows[0]["ratio"] >= 0.9
    assert rows[1]["verdict"] == "FAIL" and rows[1]["ratio"] < 0.9


def test_audit_unverifiable_hosts_and_missing_remote():
    cur = au.by_conf_url([_rec("https://peerj.com/x", title="T")])
    entries = [_entry(new="https://peerj.com/articles/20567/table-4"),
               _entry(new=NEW_DOI)]  # remote missing → unverifiable too
    rows = au.audit_entries(entries, cur, None, {NEW_DOI: None})
    assert rows[0]["verdict"] == "unverifiable"  # 非 doi/arxiv host 不联网
    assert rows[1]["verdict"] == "unverifiable"  # 远端取不到
    assert rows[0]["ratio"] is None


def test_audit_merge_uses_prerepair_cache_then_log_fallback():
    """merge 条目的本地标题：修复前缓存优先；缺日志 old_title 时兼容存量行。"""
    pre = au.by_conf_url([_rec(OLD_URL, title="Title From Prerepair")])
    entries = [_entry(action="merge", old_title="Title From Log")]
    rows = au.audit_entries(entries, {}, pre, {NEW_DOI: "Title From Prerepair"})
    assert rows[0]["local_title"] == "Title From Prerepair"
    assert rows[0]["verdict"] == "PASS"
    # 修复前缓存没有该记录 → 用日志行的 old_title（评审问题 6 修复前的存量行）
    rows = au.audit_entries(entries, {}, {}, {NEW_DOI: "Title From Log"})
    assert rows[0]["local_title"] == "Title From Log"
    assert rows[0]["verdict"] == "PASS"
    # 两者皆无 → 空标题必然 FAIL（ratio 0）
    rows = au.audit_entries([_entry(action="merge")], {}, {}, {NEW_DOI: "Whatever"})
    assert rows[0]["verdict"] == "FAIL"


def test_audit_collision_group_mixed_verdicts():
    """同一 new_url 分给两篇不同论文：达标的保留、不达标的 FAIL。"""
    cur = au.by_conf_url([
        _rec(NEW_ARXIV, title="Credence A Decentralized Object Reputation System", conf="UAI2004"),
        _rec(NEW_ARXIV, title="Completely Different AAAI Paper", conf="AAAI2004"),
    ])
    entries = [
        _entry(new=NEW_ARXIV, conf="UAI2004"),
        _entry(new=NEW_ARXIV, conf="AAAI2004"),
    ]
    remote = {NEW_ARXIV: "Credence: A Decentralized Object Reputation System"}
    rows = au.audit_entries(entries, cur, None, remote)
    assert rows[0]["verdict"] == "PASS"
    assert rows[1]["verdict"] == "FAIL"


def test_resolve_remote_titles_routing():
    urls = [NEW_DOI, NEW_ARXIV, "https://peerj.com/x"]
    out = au.resolve_remote_titles(
        urls,
        crossref_fn=lambda u: "Crossref Title",
        arxiv_batch_fn=lambda ids: {i: f"ArXiv {i}" for i in ids},
    )
    assert out[NEW_DOI] == "Crossref Title"
    assert out[NEW_ARXIV] == "ArXiv 1207.4114"  # id 取路径末段
    assert out["https://peerj.com/x"] is None  # 不联网


def test_arxiv_batch_url_carries_max_results_and_keeps_category_prefix(monkeypatch):
    """Regression (audit round 2/3): arXiv id_list defaults to max_results=10,
    so a 100-id batch silently returned only 10 entries (852 ids → 80
    resolved); and old-style ids keep their category prefix
    (``cs/0003013``), which naive last-segment extraction destroyed."""
    captured = {}

    def fake(method, url, **kw):
        captured["url"] = url
        resp = MagicMock()
        resp.status_code = 200
        resp.text = (
            '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">'
            "<entry><id>http://arxiv.org/abs/cs/0003013v2</id>"
            "<title>Old Category Paper</title></entry></feed>"
        )
        return resp, ""

    monkeypatch.setattr(au, "_s2_request", fake)
    ids = ["cs/0003013"] + [f"1301.{2200 + i}" for i in range(99)]
    out = au._arxiv_titles_batch(ids)
    assert "max_results=100" in captured["url"]
    assert out["cs/0003013"] == "Old Category Paper"  # 分类前缀 + 去版本号


# ---------- revert ---------------------------------------------------------------


def test_revert_rewrite_restores_url_and_clears_filled_abstract():
    papers = [_rec(NEW_DOI, abstract="abstract of the WRONG paper")]
    progress = {NEW_DOI: {"status": "success", "source": "crossref_title", "chars": 10}}
    rows = [{"entry": _entry(filled=True), "verdict": "FAIL"}]
    stats = au.revert_failed(rows, papers, None, progress, dry_run=False)
    assert stats["rewrite_reverted"] == 1
    assert papers[0]["paper_url"] == OLD_URL
    assert papers[0]["paper_abstract"] == ""  # 错误论文的摘要被清掉
    assert progress[NEW_DOI]["status"] == "failed"
    assert progress[NEW_DOI]["reason"] == "title_mismatch"
    assert progress[NEW_DOI]["last_source"] == "wayback_repair_audit"


def test_revert_rewrite_keeps_own_abstract_when_not_filled():
    papers = [_rec(NEW_DOI, abstract="legit pre-existing abstract")]
    progress = {}
    rows = [{"entry": _entry(filled=False), "verdict": "FAIL"}]
    au.revert_failed(rows, papers, None, progress, dry_run=False)
    assert papers[0]["paper_abstract"] == "legit pre-existing abstract"


def test_revert_merge_restores_record_and_target_fields():
    orig = _rec(OLD_URL, title="Original Wayback Record", abstract="orig abs")
    target_pre = _rec(NEW_DOI, title="Target", abstract="pre-repair abs", code="#")
    prerepair = au.by_conf_url([orig, target_pre])
    # 当前缓存：只有 merge 目标（wayback 记录已被删），且被并入了错误摘要
    target_now = _rec(NEW_DOI, title="Target", abstract="WRONG merged abstract",
                      code="https://github.com/wrong")
    papers = [target_now]
    progress = {}
    rows = [{"entry": _entry(action="merge", filled=False), "verdict": "FAIL"}]
    stats = au.revert_failed(rows, papers, prerepair, progress, dry_run=False)
    assert stats["merge_reverted"] == 1
    assert stats["target_restored"] == 1
    urls = {p["paper_url"] for p in papers}
    assert urls == {OLD_URL, NEW_DOI}  # 原记录整条恢复
    restored = next(p for p in papers if p["paper_url"] == OLD_URL)
    assert restored["paper_name"] == "Original Wayback Record"
    assert restored["paper_abstract"] == "orig abs"
    # 目标字段恢复为修复前版本
    assert target_now["paper_abstract"] == "pre-repair abs"
    assert target_now["paper_code"] == "#"


def test_revert_merge_target_created_this_round_not_restored():
    """merge 目标在修复前不存在（是本轮某条 rewrite 创建的）→ 不做字段恢复。"""
    orig = _rec(OLD_URL, title="Original")
    prerepair = au.by_conf_url([orig])  # 注意：不含 new_url 目标
    papers = [_rec(NEW_DOI, abstract="whatever")]
    rows = [{"entry": _entry(action="merge"), "verdict": "FAIL"}]
    stats = au.revert_failed(rows, papers, prerepair, {}, dry_run=False)
    assert stats["merge_reverted"] == 1
    assert stats["target_restored"] == 0
    assert papers[0]["paper_abstract"] == "whatever"  # 未被触碰


def test_revert_missing_records_counted():
    rows = [{"entry": _entry(), "verdict": "FAIL"}]
    stats = au.revert_failed(rows, [], None, {}, dry_run=False)
    assert stats["missing_current"] == 1
    rows = [{"entry": _entry(action="merge"), "verdict": "FAIL"}]
    stats = au.revert_failed(rows, [], None, {}, dry_run=False)
    assert stats["missing_prerepair"] == 1


def test_revert_dry_run_mutates_nothing():
    papers = [_rec(NEW_DOI, abstract="wrong abstract")]
    progress = {NEW_DOI: {"status": "success"}}
    rows = [{"entry": _entry(filled=True), "verdict": "FAIL"}]
    stats = au.revert_failed(rows, papers, None, progress, dry_run=True)
    assert stats["rewrite_reverted"] == 1  # 计数照算
    assert papers[0]["paper_url"] == NEW_DOI  # 但未改
    assert papers[0]["paper_abstract"] == "wrong abstract"
    assert progress[NEW_DOI]["status"] == "success"


# ---------- log loading / dedupe ---------------------------------------------------

def test_load_log_entries_dedupes(tmp_path):
    a = tmp_path / "a.jsonl"
    b = tmp_path / "b.jsonl"
    line = '{"old_url": "u1", "new_url": "u2", "action": "rewrite"}'
    a.write_text(line + "\n" + line + "\n", encoding="utf-8")
    b.write_text(line + "\n" + '{"old_url": "u3", "new_url": "u4", "action": "merge"}\n',
                 encoding="utf-8")
    entries = au.load_log_entries([a, b, tmp_path / "missing.jsonl"])
    assert len(entries) == 2


# ---------- --revert-max-ratio 收窄 + 硬规则 -----------------------------------


def _row(ratio, url=NEW_DOI, verdict="FAIL"):
    return {"entry": _entry(new=url), "ratio": ratio, "verdict": verdict,
            "local_title": "", "remote_title": ""}


def test_should_revert_threshold_boundary():
    # 默认阈值已升至 0.90（用户确认；0.75 过宽会留下非同作品错链）
    assert au.REVERT_DEFAULT_MAX_RATIO == 0.90
    assert au.should_revert(_row(0.74)) is True
    assert au.should_revert(_row(0.89)) is True    # 0.75–0.90 带也回滚
    assert au.should_revert(_row(0.89)) is True
    assert au.should_revert(_row(0.20)) is True
    # 显式给 0.75 时恢复旧边界（含头不含尾）
    assert au.should_revert(_row(0.75), 0.75) is False
    assert au.should_revert(_row(0.74), 0.75) is True
    # PASS 永不回滚
    assert au.should_revert(_row(0.3, verdict="PASS")) is False


def test_should_revert_hard_rules():
    # peerj table-4（ratio 0.81）必须被硬规则覆盖
    assert au.should_revert(_row(0.81, "https://peerj.com/articles/20567/table-4")) is True
    assert au.should_revert(_row(0.85, "https://doi.org/10.1007/springerreference_61955")) is True
    assert au.should_revert(_row(0.83, "https://example.org/paper/figure-2")) is True
    # 硬规则在 >=0.90 不再适用（那已是 PASS，不会走到这）
    assert au.should_revert(_row(0.91, "https://doi.org/10.1007/springerreference_61955")) is False
    # 普通 DOI 高于（显式 0.75）阈值保留
    assert au.should_revert(_row(0.86, NEW_DOI), 0.75) is False


def test_revert_selection_leaves_same_paper_variants_untouched():
    """显式阈值 0.75 时，0.82 的同论文变体保留：URL 与 progress 均不动。"""
    papers = [_rec(NEW_DOI, abstract="variant abstract kept")]
    progress = {NEW_DOI: {"status": "success", "source": "crossref_title"}}
    rows = [{"entry": _entry(filled=True), "ratio": 0.82, "verdict": "FAIL",
             "local_title": "", "remote_title": ""}]
    to_revert = [r for r in rows if au.should_revert(r, 0.75)]
    kept = [r for r in rows if not au.should_revert(r, 0.75)]
    assert to_revert == [] and len(kept) == 1
    stats = au.revert_failed(to_revert, papers, None, progress, dry_run=False)
    assert stats["rewrite_reverted"] == 0
    assert papers[0]["paper_url"] == NEW_DOI
    assert progress[NEW_DOI]["status"] == "success"


def test_revert_default_threshold_090_recovers_075_090_band():
    """默认 0.90：0.82 的 FAIL 条目本轮应回滚（0.75 时代的保留带被收回）。"""
    papers = [_rec(NEW_DOI, abstract="wrong abstract")]
    progress = {NEW_DOI: {"status": "success"}}
    rows = [{"entry": _entry(filled=True), "ratio": 0.82, "verdict": "FAIL",
             "local_title": "", "remote_title": ""}]
    to_revert = [r for r in rows if au.should_revert(r)]
    assert len(to_revert) == 1
    stats = au.revert_failed(to_revert, papers, None, progress, dry_run=False)
    assert stats["rewrite_reverted"] == 1
    assert papers[0]["paper_url"] == OLD_URL


def test_should_revert_unverifiable_hard_patterns():
    """Fix 5: unverifiable 条目中表格/图子页一律回滚，无坏 pattern 的保留。"""
    assert au.should_revert(_row(None, "https://peerj.com/articles/20567/table-4", verdict="unverifiable")) is True
    assert au.should_revert(_row(None, "https://doi.org/10.1007/x/tables/3", verdict="unverifiable")) is True
    assert au.should_revert(_row(None, "https://doi.org/10.1007/x/figures/2", verdict="unverifiable")) is True
    assert au.should_revert(_row(None, "https://doi.org/10.1007/springerreference_1", verdict="unverifiable")) is True
    # 无坏 pattern 的 unverifiable 继续保留
    assert au.should_revert(_row(None, "https://peerj.com/articles/20567/", verdict="unverifiable")) is False
    assert au.should_revert(_row(None, NEW_DOI, verdict="unverifiable")) is False


def test_candidate_year_crossref_fallback_chain():
    """Fix 2: Crossref 年份按 published → published-print → issued 回退。"""
    base = dict(CROSSREF_ITEM)
    assert rw._candidate_year("crossref", base) == 2006  # published 优先
    item = {k: v for k, v in base.items() if k != "published"}
    item["published-print"] = {"date-parts": [[2005]]}
    item["issued"] = {"date-parts": [[2007]]}
    assert rw._candidate_year("crossref", item) == 2005
    item = {k: v for k, v in item.items() if k != "published-print"}
    assert rw._candidate_year("crossref", item) == 2007
    item = {k: v for k, v in base.items() if k not in ("published",)}
    assert rw._candidate_year("crossref", item) is None


def test_crossref_fetch_select_includes_year_fallbacks(monkeypatch):
    """Fix 2: select 必须含 published/published-print/issued。"""
    captured = {}
    def fake(method, url, **kw):
        captured["params"] = kw.get("params")
        return _resp({"message": {"items": [CROSSREF_ITEM]}}), ""
    monkeypatch.setattr(rw, "_s2_request", fake)
    rw._crossref_repair_fetch("Some Old Paper")
    select = captured["params"]["select"]
    for f in ("published", "published-print", "issued"):
        assert f in select


def test_merge_log_carries_old_code(monkeypatch):
    """Fix 3: merge 日志行带 old_code（_merge_paper_record 会合并该字段）。"""
    new_url = "https://doi.org/10.1609/aaai.v21i1.7326"
    existing = _paper(new_url, conf="AAAI2012")
    existing["paper_abstract"] = "existing abstract long enough to win the merge tiebreaker."
    wayback = _paper(OLD_URL, conf="AAAI2012")
    wayback["paper_code"] = "https://github.com/old/repo"
    papers = [existing, wayback]
    progress = {}

    import tempfile, pathlib
    log = pathlib.Path(tempfile.mkdtemp()) / "log.jsonl"
    monkeypatch.setattr(rw, "WAYBACK_REPAIR_LOG", log)
    monkeypatch.setattr(rw, "_write_cache_atomic", MagicMock())
    monkeypatch.setattr(rw, "save_progress", MagicMock())

    work = _work(doi=new_url, abstract=False)
    rw._repair_pass(
        papers, progress, max_papers=None, dry_run=False,
        lookup_fn=lambda _t, _c: ("openalex", work, "", []),
    )
    import json as _json
    rec = _json.loads(log.read_text(encoding="utf-8").strip())
    assert rec["action"] == "merge"
    assert rec["old_code"] == "https://github.com/old/repo"


def test_report_lists_kept_variants(tmp_path):
    rows = [
        {"entry": _entry(), "ratio": 0.82, "verdict": "FAIL",
         "local_title": "Local Variant Title", "remote_title": "Remote Canonical Title"},
    ]
    out = tmp_path / "report.md"
    au.write_report(rows, out, kept_variants=rows)
    text = out.read_text(encoding="utf-8")
    assert "same-paper variant" in text
    assert "Remote Canonical Title" in text
    # 不给 kept_variants 时没有这一节
    out2 = tmp_path / "report2.md"
    au.write_report(rows, out2)
    assert "same-paper variant" not in out2.read_text(encoding="utf-8")

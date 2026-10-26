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
    assert au.should_revert(_row(0.74)) is True
    assert au.should_revert(_row(0.75)) is False   # 阈值含头不含尾
    assert au.should_revert(_row(0.89)) is False
    assert au.should_revert(_row(0.20)) is True
    # PASS / unverifiable 永不回滚
    assert au.should_revert(_row(0.3, verdict="PASS")) is False
    assert au.should_revert(_row(None, verdict="unverifiable")) is False


def test_should_revert_hard_rules():
    # peerj table-4（ratio 0.81）必须被硬规则覆盖
    assert au.should_revert(_row(0.81, "https://peerj.com/articles/20567/table-4")) is True
    assert au.should_revert(_row(0.85, "https://doi.org/10.1007/springerreference_61955")) is True
    assert au.should_revert(_row(0.83, "https://example.org/paper/figure-2")) is True
    # 硬规则在 >=0.90 不再适用（那已是 PASS，不会走到这）
    assert au.should_revert(_row(0.91, "https://doi.org/10.1007/springerreference_61955")) is False
    # 普通 DOI 高于阈值保留
    assert au.should_revert(_row(0.86, NEW_DOI)) is False


def test_revert_selection_leaves_same_paper_variants_untouched():
    """阈值以上的 FAIL（同论文变体）保留：URL 与 progress 均不动。"""
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

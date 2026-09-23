"""Guard test: a DBLP page that yields 0 papers must NOT be marked as
collected in the per-URL progress (it would otherwise be skipped forever,
e.g. when an anti-bot challenge page parses as empty)."""

from __future__ import annotations

import json

import collector


def _all_conf_progress():
    """Every conf URL across all sources, marked collected."""
    progress = {}
    for filename, source in [
        ("acl_conf.json", "ACL"),
        ("iclr_conf.json", "ICLR"),
        ("thecvf_conf.json", "thecvf"),
        ("nips_conf.json", "NeurIPS"),
        ("dblp_conf.json", "DBLP"),
    ]:
        with open(f"conf/{filename}", encoding="utf-8") as f:
            for conf in json.load(f):
                progress[f"{source}::{conf['url']}"] = {
                    "name": conf["name"], "ts": "2026-01-01T00:00:00",
                }
    return progress


def _run_collect(monkeypatch, tmp_path, progress, search_stub):
    monkeypatch.setattr(collector, "load_collect_progress", lambda: progress)
    captured = {}

    def _save(p):
        # 管线就地修改同一个 progress dict，按引用捕获即可看到最终状态；
        # 节流（5s 一次）的 save 调用本身不代表进度内容。
        captured["progress"] = p

    monkeypatch.setattr(collector, "save_collect_progress", _save)
    monkeypatch.setattr(collector, "search_from_dblp", search_stub)
    failures_file = tmp_path / "failures.json"
    monkeypatch.setattr(collector, "COLLECT_FAILURES_FILE", str(failures_file))

    collector.collect(cache_file=None)
    return captured.get("progress", progress), json.loads(failures_file.read_text(encoding="utf-8"))


def test_dblp_empty_result_not_marked_collected(monkeypatch, tmp_path):
    progress = _all_conf_progress()
    target_url = "https://dblp.org/db/journals/aeog/aeog94.html"
    target_key = f"DBLP::{target_url}"
    progress.pop(target_key)

    final_progress, failures = _run_collect(
        monkeypatch, tmp_path, progress, lambda url, name, res: res
    )
    assert target_key not in final_progress
    assert any(f["url"] == target_url and f["source"] == "DBLP" for f in failures)


def test_dblp_non_empty_result_marked_collected(monkeypatch, tmp_path):
    progress = _all_conf_progress()
    target_url = "https://dblp.org/db/journals/aeog/aeog94.html"
    target_key = f"DBLP::{target_url}"
    progress.pop(target_key)

    def _stub(url, name, res):
        res.setdefault(name, []).append({
            "paper_name": "A Paper", "paper_url": "https://doi.org/10.1/x",
            "paper_authors": ["A"], "paper_abstract": "", "paper_code": "#",
        })
        return res

    final_progress, failures = _run_collect(
        monkeypatch, tmp_path, progress, _stub
    )
    assert target_key in final_progress
    assert not any(f["url"] == target_url for f in failures)

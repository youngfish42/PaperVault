"""Guard tests for the DBLP empty-result semantics in `collector/pipeline.py`.

A DBLP page that yields 0 papers is marked with an `empty` flag in the
per-URL progress (instead of a failure entry): the URL is skipped while the
marker is fresh and retried after EMPTY_RESULT_RETRY_TTL. This keeps
genuinely-empty pages from being refetched every run while ensuring
silently-blocked pages are never skipped forever."""

from __future__ import annotations

import json
import time

import collector
from collector.pipeline import EMPTY_RESULT_RETRY_TTL

TARGET_URL = "https://dblp.org/db/journals/aeog/aeog94.html"
TARGET_KEY = f"DBLP::{TARGET_URL}"


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


def _empty_stub(calls):
    def _stub(url, name, res):
        calls.append(url)
        return res
    return _stub


def test_empty_result_marked_empty_not_failure(monkeypatch, tmp_path):
    progress = _all_conf_progress()
    progress.pop(TARGET_KEY)

    final_progress, failures = _run_collect(
        monkeypatch, tmp_path, progress, _empty_stub([])
    )
    entry = final_progress.get(TARGET_KEY)
    assert entry is not None and entry.get("empty") is True
    assert not any(f["url"] == TARGET_URL for f in failures)


def test_non_empty_result_marked_collected(monkeypatch, tmp_path):
    progress = _all_conf_progress()
    progress.pop(TARGET_KEY)

    def _stub(url, name, res):
        res.setdefault(name, []).append({
            "paper_name": "A Paper", "paper_url": "https://doi.org/10.1/x",
            "paper_authors": ["A"], "paper_abstract": "", "paper_code": "#",
        })
        return res

    final_progress, failures = _run_collect(monkeypatch, tmp_path, progress, _stub)
    entry = final_progress.get(TARGET_KEY)
    assert entry is not None and not entry.get("empty")
    assert not any(f["url"] == TARGET_URL for f in failures)


def test_fresh_empty_marker_is_skipped(monkeypatch, tmp_path):
    progress = _all_conf_progress()
    progress[TARGET_KEY] = {
        "name": "IJAEO2021",
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "empty": True,
    }
    calls = []
    _run_collect(monkeypatch, tmp_path, progress, _empty_stub(calls))
    assert calls == []


def test_expired_empty_marker_is_retried_and_refreshed(monkeypatch, tmp_path):
    progress = _all_conf_progress()
    old_ts = time.strftime(
        "%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - EMPTY_RESULT_RETRY_TTL - 3600)
    )
    progress[TARGET_KEY] = {"name": "IJAEO2021", "ts": old_ts, "empty": True}

    calls = []
    final_progress, failures = _run_collect(
        monkeypatch, tmp_path, progress, _empty_stub(calls)
    )
    assert calls == [TARGET_URL]
    entry = final_progress[TARGET_KEY]
    assert entry.get("empty") is True
    assert entry["ts"] != old_ts
    assert not any(f["url"] == TARGET_URL for f in failures)

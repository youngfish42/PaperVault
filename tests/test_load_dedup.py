"""加载期去重的回归测试。

背景
----
``collector`` 会持续向 ``cache/cache.jsonl.gz`` 追加写入，历史版本的
``PaperRepository._load`` 从未去重，因此搜索索引（以及 UI）可能多次
出现同一篇论文。本次改动在加载期按 ``Paper.id``
（即 ``sha1(conf|year|title)[:16]``）丢弃后续重复行，让索引与结果一致。

新增契约
--------
* 同一 ``(conf, year, title)`` 的多行只保留首个，重复行被丢弃。
* 不同论文之间互不影响。
* 启动日志体现丢弃数量，便于运维观察去重率。
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from papervault.services.papers import PaperRepository


# ----------------------------------------------------------------------
# 测试夹具
# ----------------------------------------------------------------------


def _write_cache_with_duplicates(gz_path: Path) -> None:
    """把同一篇论文写三份，模拟未去重的缓存。"""
    gz_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "conf": "ICLR 2026",
        "paper_name": "Time Series Agent for Test",
        "paper_authors": ["Alice Adams"],
        "paper_url": "https://example.org/x",
        "paper_abstract": "A study of time series and agents.",
        "paper_code": None,
    }
    with gzip.open(gz_path, "wt", encoding="utf-8") as fh:
        for _ in range(3):
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


# ----------------------------------------------------------------------
# 用例
# ----------------------------------------------------------------------


def test_load_dedupes_duplicate_cache_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    cache = tmp_path / "cache.jsonl.gz"
    _write_cache_with_duplicates(cache)

    monkeypatch.setenv("PAPERVAULT_OFFLINE", "1")
    repo = PaperRepository(cache_path=cache, refresh_on_load=False)
    repo.ensure_loaded()

    # 3 行原始数据应折叠为单个 Paper 对象。
    assert len(repo.all_papers()) == 1
    # by_conf 索引必须保持一致；否则搜索仍会在同一会议下看到重复论文。
    assert len(repo.confs()["ICLR"]) == 1


def test_load_dedup_preserves_distinct_papers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    cache = tmp_path / "cache.jsonl.gz"
    cache.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {
            "conf": "ICLR 2026",
            "paper_name": "Distinct Paper Alpha",
            "paper_authors": ["A"],
            "paper_url": None,
            "paper_abstract": None,
            "paper_code": None,
        },
        {
            "conf": "ICLR 2026",
            "paper_name": "Distinct Paper Beta",
            "paper_authors": ["B"],
            "paper_url": None,
            "paper_abstract": None,
            "paper_code": None,
        },
    ]
    with gzip.open(cache, "wt", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    monkeypatch.setenv("PAPERVAULT_OFFLINE", "1")
    repo = PaperRepository(cache_path=cache, refresh_on_load=False)
    repo.ensure_loaded()

    assert len(repo.all_papers()) == 2


def test_load_dedup_logs_dropped_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
):
    cache = tmp_path / "cache.jsonl.gz"
    _write_cache_with_duplicates(cache)

    monkeypatch.setenv("PAPERVAULT_OFFLINE", "1")
    repo = PaperRepository(cache_path=cache, refresh_on_load=False)
    with caplog.at_level("INFO", logger="papervault.papers"):
        repo.ensure_loaded()

    # 写入 3 行，丢弃 2 行；只校验关键计数，不绑定日志格式。
    assert any("2 duplicate rows dropped" in rec.message for rec in caplog.records)
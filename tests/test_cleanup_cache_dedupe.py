"""Unit tests for ``scripts/cleanup_cache_dedupe.py`` (PR B).

Covers the cache-cleanup contract required by the audit on
``cache/cache.jsonl.gz``:

* same-pid rows are folded down via ``collector._merge_paper_record`` so
  longer abstracts / longer author lists / non-placeholder code links
  survive;
* ``--dry-run`` produces statistics without ever touching the original
  file or leaving ``.tmp`` / ``.bak`` artefacts;
* the real write path atomically replaces the original file *and*
  leaves a ``.bak`` backup that the operator can roll back to;
* running the cleanup a second time on the just-cleaned file is a
  no-op (idempotency), guaranteeing that re-running collector and then
  cleanup will not silently lose information.

Everything is exercised against a tiny synthetic ``cache.jsonl.gz`` in
``tmp_path`` — no I/O against the real cache.
"""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import cleanup_cache_dedupe as cc  # noqa: E402


def _write_jsonl_gz(path: Path, records: list[dict]) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _read_jsonl_gz(path: Path) -> list[dict]:
    out: list[dict] = []
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


# ---------------------------------------------------------------------------
# 1. Field-level merge produces the union of information
# ---------------------------------------------------------------------------


def test_cleanup_merges_same_pid_field_wise(tmp_path):
    cache = tmp_path / "cache.jsonl.gz"
    records = [
        {
            "paper_name": "Common Corpus",
            "paper_url": "https://openreview.net/pdf?id=AAA",
            "paper_authors": ["A", "B"],
            "paper_abstract": "",
            "paper_code": "#",
            "conf": "ICLR2026",
        },
        # 同 conf + 同 url → 同 pid。带上更长 abstract / 更长 authors / 真实 code。
        {
            "paper_name": "Common Corpus",
            "paper_url": "https://openreview.net/pdf?id=AAA",
            "paper_authors": ["A", "B", "C"],
            "paper_abstract": "A long abstract that should survive.",
            "paper_code": "https://github.com/example/repo",
            "conf": "ICLR2026",
        },
        # 不同 url → 不同 pid，必须保留。
        {
            "paper_name": "Other",
            "paper_url": "https://openreview.net/pdf?id=BBB",
            "paper_authors": ["X"],
            "paper_abstract": "Different paper.",
            "paper_code": "#",
            "conf": "ICLR2026",
        },
    ]
    _write_jsonl_gz(cache, records)

    stats = cc.cleanup(cache, dry_run=False, backup=True)

    rows = _read_jsonl_gz(cache)
    assert stats["total_rows"] == 3
    assert stats["unique_pids"] == 2
    assert stats["rows_saved"] == 1
    assert stats["merge_events"] == 1
    assert stats["abstract_rescued"] == 1
    assert stats["code_upgraded"] == 1
    assert stats["author_upgraded"] == 1
    assert len(rows) == 2

    merged = next(r for r in rows if r["paper_url"].endswith("AAA"))
    assert merged["paper_abstract"] == "A long abstract that should survive."
    assert merged["paper_authors"] == ["A", "B", "C"]
    assert merged["paper_code"] == "https://github.com/example/repo"


# ---------------------------------------------------------------------------
# 2. paper_url fallback to paper_name when url is empty
# ---------------------------------------------------------------------------


def test_cleanup_falls_back_to_paper_name_when_url_missing(tmp_path):
    cache = tmp_path / "cache.jsonl.gz"
    records = [
        # 两条都没有 paper_url，但 paper_name 相同 → 同 pid，必须合并。
        {
            "paper_name": "Mystery Paper",
            "paper_url": "",
            "paper_authors": [],
            "paper_abstract": "",
            "paper_code": "#",
            "conf": "NIPS2024",
        },
        {
            "paper_name": "Mystery Paper",
            "paper_url": "",
            "paper_authors": ["Alice"],
            "paper_abstract": "Actual abstract.",
            "paper_code": "#",
            "conf": "NIPS2024",
        },
        # 同 name 但不同 conf → 不同 pid，必须保留为两行。
        {
            "paper_name": "Mystery Paper",
            "paper_url": "",
            "paper_authors": [],
            "paper_abstract": "",
            "paper_code": "#",
            "conf": "ICLR2026",
        },
    ]
    _write_jsonl_gz(cache, records)

    stats = cc.cleanup(cache, dry_run=False, backup=False)
    rows = _read_jsonl_gz(cache)

    assert stats["unique_pids"] == 2
    assert len(rows) == 2
    merged = next(r for r in rows if r["conf"] == "NIPS2024")
    assert merged["paper_abstract"] == "Actual abstract."
    assert merged["paper_authors"] == ["Alice"]


# ---------------------------------------------------------------------------
# 3. --dry-run does not touch disk
# ---------------------------------------------------------------------------


def test_cleanup_dry_run_does_not_write(tmp_path):
    cache = tmp_path / "cache.jsonl.gz"
    records = [
        {"paper_name": "P", "paper_url": "u1", "paper_authors": [],
         "paper_abstract": "", "paper_code": "#", "conf": "C"},
        {"paper_name": "P", "paper_url": "u1", "paper_authors": [],
         "paper_abstract": "x", "paper_code": "#", "conf": "C"},
    ]
    _write_jsonl_gz(cache, records)
    original_mtime = cache.stat().st_mtime_ns
    original_bytes = cache.read_bytes()

    stats = cc.cleanup(cache, dry_run=True)

    assert stats["dry_run"] is True
    assert stats["unique_pids"] == 1
    assert stats["abstract_rescued"] == 1
    # 原文件字节、mtime、以及 .tmp / .bak 都不应被触碰
    assert cache.read_bytes() == original_bytes
    assert cache.stat().st_mtime_ns == original_mtime
    assert not (tmp_path / "cache.jsonl.gz.tmp").exists()
    assert not (tmp_path / "cache.jsonl.gz.bak").exists()


# ---------------------------------------------------------------------------
# 4. atomic replace + .bak backup
# ---------------------------------------------------------------------------


def test_cleanup_creates_backup_and_replaces_original(tmp_path):
    cache = tmp_path / "cache.jsonl.gz"
    records = [
        {"paper_name": "P", "paper_url": "u1", "paper_authors": [],
         "paper_abstract": "", "paper_code": "#", "conf": "C"},
        {"paper_name": "P", "paper_url": "u1", "paper_authors": [],
         "paper_abstract": "x", "paper_code": "#", "conf": "C"},
    ]
    _write_jsonl_gz(cache, records)
    pre_bytes = cache.read_bytes()

    stats = cc.cleanup(cache, dry_run=False, backup=True)

    bak = tmp_path / "cache.jsonl.gz.bak"
    assert bak.exists()
    assert bak.read_bytes() == pre_bytes  # 备份位等于清洗前的原文件
    assert stats["backup"] == str(bak)
    assert not (tmp_path / "cache.jsonl.gz.tmp").exists()
    rows = _read_jsonl_gz(cache)
    assert len(rows) == 1


def test_cleanup_no_backup_flag_skips_bak(tmp_path):
    cache = tmp_path / "cache.jsonl.gz"
    _write_jsonl_gz(cache, [{"paper_name": "P", "paper_url": "u1",
                              "paper_authors": [], "paper_abstract": "",
                              "paper_code": "#", "conf": "C"}])
    stats = cc.cleanup(cache, dry_run=False, backup=False)
    assert stats["backup"] is None
    assert not (tmp_path / "cache.jsonl.gz.bak").exists()


# ---------------------------------------------------------------------------
# 5. Idempotency: cleaning twice changes nothing
# ---------------------------------------------------------------------------


def test_cleanup_is_idempotent(tmp_path):
    cache = tmp_path / "cache.jsonl.gz"
    records = [
        {"paper_name": "P", "paper_url": f"u{i % 3}", "paper_authors": ["A"],
         "paper_abstract": "abs", "paper_code": "#", "conf": "ICLR2026"}
        for i in range(9)
    ]
    _write_jsonl_gz(cache, records)

    cc.cleanup(cache, dry_run=False, backup=False)
    first = _read_jsonl_gz(cache)
    cc.cleanup(cache, dry_run=False, backup=False)
    second = _read_jsonl_gz(cache)

    assert first == second
    assert len(first) == 3  # u0/u1/u2


# ---------------------------------------------------------------------------
# 6. Missing file -> FileNotFoundError
# ---------------------------------------------------------------------------


def test_cleanup_missing_source_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        cc.cleanup(tmp_path / "does_not_exist.jsonl.gz", dry_run=True)


# ---------------------------------------------------------------------------
# 7. Optional report file
# ---------------------------------------------------------------------------


def test_cleanup_writes_report_when_requested(tmp_path):
    cache = tmp_path / "cache.jsonl.gz"
    _write_jsonl_gz(cache, [{"paper_name": "P", "paper_url": "u1",
                              "paper_authors": [], "paper_abstract": "",
                              "paper_code": "#", "conf": "C"},
                             {"paper_name": "P", "paper_url": "u1",
                              "paper_authors": [], "paper_abstract": "x",
                              "paper_code": "#", "conf": "C"}])
    report = tmp_path / "report.txt"
    cc.cleanup(cache, dry_run=True, report_path=report)
    text = report.read_text(encoding="utf-8")
    assert "unique_pids: 1" in text
    assert "total_rows: 2" in text


# ---------------------------------------------------------------------------
# 8. Parse-error threshold contract (PR review fix #2)
# ---------------------------------------------------------------------------


def test_cleanup_raises_when_parse_errors_exceed_threshold(tmp_path):
    """坏行存在时，默认阈值 0.0 必须抛 ParseErrorThresholdExceeded。

    回归 PR#94 review #2：模块 docstring 声称「解析异常超过阈值」会以
    exit 1 终止，但旧实现只累计 parse_errors 不做任何阈值判断，会让
    坏行被全量重写后静默丢失。
    """
    cache = tmp_path / "cache.jsonl.gz"
    with gzip.open(cache, "wt", encoding="utf-8") as f:
        f.write(json.dumps({"paper_name": "P", "paper_url": "u1",
                            "paper_authors": [], "paper_abstract": "",
                            "paper_code": "#", "conf": "C"}) + "\n")
        f.write("{not valid json\n")
    pre_bytes = cache.read_bytes()

    with pytest.raises(cc.ParseErrorThresholdExceeded):
        cc.cleanup(cache, dry_run=False, backup=False)

    # 原文件未被改写，.tmp / .bak 都不应残留
    assert cache.read_bytes() == pre_bytes
    assert not (tmp_path / "cache.jsonl.gz.tmp").exists()
    assert not (tmp_path / "cache.jsonl.gz.bak").exists()


def test_cleanup_allows_parse_errors_when_ratio_relaxed(tmp_path):
    """显式提高阈值时坏行应被允许丢弃，正常完成清洗。"""
    cache = tmp_path / "cache.jsonl.gz"
    with gzip.open(cache, "wt", encoding="utf-8") as f:
        f.write(json.dumps({"paper_name": "P", "paper_url": "u1",
                            "paper_authors": [], "paper_abstract": "",
                            "paper_code": "#", "conf": "C"}) + "\n")
        f.write("{not valid json\n")

    stats = cc.cleanup(
        cache, dry_run=False, backup=False, max_parse_error_ratio=1.0
    )
    assert stats["parse_errors"] == 1
    assert stats["total_rows"] == 1
    assert stats["unique_pids"] == 1


def test_main_returns_exit_code_1_on_parse_errors(tmp_path):
    """CLI 路径同样要遵守契约：解析错误超限以 exit code 1 退出。"""
    cache = tmp_path / "cache.jsonl.gz"
    with gzip.open(cache, "wt", encoding="utf-8") as f:
        f.write("not json at all\n")

    rc = cc.main(["--source", str(cache)])
    assert rc == 1


# ---------------------------------------------------------------------------
# 9. Missing conf field never triggers cross-record merge (PR review fix #3)
# ---------------------------------------------------------------------------


def test_cleanup_does_not_merge_records_missing_conf(tmp_path):
    """两条 conf 缺失但 paper_url 相同的记录绝不能被字段级合并。

    回归 PR#94 review #3：旧实现把缺失的 conf 退化为空串后参与 sha1，
    导致跨会议同 URL 在 conf 字段缺失时被 `_merge_paper_record` 无声
    地字段级合并，产生不可逆的数据污染。
    """
    cache = tmp_path / "cache.jsonl.gz"
    records = [
        # 两条都缺 conf；如果按空串当 conf 参与 hash，会被错合
        {"paper_name": "X", "paper_url": "https://example.com/p1",
         "paper_authors": ["A"], "paper_abstract": "abs from source 1",
         "paper_code": "#"},
        {"paper_name": "Y different title", "paper_url": "https://example.com/p1",
         "paper_authors": ["B"], "paper_abstract": "abs from source 2",
         "paper_code": "https://github.com/x/y"},
        # conf 不是 str（异常类型）也不应参与合并
        {"paper_name": "Z", "paper_url": "https://example.com/p1",
         "paper_authors": ["C"], "paper_abstract": "abs from source 3",
         "paper_code": "#", "conf": 12345},
    ]
    _write_jsonl_gz(cache, records)

    stats = cc.cleanup(cache, dry_run=False, backup=False)
    rows = _read_jsonl_gz(cache)

    assert stats["skipped_no_conf"] == 3
    assert stats["merge_events"] == 0
    assert len(rows) == 3
    # 字段不能交叉污染
    abstracts = sorted(r["paper_abstract"] for r in rows)
    assert abstracts == ["abs from source 1", "abs from source 2", "abs from source 3"]
    authors_sets = sorted([r["paper_authors"] for r in rows], key=str)
    assert authors_sets == [["A"], ["B"], ["C"]]


def test_cleanup_still_merges_when_conf_present(tmp_path):
    """有 conf 时仍要正常合并——确认 #3 修复没有破坏正常路径。"""
    cache = tmp_path / "cache.jsonl.gz"
    records = [
        {"paper_name": "P", "paper_url": "u1", "paper_authors": [],
         "paper_abstract": "", "paper_code": "#", "conf": "ICLR2026"},
        {"paper_name": "P", "paper_url": "u1", "paper_authors": ["A"],
         "paper_abstract": "long abs", "paper_code": "#", "conf": "ICLR2026"},
    ]
    _write_jsonl_gz(cache, records)

    stats = cc.cleanup(cache, dry_run=False, backup=False)
    rows = _read_jsonl_gz(cache)
    assert stats["skipped_no_conf"] == 0
    assert stats["merge_events"] == 1
    assert len(rows) == 1
    assert rows[0]["paper_abstract"] == "long abs"

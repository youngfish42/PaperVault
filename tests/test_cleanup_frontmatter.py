"""Unit tests for ``scripts/cleanup_cache_frontmatter.py``.

Covers the front-matter predicate contract and the file-level cleanup:

* predicate: front-matter title patterns matched case-insensitively;
  venue-index URL alone does not suffice without the title pattern;
  a matched title with an abstract or a real code link survives;
* ``--dry-run`` never touches the file (bytes + mtime) and leaves no
  ``.tmp`` / ``.bak`` artefacts;
* ``--write`` removes exactly the matched rows, atomically, with backup.

Style follows ``tests/test_cleanup_cache_dedupe.py``: tiny synthetic
``cache.jsonl.gz`` under ``tmp_path``, no I/O against the real cache.
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

import cleanup_cache_frontmatter as cf  # noqa: E402


def _rec(title, url="https://example.org/p/1", abstract="", code="#", conf="ICSE2026"):
    return {
        "paper_name": title,
        "paper_url": url,
        "paper_authors": [],
        "paper_abstract": abstract,
        "paper_code": code,
        "conf": conf,
    }


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


# ---------- predicate: positives ----------------------------------------------

@pytest.mark.parametrize(
    "title",
    [
        "Proceedings of the IEEE/ACM 48th International Conference on Software Engineering",
        "Proceedings of The TerraBytes ICML Workshop",
        "proceedings of the thirty-fourth international joint conference",
        "Front Matter",
        "Frontmatter",
        "Table of Contents",
        "Preface",
        "Preface to the special issue",
        "Findings of the Association for Computational Linguistics: ACL 2022",
    ],
)
def test_frontmatter_titles_matched(title):
    # 无摘要 + 无代码链接 → 佐证成立
    assert cf.is_frontmatter_record(_rec(title)) is True


def test_venue_index_url_suffices_even_with_abstract():
    """标题命中 + venue-index URL → 即便有摘要也删（卷首页 stub）。"""
    rec = _rec(
        "Front Matter",
        url="https://aclanthology.org/2021.acl-long.0/",
        abstract="Auto-generated volume front matter text that is long enough.",
    )
    assert cf.is_frontmatter_record(rec) is True


# ---------- predicate: negatives ------------------------------------------------

@pytest.mark.parametrize(
    "title",
    [
        "Attention Is All You Need",
        "Deep Residual Learning for Image Recognition",
        "On the Proceedings of Thought: A Study",  # 不是开头 → 不命中
        "Workshop Proceedings Revisited",
    ],
)
def test_real_paper_titles_not_matched(title):
    assert cf.is_frontmatter_record(_rec(title)) is False


def test_matched_title_with_abstract_survives():
    """标题命中但记录有摘要 → 不是裸 front-matter，保留。"""
    rec = _rec("Proceedings of Some Conference", abstract="A" * 100)
    assert cf.is_frontmatter_record(rec) is False


def test_matched_title_with_code_link_survives():
    rec = _rec("Proceedings of Some Conference", code="https://github.com/x/y")
    assert cf.is_frontmatter_record(rec) is False


def test_venue_index_url_alone_not_matched():
    """venue-index URL 但标题是真实论文 → 保留（双重条件）。"""
    rec = _rec("Attention Is All You Need", url="https://aclanthology.org/2021.acl-long.0/")
    assert cf.is_frontmatter_record(rec) is False


def test_non_dict_record_not_matched():
    assert cf.is_frontmatter_record("not a dict") is False


# ---------- cleanup(): dry-run / write ------------------------------------------

def _sample_records():
    return [
        _rec("Attention Is All You Need", url="https://arxiv.org/abs/1706.03762"),
        _rec("Proceedings of the IEEE/ACM 48th ICSE", url="https://doi.org/10.1145/3786581"),
        _rec("Front Matter", url="https://ijcai.org/proceedings/2025/"),
        _rec("Preface", url="https://example.org/preface", abstract="has one " * 20),
        _rec("Table of Contents", url="https://example.org/toc/1"),
    ]


def test_cleanup_dry_run_does_not_write(tmp_path):
    cache = tmp_path / "cache.jsonl.gz"
    _write_jsonl_gz(cache, _sample_records())
    original_bytes = cache.read_bytes()
    original_mtime = cache.stat().st_mtime_ns

    stats = cf.cleanup(cache, dry_run=True)

    # ICSE proceedings + IJCAI front-matter（venue-index）+ TOC；Preface 有摘要保留
    assert stats["matched_frontmatter"] == 3
    assert stats["kept"] == 2
    assert stats["dry_run"] is True
    assert cache.read_bytes() == original_bytes
    assert cache.stat().st_mtime_ns == original_mtime
    assert not (tmp_path / "cache.jsonl.gz.tmp").exists()
    assert not (tmp_path / "cache.jsonl.gz.bak").exists()
    # 报告里带完整待删清单（--review 用）
    assert len(stats["matched"]) == 3
    assert stats["matched"][0][0] == "ICSE2026"


def test_cleanup_write_removes_exactly_matched(tmp_path):
    cache = tmp_path / "cache.jsonl.gz"
    _write_jsonl_gz(cache, _sample_records())
    pre_bytes = cache.read_bytes()

    stats = cf.cleanup(cache, dry_run=False, backup=True)

    rows = _read_jsonl_gz(cache)
    assert len(rows) == 2
    assert stats["matched_frontmatter"] == 3
    titles = {r["paper_name"] for r in rows}
    assert "Attention Is All You Need" in titles
    assert "Preface" in titles  # 有摘要 → 保留
    assert "Proceedings of the IEEE/ACM 48th ICSE" not in titles
    assert "Front Matter" not in titles
    assert "Table of Contents" not in titles
    # 原子替换 + 备份
    bak = tmp_path / "cache.jsonl.gz.bak"
    assert bak.exists() and bak.read_bytes() == pre_bytes
    assert not (tmp_path / "cache.jsonl.gz.tmp").exists()


def test_cleanup_is_idempotent(tmp_path):
    cache = tmp_path / "cache.jsonl.gz"
    _write_jsonl_gz(cache, _sample_records())
    cf.cleanup(cache, dry_run=False, backup=False)
    first = _read_jsonl_gz(cache)
    stats2 = cf.cleanup(cache, dry_run=False, backup=False)
    assert _read_jsonl_gz(cache) == first
    assert stats2["matched_frontmatter"] == 0


def test_cleanup_missing_source_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        cf.cleanup(tmp_path / "nope.jsonl.gz", dry_run=True)

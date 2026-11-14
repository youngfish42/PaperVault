"""Startup materialisation and reuse tests for the SQLite paper index."""

from __future__ import annotations

import gzip
import json
import sqlite3
from pathlib import Path

from papervault.services.papers import PaperRepository, SearchCriteria


def _write_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")


def _paper(title: str, *, year: int = 2026) -> dict:
    return {
        "conf": f"ICLR {year}",
        "paper_name": title,
        "paper_authors": ["Alice Adams"],
        "paper_url": f"https://example.org/{title.lower().replace(' ', '-')}",
        "paper_abstract": f"An abstract for {title}.",
        "paper_code": None,
    }


def test_first_load_materialises_versioned_sqlite_index(tmp_path: Path):
    cache = tmp_path / "cache.jsonl.gz"
    _write_rows(cache, [_paper("First Paper"), _paper("Second Paper")])

    repo = PaperRepository(cache_path=cache, refresh_on_load=False)
    repo.ensure_loaded()

    assert repo.database_path == tmp_path / "papers.sqlite3"
    assert repo.database_path.is_file()
    assert repo.paper_count() == 2
    with sqlite3.connect(repo.database_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert {"papers", "paper_fts", "metadata"}.issubset(names)


def test_unchanged_cache_reuses_existing_database(tmp_path: Path, monkeypatch):
    cache = tmp_path / "cache.jsonl.gz"
    _write_rows(cache, [_paper("Stable Paper")])
    first = PaperRepository(cache_path=cache, refresh_on_load=False)
    first.ensure_loaded()
    original_mtime = first.database_path.stat().st_mtime_ns

    second = PaperRepository(cache_path=cache, refresh_on_load=False)

    def fail_build(*_args, **_kwargs):
        raise AssertionError("unchanged cache should not rebuild SQLite")

    monkeypatch.setattr(second, "_build_database", fail_build)
    second.ensure_loaded()

    assert second.paper_count() == 1
    assert second.database_path.stat().st_mtime_ns == original_mtime


def test_changed_cache_rebuilds_database(tmp_path: Path):
    cache = tmp_path / "cache.jsonl.gz"
    _write_rows(cache, [_paper("Original Paper")])
    first = PaperRepository(cache_path=cache, refresh_on_load=False)
    first.ensure_loaded()
    original_inode = first.database_path.stat().st_ino

    _write_rows(cache, [_paper("Original Paper"), _paper("New Paper", year=2025)])
    second = PaperRepository(cache_path=cache, refresh_on_load=False)
    second.ensure_loaded()

    assert second.paper_count() == 2
    assert second.database_path.stat().st_ino != original_inode
    assert {paper.title for paper in second.all_papers()} == {
        "Original Paper",
        "New Paper",
    }


def test_missing_cache_builds_valid_empty_database(tmp_path: Path):
    cache = tmp_path / "missing" / "cache.jsonl.gz"
    repo = PaperRepository(cache_path=cache, refresh_on_load=False)

    repo.ensure_loaded()

    assert repo.paper_count() == 0
    assert repo.conference_count() == 0


def _criteria(**overrides) -> SearchCriteria:
    values = {
        "query": None,
        "field": "any",
        "confs": [],
        "since": None,
        "until": None,
        "author": None,
        "sort": "-year",
        "page": 1,
        "size": 50,
    }
    values.update(overrides)
    return SearchCriteria(**values)


def test_search_preserves_non_prefix_and_short_substring_matches(
    repository_with_sample,
):
    hits, total = repository_with_sample.search(
        _criteria(query="tion", field="title")
    )

    assert total == 4
    assert {paper.title for paper in hits} == {
        "Attention Is All You Need Revisited",
        "Diffusion Models for Text",
        "Legacy Title with-Hyphen Inside",
        "Retrieval Augmented Generation at Scale",
    }

    short_hits, short_total = repository_with_sample.search(
        _criteria(query="is", field="title")
    )
    assert short_total == 2
    assert {paper.title for paper in short_hits} == {
        "Attention Is All You Need Revisited",
        "Vision Transformers Revisited",
    }


def test_search_trigram_tokens_may_include_punctuation(tmp_path: Path):
    cache = tmp_path / "cache.jsonl.gz"
    _write_rows(
        cache,
        [
            _paper("Modern C++11 Programming"),
            _paper("Practical R&D Methods"),
            _paper("Node.js Systems"),
        ],
    )
    repo = PaperRepository(cache_path=cache, refresh_on_load=False)
    repo.ensure_loaded()

    for query, expected_title in (
        ("c++", "Modern C++11 Programming"),
        ("c++11", "Modern C++11 Programming"),
        ("r&d", "Practical R&D Methods"),
        ("node.js", "Node.js Systems"),
    ):
        hits, total = repo.search(_criteria(query=query, field="title"))
        assert total == 1
        assert [paper.title for paper in hits] == [expected_title]


def test_search_respects_title_author_and_any_field_semantics(
    repository_with_sample,
):
    title_hits, title_total = repository_with_sample.search(
        _criteria(query="attention", field="title")
    )
    assert title_total == 1
    assert [paper.title for paper in title_hits] == [
        "Attention Is All You Need Revisited"
    ]

    author_hits, author_total = repository_with_sample.search(
        _criteria(query="alice", field="author")
    )
    assert author_total == 2
    assert {paper.title for paper in author_hits} == {
        "Attention Is All You Need Revisited",
        "Retrieval Augmented Generation at Scale",
    }

    any_hits, any_total = repository_with_sample.search(
        _criteria(query="alice", field="any")
    )
    assert any_total == author_total
    assert {paper.title for paper in any_hits} == {
        paper.title for paper in author_hits
    }


def test_search_combines_author_filter_with_query_and_short_alternatives(
    repository_with_sample,
):
    hits, total = repository_with_sample.search(
        _criteria(query="revisited", field="title", author="ivy")
    )
    assert total == 1
    assert [paper.title for paper in hits] == ["Vision Transformers Revisited"]

    mixed_hits, mixed_total = repository_with_sample.search(
        _criteria(author="iv alice")
    )
    assert mixed_total == 3
    assert {paper.title for paper in mixed_hits} == {
        "Attention Is All You Need Revisited",
        "Retrieval Augmented Generation at Scale",
        "Vision Transformers Revisited",
    }


def test_search_orders_by_relevance_then_requested_sort(repository_with_sample):
    hits, total = repository_with_sample.search(
        _criteria(query="revisited", field="title", sort="-year")
    )

    assert total == 2
    assert [paper.title for paper in hits] == [
        "Vision Transformers Revisited",
        "Attention Is All You Need Revisited",
    ]


def test_search_pagination_total_and_hyphen_normalisation(repository_with_sample):
    page, total = repository_with_sample.search(_criteria(page=2, size=2))
    assert total == 7
    assert len(page) == 2

    hits, hyphen_total = repository_with_sample.search(
        _criteria(query="with hyphen", field="title")
    )
    assert hyphen_total == 1
    assert [paper.title for paper in hits] == ["Legacy Title with-Hyphen Inside"]

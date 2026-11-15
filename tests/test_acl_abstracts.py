from __future__ import annotations

import gzip
import json

import pytest

from scripts import fetch_abstracts as generic
from scripts import fetch_acl_abstracts as backfill
from scripts.acl_abstracts import (
    canonical_acl_url,
    clean_acl_abstract,
    is_acl_paper_url,
    iter_bibtex_entries,
    load_bibtex_metadata,
    parse_bibtex_entry,
)


def _paper(conf: str, paper_id: str, title: str) -> dict:
    return {
        "conf": conf,
        "paper_name": title,
        "paper_url": f"https://aclanthology.org/{paper_id}/",
        "paper_authors": [],
        "paper_abstract": "",
        "paper_code": "#",
    }


@pytest.mark.parametrize("paper_id", ["2025.coling-main.1", "C04-1001", "E03-1001", "A00-1001", "D16-1001", "P05-1001"])
def test_acl_url_accepts_all_series_and_id_styles(paper_id):
    assert is_acl_paper_url(f"https://aclanthology.org/{paper_id}/")
    assert canonical_acl_url(f"http://www.aclanthology.org/{paper_id}") == f"https://aclanthology.org/{paper_id}/"


def test_bibtex_parser_handles_nested_braces_and_abstract():
    raw = """@inproceedings{example-2005-paper,
    title = "A {Nested} Title",
    author = "Freitas, Andr{\\'e}",
    url = "https://aclanthology.org/P05-1001/",
    doi = "10.3115/example",
    abstract = "An abstract with $x_{i}$ and \\url{https://example.org}."
}
"""
    entry = next(iter_bibtex_entries(raw.splitlines(keepends=True)))
    fields = parse_bibtex_entry(entry)
    assert fields["title"] == "A {Nested} Title"
    assert fields["url"] == "https://aclanthology.org/P05-1001/"
    assert fields["abstract"].startswith("An abstract with $x_{i}$")


def test_load_bibtex_metadata_filters_targets(tmp_path):
    raw = """@inproceedings{one,
    title = "One",
    url = "https://aclanthology.org/C04-1001/",
    abstract = "A sufficiently long abstract for the first paper."
}
@inproceedings{two,
    title = "Two",
    url = "https://aclanthology.org/P05-1001/",
    abstract = "Another sufficiently long abstract for the second paper."
}
"""
    path = tmp_path / "metadata.bib.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write(raw)
    metadata = load_bibtex_metadata(path, {"https://aclanthology.org/C04-1001/"})
    assert set(metadata) == {"https://aclanthology.org/C04-1001/"}


def test_clean_acl_abstract_preserves_text_and_links():
    raw = r"Andr{\'e} and {\v{Z}}elezn{\'y} use $\lambda$-calculus and \url{https://example.org} with 20\% coverage."
    cleaned = clean_acl_abstract(raw)
    assert "André" in cleaned
    assert "Železný" in cleaned
    assert "λ-calculus" in cleaned
    assert "https://example.org" in cleaned
    assert "20% coverage" in cleaned


def test_collect_targets_covers_all_series_and_failed_gate():
    records = [
        _paper("ACL2005", "P05-1001", "ACL"),
        _paper("COLING2004", "C04-1001", "COLING"),
        _paper("EACL2003", "E03-1001", "EACL"),
        _paper("EMNLP2000", "D00-1001", "EMNLP"),
        _paper("NAACL2000", "A00-1001", "NAACL"),
    ]
    progress = {"https://aclanthology.org/P05-1001/": {"status": "failed"}}
    assert len(backfill.collect_targets(records, progress, None, None, False)) == 4
    assert len(backfill.collect_targets(records, progress, None, None, True)) == 5
    assert len(backfill.collect_targets(records, progress, {"COLING2004"}, None, True)) == 1
    assert backfill.collect_targets(records, progress, set(), None, True) == []


def test_s2_pass_uses_anthology_ids_and_preserves_title_gate(monkeypatch):
    records = [
        _paper("COLING2004", "C04-1001", "First Paper"),
        _paper("EACL2003", "E03-1001", "Second Paper"),
    ]
    calls = []

    def fake_batch(ids):
        calls.append(ids)
        return [
            {"title": "First Paper", "abstract": "A complete abstract for the first paper."},
            {"title": "Second Paper", "abstract": "A complete abstract for the second paper."},
        ], ""

    monkeypatch.setattr(backfill, "_s2_batch_fetch", fake_batch)
    monkeypatch.setattr(backfill, "_flush", lambda *_args: None)
    monkeypatch.setattr(generic, "S2_MIN_ABSTRACT_CHARS", 40)
    stats = {"changed": False, "code": 0, "dirty": False}
    progress = {}
    backfill.run_s2_pass(records, records, progress, stats, 0.0, None, False)
    assert calls == [["ACL:C04-1001", "ACL:E03-1001"]]
    assert all(record["paper_abstract"] for record in records)
    assert stats["semanticscholar_batch"] == 2


def test_s2_batch_helper_keeps_qualified_acl_ids(monkeypatch):
    captured = {}

    class Response:
        status_code = 200

        def json(self):
            return []

    monkeypatch.setattr(generic, "_s2_request", lambda *args, **kwargs: captured.update(kwargs) or (Response(), ""))
    generic._s2_batch_fetch(["10.3115/example", "ACL:C04-1001"])
    assert captured["json"]["ids"] == ["DOI:10.3115/example", "ACL:C04-1001"]


def test_bibtex_pass_fills_official_abstract_and_code_link():
    record = _paper("ACL2021", "2021.findings-acl.1", "Official Paper")
    abstract = "An official abstract with a code repository https://github.com/example/repository and enough text."
    metadata = {record["paper_url"]: {"abstract": abstract}}
    stats = {"changed": False, "code": 0, "dirty": False}
    backfill.run_bibtex_pass([record], [record], {}, metadata, stats)
    assert record["paper_abstract"] == abstract
    assert record["paper_code"] == "https://github.com/example/repository"
    assert stats["acl_bibtex"] == 1
    assert stats["code"] == 1


def test_openalex_pass_uses_title_and_year_gates(monkeypatch):
    record = _paper("EACL2023", "2023.eacl-main.1", "OpenAlex Paper")
    monkeypatch.setattr(generic, "S2_MIN_ABSTRACT_CHARS", 10)
    monkeypatch.setattr(
        backfill,
        "_openalex_title_match_fetch",
        lambda _title: ([{
            "title": "OpenAlex Paper",
            "publication_year": 2023,
            "abstract_inverted_index": {"A": [0], "complete": [1], "abstract": [2], "for": [3], "validation": [4]},
        }], ""),
    )
    monkeypatch.setattr(backfill, "_flush", lambda *_args: None)
    stats = {"changed": False, "code": 0, "dirty": False}
    backfill.run_openalex_pass([record], [record], {}, stats, 0.0, None, False)
    assert record["paper_abstract"] == "A complete abstract for validation"
    assert stats["openalex_title"] == 1


def test_no_targets_skips_bibtex_and_api(monkeypatch, tmp_path):
    cache = tmp_path / "cache.jsonl.gz"
    record = _paper("ACL2021", "2021.findings-acl.1", "Complete Paper")
    record["paper_abstract"] = "already complete"
    with gzip.open(cache, "wt", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")
    monkeypatch.setattr(backfill, "CACHE_FILE", cache)
    monkeypatch.setattr(backfill, "PROGRESS_FILE", tmp_path / "progress.jsonl.gz")
    monkeypatch.setattr(backfill, "ensure_bibtex", lambda **_kwargs: pytest.fail("must not download"))
    monkeypatch.setattr(backfill, "_s2_batch_fetch", lambda _ids: pytest.fail("must not call S2"))
    monkeypatch.setattr(backfill, "_openalex_title_match_fetch", lambda _title: pytest.fail("must not call OpenAlex"))
    args = backfill.parse_args(["--offline", "--no-sync"])
    assert backfill.run(args) == 0


def test_openalex_transient_failures_trigger_circuit_breaker(monkeypatch):
    records = [_paper("ACL2005", f"P05-{1000 + index}", f"Paper {index}") for index in range(6)]
    calls = []
    monkeypatch.setattr(
        backfill,
        "_openalex_title_match_fetch",
        lambda _title: calls.append(_title) or (None, "rate_limited"),
    )
    flushes = []
    monkeypatch.setattr(backfill, "_flush", lambda *args: flushes.append(args))
    stats = {"changed": False, "code": 0, "dirty": False}
    progress = {}
    backfill.run_openalex_pass(records, records, progress, stats, 0.0, None, False)
    assert len(calls) == 5
    assert len(flushes) == 1
    assert all(record["paper_abstract"] == "" for record in records)
    assert all(item["attempts"] == 0 for item in progress.values())


def test_s2_title_fallback_uses_existing_gates(monkeypatch):
    record = _paper("COLING2010", "C10-1001", "Fallback Paper")
    monkeypatch.setattr(generic, "S2_MIN_ABSTRACT_CHARS", 40)
    monkeypatch.setattr(
        backfill,
        "_s2_title_match_fetch",
        lambda _title: ({"title": "Fallback Paper", "abstract": "A sufficiently detailed fallback abstract for this paper."}, ""),
    )
    monkeypatch.setattr(backfill, "_flush", lambda *_args: None)
    stats = {"changed": False, "code": 0, "dirty": False}
    backfill.run_s2_title_pass([record], [record], {}, stats, 0.0, None)
    assert record["paper_abstract"].startswith("A sufficiently detailed fallback abstract")
    assert stats["semanticscholar_title"] == 1


def test_openalex_rejected_key_falls_back_to_anonymous(monkeypatch):
    calls = []

    class Response:
        def __init__(self, status):
            self.status_code = status

        def json(self):
            return {"results": [{"title": "Paper"}]}

    monkeypatch.setenv("OPENALEX_API_KEY", "invalid-test-key")

    def fake_request(*_args, **kwargs):
        calls.append(kwargs["headers"].copy())
        return Response(429 if len(calls) == 1 else 200), ""

    monkeypatch.setattr(generic, "_s2_request", fake_request)
    candidates, error = generic._openalex_title_search("Paper", "title")
    assert error == ""
    assert len(candidates) == 1
    assert "Authorization" in calls[0]
    assert "Authorization" not in calls[1]

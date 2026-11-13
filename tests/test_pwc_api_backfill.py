from __future__ import annotations

import gzip
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import requests

from scripts import fetch_pwc_api_backfill as pwc

LONG_ABSTRACT = (
    "We present a sufficiently detailed synthetic abstract for the Papers with Code "
    "backfill tests, with more than enough characters to pass the minimum length gate."
)


def _paper(
    title: str = "A Test Paper",
    url: str = "https://example.org/paper",
    conf: str = "TEST2024",
) -> dict:
    return {
        "paper_name": title,
        "paper_url": url,
        "paper_abstract": "",
        "paper_code": "#",
        "conf": conf,
    }


def _args(**overrides) -> SimpleNamespace:
    values = {
        "conf": None,
        "year": None,
        "chunk_size": 500,
        "max_papers": None,
        "soft_timeout": None,
        "max_failed_attempts": 3,
        "min_interval": 0,
        "max_retries": 1,
        "retry_failed": False,
        "force": False,
        "dry_run": False,
        "offline": True,
        "no_sync": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _write_cache(path: Path, papers: list[dict]) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for paper in papers:
            handle.write(json.dumps(paper) + "\n")


def _result(entry: dict, **overrides) -> dict:
    paper = {
        "id": "123",
        "title": entry["paper"]["paper_name"],
        "abstract": LONG_ABSTRACT,
        "code_repository_count": 1,
    }
    if entry.get("arxiv_id"):
        paper["arxiv_id"] = entry["arxiv_id"]
        matched_by = "arxiv_id"
    else:
        matched_by = "title"
    paper.update(overrides)
    return {"key": entry["key"], "matched_by": matched_by, "paper": paper}


class _FakeClient:
    def __init__(self, papers: list[dict], repositories=None) -> None:
        self.entries = [pwc.make_lookup_entry(paper) for paper in papers]
        self.by_key = {entry["key"]: _result(entry) for entry in self.entries}
        self._repositories = repositories if repositories is not None else [
            {"url": "https://github.com/example/project", "num_stars": 10}
        ]
        self.repository_calls = []
        self.lookup_calls = []

    def lookup(self, references):
        self.lookup_calls.append(references)
        return {"references": [self.by_key[ref["key"]] for ref in references]}

    def repositories(self, paper_id):
        self.repository_calls.append(paper_id)
        return self._repositories


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://arxiv.org/abs/1706.03762", "1706.03762"),
        ("https://arxiv.org/pdf/1706.03762v3.pdf", "1706.03762"),
        ("https://www.arxiv.org/abs/cs.DS/0610046v2", "cs.DS/0610046"),
        ("https://arxiv.org/abs/1706.03762?context=cs", "1706.03762"),
        ("https://example.org/arxiv.org/abs/1706.03762", None),
        ("https://arxiv.org/list/cs/1706", None),
    ],
)
def test_extract_arxiv_id(url, expected):
    assert pwc.extract_arxiv_id(url) == expected


def test_lookup_match_requires_exact_identity():
    arxiv_paper = _paper(url="https://arxiv.org/abs/1706.03762v2")
    arxiv_entry = pwc.make_lookup_entry(arxiv_paper)
    assert pwc.validate_match(arxiv_entry, _result(arxiv_entry))["id"] == "123"
    wrong_arxiv = _result(arxiv_entry, arxiv_id="1706.00000")
    assert pwc.validate_match(arxiv_entry, wrong_arxiv) is None

    titled = _paper(title="Attention: Is All You Need?")
    entry = pwc.make_lookup_entry(titled)
    result = _result(entry, title="attention is all you need")
    assert pwc.validate_match(entry, result) is not None
    assert pwc.validate_match(entry, _result(entry, title="A Different Paper")) is None
    assert pwc.validate_match(entry, dict(_result(entry), key="wrong")) is None


def test_select_repository_prefers_official_then_stars_and_canonicalizes():
    repositories = [
        {"url": "https://github.com/popular/unofficial", "num_stars": 10000},
        {"url": "https://github.com/small/official", "num_stars": 2, "is_official": True},
        {"url": "https://github.com/large/official.git", "num_stars": 20, "is_official": True},
        {"url": "https://gitlab.com/ignored/project", "num_stars": 99999, "is_official": True},
        {"url": "not a url", "num_stars": 99999},
    ]
    assert pwc.select_repository(repositories) == "https://github.com/large/official"
    assert pwc.select_repository(repositories[:1]) == "https://github.com/popular/unofficial"
    assert pwc.select_repository([]) == ""


def _response(status: int, payload, headers=None) -> MagicMock:
    response = MagicMock()
    response.status_code = status
    response.headers = headers or {}
    response.json.return_value = payload
    return response


def test_client_honours_retry_after_and_preserves_rate_limit_reason():
    session = requests.Session()
    session.request = MagicMock(
        side_effect=[
            _response(429, {"detail": "slow down"}, {"Retry-After": "7"}),
            _response(429, {"detail": "slow down"}, {"Retry-After": "8"}),
        ]
    )
    sleeps = []
    client = pwc.PwcClient(
        session,
        min_interval=0,
        max_retries=2,
        sleep_func=sleeps.append,
        jitter_func=lambda _low, _high: 0,
    )
    with pytest.raises(pwc.PwcTransientError) as caught:
        client.lookup([{"key": "x", "titles": ["X"]}])
    assert caught.value.reason == "rate_limited"
    assert sleeps == [7.0]
    assert session.request.call_count == 2


def test_client_success_and_batch_bounds():
    session = requests.Session()
    session.request = MagicMock(return_value=_response(200, {"references": []}))
    client = pwc.PwcClient(session, min_interval=0, jitter_func=lambda _a, _b: 0)
    assert client.lookup([{"key": "x", "titles": ["X"]}]) == {"references": []}
    with pytest.raises(ValueError):
        client.lookup([])
    with pytest.raises(ValueError):
        client.lookup([{"key": str(i)} for i in range(501)])


def test_progress_ledger_latest_record_wins(tmp_path):
    path = tmp_path / "progress.jsonl.gz"
    ledger = pwc.ProgressLedger(path)
    ledger.update("a", {"terminal": False, "attempts": 1})
    ledger.save()
    ledger.update("a", {"terminal": True, "attempts": 1})
    ledger.update("b", {"terminal": True, "attempts": 0})
    ledger.save()

    loaded = pwc.ProgressLedger(path)
    assert loaded.records["a"]["terminal"] is True
    assert loaded.records["b"]["attempts"] == 0
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        header = json.loads(handle.readline())
    assert header["schema"] == pwc.PROGRESS_SCHEMA


def test_collect_targets_priority_terminal_and_retry_semantics():
    missing_abstract = _paper("Missing abstract", "https://example.org/1", "AAA2024")
    code_only = _paper("Code only", "https://example.org/2", "BBB2023")
    code_only["paper_abstract"] = "already present"
    complete = _paper("Complete", "https://example.org/3")
    complete["paper_abstract"] = "present"
    complete["paper_code"] = "https://github.com/a/b"
    progress = {
        pwc.paper_key(code_only): {"terminal": True, "attempts": 0},
    }
    targets = pwc.collect_targets([code_only, missing_abstract, complete], progress)
    assert targets == [missing_abstract]
    assert pwc.collect_targets([code_only], progress, force=True) == [code_only]
    assert pwc.collect_targets([code_only], progress, retry_failed=True) == [code_only]
    assert pwc.collect_targets([missing_abstract], progress, retry_failed=True) == []
    assert pwc.collect_targets([missing_abstract], progress, year="2023") == []
    assert pwc.collect_targets([missing_abstract], progress, conf="aaa*") == [missing_abstract]


def test_collect_targets_skips_venue_indexes_and_front_matter():
    venue_index = _paper("ACL 2024", "https://aclanthology.org/events/acl-2024/")
    front_matter = _paper("Proceedings of Test Conference", "https://example.org/proceedings")
    whole_book = _paper(
        "Medical Image Computing and Computer Assisted Intervention, Proceedings, Part II",
        "https://doi.org/10.1007/978-3-032-38062-3",
    )
    chapter = _paper(
        "A Real Springer Chapter",
        "https://doi.org/10.1007/978-3-032-38062-3_1",
    )
    real_paper = _paper("A Real Paper", "https://example.org/paper")
    assert pwc.collect_targets([venue_index, front_matter, whole_book, chapter, real_paper], {}) == [
        chapter,
        real_paper,
    ]


def _install_run_files(tmp_path, monkeypatch, papers):
    cache = tmp_path / "cache.jsonl.gz"
    progress = tmp_path / "pwc_progress.jsonl.gz"
    backup = tmp_path / "cache.bak"
    _write_cache(cache, papers)
    monkeypatch.setattr(pwc, "CACHE_FILE", cache)
    monkeypatch.setattr(pwc, "PROGRESS_FILE", progress)
    monkeypatch.setattr(pwc, "BACKUP_FILE", backup)
    return cache, progress, backup


def test_run_fills_only_empty_fields_and_records_terminal_miss(tmp_path, monkeypatch):
    papers = [
        _paper("Fill me", "https://example.org/fill"),
        _paper("Keep me", "https://example.org/keep"),
        _paper("No match", "https://example.org/miss"),
    ]
    papers[1]["paper_abstract"] = "keep abstract"
    papers[1]["paper_code"] = "https://github.com/keep/existing"
    cache, progress, _backup = _install_run_files(tmp_path, monkeypatch, papers)
    client = _FakeClient(papers)
    del client.by_key[pwc.paper_key(papers[2])]
    client.lookup = lambda references: {
        "references": [client.by_key[ref["key"]] for ref in references if ref["key"] in client.by_key]
        + [
            {"key": ref["key"], "matched_by": None, "paper": None}
            for ref in references
            if ref["key"] not in client.by_key
        ]
    }
    monkeypatch.setattr(pwc, "PwcClient", lambda **_kwargs: client)

    stats = pwc.run(_args())

    assert stats == {"processed": 2, "matched": 1, "abstracts": 1, "code": 1, "failed": 0}
    saved = pwc.load_cache(cache)
    assert saved[0]["paper_abstract"] == LONG_ABSTRACT
    assert saved[0]["paper_code"] == "https://github.com/example/project"
    assert saved[1]["paper_abstract"] == "keep abstract"
    assert saved[1]["paper_code"] == "https://github.com/keep/existing"
    assert client.repository_calls == ["123"]
    ledger = pwc.ProgressLedger(progress)
    assert ledger.records[pwc.paper_key(papers[2])]["reason"] == "not_found"
    assert ledger.records[pwc.paper_key(papers[2])]["terminal"] is True


def test_dry_run_calls_api_without_writing_or_syncing(tmp_path, monkeypatch):
    paper = _paper()
    cache, progress, backup = _install_run_files(tmp_path, monkeypatch, [paper])
    original = cache.read_bytes()
    client = _FakeClient([paper])
    monkeypatch.setattr(pwc, "PwcClient", lambda **_kwargs: client)
    sync = MagicMock()
    ensure = MagicMock()
    monkeypatch.setattr(pwc, "sync_cache_artifacts", sync)
    monkeypatch.setattr(pwc, "ensure_cache_local", ensure)

    stats = pwc.run(_args(dry_run=True, offline=False, no_sync=False))

    assert stats["abstracts"] == 1
    assert stats["code"] == 1
    assert cache.read_bytes() == original
    assert not progress.exists()
    assert not backup.exists()
    sync.assert_not_called()
    ensure.assert_not_called()


def test_batch_failure_does_not_advance_attempts_or_files(tmp_path, monkeypatch):
    paper = _paper()
    cache, progress, _backup = _install_run_files(tmp_path, monkeypatch, [paper])
    original = cache.read_bytes()
    client = _FakeClient([paper])
    client.lookup = MagicMock(side_effect=pwc.PwcTransientError("rate_limited"))
    monkeypatch.setattr(pwc, "PwcClient", lambda **_kwargs: client)

    stats = pwc.run(_args())

    assert stats["processed"] == 0
    assert cache.read_bytes() == original
    assert not progress.exists()


def test_repository_failure_is_resumable_and_counts_one_attempt(tmp_path, monkeypatch):
    paper = _paper()
    _cache, progress, _backup = _install_run_files(tmp_path, monkeypatch, [paper])
    client = _FakeClient([paper])
    client.repositories = MagicMock(side_effect=pwc.PwcTransientError("timeout"))
    monkeypatch.setattr(pwc, "PwcClient", lambda **_kwargs: client)

    stats = pwc.run(_args())

    assert stats["abstracts"] == 1
    assert stats["failed"] == 1
    record = pwc.ProgressLedger(progress).records[pwc.paper_key(paper)]
    assert record["terminal"] is False
    assert record["reason"] == "timeout"
    assert record["attempts"] == 1


def test_zero_repositories_skips_detail_request(tmp_path, monkeypatch):
    paper = _paper()
    _cache, progress, _backup = _install_run_files(tmp_path, monkeypatch, [paper])
    client = _FakeClient([paper])
    key = pwc.paper_key(paper)
    client.by_key[key] = _result(client.entries[0], code_repository_count=0)
    client.repositories = MagicMock()
    monkeypatch.setattr(pwc, "PwcClient", lambda **_kwargs: client)

    stats = pwc.run(_args())

    assert stats["abstracts"] == 1
    assert stats["code"] == 0
    client.repositories.assert_not_called()
    record = pwc.ProgressLedger(progress).records[key]
    assert record["terminal"] is True
    assert record["reason"] == "no_repository"


def test_soft_timeout_saves_completed_part_of_batch(tmp_path, monkeypatch):
    papers = [_paper("First", "https://example.org/1"), _paper("Second", "https://example.org/2")]
    cache, _progress, _backup = _install_run_files(tmp_path, monkeypatch, papers)
    client = _FakeClient(papers)
    monkeypatch.setattr(pwc, "PwcClient", lambda **_kwargs: client)
    times = iter([0, 0, 0, 2, 2])
    monkeypatch.setattr(pwc.time, "monotonic", lambda: next(times))

    stats = pwc.run(_args(chunk_size=2, soft_timeout=1))

    assert stats["processed"] == 1
    saved = pwc.load_cache(cache)
    assert saved[0]["paper_abstract"] == LONG_ABSTRACT
    assert saved[1]["paper_abstract"] == ""

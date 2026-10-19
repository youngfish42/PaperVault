"""Tests for the Semantic Scholar backfill modes in ``scripts/fetch_abstracts``.

Covers ``--s2-batch`` (DOI bulk lookup via ``/paper/batch``) and
``--s2-title-match`` (long-tail title lookup via ``/paper/search/match``):

* batch response mapping, including ``null`` entries (S2 has no record
  for that DOI) and request-level failures,
* the stricter title gate (normalised equality / containment /
  SequenceMatcher ratio >= 0.85) and the ±1 year guard,
* the excluded-host set for the title mode (specialised-script hosts and
  fetcher-registry hosts must be skipped),
* progress-record writing (``semanticscholar_batch`` /
  ``semanticscholar_title`` sources, REASON_ENUM-aligned reasons),
* ``--dry-run``: API calls happen but nothing is persisted.

HTTP is stubbed at the ``fa._s2_request`` / ``fa._s2_batch_fetch`` /
``fa._s2_title_match_fetch`` boundary; every test runs fully offline.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests

from scripts import fetch_abstracts as fa


LONG_ABSTRACT = (
    "We present a sufficiently long synthetic abstract for the Semantic "
    "Scholar backfill tests, comfortably clearing the sixty-character "
    "minimum gate used by the pipeline."
)


def _paper(url: str, title: str = "Some Paper Title", conf: str = "BMVC2022") -> dict:
    return {
        "paper_url": url,
        "paper_name": title,
        "paper_abstract": "",
        "conf": conf,
    }


def _failed_progress(*urls: str) -> dict:
    return {u: {"status": "failed", "attempts": 1, "ts": "2026-01-01T00:00:00"} for u in urls}


def _s2_response(payload, status: int = 200, headers: dict = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = payload
    resp.headers = headers or {}
    return resp


# ---------- Target collection ------------------------------------------------

def test_s2_batch_targets_select_failed_empty_doi_only():
    doi_url = "https://doi.org/10.1007/ABC-DEF_1"
    ok_url = "https://doi.org/10.1007/ALREADY-DONE_2"
    fresh_url = "https://doi.org/10.1007/NEVER-TRIED_3"
    nondoi_url = "https://dl.acm.org/doi/10.1145/3447548.3467165"
    papers = [
        _paper(doi_url),
        _paper(ok_url),
        _paper(fresh_url),
        _paper(nondoi_url),
    ]
    papers[1]["paper_abstract"] = "already has one"
    progress = _failed_progress(doi_url, ok_url, nondoi_url)
    # fresh_url has no progress record → not a *failed* record → excluded.

    targets = fa._collect_s2_batch_targets(papers, progress)

    assert [p["paper_url"] for p, _ in targets] == [doi_url]
    # DOI 回映射一律小写归一。
    assert targets[0][1] == "10.1007/abc-def_1"


def test_s2_title_targets_exclusion_set():
    included = [
        "https://dl.acm.org/doi/10.1145/3447548.3467165",
        "https://vldb.org/pvldb/vol13/p123-doe.pdf",
        "https://web.archive.org/web/2020/https://example.org/paper",
        "https://www.auai.org/uai2020/proceedings/123.html",
        "https://ceur-ws.org/Vol-2600/paper1.pdf",
        "https://ieeexplore.ieee.org/document/1234567",
        "https://www.bmvc2020-conference.com/programme/papers/123.html",
    ]
    excluded = [
        "https://doi.org/10.1007/978-3-540-40899-4_1",       # DOI → --s2-batch
        "https://openaccess.thecvf.com/content/x.html",       # specialised script
        "https://openreview.net/forum?id=abc",                # specialised script
        "https://aclanthology.org/2023.acl-long.1/",          # fetcher registry
        "https://proceedings.mlr.press/v202/foo23a.html",
        "https://www.jmlr.org/papers/v25/23-1044.html",       # www stripped → jmlr.org
        "https://www.isca-archive.org/interspeech_2023/x.html",
        "https://ojs.aaai.org/index.php/AAAI/article/view/1",
        "https://ijcai.org/proceedings/2023/1",
        "https://link.springer.com/chapter/10.1007/x_1",
        "https://www.usenix.org/conference/nsdi24/presentation/y",
        "https://www.ndss-symposium.org/ndss2000/z/",
    ]
    papers = [_paper(u) for u in included + excluded]
    progress = _failed_progress(*(included + excluded))

    targets = fa._collect_s2_title_targets(papers, progress)

    assert [p["paper_url"] for p in targets] == included


def test_s2_title_targets_skip_empty_title_and_non_failed():
    url = "https://dl.acm.org/doi/10.1145/x"
    untitled = _paper(url, title="")
    progress = _failed_progress(url)
    assert fa._collect_s2_title_targets([untitled], progress) == []
    # success record in progress → excluded
    ok = _paper(url)
    progress_ok = {url: {"status": "success"}}
    assert fa._collect_s2_title_targets([ok], progress_ok) == []


# ---------- Gates --------------------------------------------------------------

@pytest.mark.parametrize(
    "api_title, local_title, expected",
    [
        ("Attention Is All You Need", "Attention Is All You Need", True),
        ("Attention Is All You Need!", "attention is all you need", True),
        ("A Study of Cats: Revised Edition", "A Study of Cats", True),  # containment
        ("Deep Residual Learning for Image Recognition",
         "Deep Residual Learning for Image Classification", False),     # ~0.83 < 0.85
        ("Totally Different Paper", "Some Paper Title", False),
        ("", "Some Paper Title", False),
    ],
)
def test_s2_title_gate(api_title, local_title, expected):
    assert fa._s2_title_gate(api_title, local_title) is expected


@pytest.mark.parametrize(
    "conf, s2_year, expected",
    [
        ("BMVC2022", 2022, True),
        ("BMVC2022", 2023, True),    # diff 1 allowed
        ("BMVC2022", 2021, True),
        ("BMVC2022", 2020, False),   # diff 2 rejected
        ("BMVC2022", None, True),    # missing S2 year → title-only
        ("BMVC2022", 0, True),
        ("WORKSHOP", 1999, True),    # no year in conf → title-only
    ],
)
def test_s2_year_gate(conf, s2_year, expected):
    assert fa._s2_year_gate(conf, s2_year) is expected


# ---------- Batch endpoint mapping --------------------------------------------

def test_s2_batch_fetch_maps_aligned_results_with_nulls(monkeypatch):
    payload = [
        {"title": "Paper A", "abstract": LONG_ABSTRACT, "externalIds": {"DOI": "10.1/a"}},
        None,  # S2 has no record for the second DOI
        {"title": "Paper C", "abstract": None, "externalIds": {"DOI": "10.1/c"}},
    ]
    monkeypatch.setattr(fa, "_s2_request", MagicMock(return_value=(_s2_response(payload), "")))

    results, err = fa._s2_batch_fetch(["10.1/a", "10.1/missing", "10.1/c"])

    assert err == ""
    assert results == payload
    # The POST carried DOI:-prefixed, order-preserving ids.
    call = fa._s2_request.call_args
    assert call.kwargs["json"] == {"ids": ["DOI:10.1/a", "DOI:10.1/missing", "DOI:10.1/c"]}


def test_s2_batch_fetch_non_200_maps_to_reason(monkeypatch):
    monkeypatch.setattr(fa, "_s2_request", MagicMock(return_value=(_s2_response({}, status=429), "")))
    results, err = fa._s2_batch_fetch(["10.1/a"])
    assert results is None
    assert err == "rate_limited"
    assert err in fa.REASON_ENUM

    monkeypatch.setattr(fa, "_s2_request", MagicMock(return_value=(None, "timeout")))
    results, err = fa._s2_batch_fetch(["10.1/a"])
    assert results is None
    assert err == "timeout"


# ---------- Batch pass end-to-end ---------------------------------------------

def _stub_no_disk(monkeypatch):
    writes = {"save": 0, "cache": 0}
    monkeypatch.setattr(fa, "save_progress", lambda _p: writes.__setitem__("save", writes["save"] + 1))
    monkeypatch.setattr(fa, "_write_cache_atomic", lambda _ps: writes.__setitem__("cache", writes["cache"] + 1))
    return writes


def test_s2_batch_pass_records_outcomes(monkeypatch):
    p_hit = _paper("https://doi.org/10.1/hit", title="Paper Hit")
    p_null = _paper("https://doi.org/10.1/null", title="Paper Null")
    p_mismatch = _paper("https://doi.org/10.1/mismatch", title="Paper Local Title")
    p_short = _paper("https://doi.org/10.1/short", title="Paper Short")
    papers = [p_hit, p_null, p_mismatch, p_short]
    progress = _failed_progress(*(p["paper_url"] for p in papers))

    payload = [
        {"title": "Paper Hit", "abstract": LONG_ABSTRACT},
        None,
        {"title": "Completely Unrelated Work", "abstract": LONG_ABSTRACT},
        {"title": "Paper Short", "abstract": "too short"},
    ]
    monkeypatch.setattr(fa, "_s2_batch_fetch", MagicMock(return_value=(payload, "")))
    writes = _stub_no_disk(monkeypatch)

    success, failed, timed_out = fa._run_s2_batch_pass(
        papers, progress,
        chunk_size=500, max_papers=None, soft_timeout=None,
        start_time=__import__("time").time(), dry_run=False,
    )

    assert (success, failed, timed_out) == (1, 3, False)
    assert p_hit["paper_abstract"] == LONG_ABSTRACT
    rec = progress[p_hit["paper_url"]]
    assert rec["status"] == "success"
    assert rec["source"] == "semanticscholar_batch"
    assert rec["chars"] == len(LONG_ABSTRACT)
    assert progress[p_null["paper_url"]]["reason"] == "doi_not_found"
    assert progress[p_mismatch["paper_url"]]["reason"] == "title_mismatch"
    assert progress[p_short["paper_url"]]["reason"] == "empty_abstract"
    for p in (p_null, p_mismatch, p_short):
        assert progress[p["paper_url"]]["status"] == "failed"
        assert progress[p["paper_url"]]["last_source"] == "semanticscholar_batch"
        assert progress[p["paper_url"]]["reason"] in fa.REASON_ENUM
    # 落盘：chunk 末尾一次 save_progress + 一次 cache 重写（有命中）。
    assert writes["save"] >= 1
    assert writes["cache"] >= 1


def test_s2_batch_pass_request_failure_marks_whole_subbatch(monkeypatch):
    papers = [_paper(f"https://doi.org/10.1/x{i}") for i in range(3)]
    progress = _failed_progress(*(p["paper_url"] for p in papers))
    monkeypatch.setattr(fa, "_s2_batch_fetch", MagicMock(return_value=(None, "rate_limited")))
    _stub_no_disk(monkeypatch)

    success, failed, _ = fa._run_s2_batch_pass(
        papers, progress,
        chunk_size=500, max_papers=None, soft_timeout=None,
        start_time=__import__("time").time(), dry_run=False,
    )

    assert (success, failed) == (0, 3)
    for p in papers:
        assert progress[p["paper_url"]]["reason"] == "rate_limited"


def test_s2_batch_pass_dry_run_writes_nothing(monkeypatch):
    p = _paper("https://doi.org/10.1/hit", title="Paper Hit")
    papers = [p]
    progress = _failed_progress(p["paper_url"])
    progress_snapshot = {u: dict(m) for u, m in progress.items()}
    payload = [{"title": "Paper Hit", "abstract": LONG_ABSTRACT}]
    monkeypatch.setattr(fa, "_s2_batch_fetch", MagicMock(return_value=(payload, "")))
    writes = _stub_no_disk(monkeypatch)

    success, failed, _ = fa._run_s2_batch_pass(
        papers, progress,
        chunk_size=500, max_papers=None, soft_timeout=None,
        start_time=__import__("time").time(), dry_run=True,
    )

    assert (success, failed) == (1, 0)  # 命中照算
    assert p["paper_abstract"] == ""     # 但不落 cache
    assert progress == progress_snapshot  # 也不落 progress
    assert writes == {"save": 0, "cache": 0}


# ---------- Title-match endpoint & pass ---------------------------------------

def test_s2_title_match_fetch_404_means_no_candidate(monkeypatch):
    monkeypatch.setattr(fa, "_s2_request", MagicMock(return_value=(_s2_response({}, status=404), "")))
    data, err = fa._s2_title_match_fetch("nonexistent title")
    assert data is None
    assert err == "empty_abstract"
    assert err in fa.REASON_ENUM


def test_s2_title_match_fetch_unwraps_data_wrapper(monkeypatch):
    """真实响应形状是 ``{"data": [candidate, ...]}``（verified 2026-10-19
    against the live endpoint）——必须解包取第一个候选；空列表等价于
    "无候选可回填"。"""
    candidate = {"title": "Regularized spectral learning", "abstract": LONG_ABSTRACT, "year": 2005}
    monkeypatch.setattr(
        fa, "_s2_request",
        MagicMock(return_value=(_s2_response({"data": [candidate]}), "")),
    )
    data, err = fa._s2_title_match_fetch("Regularized spectral learning")
    assert err == ""
    assert data == candidate

    monkeypatch.setattr(
        fa, "_s2_request",
        MagicMock(return_value=(_s2_response({"data": []}), "")),
    )
    data, err = fa._s2_title_match_fetch("nothing matches this")
    assert data is None
    assert err == "empty_abstract"


def test_s2_title_pass_success_and_gates(monkeypatch):
    p_ok = _paper("https://dl.acm.org/doi/10.1145/ok", title="A Fine Paper", conf="BMVC2022")
    p_year = _paper("https://dl.acm.org/doi/10.1145/year", title="Year Guarded", conf="BMVC2022")
    p_short = _paper("https://vldb.org/pvldb/vol13/p1.pdf", title="Short Abstract Paper", conf="VLDB2021")
    papers = [p_ok, p_year, p_short]
    progress = _failed_progress(*(p["paper_url"] for p in papers))

    def _fake_fetch(title):
        if title == "A Fine Paper":
            return {"title": "A Fine Paper", "abstract": LONG_ABSTRACT, "year": 2023}, ""
        if title == "Year Guarded":
            return {"title": "Year Guarded", "abstract": LONG_ABSTRACT, "year": 2019}, ""
        return {"title": "Short Abstract Paper", "abstract": "tiny", "year": 2021}, ""

    monkeypatch.setattr(fa, "_s2_title_match_fetch", MagicMock(side_effect=_fake_fetch))
    _stub_no_disk(monkeypatch)

    success, failed, _ = fa._run_s2_title_pass(
        papers, progress,
        chunk_size=500, max_papers=None, soft_timeout=None,
        start_time=__import__("time").time(), dry_run=False,
    )

    assert (success, failed) == (1, 2)
    assert progress[p_ok["paper_url"]]["status"] == "success"
    assert progress[p_ok["paper_url"]]["source"] == "semanticscholar_title"
    # 年份差 3 → title_mismatch；摘要过短 → empty_abstract。
    assert progress[p_year["paper_url"]]["reason"] == "title_mismatch"
    assert progress[p_short["paper_url"]]["reason"] == "empty_abstract"


def test_s2_title_pass_dry_run_writes_nothing(monkeypatch):
    p = _paper("https://dl.acm.org/doi/10.1145/ok", title="A Fine Paper")
    progress = _failed_progress(p["paper_url"])
    progress_snapshot = {u: dict(m) for u, m in progress.items()}
    monkeypatch.setattr(
        fa, "_s2_title_match_fetch",
        MagicMock(return_value=({"title": "A Fine Paper", "abstract": LONG_ABSTRACT, "year": 2022}, "")),
    )
    writes = _stub_no_disk(monkeypatch)

    success, failed, _ = fa._run_s2_title_pass(
        [p], progress,
        chunk_size=500, max_papers=None, soft_timeout=None,
        start_time=__import__("time").time(), dry_run=True,
    )

    assert (success, failed) == (1, 0)
    assert p["paper_abstract"] == ""
    assert progress == progress_snapshot
    assert writes == {"save": 0, "cache": 0}


# ---------- _s2_request: rate limiting + backoff --------------------------------

def test_s2_request_retries_429_honouring_retry_after(monkeypatch):
    session = MagicMock()
    session.get.side_effect = [
        _s2_response({}, status=429, headers={"Retry-After": "7"}),
        _s2_response({"title": "ok"}, status=200),
    ]
    monkeypatch.setattr(fa, "_get_session", lambda: session)
    sleeps = []
    monkeypatch.setattr(fa.time, "sleep", lambda s: sleeps.append(s))

    resp, err = fa._s2_request("get", "https://api.semanticscholar.org/x", min_interval=0)

    assert err == ""
    assert resp.status_code == 200
    assert session.get.call_count == 2
    assert 7.0 in sleeps  # Retry-After respected


def test_s2_request_network_exhaustion_returns_reason(monkeypatch):
    session = MagicMock()
    session.get.side_effect = requests.exceptions.ConnectionError("boom")
    monkeypatch.setattr(fa, "_get_session", lambda: session)
    monkeypatch.setattr(fa.time, "sleep", lambda _s: None)

    resp, err = fa._s2_request("get", "https://api.semanticscholar.org/x", max_retries=2, min_interval=0)

    assert resp is None
    assert err == "network"
    assert err in fa.REASON_ENUM
    assert session.get.call_count == 2

from __future__ import annotations

import gzip
import json

import pytest

import collector
import collector.sources.thecvf as cvf
from scripts import fetch_cvf_abstracts as backfill
from tests.test_collector_source_registry import (
    _install_stub_confs,
    _neuter_pkg_side_effects,
)


def _paper(title: str, url: str, abstract: str = "", authors=None, code: str = "#") -> dict:
    return {
        "paper_name": title,
        "paper_url": url,
        "paper_authors": authors or [],
        "paper_abstract": abstract,
        "paper_code": code,
    }


def test_parse_listing_uses_canonical_url_and_author_row():
    html = """
    <dl>
      <dt class="ptitle"><a href="/content/CVPR2025/html/A_Test_Paper_CVPR_2025_paper.html">Test Paper</a></dt>
      <dd><a href="#">Alice</a>, <a href="#">Bob</a></dd>
      <dt class="ptitle"><a href="/CVPR2025">Not a paper</a></dt>
    </dl>
    """
    papers = cvf.parse_cvf_listing(html, "https://openaccess.thecvf.com/CVPR2025?day=all")
    assert papers == [_paper(
        "Test Paper",
        "https://openaccess.thecvf.com/content/CVPR2025/html/A_Test_Paper_CVPR_2025_paper.html",
        authors=["Alice", "Bob"],
    )]
    assert cvf.is_cvf_paper_url(papers[0]["paper_url"])
    assert not cvf.is_cvf_paper_url("https://openaccess.thecvf.com/CVPR2025")


def test_collect_metadata_expands_real_date_pages(monkeypatch):
    base = "https://openaccess.thecvf.com/CVPR2018"
    day1 = "https://openaccess.thecvf.com/CVPR2018.py?day=2018-06-19"
    day2 = "https://openaccess.thecvf.com/CVPR2018.py?day=2018-06-20"
    landing = f"""
      <a href="/CVPR2018.py?day=2018-06-19">Day 1</a>
      <a href="/CVPR2018.py?day=2018-06-20">Day 2</a>
      <a href="/CVPR2019.py?day=2019-06-19">Other event</a>
      <a href="/CVPR2018.py?day=all">Invalid all</a>
    """
    pages = {
        base: landing,
        day1: '<dt class="ptitle"><a href="/content_cvpr_2018/html/A_One_CVPR_2018_paper.html">One</a></dt>',
        day2: '<dt class="ptitle"><a href="/content_cvpr_2018/html/B_Two_CVPR_2018_paper.html">Two</a></dt>',
    }
    calls = []
    monkeypatch.setattr(cvf, "_get_html", lambda url: calls.append(url) or pages[url])
    papers = cvf.collect_cvf_metadata(base)
    assert calls == [base, day1, day2]
    assert [paper["paper_name"] for paper in papers] == ["One", "Two"]


def test_collect_metadata_rejects_empty_listing(monkeypatch):
    monkeypatch.setattr(cvf, "_get_html", lambda _url: "<html>No papers</html>")
    with pytest.raises(ValueError, match="No CVF paper entries"):
        cvf.collect_cvf_metadata("https://openaccess.thecvf.com/CVPR2018")


def test_empty_cvf_result_does_not_write_success_progress(monkeypatch, tmp_path):
    conf = {"name": "CVPR2018", "url": "https://openaccess.thecvf.com/CVPR2018"}
    _install_stub_confs(monkeypatch, {"thecvf_conf.json": [conf]})
    progress_sink = []
    _neuter_pkg_side_effects(monkeypatch, tmp_path, progress_sink)

    def _fail(_url, _name, _res):
        raise ValueError("No CVF paper entries")

    monkeypatch.setattr(collector, "search_from_thecvf", _fail)
    collector.collect(cache_file=None, force=True)
    key = "thecvf::" + conf["url"]
    assert all(key not in snapshot for snapshot in progress_sink)
    failures = json.loads((tmp_path / "failures.json").read_text(encoding="utf-8"))
    assert failures[0]["source"] == "openaccess.thecvf"


def test_abstract_primary_and_meta_fallback_use_one_response():
    primary = '<div id="abstract">A multi-line\nabstract with enough detail.</div>'
    assert cvf.parse_cvf_abstract(primary) == "A multi-line abstract with enough detail."
    fallback = '<meta name="description" content="A fallback abstract with more than forty characters.">'
    assert cvf.parse_cvf_abstract(fallback).startswith("A fallback abstract")
    assert cvf.parse_cvf_abstract('<meta name="description" content="short">') == ""

    class Response:
        text = fallback
        encoding = None

        def raise_for_status(self):
            return None

    class Session:
        def __init__(self):
            self.calls = []

        def get(self, url, headers=None, timeout=None):
            self.calls.append((url, timeout))
            return Response()

    session = Session()
    result = cvf.search_abs_from_thecvf("https://openaccess.thecvf.com/paper", session=session)
    assert result.startswith("A fallback abstract")
    assert len(session.calls) == 1


def test_merge_cvf_records_dedupes_url_variants_and_preserves_best_fields():
    official_url = "https://openaccess.thecvf.com/content/CVPR2025/html/A_Test_Paper_CVPR_2025_paper.html"
    double_slash = official_url.replace(".com/", ".com//")
    doi = "https://doi.org/10.1109/test"
    records = [
        _paper("Test Paper", double_slash, "short", ["Alice"]),
        _paper("Test Paper", doi, "A much longer cached abstract", ["Alice", "Bob"], "https://github.com/a/b"),
    ]
    official = [_paper("Test Paper", official_url, authors=["Alice", "Bob"])]
    merged = cvf.merge_cvf_records(records, official)
    assert len(merged) == 1
    assert merged[0]["paper_url"] == doi
    assert merged[0]["paper_abstract"] == "A much longer cached abstract"
    assert merged[0]["paper_authors"] == ["Alice", "Bob"]
    assert merged[0]["paper_code"] == "https://github.com/a/b"


@pytest.mark.parametrize(
    ("cached", "official"),
    [
        ("Mop Moiré Patterns Using MopNet", "Mop Moire Patterns Using MopNet"),
        ("Task-Decoupled Bézier Surface Constraint", "Task-Decoupled Bezier Surface Constraint"),
        ("$\\pi$-AVAS: Neural Synthesis", "p-AVAS: Neural Synthesis"),
        ("lambda-Net: Reconstruct Images", "l-Net: Reconstruct Images"),
        ("Lidar Waveforms are Worth 40×128×33 Words", "Lidar Waveforms are Worth 40x128x33 Words"),
        ("$\\text{CO}_{2}$-Net: Global $\\text{CO}_{{2}}$ Reconstruction", "CO2-Net: Global CO2 Reconstruction"),
    ],
)
def test_normalize_cvf_title_handles_publisher_formatting(cached, official):
    assert cvf.normalize_cvf_title(cached) == cvf.normalize_cvf_title(official)


def test_paper_original_variant_merges_by_unique_title():
    doi = "https://doi.org/10.1109/CVPR52733.2024.01228"
    original = "https://openaccess.thecvf.com/content/CVPR2024/html/Zhang_DePT_Decoupled_Prompt_Tuning_CVPR_2024_paperOriginal.html"
    regular = original.replace("paperOriginal.html", "paper.html")
    records = [
        _paper("DePT: Decoupled Prompt Tuning", original, "original abstract"),
        _paper("DePT: Decoupled Prompt Tuning", doi, "longer DOI abstract"),
    ]
    merged = cvf.merge_cvf_records(records, [_paper("DePT: Decoupled Prompt Tuning", regular)])
    assert cvf.is_cvf_paper_url(original)
    assert len(merged) == 1
    assert merged[0]["paper_url"] == doi
    assert merged[0]["paper_abstract"] == "longer DOI abstract"


def test_merge_cvf_records_does_not_guess_ambiguous_titles():
    doi = "https://doi.org/10.1/same"
    records = [_paper("Same Title", doi)]
    official = [
        _paper("Same Title", "https://openaccess.thecvf.com/content/CVPR2025/html/A_Same_Title_CVPR_2025_paper.html"),
        _paper("Same Title", "https://openaccess.thecvf.com/content/CVPR2025/html/B_Same_Title_CVPR_2025_paper.html"),
    ]
    merged = cvf.merge_cvf_records(records, official)
    assert len(merged) == 3
    assert merged[0]["paper_url"] == doi


def test_official_sync_selective_tasks_and_idempotency():
    records = [
        dict(_paper("Has Abstract", "https://doi.org/10.1/full", "cached"), conf="CVPR2018"),
        dict(_paper("Needs Abstract", "https://doi.org/10.1/missing"), conf="CVPR2018"),
    ]
    official = [
        _paper("Has Abstract", "https://openaccess.thecvf.com/content_cvpr_2018/html/A_Has_Abstract_CVPR_2018_paper.html"),
        _paper("Needs Abstract", "https://openaccess.thecvf.com/content_cvpr_2018/html/B_Needs_Abstract_CVPR_2018_paper.html"),
        _paper("New Paper", "https://openaccess.thecvf.com/content_cvpr_2018/html/C_New_Paper_CVPR_2018_paper.html"),
    ]
    entries = [{"name": "CVPR2018", "url": "memory"}]
    official_by_conf, stats = backfill.sync_official_records(records, entries, lambda _url: official)
    assert stats["added"] == 1
    assert all(record["conf"] == "CVPR2018" for record in records)
    tasks = backfill.build_task(
        records,
        conf_filter={"CVPR2018"},
        year_filter=None,
        retry_failed=False,
        progress={},
        official_by_conf=official_by_conf,
    )
    assert [url for _idx, url in tasks] == [official[1]["paper_url"], official[2]["paper_url"]]

    _official, second = backfill.sync_official_records(records, entries, lambda _url: official)
    assert second["added"] == 0
    assert second["duplicates_removed"] == 0
    assert not second["changed"]


def test_sync_official_dry_run_sends_no_requests_and_writes_nothing(monkeypatch, tmp_path):
    cache = tmp_path / "cache.jsonl.gz"
    with gzip.open(cache, "wt", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(_paper("P", "https://example/p"), conf="CVPR2018")) + "\n")
    original = cache.read_bytes()
    conf = tmp_path / "thecvf_conf.json"
    conf.write_text(json.dumps([{"name": "CVPR2018", "url": "memory"}]), encoding="utf-8")
    monkeypatch.setattr(backfill, "CACHE_FILE", cache)
    monkeypatch.setattr(backfill, "CONF_FILE", conf)
    monkeypatch.setattr(backfill, "PROGRESS_FILE", tmp_path / "progress.json")
    monkeypatch.setattr(
        backfill,
        "collect_cvf_metadata",
        lambda _url: pytest.fail("dry-run must not fetch metadata"),
    )
    args = backfill.parse_args(["--sync-official", "--dry-run", "--offline", "--no-sync"])
    assert backfill.run(args) == 0
    assert cache.read_bytes() == original
    assert not (tmp_path / "progress.json").exists()


def test_empty_conf_is_rejected_before_cache_access():
    args = backfill.parse_args(["--conf", "--offline", "--no-sync"])
    assert backfill.run(args) == 2


def test_sync_failure_does_not_block_other_conferences_or_abstracts(monkeypatch, tmp_path):
    cache = tmp_path / "cache.jsonl.gz"
    with gzip.open(cache, "wt", encoding="utf-8"):
        pass
    conf = tmp_path / "thecvf_conf.json"
    conf.write_text(json.dumps([
        {"name": "BAD2026", "url": "bad"},
        {"name": "GOOD2026", "url": "good"},
    ]), encoding="utf-8")
    official_url = "https://openaccess.thecvf.com/content/CVPR2026/html/A_Good_Paper_CVPR_2026_paper.html"

    def metadata(url):
        if url == "bad":
            raise RuntimeError("read timeout")
        return [_paper("Good Paper", official_url)]

    monkeypatch.setattr(backfill, "CACHE_FILE", cache)
    monkeypatch.setattr(backfill, "BACKUP_FILE", tmp_path / "backup.gz")
    monkeypatch.setattr(backfill, "PROGRESS_FILE", tmp_path / "progress.json")
    monkeypatch.setattr(backfill, "CONF_FILE", conf)
    monkeypatch.setattr(backfill, "collect_cvf_metadata", metadata)
    monkeypatch.setattr(backfill, "fetch_cvf_abstract", lambda _url: "A complete abstract that is long enough for validation.")
    args = backfill.parse_args(["--sync-official", "--offline", "--no-sync"])

    assert backfill.run(args) == 1
    rows = backfill.load_cache(cache)
    assert len(rows) == 1
    assert rows[0]["conf"] == "GOOD2026"
    assert rows[0]["paper_name"] == "Good Paper"
    assert rows[0]["paper_abstract"].startswith("A complete abstract")
    assert (tmp_path / "progress.json").exists()

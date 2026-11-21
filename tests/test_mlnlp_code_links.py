import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.sync_mlnlp_code_links import (  # noqa: E402
    BLOCKED_ICLR_REPOS,
    GENERIC_RESOURCE_REPOS,
    Edition,
    _require_sync_baseline,
    _source_commit,
    apply_additions,
    classify_code_url,
    export_records,
    import_records,
    load_manifest,
    normalize_repo_url,
    normalize_title,
    paper_identity_keys,
    parse_markdown,
    repair_records,
    run_import,
    run_repair,
)


def _paper(conf="ICLR2024", title="A Paper", url="https://openreview.net/pdf?id=abc123", code="#"):
    return {
        "conf": conf,
        "paper_name": title,
        "paper_url": url,
        "paper_authors": ["Author"],
        "paper_abstract": "Abstract",
        "paper_code": code,
    }


def _edition(tmp_path, content, venue="ICLR", year=2024):
    directory = tmp_path / venue / str(year)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{venue}{year}.md"
    path.write_text(content, encoding="utf-8")
    return Edition(venue, year, path)


def test_parse_real_markdown_layout_variants(tmp_path):
    edition = _edition(
        tmp_path,
        """#### Chinese heading

+ 论文链接：https://aclanthology.org/2024.acl-long.1.pdf
+ 代码链接：https://github.com/org/one https://github.com/org/one-extra

### English heading
- Paper Link: https://openreview.net/attachment?id=xyz&name=pdf
- Code Link: https://github.com/org/two

[Linked title](https://ojs.aaai.org/index.php/AAAI/article/view/123)
* 论文链接：https://ojs.aaai.org/index.php/AAAI/article/view/123/456)
* 资源链接：https://github.com/org/three

Plain NAACL title

- 论文链接：https://aclanthology.org/2021.naacl-main.2.pdf
- 代码链接：https://github.com/org/four

| Title | 论文链接 | 开源代码 |
|-------|---------|---------|
| Table title | [链接](https://ijcai.org/proceedings/2024/1) | [repo](https://github.com/org/five) |
""",
        venue="AAAI",
        year=2021,
    )
    entries = parse_markdown(edition.path)
    assert [entry.title for entry in entries] == [
        "Chinese heading",
        "English heading",
        "Linked title",
        "Plain NAACL title",
        "Table title",
    ]
    assert entries[0].code_urls == [
        "https://github.com/org/one",
        "https://github.com/org/one-extra",
    ]


def test_manifest_reads_exact_supported_table(tmp_path):
    years = {
        "ACL": range(2019, 2026),
        "EMNLP": range(2019, 2026),
        "NAACL": [2019, 2021, 2022, 2024, 2025],
        "COLING": [2018, 2020, 2022, 2024, 2025],
        "AAAI": range(2019, 2026),
        "IJCAI": range(2019, 2026),
        "ICLR": range(2019, 2027),
        "NeurIPS": range(2019, 2026),
        "ICML": range(2019, 2026),
    }
    rows = []
    for venue, edition_years in years.items():
        cells = []
        for year in edition_years:
            relative = f"{venue}/{year}/{venue}{year}.md"
            path = tmp_path / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("placeholder", encoding="utf-8")
            cells.append(f"[{year}](https://github.com/owner/repo/blob/master/{relative})")
        rows.append("| " + " | ".join([venue] + cells) + " |")
    (tmp_path / "README.md").write_text(
        "## 已支持会议\n| 会议 | 年份 |\n|---|---|\n" + "\n".join(rows) + "\n\n## 免责声明\n",
        encoding="utf-8",
    )
    manifest = load_manifest(tmp_path)
    assert len(manifest) == 60
    assert next(item for item in manifest if item.venue == "NeurIPS").conf == "NIPS2019"


def test_identity_keys_normalize_publisher_variants():
    assert ("openreview", "abc") in paper_identity_keys(
        "https://openreview.net/attachment?id=abc&name=pdf"
    )
    assert ("acl", "n19-1002") in paper_identity_keys(
        "https://www.aclweb.org/anthology/N19-1002.pdf"
    )
    assert ("acl", "2024.acl-long.2") in paper_identity_keys(
        "https://aclanthology.org/2024.acl-long.2.pdf"
    )
    assert ("ijcai", "2024:3") in paper_identity_keys(
        "https://www.ijcai.org/proceedings/2024/0003.pdf"
    )
    assert ("arxiv", "1805.10616") in paper_identity_keys(
        "https://arxiv.org/pdf/1805.10616v2.pdf"
    )


def test_code_link_quality_gate():
    assert classify_code_url("https://github.com/org/repo/tree/main") == (
        "https://github.com/org/repo/tree/main",
        "",
    )
    assert classify_code_url("https://gitlab.com/org/repo")[0]
    assert classify_code_url("https://anonymous.4open.science/r/abc")[0]
    assert classify_code_url("https://huggingface.co/datasets/org/data")[1] == "model_or_dataset"
    assert classify_code_url("https://org.github.io/project")[1] == "project_page"
    assert classify_code_url("https://youtu.be/abc")[1] == "video"
    assert classify_code_url("https://example.org/paper")[1] == "non_repository"
    assert classify_code_url(next(iter(GENERIC_RESOURCE_REPOS)))[1] == "generic_resource"


def test_tex_title_normalization_is_deterministic():
    assert normalize_title(r"$\textit{Read}$: A Method!") == normalize_title("Read a method")


def test_import_matches_openreview_id_and_only_changes_empty_code(tmp_path):
    edition = _edition(
        tmp_path,
        """#### A Paper
- Paper Link: https://openreview.net/attachment?id=abc123&name=pdf
- Code Link: https://github.com/org/new

#### Existing Paper
- Paper Link: https://openreview.net/forum?id=existing
- Code Link: https://github.com/org/incoming
""",
    )
    papers = [
        _paper(),
        _paper(
            title="Existing Paper",
            url="https://openreview.net/pdf?id=existing",
            code="https://gitlab.com/local/keep",
        ),
    ]
    original = [dict(paper) for paper in papers]
    updated, stats = import_records(papers, [edition], dry_run=False)
    assert updated[0]["paper_code"] == "https://github.com/org/new"
    assert updated[1]["paper_code"] == "https://gitlab.com/local/keep"
    assert stats["filled"] == 1
    assert stats["conflicts"] == 1
    assert stats["by_method"]["openreview"] == 1
    assert papers == original
    assert [{k: v for k, v in paper.items() if k != "paper_code"} for paper in updated] == [
        {k: v for k, v in paper.items() if k != "paper_code"} for paper in original
    ]


def test_import_title_fallback_is_scoped_to_edition(tmp_path):
    content = """#### Same Title
- Paper Link: https://example.org/{year}/different
- Code Link: https://github.com/org/repo{year}
"""
    first = _edition(tmp_path, content.format(year=2024), year=2024)
    second = _edition(tmp_path, content.format(year=2025), year=2025)
    papers = [
        _paper(conf="ICLR2024", title="Same Title", url="https://local.invalid/2024"),
        _paper(conf="ICLR2025", title="Same Title", url="https://local.invalid/2025"),
    ]
    updated, stats = import_records(papers, [first, second], dry_run=False)
    assert updated[0]["paper_code"] == "https://github.com/org/repo2024"
    assert updated[1]["paper_code"] == "https://github.com/org/repo2025"
    assert stats["by_method"]["title"] == 2


def test_import_rejects_ambiguous_title_and_dead_url(tmp_path):
    edition = _edition(
        tmp_path,
        """#### Duplicate
- Paper Link: https://external.invalid/paper
- Code Link: https://github.com/org/repo
""",
    )
    duplicate = _paper(title="Duplicate", url="https://local.invalid/1")
    updated, stats = import_records([duplicate, dict(duplicate)], [edition], dry_run=False)
    assert stats["ambiguous"] == 1
    assert all(paper["paper_code"] == "#" for paper in updated)

    unique = [_paper(title="Duplicate", url="https://local.invalid/1")]
    updated, stats = import_records(
        unique,
        [edition],
        dry_run=False,
        rejected_urls={"https://github.com/org/repo"},
    )
    assert updated[0]["paper_code"] == "#"
    assert stats["rejections"]["not_found"] == 1


def test_import_dry_run_does_not_return_modified_records(tmp_path):
    edition = _edition(
        tmp_path,
        """#### A Paper
- Paper Link: https://openreview.net/forum?id=abc123
- Code Link: https://github.com/org/new
""",
    )
    papers = [_paper()]
    updated, stats = import_records(papers, [edition], dry_run=True)
    assert updated is papers
    assert updated[0]["paper_code"] == "#"
    assert stats["filled"] == 1


def test_export_appends_only_missing_paper_and_is_idempotent(tmp_path):
    existing = """#### Existing Paper
- Paper Link: https://openreview.net/forum?id=existing
- Code Link: https://github.com/org/existing
"""
    edition = _edition(tmp_path, existing)
    papers = [
        _paper(title="Existing Paper", url="https://openreview.net/pdf?id=existing", code="https://github.com/org/existing"),
        _paper(title="New Paper", url="https://openreview.net/pdf?id=new", code="https://github.com/org/new"),
        _paper(conf="ICLR2025", title="Existing Paper", url="https://openreview.net/pdf?id=other-year", code="https://github.com/org/other"),
        _paper(title="No Repository", url="https://openreview.net/pdf?id=none", code="#"),
    ]
    additions, stats = export_records(papers, [edition])
    assert stats["added"] == 1
    assert additions["ICLR2024"][0]["title"] == "New Paper"
    original_prefix = edition.path.read_bytes()
    apply_additions([edition], additions)
    rendered = edition.path.read_bytes()
    assert rendered.startswith(original_prefix)
    assert not rendered.endswith((b"\n\n", b"\r\n\r\n"))
    additions_again, stats_again = export_records(papers, [edition])
    assert additions_again == {}
    assert stats_again["added"] == 0


def test_export_skips_ambiguous_local_title_and_blocked_iclr_repo(tmp_path):
    edition = _edition(tmp_path, "#### Existing\n- Paper Link: https://openreview.net/forum?id=existing\n- Code Link: https://github.com/org/existing\n")
    blocked_url = next(iter(BLOCKED_ICLR_REPOS))
    papers = [
        _paper(title="Duplicate", url="https://local.invalid/1", code="https://github.com/org/one"),
        _paper(title="Duplicate", url="https://local.invalid/2", code="https://github.com/org/two"),
        _paper(title="Blocked", url="https://openreview.net/pdf?id=blocked", code=blocked_url),
    ]
    additions, stats = export_records(papers, [edition])
    assert additions == {}
    assert stats["ambiguous"] == 2
    assert stats["quarantined"] == 1


def test_export_rejected_dead_url_is_not_added(tmp_path):
    edition = _edition(tmp_path, "#### Existing\n- Paper Link: https://openreview.net/forum?id=existing\n- Code Link: https://github.com/org/existing\n")
    papers = [_paper(title="Dead", url="https://openreview.net/pdf?id=dead", code="https://github.com/org/dead")]
    additions, stats = export_records(
        papers,
        [edition],
        rejected_urls={"https://github.com/org/dead"},
    )
    assert additions == {}
    assert stats["rejections"]["not_found"] == 1


def test_unknown_nonempty_markdown_fails_loudly(tmp_path):
    edition = _edition(tmp_path, "This is not a supported paper list")
    with pytest.raises(ValueError, match="No supported paper entries"):
        parse_markdown(edition.path)


def test_export_manual_exclusion_is_stable(tmp_path):
    edition = _edition(tmp_path, "#### Existing\n- Paper Link: https://openreview.net/forum?id=existing\n- Code Link: https://github.com/org/existing\n")
    paper = _paper(title="Reviewed Bad Link", url="https://openreview.net/pdf?id=bad", code="https://github.com/org/wrong")
    exclusions = {("ICLR2024", normalize_title(paper["paper_name"]), "https://github.com/org/wrong")}
    additions, stats = export_records([paper], [edition], exclusions=exclusions)
    assert additions == {}
    assert stats["rejections"]["manual_review"] == 1


def test_export_skips_duplicate_local_identity_with_different_titles(tmp_path):
    edition = _edition(tmp_path, "#### Existing\n- Paper Link: https://openreview.net/forum?id=existing\n- Code Link: https://github.com/org/existing\n")
    papers = [
        _paper(title="Title A", url="https://openreview.net/pdf?id=same", code="https://github.com/org/a"),
        _paper(title="Title B", url="https://openreview.net/forum?id=same", code="https://github.com/org/b"),
    ]
    additions, stats = export_records(papers, [edition])
    assert additions == {}
    assert stats["ambiguous"] == 2
    assert all(item["reason"] == "multiple_local_identity_matches" for item in stats["samples"]["ambiguous"])


def _repair_fixture(tmp_path):
    return _edition(
        tmp_path,
        """#### Replace Me
- Paper Link: https://openreview.net/forum?id=replace
- Code Link: https://github.com/org/correct

#### Genuine FFA Paper
- Paper Link: https://openreview.net/forum?id=genuine
- Code Link: https://github.com/mlii0117/FFA-IR
""",
    )


def test_repair_replaces_clears_and_preserves_evidence(tmp_path):
    edition = _repair_fixture(tmp_path)
    papers = [
        _paper(title="Replace Me", url="https://openreview.net/pdf?id=replace", code="https://github.com/mlii0117/FFA-IR"),
        _paper(title="Genuine FFA Paper", url="https://openreview.net/pdf?id=genuine", code="https://github.com/mlii0117/FFA-IR"),
        _paper(title="Unknown Bad Assignment", url="https://openreview.net/pdf?id=unknown", code="https://github.com/A4Bio/OpenCPD"),
        _paper(title="Abstract Evidence", url="https://openreview.net/pdf?id=abstract", code="https://github.com/A4Bio/OpenCPD"),
        _paper(title="Unrelated", url="https://openreview.net/pdf?id=unrelated", code="https://github.com/org/good"),
    ]
    papers[3]["paper_abstract"] = "Code: https://github.com/A4Bio/OpenCPD"
    original = [dict(paper) for paper in papers]
    updated, stats = repair_records(papers, [edition], dry_run=False)
    assert [paper["paper_code"] for paper in updated] == [
        "https://github.com/org/correct",
        "https://github.com/mlii0117/FFA-IR",
        "#",
        "https://github.com/A4Bio/OpenCPD",
        "https://github.com/org/good",
    ]
    assert stats["affected"] == 4
    assert stats["replaced"] == 1
    assert stats["cleared"] == 1
    assert stats["kept"] == 2
    assert papers == original


def test_repair_dry_run_and_rejected_replacement(tmp_path):
    edition = _repair_fixture(tmp_path)
    papers = [_paper(title="Replace Me", url="https://openreview.net/pdf?id=replace", code="https://github.com/mlii0117/FFA-IR")]
    dry, dry_stats = repair_records(papers, [edition], dry_run=True)
    assert dry is papers and dry[0]["paper_code"].endswith("FFA-IR")
    assert dry_stats["replaced"] == 1
    updated, stats = repair_records(
        papers,
        [edition],
        dry_run=False,
        rejected_urls={"https://github.com/org/correct"},
    )
    assert updated[0]["paper_code"] == "#"
    assert stats["cleared"] == 1
    assert stats["replaced"] == 0


def test_export_treats_upstream_paper_without_code_as_existing(tmp_path):
    edition = _edition(
        tmp_path,
        """#### Already Listed Without Code
- Paper Link: https://openreview.net/forum?id=existing
- Code Link:

| Title | 论文链接 | 开源代码 |
|-------|---------|---------|
| Table Without Code | [paper](https://ijcai.org/proceedings/2024/999) |  |
""",
    )
    entries = parse_markdown(edition.path)
    assert len(entries) == 2 and all(entry.code_urls == [] for entry in entries)
    papers = [
        _paper(title="Already Listed Without Code", url="https://openreview.net/pdf?id=existing", code="https://github.com/org/existing"),
        _paper(title="Table Without Code", url="https://ijcai.org/proceedings/2024/999", code="https://github.com/org/table"),
    ]
    additions, stats = export_records(papers, [edition])
    assert additions == {}
    assert stats["already_present"] == 2


def test_import_rejects_multiple_distinct_repositories(tmp_path):
    edition = _edition(
        tmp_path,
        """#### A Paper
- Paper Link: https://openreview.net/forum?id=abc123
- Code Link: https://github.com/org/first https://github.com/another/second
""",
    )
    updated, stats = import_records([_paper()], [edition], dry_run=False)
    assert updated[0]["paper_code"] == "#"
    assert stats["filled"] == 0
    assert stats["ambiguous"] == 1
    assert stats["samples"]["ambiguous"][0]["reason"] == "multiple_external_codes"


def test_upload_requires_online_parent_commit():
    with pytest.raises(RuntimeError, match="offline"):
        _require_sync_baseline(SimpleNamespace(dry_run=False, no_sync=False, offline=True), "parent")
    with pytest.raises(RuntimeError, match="parent commit"):
        _require_sync_baseline(SimpleNamespace(dry_run=False, no_sync=False, offline=False), None)
    _require_sync_baseline(SimpleNamespace(dry_run=False, no_sync=False, offline=False), "parent")
    _require_sync_baseline(SimpleNamespace(dry_run=False, no_sync=True, offline=True), None)


def test_source_commit_is_optional_for_reports(tmp_path):
    assert _source_commit(tmp_path) == ""


def _cli_args(tmp_path, **overrides):
    args = SimpleNamespace(
        cache=tmp_path / "cache.jsonl.gz",
        repo=tmp_path,
        venue=None,
        dry_run=True,
        offline=True,
        no_sync=True,
        check_links=True,
        report=tmp_path / "report.json",
    )
    return args


def test_import_rechecks_fallback_repository_url(tmp_path, monkeypatch):
    first = "https://github.com/org/repo/tree/dead"
    second = "https://github.com/org/repo/tree/main"
    edition = _edition(
        tmp_path,
        f"""#### A Paper
- Paper Link: https://openreview.net/forum?id=abc123
- Code Link: {first} {second}
""",
    )
    monkeypatch.setattr("scripts.sync_mlnlp_code_links.ensure_cache_local", lambda *a, **k: (a[0], "parent"))
    monkeypatch.setattr("scripts.sync_mlnlp_code_links._selected_manifest", lambda *a: [edition])
    monkeypatch.setattr("scripts.sync_mlnlp_code_links.load_papers", lambda path: [_paper()])
    monkeypatch.setattr("scripts.sync_mlnlp_code_links.write_report", lambda *a, **k: None)
    checked = []

    def fake_check(urls):
        checked.append(set(urls))
        return {url: ("not_found" if url == first else "reachable") for url in urls}

    monkeypatch.setattr("scripts.sync_mlnlp_code_links.check_urls", fake_check)
    stats = run_import(_cli_args(tmp_path))
    assert stats["filled"] == 1
    assert checked == [{first}, {second}]
    assert stats["url_check_details"] == {first: "not_found", second: "reachable"}


def test_repair_rechecks_fallback_repository_url(tmp_path, monkeypatch):
    first = "https://github.com/org/repo/tree/dead"
    second = "https://github.com/org/repo/tree/main"
    edition = _edition(
        tmp_path,
        f"""#### A Paper
- Paper Link: https://openreview.net/forum?id=abc123
- Code Link: {first} {second}
""",
    )
    paper = _paper(code="https://github.com/mlii0117/FFA-IR")
    monkeypatch.setattr("scripts.sync_mlnlp_code_links.ensure_cache_local", lambda *a, **k: (a[0], "parent"))
    monkeypatch.setattr("scripts.sync_mlnlp_code_links._selected_manifest", lambda *a: [edition])
    monkeypatch.setattr("scripts.sync_mlnlp_code_links.load_papers", lambda path: [paper])
    monkeypatch.setattr("scripts.sync_mlnlp_code_links.write_report", lambda *a, **k: None)
    checked = []

    def fake_check(urls):
        checked.append(set(urls))
        return {url: ("not_found" if url == first else "reachable") for url in urls}

    monkeypatch.setattr("scripts.sync_mlnlp_code_links.check_urls", fake_check)
    stats = run_repair(_cli_args(tmp_path))
    assert stats["replaced"] == 1
    assert stats["changes"][0]["new_code"] == second
    assert stats["repair_mode"] == "iclr_clusters"
    assert checked == [{first}, {second}]


def test_generic_resource_repair_replaces_or_clears_without_keeping_list_repos(tmp_path):
    edition = _edition(
        tmp_path,
        """#### Replace Me
- Paper Link: https://openreview.net/forum?id=replace
- Code Link: https://github.com/org/correct
""",
        venue="ICML",
        year=2021,
    )
    generic = next(iter(GENERIC_RESOURCE_REPOS))
    papers = [
        _paper(conf="ICML2021", title="Replace Me", url="https://openreview.net/pdf?id=replace", code=generic),
        _paper(conf="ICML2021", title="Clear Me", url="https://openreview.net/pdf?id=clear", code=generic),
        _paper(conf="ICML2021", title="List Citation Is Not Code", url="https://openreview.net/pdf?id=list", code=generic),
    ]
    papers[2]["paper_abstract"] = f"Resources: {generic}"
    updated, stats = repair_records(
        papers,
        [edition],
        dry_run=False,
        blocked_repos={generic},
        only_iclr=False,
        keep_evidence=False,
    )
    assert [paper["paper_code"] for paper in updated] == [
        "https://github.com/org/correct",
        "#",
        "#",
    ]
    assert stats["affected"] == 3
    assert stats["replaced"] == 1
    assert stats["cleared"] == 2
    assert stats["kept"] == 0


def test_import_never_reintroduces_generic_resource_repo(tmp_path):
    generic = next(iter(GENERIC_RESOURCE_REPOS))
    edition = _edition(
        tmp_path,
        f"""#### A Paper
- Paper Link: https://openreview.net/forum?id=abc123
- Code Link: {generic}
""",
    )
    updated, stats = import_records([_paper()], [edition], dry_run=False)
    assert updated[0]["paper_code"] == "#"
    assert stats["filled"] == 0
    assert stats["rejections"]["generic_resource"] == 1


def test_normalize_repo_url_unifies_scheme_www_and_git_suffix():
    assert normalize_repo_url("http://www.github.com/Org/Repo.git/") == "https://github.com/org/repo"

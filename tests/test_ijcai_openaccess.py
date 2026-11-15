import json
from pathlib import Path

import pytest

from collector.merge import _merge_with_cache
from collector.sources.ijcai import (
    IJCAICollectionError,
    canonicalize_ijcai_url,
    collect_ijcai_papers,
    merge_ijcai_records,
    normalize_ijcai_title,
    parse_ijcai_detail,
    parse_ijcai_json,
    parse_ijcai_listing,
    parse_ijcai_proceedings_index,
    search_from_ijcai,
)

DETAIL_URL = "https://ijcai.org/proceedings/2001/1"
CURRENT_FIXTURE = Path(__file__).parent / "fixtures" / "abstract_fetchers" / "ijcai_current.html"


def _paper(title, url, abstract="", authors=None):
    return {
        "paper_name": title,
        "paper_url": url,
        "paper_authors": authors if authors is not None else ["Emilio Remolina", "Benjamin Kuipers"],
        "paper_abstract": abstract,
        "paper_code": "#",
    }


def _row(title, detail, authors="Emilio Remolina, Benjamin Kuipers", doi=""):
    doi_html = f' · <a href="https://doi.org/{doi}">doi:{doi}</a>' if doi else ""
    return f"""
    <div class="paper_wrapper paper-row">
      <div class="paper-row__text">
        <a class="title" href="{detail}">{title}</a>
        <div class="authors">{authors}</div>
        <div class="details">Pages 5–10{doi_html}</div>
      </div>
      <div class="paper-row__actions">
        <a href="{detail}">Details</a><a href="/proceedings/2001/0001.pdf">PDF</a>
      </div>
    </div>
    """


def test_index_accepts_only_main_proceedings():
    entries = parse_ijcai_proceedings_index("""
      <a href="/proceedings/2026/">Main</a>
      <a href="/proceedings_aai4g/2024/">Special</a>
      <a href="/proceedings/2001-1">Volume 1</a>
      <a href="/proceedings/2001-2">Volume 2</a>
      <a href="https://example.com/proceedings/2003">External</a>
    """)
    assert entries == [
        {"name": "IJCAI2026", "url": "https://ijcai.org/proceedings/2026"},
        {"name": "IJCAI2001", "url": "https://ijcai.org/proceedings/2001-1"},
        {"name": "IJCAI2001", "url": "https://ijcai.org/proceedings/2001-2"},
    ]


def test_listing_uses_detail_metadata_and_never_pdf_url():
    papers = parse_ijcai_listing(
        _row("A Logical Account of Causal and Topological Maps", "/proceedings/2001/0001"),
        "https://ijcai.org/proceedings/2001-1",
    )
    assert papers == [{
        "paper_name": "A Logical Account of Causal and Topological Maps",
        "paper_url": DETAIL_URL,
        "paper_authors": [],
        "paper_abstract": "",
        "paper_code": "#",
        "_detail_url": DETAIL_URL,
    }]
    assert canonicalize_ijcai_url("https://www.ijcai.org/proceedings/2001/0001/") == DETAIL_URL


def test_listing_rejects_changed_or_challenge_page():
    with pytest.raises(ValueError, match="No IJCAI paper entries"):
        parse_ijcai_listing("<html>Challenge</html>", "https://ijcai.org/proceedings/2001")


def test_json_reads_official_abstract_authors_and_doi():
    listing = _paper("Fallback", DETAIL_URL)
    content = json.dumps({
        "type": "ScholarlyArticle",
        "title": "A Logical Account",
        "authors": [{"name": "Emilio Remolina"}, {"name": "Benjamin Kuipers"}],
        "abstract": "An official abstract\nwith whitespace normalized.",
        "doi": "10.24963/ijcai.2026/1",
        "pdf": "https://ijcai.org/proceedings/2026/0001.pdf",
    })
    paper = parse_ijcai_json(content, DETAIL_URL, listing)
    assert paper == {
        "paper_name": "A Logical Account",
        "paper_url": "https://doi.org/10.24963/ijcai.2026/1",
        "paper_authors": ["Emilio Remolina", "Benjamin Kuipers"],
        "paper_abstract": "An official abstract with whitespace normalized.",
        "paper_code": "#",
    }


def test_current_real_detail_fixture_extracts_only_abstract():
    paper = parse_ijcai_detail(
        CURRENT_FIXTURE.read_text(encoding="utf-8"),
        "https://ijcai.org/proceedings/2026/1",
    )
    assert paper["paper_name"].startswith("Semantically Labelled Automata")
    assert paper["paper_url"] == "https://doi.org/10.24963/ijcai.2026/1"
    assert paper["paper_abstract"].startswith("We study multi-task reinforcement learning")
    assert "References" not in paper["paper_abstract"]
    assert "Aminof" not in paper["paper_abstract"]


def test_merge_enriches_main_records_and_preserves_satellites():
    official = [_paper(
        "A Logical Account of Causal and Topological Maps",
        DETAIL_URL,
        "Official abstract",
    )]
    cached = [
        _paper(
            "A Logical Account of Causal and Topological Maps",
            "https://doi.org/10.24963/ijcai.2001/1",
            "A longer cached abstract that must be preserved",
        ),
        _paper(
            "A Logical Account of Causal and Topological Maps",
            "https://ceur-ws.org/Vol-1/workshop-paper.pdf",
            "Workshop version",
        ),
    ]
    merged = merge_ijcai_records(cached, official)
    assert len(merged) == 2
    assert merged[0]["paper_url"] == "https://doi.org/10.24963/ijcai.2001/1"
    assert merged[0]["paper_abstract"].startswith("A longer cached")
    assert merged[1]["paper_abstract"] == "Workshop version"
    assert len(merge_ijcai_records(merged, official)) == 2


def test_merge_rejects_same_title_with_disjoint_authors():
    official = [_paper("Shared Title", DETAIL_URL, "Official", ["Alice Author"])]
    cached = [_paper("Shared Title", "https://www.ijcai.org/proceedings/2001/0099", "Other", ["Bob Writer"])]
    merged = merge_ijcai_records(cached, official)
    assert len(merged) == 2


def test_ambiguous_official_titles_are_not_title_merged():
    official = [
        _paper("Same Title", DETAIL_URL),
        _paper("Same Title", "https://ijcai.org/proceedings/2001/2"),
    ]
    cached = [_paper("Same Title", "https://doi.org/10.24963/ijcai.2001/1")]
    assert len(merge_ijcai_records(cached, official)) == 3


def test_repeated_volume_entries_do_not_make_official_title_ambiguous():
    official = _paper("Repeated Volume Paper", DETAIL_URL, "Official abstract")
    cached = _paper(
        "Repeated Volume Paper", "https://doi.org/10.24963/ijcai.2001/1", "Cached"
    )
    merged = merge_ijcai_records([cached], [official, dict(official)])
    assert len(merged) == 1
    assert merged[0]["paper_abstract"] == "Official abstract"


def test_merge_does_not_dedupe_empty_urls():
    assert canonicalize_ijcai_url("") == ""
    records = [_paper("First", ""), _paper("Second", "")]
    assert len(merge_ijcai_records(records, [])) == 2


def test_title_normalization_handles_accents_and_punctuation():
    assert normalize_ijcai_title("  Křetínský: LTL—Planning! ") == normalize_ijcai_title("Kretinsky LTL Planning")


def test_cache_merge_is_scoped_to_selected_ijcai_year():
    official = _paper("Official Paper", DETAIL_URL, "Official")
    cached = _paper("Official Paper", "https://doi.org/10.24963/ijcai.2001/1", "A longer cached abstract")
    untouched = [_paper("Old Paper", "https://ijcai.org/proceedings/1969/1")]
    merged = _merge_with_cache(
        {"IJCAI2001": [official]},
        {"IJCAI2001": [cached], "IJCAI1969": untouched},
        set(), set(), ijcai_title_merge_records={"IJCAI2001": [official]},
    )
    assert len(merged["IJCAI2001"]) == 1
    assert merged["IJCAI2001"][0]["paper_abstract"] == "A longer cached abstract"
    assert merged["IJCAI1969"] == untouched


def test_collection_snapshots_json_and_replays_without_network(monkeypatch, tmp_path):
    listing_url = "https://ijcai.org/proceedings/2001-1"
    listing_html = _row("A Logical Account", "/proceedings/2001/1", "<a>Emilio Remolina</a>")
    metadata = json.dumps({
        "type": "ScholarlyArticle", "title": "A Logical Account",
        "authors": [{"name": "Emilio Remolina"}], "abstract": "Official abstract", "doi": None,
    })
    calls = []

    class Response:
        def __init__(self, text):
            self.text = text
            self.encoding = ""

        def raise_for_status(self):
            return None

    def get(url, **kwargs):
        calls.append(url)
        return Response(listing_html if url == listing_url else metadata)

    monkeypatch.setattr("collector.sources.ijcai.SESSION.get", get)
    papers = collect_ijcai_papers(listing_url, snapshot_dir=tmp_path)
    assert papers[0]["paper_abstract"] == "Official abstract"
    assert calls == [listing_url, DETAIL_URL + ".json"]
    assert (tmp_path / "proceedings" / "2001-1.html").exists()
    assert (tmp_path / "proceedings" / "2001" / "1.json").exists()

    monkeypatch.setattr("collector.sources.ijcai.SESSION.get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    replayed = collect_ijcai_papers(listing_url, snapshot_dir=tmp_path, allow_network=False)
    assert replayed == papers


def test_collection_skips_detail_for_unique_cached_abstract(monkeypatch, tmp_path):
    listing_url = "https://ijcai.org/proceedings/2001-1"
    (tmp_path / "proceedings").mkdir(parents=True)
    (tmp_path / "proceedings" / "2001-1.html").write_text(
        _row("A Logical Account", "/proceedings/2001/1"), encoding="utf-8"
    )
    cached = [_paper("A Logical Account", DETAIL_URL, "Cached abstract")]
    papers = collect_ijcai_papers(
        listing_url, snapshot_dir=tmp_path, cached_records=cached, allow_network=False
    )
    assert papers[0]["paper_abstract"] == ""
    assert "_detail_url" not in papers[0]


def test_failed_collection_does_not_add_partial_records(monkeypatch):
    partial = _paper("Partial", DETAIL_URL, "Partial abstract")

    def _fail(*args, **kwargs):
        raise IJCAICollectionError([(DETAIL_URL, "network error")], [partial])

    monkeypatch.setattr("collector.sources.ijcai.collect_ijcai_papers", _fail)
    res = {"IJCAI2001": []}
    with pytest.raises(IJCAICollectionError, match="network error"):
        search_from_ijcai("https://ijcai.org/proceedings/2001", "IJCAI2001", res)
    assert res["IJCAI2001"] == []



def test_empty_json_abstract_falls_back_to_html_snapshot(tmp_path):
    listing_url = "https://ijcai.org/proceedings/2026"
    detail_dir = tmp_path / "proceedings" / "2026"
    detail_dir.mkdir(parents=True)
    (tmp_path / "proceedings" / "2026.html").write_text(
        _row("Semantically Labelled Automata", "/proceedings/2026/1"), encoding="utf-8"
    )
    (detail_dir / "1.json").write_text(json.dumps({
        "type": "ScholarlyArticle", "title": "Semantically Labelled Automata",
        "authors": [], "abstract": "", "doi": "10.24963/ijcai.2026/1",
    }), encoding="utf-8")
    (detail_dir / "1.html").write_text(CURRENT_FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")

    papers = collect_ijcai_papers(
        listing_url, snapshot_dir=tmp_path, allow_network=False
    )
    assert papers[0]["paper_abstract"].startswith("We study multi-task reinforcement learning")



def test_legacy_official_pdf_url_is_identity_evidence_without_fetching_it():
    official = _paper("Legacy Official Paper", DETAIL_URL, "Official abstract")
    cached = _paper(
        "Legacy Official Paper",
        "http://ijcai.org/Proceedings/03/Papers/001.pdf",
        "",
        ["Emilio Remolina"],
    )
    merged = merge_ijcai_records([cached], [official])
    assert len(merged) == 1
    assert merged[0]["paper_url"] == DETAIL_URL
    assert merged[0]["paper_abstract"] == "Official abstract"



def test_archived_ijcai_submission_url_merges_with_author_corroboration():
    official = _paper("Archived Main Paper", DETAIL_URL, "Official abstract", ["Jon M. Kleinberg"])
    cached = _paper(
        "Archived Main Paper",
        "https://web.archive.org/web/*/http://www.aaai.org/ocs/index.php/IJCAI/IJCAI13/paper/view/6896",
        "",
        ["Jon M. Kleinberg"],
    )
    merged = merge_ijcai_records([cached], [official])
    assert len(merged) == 1
    assert merged[0]["paper_url"] == DETAIL_URL



def test_author_merge_handles_german_umlaut_transliteration():
    official = _paper("Same Work", DETAIL_URL, "Official", ["Christoph Benzmüller"])
    cached = _paper(
        "Same Work", "https://arxiv.org/abs/1304.0001", "", ["Christoph Benzmueller"]
    )
    merged = merge_ijcai_records([cached], [official])
    assert len(merged) == 1
    assert merged[0]["paper_url"] == DETAIL_URL

from pathlib import Path

import pytest

from collector.merge import _merge_with_cache
from collector.sources.miccai import (
    MICCAICollectionError,
    merge_miccai_records,
    normalize_miccai_title,
    parse_miccai_detail,
    parse_miccai_listing,
    search_from_miccai,
)

FIXTURES = Path(__file__).parent / "fixtures" / "miccai"
DETAIL_URL = "https://papers.miccai.org/miccai-2026/0001-Paper0308.html"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _paper(title: str, url: str, abstract: str = "", code: str = "#") -> dict:
    return {
        "paper_name": title,
        "paper_url": url,
        "paper_authors": ["Author"],
        "paper_abstract": abstract,
        "paper_code": code,
    }


def test_listing_extracts_unique_official_detail_urls():
    links = parse_miccai_listing(
        _fixture("listing.html"), "https://papers.miccai.org/miccai-2026/"
    )
    assert links == [
        DETAIL_URL,
        "https://papers.miccai.org/miccai-2026/0002-Paper0002.html",
    ]


def test_listing_rejects_missing_or_changed_page_structure():
    with pytest.raises(ValueError, match="No MICCAI paper detail links"):
        parse_miccai_listing("<html>challenge page</html>", DETAIL_URL)


def test_detail_reads_abstract_code_and_doi_from_dedicated_sections():
    paper = parse_miccai_detail(_fixture("detail_code_doi.html"), DETAIL_URL)
    assert paper == {
        "paper_name": "µ² Tokenizer: Differentiable Aβ-PET Fusion",
        "paper_url": "https://doi.org/10.1007/978-3-032-00000-0_1",
        "paper_authors": ["Li, Siyou", "Wang, Changmiao"],
        "paper_abstract": (
            "We propose a tokenizer for Aβ-PET fusion. "
            "An abstract-only link is https://github.com/wrong/abstract-link. "
            "It preserves a second paragraph."
        ),
        "paper_code": "https://github.com/Siyou-Li/u2Tokenizer",
    }


def test_detail_without_doi_or_code_uses_official_url_and_placeholder():
    paper = parse_miccai_detail(_fixture("detail_no_code.html"), DETAIL_URL)
    assert paper["paper_url"] == DETAIL_URL
    assert paper["paper_code"] == "#"
    assert "not-the-code-field" in paper["paper_abstract"]
    assert "Review text" not in paper["paper_abstract"]


def test_title_normalization_handles_unicode_width_punctuation_and_case():
    assert normalize_miccai_title("  µ²-Tokenizer: Aβ-PET! ") == "μ2tokenizeraβpet"
    assert normalize_miccai_title("μ2 Tokenizer Aβ PET") == "μ2tokenizeraβpet"
    assert normalize_miccai_title(r"$\mathrm {IterMask^2}$: A Study") == normalize_miccai_title("IterMask2: A Study")
    assert normalize_miccai_title("[inline-graphic not available: see fulltext]MedObvious: A Study") == normalize_miccai_title("MedObvious: A Study")


def test_merge_enriches_old_doi_records_without_losing_information():
    official = [
        _paper(
            "µ² Tokenizer: Aβ-PET",
            DETAIL_URL,
            "Official abstract",
            "https://github.com/official/repo",
        ),
        _paper(
            "CPR: Chained Perceptual Refinement",
            "https://papers.miccai.org/miccai-2026/0002-Paper0002.html",
            "Official CPR abstract",
        ),
        _paper(
            "MedObvious: A New Official Paper",
            "https://papers.miccai.org/miccai-2026/0003-Paper0003.html",
            "New abstract",
        ),
    ]
    cached = [
        _paper(
            "μ2-Tokenizer: Aβ PET",
            "https://doi.org/10.1007/tokenizer",
            "A longer cached abstract that must be retained instead of the shorter official one",
            "https://github.com/old/repo",
        ),
        _paper(
            "CPR Chained Perceptual Refinement",
            "https://doi.org/10.1007/cpr",
            "Short",
            "https://github.com/old/cpr",
        ),
        _paper("A Workshop", "https://doi.org/10.1007/workshop", "Workshop"),
    ]

    merged = merge_miccai_records(official + cached, official)
    assert len(merged) == 4
    by_title = {record["paper_name"]: record for record in merged}
    tokenizer = by_title["µ² Tokenizer: Aβ-PET"]
    assert tokenizer["paper_url"] == "https://doi.org/10.1007/tokenizer"
    assert tokenizer["paper_abstract"].startswith("A longer cached abstract")
    assert tokenizer["paper_code"] == "https://github.com/official/repo"
    cpr = by_title["CPR: Chained Perceptual Refinement"]
    assert cpr["paper_url"] == "https://doi.org/10.1007/cpr"
    assert cpr["paper_abstract"] == "Official CPR abstract"
    assert cpr["paper_code"] == "https://github.com/old/cpr"

    assert len(merge_miccai_records(official + merged, official)) == 4


def test_ambiguous_official_titles_are_not_title_merged():
    official = [
        _paper("Same Title", "https://papers.miccai.org/miccai-2026/1.html"),
        _paper("Same Title", "https://papers.miccai.org/miccai-2026/2.html"),
    ]
    cached = [_paper("Same Title", "https://doi.org/10.1007/same")]
    assert len(merge_miccai_records(official + cached, official)) == 3


def test_cache_merge_is_scoped_to_selected_miccai_years():
    official = _paper("Shared Title", DETAIL_URL, "Official")
    cached = _paper("Shared Title", "https://doi.org/10.1007/shared", "Cached")
    old_records = [
        _paper("Old Duplicate", "https://doi.org/10.1007/old-1"),
        _paper("Old Duplicate", "https://doi.org/10.1007/old-2"),
    ]
    merged = _merge_with_cache(
        {"MICCAI2026": [official]},
        {"MICCAI2026": [cached], "MICCAI2023": old_records},
        set(),
        set(),
        title_merge_records={"MICCAI2026": [official]},
    )
    assert len(merged["MICCAI2026"]) == 1
    assert merged["MICCAI2026"][0]["paper_url"] == "https://doi.org/10.1007/shared"
    assert merged["MICCAI2023"] == old_records



def test_failed_collection_does_not_add_partial_records(monkeypatch):
    partial = _paper("Partial Paper", DETAIL_URL, "Partial abstract")

    def _fail(*args, **kwargs):
        raise MICCAICollectionError([(DETAIL_URL, "network error")], [partial])

    monkeypatch.setattr("collector.sources.miccai.collect_miccai_papers", _fail)
    res = {"MICCAI2026": []}
    with pytest.raises(MICCAICollectionError, match="network error"):
        search_from_miccai("https://papers.miccai.org/miccai-2026/", "MICCAI2026", res)
    assert res["MICCAI2026"] == []

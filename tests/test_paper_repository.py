"""Service-level tests bypassing the HTTP layer. Faster than the
client-based suite and produces sharper tracebacks when search logic
regresses.
"""

from __future__ import annotations

from papervault.services.papers import (
    PaperRepository,
    SearchCriteria,
    search_papers,
)


def _criteria(**overrides) -> SearchCriteria:
    base = dict(
        query=None,
        field="title",
        confs=[],
        since=None,
        until=None,
        author=None,
        sort="-year",
        page=1,
        size=50,
    )
    base.update(overrides)
    return SearchCriteria(**base)


def test_repository_loads_expected_count(repository_with_sample: PaperRepository):
    # 7 valid records + 1 silently dropped (no-year) = 7 surviving.
    assert len(repository_with_sample.all_papers()) == 7


def test_repository_skips_conf_without_year(repository_with_sample: PaperRepository):
    confs = repository_with_sample.confs()
    assert "WORKSHOPNOYEAR" not in confs


def test_paper_id_is_stable_across_reloads(sample_cache_path):
    repo1 = PaperRepository(cache_path=sample_cache_path, refresh_on_load=False)
    repo1.ensure_loaded()
    repo2 = PaperRepository(cache_path=sample_cache_path, refresh_on_load=False)
    repo2.ensure_loaded()

    ids1 = sorted(p.id for p in repo1.all_papers())
    ids2 = sorted(p.id for p in repo2.all_papers())
    assert ids1 == ids2
    # Stable hash must also be deterministic prefix length (16 hex chars).
    assert all(len(i) == 16 for i in ids1)


def test_search_filter_by_conf_case_insensitive(repository_with_sample):
    items, total = search_papers(
        repository_with_sample, _criteria(confs=["acl"])
    )
    assert total == 2
    assert all(p.conf == "ACL" for p in items)


def test_search_year_range_excludes_outliers(repository_with_sample):
    items, total = search_papers(
        repository_with_sample, _criteria(since=2023, until=2024)
    )
    assert total == 4
    assert all(2023 <= int(p.year) <= 2024 for p in items)


def test_search_query_hash_means_match_all(repository_with_sample):
    # '#' is the documented "match all" sentinel used by the legacy UI;
    # the service should still honour it.
    items, total = search_papers(
        repository_with_sample, _criteria(query="#")
    )
    assert total == len(repository_with_sample.all_papers())
    assert len(items) == total


def test_search_pagination_slice(repository_with_sample):
    page_one, total = search_papers(
        repository_with_sample, _criteria(sort="title", size=3, page=1)
    )
    page_two, total_two = search_papers(
        repository_with_sample, _criteria(sort="title", size=3, page=2)
    )
    assert total == total_two
    assert len(page_one) == 3
    assert len(page_two) >= 1
    ids_one = {p.id for p in page_one}
    ids_two = {p.id for p in page_two}
    assert ids_one.isdisjoint(ids_two)

"""Regression tests for the README recent-update meta snapshot."""

from maintain import _recent_update_meta


def test_meta_records_delta_not_totals():
    before = {"NIPS2024": [{}] * 100, "ICML2024": [{}] * 50}
    after = {"NIPS2024": [{}] * 100, "ICML2024": [{}] * 50, "ICLR2025": [{}] * 12}
    meta = _recent_update_meta(before, after)
    assert meta["new_papers"] == 12
    assert meta["new_conferences"] == 1


def test_meta_force_rebuild_against_existing_cache_is_zero():
    # A force rebuild of an unchanged corpus must report zero new papers,
    # never the whole-corpus total.
    cache = {"NIPS2024": [{}] * 100, "ICML2024": [{}] * 50}
    meta = _recent_update_meta(cache, {k: list(v) for k, v in cache.items()})
    assert meta["new_papers"] == 0
    assert meta["new_conferences"] == 0


def test_meta_empty_bootstrap_counts_everything():
    after = {"NIPS2024": [{}] * 100}
    meta = _recent_update_meta({}, after)
    assert meta["new_papers"] == 100
    assert meta["new_conferences"] == 1

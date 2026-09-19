"""Regression tests for the README recent-update meta snapshot."""

from maintain import _recent_update_meta


def _papers(prefix, n):
    return [{"paper_url": f"https://example.org/{prefix}/{i}"} for i in range(n)]


def test_meta_records_new_identities_not_totals():
    before = {"NIPS2024": _papers("nips", 100), "ICML2024": _papers("icml", 50)}
    after = {**before, "ICLR2025": _papers("iclr", 12)}
    meta = _recent_update_meta(before, after)
    assert meta["new_papers"] == 12
    assert meta["new_conferences"] == 1


def test_meta_force_rebuild_against_existing_cache_is_zero():
    # A force rebuild of an unchanged corpus must report zero new papers,
    # never the whole-corpus total.
    cache = {"NIPS2024": _papers("nips", 100), "ICML2024": _papers("icml", 50)}
    meta = _recent_update_meta(cache, {k: list(v) for k, v in cache.items()})
    assert meta["new_papers"] == 0
    assert meta["new_conferences"] == 0


def test_meta_empty_bootstrap_counts_everything():
    after = {"NIPS2024": _papers("nips", 100)}
    meta = _recent_update_meta({}, after)
    assert meta["new_papers"] == 100
    assert meta["new_conferences"] == 1


def test_meta_shrinking_corpus_never_goes_negative():
    # A full rebuild that legitimately shrinks the corpus (dropped conf,
    # fewer records) must not produce a negative "new papers" count.
    before = {"NIPS2024": _papers("nips", 100), "ICML2024": _papers("icml", 50)}
    after = {"NIPS2024": _papers("nips", 80)}
    meta = _recent_update_meta(before, after)
    assert meta["new_papers"] == 0
    assert meta["new_conferences"] == 0


def test_meta_counts_only_genuinely_new_records_on_shrink():
    before = {"NIPS2024": _papers("nips", 100)}
    after = {"NIPS2024": _papers("nips", 80) + _papers("nips-new", 3)}
    meta = _recent_update_meta(before, after)
    assert meta["new_papers"] == 3
    assert meta["new_conferences"] == 0


def test_meta_falls_back_to_title_when_url_missing():
    before = {"NIPS2024": [{"paper_name": "Paper A"}]}
    after = {"NIPS2024": [{"paper_name": "Paper A"}, {"paper_name": "Paper B"}]}
    meta = _recent_update_meta(before, after)
    assert meta["new_papers"] == 1

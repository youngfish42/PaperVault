"""Behavioural tests for /api/v1/papers and /api/v1/confs against a
small synthetic cache. The fixture ``client_with_sample`` is wired up
in conftest.py and runs fully offline.
"""

from __future__ import annotations


def _items_titles(body):
    return [it["title"] for it in body["items"]]


def test_confs_groups_years_and_skips_invalid(client_with_sample):
    resp = client_with_sample.get("/api/v1/confs")
    assert resp.status_code == 200
    body = resp.get_json()

    names = {it["name"] for it in body["items"]}
    assert "ACL" in names
    assert "EMNLP" in names
    assert "CVPR" in names
    assert "NEURIPS" in names
    assert "NIPS" in names
    assert "WORKSHOP W00" in names
    # Negative sample without a 4-digit year must be dropped at load time.
    assert "WORKSHOPNOYEAR" not in names

    acl = next(it for it in body["items"] if it["name"] == "ACL")
    years = {y["year"]: y["count"] for y in acl["years"]}
    assert years == {"2023": 1, "2024": 1}
    assert acl["total"] == 2


def test_search_title_hit(client_with_sample):
    resp = client_with_sample.get("/api/v1/papers?q=attention&field=title")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["meta"]["total"] == 1
    assert body["items"][0]["conf"] == "ACL"
    assert body["items"][0]["year"] == "2023"


def test_search_field_any_or_logic(client_with_sample):
    resp = client_with_sample.get("/api/v1/papers?q=vision&field=any&size=10")
    body = resp.get_json()
    titles = _items_titles(body)
    assert any("Vision" in t for t in titles)


def test_search_author_single_word_substring(client_with_sample):
    # Single-token needle uses substring on the joined author string.
    resp = client_with_sample.get("/api/v1/papers?author=alice&size=10")
    body = resp.get_json()
    titles = _items_titles(body)
    assert "Attention Is All You Need Revisited" in titles
    assert "Retrieval Augmented Generation at Scale" in titles


def test_search_author_multiword_exact(client_with_sample):
    # Multi-token needle requires a full author match (not substring).
    resp = client_with_sample.get("/api/v1/papers?author=Kai+Lin&size=10")
    body = resp.get_json()
    titles = _items_titles(body)
    assert titles == ["Graph Neural Networks Survey"]


def test_filter_multi_conf(client_with_sample):
    resp = client_with_sample.get("/api/v1/papers?conf=ACL&conf=EMNLP&size=10")
    body = resp.get_json()
    confs = {it["conf"] for it in body["items"]}
    assert confs == {"ACL", "EMNLP"}
    assert body["meta"]["total"] == 3


def test_year_range_inclusive(client_with_sample):
    resp = client_with_sample.get("/api/v1/papers?since=2023&until=2024&size=20")
    body = resp.get_json()
    years = {it["year"] for it in body["items"]}
    assert years == {"2023", "2024"}


def test_sort_desc_year_default(client_with_sample):
    resp = client_with_sample.get("/api/v1/papers?size=20")
    body = resp.get_json()
    years = [int(it["year"]) for it in body["items"]]
    assert years == sorted(years, reverse=True)


def test_pagination_disjoint(client_with_sample):
    page1 = client_with_sample.get("/api/v1/papers?size=2&page=1&sort=title").get_json()
    page2 = client_with_sample.get("/api/v1/papers?size=2&page=2&sort=title").get_json()
    ids1 = {it["id"] for it in page1["items"]}
    ids2 = {it["id"] for it in page2["items"]}
    assert ids1.isdisjoint(ids2)
    assert page1["meta"]["total"] == page2["meta"]["total"]


def test_hyphen_normalization_in_title(client_with_sample):
    # Title is "Legacy Title with-Hyphen Inside"; query without hyphen must match.
    resp = client_with_sample.get("/api/v1/papers?q=with+hyphen&field=title")
    body = resp.get_json()
    titles = _items_titles(body)
    assert "Legacy Title with-Hyphen Inside" in titles


def test_paper_out_schema_roundtrip(client_with_sample):
    from papervault.schemas import PaperOut

    resp = client_with_sample.get("/api/v1/papers?size=20")
    body = resp.get_json()
    # Every returned item must validate cleanly against the public schema.
    for item in body["items"]:
        PaperOut.model_validate(item)

"""Regression tests for `discovery/dblp.py` root-page dedupe semantics.

The collector config records one entry per DBLP page. A single year often
maps to several pages (multi-volume "-N" conference volumes, SIGSPATIAL/WWW
satellite workshops, per-volume journal pages such as IJAEO/CEUS/JOSIS), so
the conf `name` (PREFIX + year) is not a unique key. Dedupe must be URL-based
(normalised across DBLP mirror hosts); otherwise a page that DBLP adds later
for an already-recorded year would be wrongly skipped.
"""

from __future__ import annotations

from discovery.dblp import DBLPDiscovery, _url_key


def _discovery(existing):
    inst = DBLPDiscovery(existing_conf=existing)
    return inst


def test_url_key_normalises_dblp_mirrors():
    assert _url_key("https://dblp.uni-trier.de/db/journals/ijcv/ijcv128.html") == \
        _url_key("https://dblp.org/db/journals/ijcv/ijcv128.html")
    assert _url_key("https://dblp.org/db/journals/ijcv/ijcv129.html") != \
        _url_key("https://dblp.org/db/journals/ijcv/ijcv128.html")
    # Non-DBLP URLs keep the full string as key.
    assert _url_key("https://aclanthology.org/2024.acl-long.1/") == \
        "https://aclanthology.org/2024.acl-long.1/"


def test_journal_discovers_new_volume_for_recorded_year():
    """A new volume URL for an already-recorded year must be discovered."""
    page = """
    <ul>
    <li>2021: Volumes <a href="https://dblp.org/db/journals/aeog/aeog94.html">94</a>,
    <a href="https://dblp.org/db/journals/aeog/aeog106.html">106</a></li>
    <li>2022: Volume <a href="https://dblp.org/db/journals/aeog/aeog107.html">107</a></li>
    </ul>
    """
    inst = _discovery([{"name": "IJAEO2021",
                        "url": "https://dblp.org/db/journals/aeog/aeog94.html"}])
    inst._get_text = lambda url, **kw: page
    meta = {"root": "https://dblp.org/db/journals/aeog/index.html", "name": "IJAEO"}
    res = inst._discover_journal_from_root(
        meta, 1980, 2026, {"https://dblp.org/db/journals/aeog/aeog94.html"})
    urls = [r["url"] for r in res]
    assert urls == [
        "https://dblp.org/db/journals/aeog/aeog106.html",
        "https://dblp.org/db/journals/aeog/aeog107.html",
    ]
    assert [r["name"] for r in res] == ["IJAEO2021", "IJAEO2022"]


def test_conf_discovers_new_satellite_page_for_recorded_year():
    """SIGSPATIAL-style satellite workshop pages carry no '-' in the filename;
    a newly added one must still be discovered for a recorded year."""
    page = """
    <ul>
    <li><a href="https://dblp.org/db/conf/gis/gis2012.html">GIS 2012</a></li>
    <li><a href="https://dblp.org/db/conf/gis/lbsn2012.html">LBSN 2012</a></li>
    <li><a href="https://dblp.org/db/conf/gis/gis2013.html">GIS 2013</a></li>
    </ul>
    """
    inst = _discovery([{"name": "SIGSPATIAL2012",
                        "url": "https://dblp.org/db/conf/gis/gis2012.html"}])
    inst._get_text = lambda url, **kw: page
    meta = {"root": "https://dblp.org/db/conf/gis/index.html", "name": "SIGSPATIAL"}
    res = inst._discover_conf_from_root(
        meta, 1980, 2026, {"https://dblp.org/db/conf/gis/gis2012.html"})
    urls = [r["url"] for r in res]
    assert urls == [
        "https://dblp.org/db/conf/gis/lbsn2012.html",
        "https://dblp.org/db/conf/gis/gis2013.html",
    ]


def test_duplicate_links_on_page_are_not_added_twice():
    page = """
    <ul>
    <li>2021: Volumes <a href="https://dblp.org/db/journals/aeog/aeog94.html">94</a>,
    <a href="https://dblp.org/db/journals/aeog/aeog94.html">94 again</a></li>
    </ul>
    """
    inst = _discovery([])
    inst._get_text = lambda url, **kw: page
    meta = {"root": "https://dblp.org/db/journals/aeog/index.html", "name": "IJAEO"}
    res = inst._discover_journal_from_root(meta, 1980, 2026, set())
    assert [r["url"] for r in res] == ["https://dblp.org/db/journals/aeog/aeog94.html"]


def test_mirror_host_existing_entry_still_deduped():
    """Entries recorded with the dblp.uni-trier.de mirror host (e.g. IJCV2020)
    must still be recognised when the index links the canonical dblp.org URL."""
    page = """
    <ul>
    <li>Volume 128: 2020 <a href="https://dblp.org/db/journals/ijcv/ijcv128.html">link</a></li>
    <li>Volume 131: 2023 <a href="https://dblp.org/db/journals/ijcv/ijcv131.html">link</a></li>
    </ul>
    """
    inst = _discovery([{"name": "IJCV2020",
                        "url": "https://dblp.uni-trier.de/db/journals/ijcv/ijcv128.html"}])
    inst._get_text = lambda url, **kw: page
    meta = {"root": "https://dblp.org/db/journals/ijcv/index.html", "name": "IJCV"}
    res = inst._discover_journal_from_root(
        meta, 1980, 2026, {"https://dblp.uni-trier.de/db/journals/ijcv/ijcv128.html"})
    assert [r["url"] for r in res] == ["https://dblp.org/db/journals/ijcv/ijcv131.html"]

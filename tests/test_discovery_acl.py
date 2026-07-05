"""Unit tests for [discovery/acl.py](../discovery/acl.py) — pins down the fix for
the ACL2026 `^P26-*` regression.

The bug: the pre-fix `_detect_event_prefix` only recognised the legacy
Anthology paper-ID scheme (`/P05-1001/`) and fell back to a hard-coded
`P{yy}` for anything modern, which produced the unusable tag `^P26-*`
on ACL 2026 (whose real paper hrefs are `/2026.acl-long.N/`).

These tests exercise the discovery layer in isolation by stubbing the
network fetchers, so they run purely in-process with no real HTTP.
"""

from __future__ import annotations

from typing import Dict

import pytest

from discovery.acl import ACLDiscovery, _tag_for


def _modern_event_html(year: int, venue_id: str, tracks=("long", "short", "demo", "srw")) -> str:
    """Build a minimal ACL Anthology event page that only contains modern
    (2020+) paper hrefs like `/2026.acl-long.1/`.
    """
    parts = ["<html><body>"]
    for track in tracks:
        for idx in range(1, 4):
            href = f"/{year}.{venue_id}-{track}.{idx}/"
            parts.append(f'<strong><a href="{href}">Paper {idx}</a></strong>')
    parts.append("</body></html>")
    return "\n".join(parts)


def _legacy_event_html(prefix: str) -> str:
    """Build a minimal event page that only uses the legacy `/Xyy-NNNN/`
    scheme (pre-2020 Anthology behaviour).
    """
    parts = ["<html><body>"]
    for idx in range(1, 6):
        href = f"/{prefix}-{1000 + idx}/"
        parts.append(f'<strong><a href="{href}">Paper {idx}</a></strong>')
    parts.append("</body></html>")
    return "\n".join(parts)


class _FakeACLDiscovery(ACLDiscovery):
    """Subclass that replaces the HTTP layer with a fixture dict."""

    def __init__(self, url_to_text: Dict[str, str], head_ok_urls=None, existing_conf=None):
        super().__init__(existing_conf=existing_conf)
        self._url_to_text = url_to_text
        self._head_ok_urls = set(head_ok_urls or [])

    def _get_text(self, url: str, timeout: int = 15, retries: int = 2) -> str:  # noqa: D401
        return self._url_to_text.get(url, "")

    def _head_ok(self, url: str, timeout: int = 10) -> bool:  # noqa: D401
        return url in self._head_ok_urls


# ---------------------------------------------------------------------------
# 1. Modern (2020+) format detection — the actual bug fix
# ---------------------------------------------------------------------------

def test_detect_event_prefix_modern_acl_2026_uses_year_venue_tag():
    """The core regression: ACL 2026's paper hrefs are `/2026.acl-long.N/`,
    so the detected tag body MUST collapse to `2026.acl` (→ `^2026.acl*`),
    NOT the legacy `P26-` / `^P26-*` output.
    """
    event_url = "https://aclanthology.org/events/acl-2026/"
    disc = _FakeACLDiscovery({event_url: _modern_event_html(2026, "acl")})

    body = disc._detect_event_prefix(event_url, venue_id="acl", year=2026)
    assert body == "2026.acl"
    assert _tag_for(body) == "^2026.acl*"


def test_detect_event_prefix_modern_matches_existing_2024_2025_tags():
    """Same rule applied to 2024 / 2025 must reproduce the tags already
    committed by humans in `conf/acl_conf.json`, i.e. `^2024.acl*` /
    `^2025.acl*`."""
    for year in (2024, 2025):
        event_url = f"https://aclanthology.org/events/acl-{year}/"
        disc = _FakeACLDiscovery({event_url: _modern_event_html(year, "acl")})
        body = disc._detect_event_prefix(event_url, venue_id="acl", year=year)
        assert _tag_for(body) == f"^{year}.acl*"


def test_detect_event_prefix_modern_ignores_cross_venue_stems():
    """A 2024 EMNLP event page that happens to reference `/2024.acl-long.1/`
    (e.g. a cross-conference citation) must still land on `2024.emnlp`."""
    event_url = "https://aclanthology.org/events/emnlp-2024/"
    html = _modern_event_html(2024, "emnlp") + \
        '\n<strong><a href="/2024.acl-long.1/">Cross ref</a></strong>'
    disc = _FakeACLDiscovery({event_url: html})

    body = disc._detect_event_prefix(event_url, venue_id="emnlp", year=2024)
    assert body == "2024.emnlp"


# ---------------------------------------------------------------------------
# 2. Legacy (≤2019) format still works — byte-for-byte compatibility guard
# ---------------------------------------------------------------------------

def test_detect_event_prefix_legacy_p05_stays_p05():
    event_url = "https://aclanthology.org/events/acl-2005/"
    disc = _FakeACLDiscovery({event_url: _legacy_event_html("P05")})
    body = disc._detect_event_prefix(event_url, venue_id="acl", year=2005)
    assert body == "P05-"
    assert _tag_for(body) == "^P05-*"


def test_detect_event_prefix_returns_empty_on_empty_page():
    event_url = "https://aclanthology.org/events/acl-1999/"
    disc = _FakeACLDiscovery({event_url: "<html><body></body></html>"})
    assert disc._detect_event_prefix(event_url, venue_id="acl", year=1999) == ""


# ---------------------------------------------------------------------------
# 3. Fallback contract — never re-emit the old `^P26-*` shape for 2020+
# ---------------------------------------------------------------------------

def test_fallback_prefix_modern_year_uses_new_scheme():
    disc = _FakeACLDiscovery({})
    body = disc._fallback_prefix("acl", 2026)
    assert body == "2026.acl"
    assert _tag_for(body) == "^2026.acl*"


def test_fallback_prefix_legacy_year_preserves_old_scheme():
    disc = _FakeACLDiscovery({})
    body = disc._fallback_prefix("acl", 2005)
    assert body == "P05-"
    assert _tag_for(body) == "^P05-*"


# ---------------------------------------------------------------------------
# 4. End-to-end discover() smoke test on a fixture Anthology
# ---------------------------------------------------------------------------

def test_discover_generates_correct_acl2026_tag(monkeypatch: pytest.MonkeyPatch):
    """Full path: venues page → events page → detected tag.
    Guards against a re-introduction of the `^P26-*` bug."""
    venue_page = "https://aclanthology.org/venues/acl/"
    event_page = "https://aclanthology.org/events/acl-2026/"
    findings_page = "https://aclanthology.org/volumes/2026.findings-acl/"

    venue_html = (
        '<html><body>'
        f'<a href="/events/acl-2026/">ACL 2026</a>'
        '</body></html>'
    )
    disc = _FakeACLDiscovery(
        url_to_text={
            venue_page: venue_html,
            event_page: _modern_event_html(2026, "acl"),
        },
        head_ok_urls={findings_page},
    )

    # Only exercise the ACL venue for this test.
    monkeypatch.setattr("discovery.acl.CORE_VENUES", {"acl": "ACL"})

    results = disc.discover(2026, 2026)
    tags = [(r["name"], r["tag"], r["url"]) for r in results]

    assert ("ACL2026", "^2026.acl*", event_page) in tags
    assert ("ACL2026", "^2026.findings*", findings_page) in tags
    # Regression pin: the broken tag MUST NOT reappear.
    assert not any(t == "^P26-*" for _, t, _ in tags)


# ---------------------------------------------------------------------------
# 5. URL-based dedup — pin the fix for the "only the broken main-conf entry
#    was deleted, findings entry survived" real-world scenario.
# ---------------------------------------------------------------------------

def test_discover_rewrites_main_conf_when_only_findings_entry_survived(
    monkeypatch: pytest.MonkeyPatch,
):
    """`conf/acl_conf.json` still has a `name=ACL2026` findings entry
    after we manually deleted the broken `^P26-*` main-conf entry.
    Under the old name-based dedup, `existing_names={"ACL2026"}` would
    make discovery `continue` past the main-conf event and never rewrite
    the missing tag. URL-based dedup fixes this.
    """
    venue_page = "https://aclanthology.org/venues/acl/"
    event_page = "https://aclanthology.org/events/acl-2026/"
    findings_page = "https://aclanthology.org/volumes/2026.findings-acl/"

    venue_html = (
        '<html><body>'
        f'<a href="/events/acl-2026/">ACL 2026</a>'
        '</body></html>'
    )
    existing = [
        # Simulates the state of conf/acl_conf.json after Task 2's edit:
        # findings entry survived, main-conf entry was deleted.
        {"name": "ACL2026", "tag": "^2026.findings*", "url": findings_page},
    ]
    disc = _FakeACLDiscovery(
        url_to_text={
            venue_page: venue_html,
            event_page: _modern_event_html(2026, "acl"),
        },
        head_ok_urls={findings_page},
        existing_conf=existing,
    )
    monkeypatch.setattr("discovery.acl.CORE_VENUES", {"acl": "ACL"})

    results = disc.discover(2026, 2026)
    # Main-conf entry with the CORRECT modern tag must now be produced.
    assert any(
        r["url"] == event_page and r["tag"] == "^2026.acl*"
        for r in results
    ), f"main-conf ACL2026 entry not regenerated: {results!r}"
    # Findings must NOT be duplicated (URL already present in `existing`).
    assert not any(r["url"] == findings_page for r in results), \
        f"findings duplicated in results: {results!r}"

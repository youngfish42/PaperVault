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


# ---------------------------------------------------------------------------
# 6. Single-track modern page — pins the _longest_common_prefix degeneracy fix.
# ---------------------------------------------------------------------------

def test_detect_event_prefix_modern_single_track_still_collapses_to_venue():
    """When an event page exposes only ONE modern track (e.g. `/2026.acl-long.N/`
    but short/demo/srw haven't been published yet), the detected tag body MUST
    still collapse to `2026.acl`, NOT the single-track stem `2026.acl-long`.

    Regression pin for the `_longest_common_prefix([single]) → full string`
    degeneracy, which would otherwise emit an overly narrow `^2026.acl-long*`
    tag that misses volumes uploaded later.
    """
    event_url = "https://aclanthology.org/events/acl-2026/"
    html = _modern_event_html(2026, "acl", tracks=("long",))  # only one track
    disc = _FakeACLDiscovery({event_url: html})

    body = disc._detect_event_prefix(event_url, venue_id="acl", year=2026)
    assert body == "2026.acl", (
        f"single-track detection must collapse to '2026.acl', got {body!r}"
    )
    assert _tag_for(body) == "^2026.acl*"


def test_detect_event_prefix_defensive_fallback_when_venue_id_missing():
    """Defence-in-depth pin: when a caller invokes `_detect_event_prefix`
    WITHOUT passing `venue_id` (public signature default = ""), the
    `same_venue` filter list is empty by construction and control flow
    reaches the terminal `else` branch. That branch must still peel the
    track segment off the stem (via `rsplit("-", 1)[0]`), so a modern
    single-track page yields the venue-level body `2026.acl`, not the
    over-narrow `2026.acl-long` that a naive `rstrip("-")` would return.

    Guards the public signature `_detect_event_prefix(url)` against
    silently re-introducing the same single-track LCP degeneracy that the
    `venue_id`-aware branch was hardened against.
    """
    event_url = "https://aclanthology.org/events/acl-2026/"
    html = _modern_event_html(2026, "acl", tracks=("long",))
    disc = _FakeACLDiscovery({event_url: html})

    body = disc._detect_event_prefix(event_url)  # no venue_id / year
    assert body == "2026.acl", (
        f"defensive else branch must peel track segment, got {body!r}"
    )
    assert _tag_for(body) == "^2026.acl*"


# ---------------------------------------------------------------------------
# 7. URL normalization on existing_urls — pins the _canon_url dedup fix.
# ---------------------------------------------------------------------------

def test_discover_dedupes_across_url_variants(monkeypatch: pytest.MonkeyPatch):
    """`_canon_url` should let discovery recognise that a hand-edited
    `conf/acl_conf.json` entry with an alternative URL form (no trailing
    slash, `http://` scheme, mixed-case host) is equivalent to the freshly
    discovered canonical URL, and therefore must NOT re-emit a duplicate
    main-conf entry.
    """
    venue_page = "https://aclanthology.org/venues/acl/"
    event_page = "https://aclanthology.org/events/acl-2025/"

    venue_html = (
        '<html><body>'
        f'<a href="/events/acl-2025/">ACL 2025</a>'
        '</body></html>'
    )
    # Existing entry with a de-canonical URL variant (no trailing slash,
    # mixed-case host). Under the old identity-string dedup this would look
    # different and cause a duplicate re-emit.
    existing = [
        {
            "name": "ACL2025",
            "tag": "^2025.acl*",
            "url": "https://ACLAnthology.org/events/acl-2025",
        }
    ]
    disc = _FakeACLDiscovery(
        url_to_text={
            venue_page: venue_html,
            event_page: _modern_event_html(2025, "acl"),
        },
        head_ok_urls=set(),
        existing_conf=existing,
    )
    monkeypatch.setattr("discovery.acl.CORE_VENUES", {"acl": "ACL"})

    results = disc.discover(2025, 2025)
    # No duplicate main-conf entry for the same canonical event URL.
    assert not any(r["url"] == event_page for r in results), (
        f"main-conf duplicated across URL variants: {results!r}"
    )


def test_canon_url_normalizes_case_and_trailing_slash():
    """`_canon_url` must equate the four hand-edit friendly variants."""
    canon = "https://aclanthology.org/events/acl-2026"
    assert ACLDiscovery._canon_url("https://aclanthology.org/events/acl-2026/") == canon
    assert ACLDiscovery._canon_url("https://ACLAnthology.org/events/acl-2026/") == canon
    assert ACLDiscovery._canon_url("  https://aclanthology.org/events/acl-2026  ") == canon
    assert ACLDiscovery._canon_url(None) == ""


# ---------------------------------------------------------------------------
# 8. Collector-side: zero-result soft-failure MUST NOT write progress.
# ---------------------------------------------------------------------------

def test_collector_acl_loop_gates_progress_write_on_nonzero_delta():
    """Structural pin: in the ACL collection loop in `collector.py`,
    the `progress[f"ACL::{url}"] = {...}` write MUST live under the `else`
    branch of `if after == before:`, not at the same indent level as the
    `if`/`else`.

    Rationale: if the write escapes the `else`, the zero-result soft-failure
    path stamps progress unconditionally and `_should_skip` will then skip
    the URL on every subsequent run — silently defeating the tag fix on the
    discovery side.

    A behavioural test would need to reload the entire `collect()` pipeline
    (which reads `conf/*.json` directly, runs `_save_state`'s 5-second
    debounce, calls `_merge_with_cache` and `add_code_links`, and depends
    on HF sync). A focused source-shape assertion is the cheapest and most
    stable regression pin for this specific bug.
    """
    from pathlib import Path
    import re

    collector_src = Path(__file__).resolve().parent.parent / "collector.py"
    text = collector_src.read_text(encoding="utf-8")

    # Locate the ACL loop block and its `if after == before:` branch.
    m = re.search(
        r"for conf in tqdm\(acl_conf,.*?\n"
        r"(?P<block>.*?)(?=^\s*except Exception as e:\n)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    assert m, "could not locate ACL collection loop in collector.py"
    block = m.group("block")

    # The progress-write line must appear indented one level deeper than
    # the `if after == before:` guard — i.e. under `else:`, not at the
    # `if`/`else` indent.
    assert re.search(
        r"^            else:\n\s+progress\[f\"ACL::\{url\}\"\]",
        block,
        re.MULTILINE,
    ), (
        "collector.py ACL loop must write progress[f'ACL::{url}'] under an "
        "`else:` branch of `if after == before:`; got block:\n" + block
    )

    # And it must NOT appear at the outer indent level (which would mean it
    # runs on the zero-result path too).
    assert not re.search(
        r"^            progress\[f\"ACL::\{url\}\"\]",
        block,
        re.MULTILINE,
    ), (
        "progress write must be gated by `else:`, but found it at outer "
        "indent (would run on zero-result path):\n" + block
    )

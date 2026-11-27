from __future__ import annotations

import pytest

from discovery.openreview import OpenReviewDiscovery


ACCEPTED_SCHEDULE_HTML = """
<div class="maincard poster">
  <div class="maincardBody">A paper</div>
  <a href="https://openreview.net/forum?id=paper1">OpenReview</a>
</div>
"""


def test_iclr_schedule_fallback_when_openreview_is_challenged(monkeypatch: pytest.MonkeyPatch):
    discovery = OpenReviewDiscovery()

    def fail_fetch(invitation_tpl, year):
        raise RuntimeError("403 ChallengeRequiredError")

    monkeypatch.setattr(discovery, "_fetch_venues", fail_fetch)
    monkeypatch.setattr(
        discovery,
        "_get_text",
        lambda url, timeout, retries: ACCEPTED_SCHEDULE_HTML,
    )

    assert discovery.discover(2026, 2026) == [
        {
            "name": "ICLR2026",
            "url": "https://iclr.cc/Conferences/2026/Schedule",
        }
    ]


def test_iclr_schedule_fallback_when_venue_count_is_challenged(monkeypatch: pytest.MonkeyPatch):
    discovery = OpenReviewDiscovery()

    def venues(invitation_tpl, year):
        if invitation_tpl.startswith("ICLR"):
            return [f"ICLR {year} Poster"]
        return []

    def fail_count(venue, invitation, year):
        raise RuntimeError("403 ChallengeRequiredError")

    monkeypatch.setattr(discovery, "_fetch_venues", venues)
    monkeypatch.setattr(discovery, "_count_for_venue", fail_count)
    monkeypatch.setattr(
        discovery,
        "_get_text",
        lambda url, timeout, retries: ACCEPTED_SCHEDULE_HTML,
    )

    assert discovery.discover(2026, 2026) == [
        {
            "name": "ICLR2026",
            "url": "https://iclr.cc/Conferences/2026/Schedule",
        }
    ]


def test_empty_iclr_schedule_does_not_create_config(monkeypatch: pytest.MonkeyPatch):
    discovery = OpenReviewDiscovery()
    monkeypatch.setattr(discovery, "_fetch_venues", lambda invitation_tpl, year: [])
    monkeypatch.setattr(
        discovery,
        "_get_text",
        lambda url, timeout, retries: """
            <div class="maincard workshop">
              <a href="https://openreview.net/forum?id=workshop">Workshop</a>
            </div>
            <div class="maincard poster">
              <a href="https://iclr.cc/">No paper link</a>
            </div>
        """,
    )

    assert discovery.discover(2027, 2027) == []


def test_successful_openreview_discovery_does_not_use_schedule(monkeypatch: pytest.MonkeyPatch):
    discovery = OpenReviewDiscovery()

    def venues(invitation_tpl, year):
        if invitation_tpl.startswith("ICLR"):
            return [f"ICLR {year} Poster"]
        return []

    monkeypatch.setattr(discovery, "_fetch_venues", venues)
    monkeypatch.setattr(discovery, "_count_for_venue", lambda venue, invitation, year: 1001)

    def fail_schedule(url, timeout, retries):
        raise AssertionError("official schedule should not be requested")

    monkeypatch.setattr(discovery, "_get_text", fail_schedule)

    result = discovery.discover(2026, 2026)
    assert [item["name"] for item in result] == ["ICLR2026", "ICLR2026"]
    assert "offset=0" in result[0]["url"]
    assert "offset=1000" in result[1]["url"]


def test_existing_iclr_schedule_is_reused_without_download(monkeypatch: pytest.MonkeyPatch):
    entry = {
        "name": "ICLR2026",
        "url": "https://iclr.cc/Conferences/2026/Schedule",
    }
    discovery = OpenReviewDiscovery(existing_conf=[entry])

    def fail_download(url, timeout, retries):
        raise AssertionError("existing schedule should not be downloaded again")

    monkeypatch.setattr(discovery, "_get_text", fail_download)

    assert discovery._discover_iclr_schedule(2026) == entry

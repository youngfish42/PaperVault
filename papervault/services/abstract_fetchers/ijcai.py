"""IJCAI (``ijcai.org/proceedings/...``) abstract fetcher.

Empirical DOM (verified against
``https://www.ijcai.org/proceedings/{2017,2019,2023,2024}/0001`` on
2026-07-12) — the abstract is served **without** an ``<h2>Abstract</h2>``
heading and **without** the literal ``Abstract`` prefix. The relevant
region always looks like::

    <div class="row">
        <div class="col-md-12">
            <full abstract prose>
        </div>
        <div class="col-md-12">
            <div class="keywords">Keywords: ...</div>
        </div>
    </div>

The previous heuristic ("first ``col-md-12`` starting with the literal
'Abstract'") therefore **never matches on real IJCAI pages** — every
production abstract was silently missing.

Strategy (priority order):

1. **Real-layout probe** (primary). Iterate every ``<div class="col-md-12">``
   in document order, skip any that contain a ``<div class="keywords">``
   subtree (that's the keywords row), and return the first remaining
   block whose cleaned text length ≥ ``MIN_ABSTRACT_CHARS``. Also strip
   a leading literal ``Abstract`` (case-insensitive) if some historical
   / mirror page ever wraps the body that way.

2. **``<h2>Abstract</h2>`` heading fallback**. Kept for mirrored /
   archived variants that DO carry an explicit heading. Concatenates
   *only* following-sibling paragraphs up to the next heading (h1..h6)
   so BibTeX / References sections stay out of the abstract text.

3. **Legacy ``col-md-12`` "startswith Abstract" fallback**. Kept for
   backwards compatibility with the pre-empirical implementation, and
   requires the trailing body (after stripping the label) to be at
   least ``MIN_ABSTRACT_CHARS`` chars.
"""

from __future__ import annotations

from bs4 import BeautifulSoup

from .base import AbstractResult, Fetcher, MIN_ABSTRACT_CHARS
from ._http import clean_text, http_get


_HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")


def _looks_like_keywords_block(div) -> bool:
    """A ``col-md-12`` that hosts the keywords row on real IJCAI pages
    always contains ``<div class="keywords">`` as (typically) its only
    child. Detect it by class-lookup so we can skip it while probing
    for the abstract body.
    """
    return div.find("div", class_="keywords") is not None


def _strip_leading_abstract_label(text: str) -> str:
    """Some mirror / legacy IJCAI pages prefix the body with the literal
    ``Abstract`` (case-insensitive). Strip it defensively so the returned
    prose never opens with the label word itself.
    """
    if text.lower().startswith("abstract"):
        remainder = text[len("Abstract"):].lstrip(" :.-\n\t")
        if remainder:
            return remainder
    return text


def _abstract_from_real_layout(soup: BeautifulSoup) -> str:
    """Primary strategy: pick the first non-keywords ``col-md-12`` whose
    text meets the length gate. This mirrors the DOM shape produced by
    the live IJCAI proceedings site since at least 2017.
    """
    for div in soup.find_all("div", class_="col-md-12"):
        if _looks_like_keywords_block(div):
            continue
        text = clean_text(div.get_text(" "))
        if not text:
            continue
        text = _strip_leading_abstract_label(text)
        if len(text) >= MIN_ABSTRACT_CHARS:
            return text
    return ""


def _abstract_from_heading(soup: BeautifulSoup) -> str:
    """Fallback for mirrored / archived pages that carry an explicit
    ``<h2>Abstract</h2>`` heading.

    Stops at the *next* heading (h1..h6) to avoid slurping BibTeX,
    References, footer nav, etc. (Review issue R3 from the second
    review pass — the previous implementation had no break condition
    and would concatenate every following sibling.)
    """
    for heading in soup.find_all(_HEADING_TAGS):
        label = clean_text(heading.get_text(" "))
        if label.lower().strip(" :.-") != "abstract":
            continue
        parts = []
        for sibling in heading.find_next_siblings():
            # Stop as soon as another heading appears — everything past
            # it belongs to a different section.
            name = getattr(sibling, "name", None)
            if name in _HEADING_TAGS:
                break
            piece = clean_text(sibling.get_text(" "))
            if piece:
                parts.append(piece)
        text = " ".join(parts).strip()
        if text:
            return text
    return ""


def _abstract_from_startswith(soup: BeautifulSoup) -> str:
    """Legacy fallback: any ``col-md-12`` whose text *starts with* the
    literal ``Abstract`` label. Kept as a defensive last resort — the
    empirical primary strategy above should already handle every real
    IJCAI page. Requires the body (after the label) to satisfy the
    length gate so a bare ``<h2>Abstract</h2>`` inside its own div
    does not slip through.
    """
    for div in soup.find_all("div", class_="col-md-12"):
        raw = clean_text(div.get_text(" "))
        if not raw.lower().startswith("abstract"):
            continue
        remainder = raw[len("Abstract"):].strip(" :.-")
        if len(remainder) >= MIN_ABSTRACT_CHARS:
            return remainder
    return ""


class _IJCAIFetcher(Fetcher):
    allowed_hosts = ("ijcai.org",)
    source = "ijcai"

    def fetch(self, url: str) -> AbstractResult:
        resp, err = http_get(url)
        if err is not None:
            return err
        soup = BeautifulSoup(resp.text, "html.parser")

        text = (
            _abstract_from_real_layout(soup)
            or _abstract_from_heading(soup)
            or _abstract_from_startswith(soup)
        )

        if not text or len(text) < MIN_ABSTRACT_CHARS:
            return AbstractResult(
                ok=False, url=url, source=self.source,
                reason="empty_abstract", http_status=resp.status_code,
            )
        return AbstractResult(
            ok=True, url=url, abstract=text, source=self.source,
            http_status=resp.status_code,
        )


IJCAI_FETCHER = _IJCAIFetcher()

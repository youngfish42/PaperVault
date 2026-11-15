"""IJCAI proceedings abstract extraction for current and archived layouts."""

from __future__ import annotations

from bs4 import BeautifulSoup

from .base import AbstractResult, Fetcher, MIN_ABSTRACT_CHARS
from ._http import clean_text, http_get, strip_abstract_label


_HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")


def _strip_leading_abstract_label(text: str) -> str:
    if text.lower().startswith("abstract"):
        remainder = text[len("Abstract"):].lstrip(" :.-\n\t")
        if remainder:
            return remainder
    return text


def _abstract_from_current_layout(soup: BeautifulSoup) -> str:
    for node in soup.select("div.article__abstract"):
        strip_abstract_label(node, tags=(*_HEADING_TAGS, "p"))
        text = clean_text(node.get_text(" "))
        if len(text) >= MIN_ABSTRACT_CHARS:
            return text
    return ""


def _looks_like_keywords_block(div) -> bool:
    return div.find("div", class_="keywords") is not None


def _abstract_from_real_layout(soup: BeautifulSoup) -> str:
    for div in soup.find_all("div", class_="col-md-12"):
        if _looks_like_keywords_block(div):
            continue
        text = _strip_leading_abstract_label(clean_text(div.get_text(" ")))
        if len(text) >= MIN_ABSTRACT_CHARS:
            return text
    return ""


def _abstract_from_heading(soup: BeautifulSoup) -> str:
    for heading in soup.find_all(_HEADING_TAGS):
        label = clean_text(heading.get_text(" "))
        if label.lower().strip(" :.-") != "abstract":
            continue
        parts = []
        for sibling in heading.find_next_siblings():
            if getattr(sibling, "name", None) in _HEADING_TAGS:
                break
            piece = clean_text(sibling.get_text(" "))
            if piece:
                parts.append(piece)
        text = " ".join(parts).strip()
        if text:
            return text
    return ""


def _abstract_from_startswith(soup: BeautifulSoup) -> str:
    for div in soup.find_all("div", class_="col-md-12"):
        raw = clean_text(div.get_text(" "))
        if not raw.lower().startswith("abstract"):
            continue
        remainder = raw[len("Abstract"):].strip(" :.-")
        if len(remainder) >= MIN_ABSTRACT_CHARS:
            return remainder
    return ""


def extract_ijcai_abstract(soup: BeautifulSoup) -> str:
    return (
        _abstract_from_current_layout(soup)
        or _abstract_from_real_layout(soup)
        or _abstract_from_heading(soup)
        or _abstract_from_startswith(soup)
    )


class _IJCAIFetcher(Fetcher):
    allowed_hosts = ("ijcai.org",)
    source = "ijcai"

    def fetch(self, url: str) -> AbstractResult:
        resp, err = http_get(url)
        if err is not None:
            return err
        text = extract_ijcai_abstract(BeautifulSoup(resp.text, "html.parser"))
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

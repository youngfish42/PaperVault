from __future__ import annotations

import re
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from collector.http import HEADERS, SESSION
from collector.merge import _merge_paper_record

MICCAI_OPENACCESS_HOST = "papers.miccai.org"
DEFAULT_CONCURRENCY = 8


class MICCAICollectionError(RuntimeError):
    def __init__(self, failures, papers):
        self.failures = failures
        self.papers = papers
        details = "; ".join(f"{url}: {error}" for url, error in failures[:3])
        super().__init__(f"{len(failures)} MICCAI detail page(s) failed: {details}")


def normalize_miccai_title(title: str) -> str:
    normalized = re.sub(
        r"^\[inline-graphic[^\]]*\]", "", title or "", flags=re.IGNORECASE
    )
    normalized = re.sub(r"\\(?:upbeta|beta)\b", "β", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\\(?:upmu|mu)\b", "μ", normalized, flags=re.IGNORECASE)
    normalized = re.sub(
        r"\\(?:mathrm|mathbf|mathit|mathsf|mathtt|mathcal|mathbb|textbf|textit|texttt|emph|underline|text|textrm)\b",
        "",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = unicodedata.normalize("NFKC", normalized).casefold()
    return re.sub(r"[^\w]+", "", normalized, flags=re.UNICODE)


def is_doi_url(url: str) -> bool:
    host = urlsplit(url or "").netloc.lower()
    return host == "doi.org" or host.endswith(".doi.org")


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def parse_miccai_listing(html: str, base_url: str) -> list[str]:
    soup = _soup(html)
    base_path = urlsplit(base_url).path
    links = []
    seen = set()
    for anchor in soup.find_all("a", href=True):
        if "paper information" not in anchor.get_text(" ", strip=True).lower():
            continue
        detail_url = urljoin(base_url, anchor["href"])
        parsed = urlsplit(detail_url)
        if (
            parsed.netloc.lower() != MICCAI_OPENACCESS_HOST
            or not parsed.path.startswith(base_path)
            or not parsed.path.endswith(".html")
        ):
            continue
        if detail_url not in seen:
            seen.add(detail_url)
            links.append(detail_url)
    if not links:
        raise ValueError(f"No MICCAI paper detail links found at {base_url}")
    return links


def _section_tags(soup: BeautifulSoup, section_id: str) -> list:
    heading = soup.find(id=section_id)
    if heading is None:
        return []
    tags = []
    for sibling in heading.next_siblings:
        if getattr(sibling, "name", None) == "h1":
            break
        if getattr(sibling, "name", None):
            tags.append(sibling)
    return tags


def _section_text(tags: list) -> str:
    return " ".join(
        text for tag in tags if (text := tag.get_text(" ", strip=True))
    ).strip()


def parse_miccai_detail(html: str, detail_url: str) -> dict:
    soup = _soup(html)
    title_node = soup.select_one(".post-title h1")
    title = title_node.get_text(" ", strip=True) if title_node else ""
    abstract_tags = _section_tags(soup, "abstract-id")
    if not title or not abstract_tags:
        raise ValueError(f"Missing MICCAI title or Abstract section at {detail_url}")

    authors = []
    for anchor in soup.select(".post-tags a.post-category"):
        author = anchor.get_text(" ", strip=True).strip(" |")
        if author and author not in authors:
            authors.append(author)

    code = "#"
    for tag in _section_tags(soup, "code-id"):
        for anchor in tag.find_all("a", href=True):
            href = urljoin(detail_url, anchor["href"].strip())
            if urlsplit(href).scheme in {"http", "https"}:
                code = href
                break
        if code != "#":
            break

    paper_url = detail_url
    for tag in _section_tags(soup, "link-id"):
        for anchor in tag.find_all("a", href=True):
            href = anchor["href"].strip()
            if is_doi_url(href):
                paper_url = href
                break
        if paper_url != detail_url:
            break

    return {
        "paper_name": title,
        "paper_url": paper_url,
        "paper_authors": authors,
        "paper_abstract": _section_text(abstract_tags),
        "paper_code": code,
    }


def _get_html(url: str) -> str:
    response = SESSION.get(url, headers=HEADERS, timeout=(10, 60))
    response.raise_for_status()
    response.encoding = "utf-8"
    return response.text


def collect_miccai_papers(
    listing_url: str,
    *,
    concurrency: int = DEFAULT_CONCURRENCY,
    limit: Optional[int] = None,
) -> list[dict]:
    detail_urls = parse_miccai_listing(_get_html(listing_url), listing_url)
    if limit is not None:
        detail_urls = detail_urls[: max(0, limit)]

    papers_by_url = {}
    failures = []
    workers = max(1, concurrency)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_get_html, url): url for url in detail_urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                papers_by_url[url] = parse_miccai_detail(future.result(), url)
            except Exception as exc:
                failures.append((url, str(exc)))

    papers = [papers_by_url[url] for url in detail_urls if url in papers_by_url]
    if failures:
        raise MICCAICollectionError(failures, papers)
    return papers


def search_from_miccai(url, name, res):
    res.setdefault(name, [])
    papers = collect_miccai_papers(url)
    res[name].extend(papers)
    return res


def merge_miccai_records(records: list[dict], official_records: list[dict]) -> list[dict]:
    title_counts = Counter(
        key for record in official_records
        if (key := normalize_miccai_title(record.get("paper_name", "")))
    )
    eligible_titles = {key for key, count in title_counts.items() if count == 1}

    merged = []
    url_index = {}
    title_index = {}
    for record in records:
        url = record.get("paper_url") or ""
        title_key = normalize_miccai_title(record.get("paper_name", ""))
        prior = url_index.get(url) if url else None
        title_match = False
        if prior is None and title_key in eligible_titles:
            prior = title_index.get(title_key)
            title_match = prior is not None
        if prior is not None:
            previous_url = prior.get("paper_url") or ""
            incoming_url = record.get("paper_url") or ""
            _merge_paper_record(prior, record)
            if title_match:
                if is_doi_url(incoming_url):
                    prior["paper_url"] = incoming_url
                elif not is_doi_url(previous_url):
                    prior["paper_url"] = previous_url or incoming_url
            continue

        merged.append(record)
        if url:
            url_index[url] = record
        if title_key in eligible_titles:
            title_index[title_key] = record
    return merged

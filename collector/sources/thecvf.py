from __future__ import annotations

import re
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from collector.http import SESSION, HEADERS
from collector.merge import _merge_paper_record
from collector.sources.miccai import is_doi_url

CVF_HOST = "openaccess.thecvf.com"
DEFAULT_CONCURRENCY = 4
CVF_TIMEOUT = (10, 40)
_DAY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def canonicalize_cvf_url(url: str) -> str:
    parsed = urlsplit((url or "").strip())
    if parsed.netloc.lower() != CVF_HOST:
        return (url or "").strip()
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    return urlunsplit(("https", CVF_HOST, path, parsed.query, parsed.fragment))


def is_cvf_paper_url(url: str) -> bool:
    try:
        parsed = urlsplit(canonicalize_cvf_url(url))
    except Exception:
        return False
    host = parsed.netloc.lower()
    return (
        host == CVF_HOST
        and "/html/" in parsed.path
        and re.search(r"_paper(?:original)?\.html$", parsed.path, re.IGNORECASE) is not None
    )


def clean_cvf_abstract(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", "", text).strip()
    text = re.sub(r"-\r?\n\s*", "", text)
    text = re.sub(r"\r?\n\s*([a-z0-9])", r" \1", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_cvf_abstract(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    node = soup.find(id="abstract")
    if node is not None:
        cleaned = clean_cvf_abstract(node.get_text(" ", strip=True))
        if cleaned:
            return cleaned
    for attrs in ({"name": "description"}, {"property": "og:description"}):
        tag = soup.find("meta", attrs=attrs)
        if tag is not None:
            cleaned = clean_cvf_abstract(tag.get("content", "") or "")
            if len(cleaned) >= 40:
                return cleaned
    return ""


def search_abs_from_thecvf(url, timeout=CVF_TIMEOUT, session=None):
    response = (session or SESSION).get(url, headers=HEADERS, timeout=timeout)
    response.raise_for_status()
    response.encoding = "utf-8"
    return parse_cvf_abstract(response.text)


def parse_cvf_listing(html: str, base_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    papers = []
    for item in soup.find_all("dt", class_="ptitle"):
        anchor = item.find("a", href=True)
        if anchor is None:
            continue
        paper_url = canonicalize_cvf_url(urljoin(base_url, anchor["href"]))
        if not is_cvf_paper_url(paper_url):
            continue
        title = anchor.get_text(" ", strip=True)
        if not title:
            continue
        authors = []
        author_row = item.find_next_sibling("dd")
        if author_row is not None:
            authors = [
                author.get_text(" ", strip=True)
                for author in author_row.find_all("a", href="#")
                if author.get_text(" ", strip=True)
            ]
        papers.append({
            "paper_name": title,
            "paper_url": paper_url,
            "paper_authors": authors,
            "paper_abstract": "",
            "paper_code": "#",
        })
    return papers


def _event_path(url: str) -> str:
    path = urlsplit(url).path
    return path[:-3] if path.endswith(".py") else path


def parse_cvf_day_links(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    links = []
    seen = set()
    for anchor in soup.find_all("a", href=True):
        url = canonicalize_cvf_url(urljoin(base_url, anchor["href"]))
        parsed = urlsplit(url)
        if parsed.netloc.lower() != CVF_HOST or _event_path(url) != _event_path(base_url):
            continue
        days = parse_qs(parsed.query).get("day", [])
        if len(days) != 1 or not _DAY_RE.fullmatch(days[0]) or url in seen:
            continue
        seen.add(url)
        links.append(url)
    return links


def _get_html(url: str, session=None) -> str:
    response = (session or SESSION).get(url, headers=HEADERS, timeout=CVF_TIMEOUT)
    response.raise_for_status()
    response.encoding = "utf-8"
    return response.text


def collect_cvf_metadata(url: str) -> list[dict]:
    html = _get_html(url)
    papers = parse_cvf_listing(html, url)
    if not papers:
        for day_url in parse_cvf_day_links(html, url):
            papers.extend(parse_cvf_listing(_get_html(day_url), day_url))
    if not papers:
        raise ValueError(f"No CVF paper entries found at {url}")
    return merge_cvf_records([], papers)


def normalize_cvf_title(title: str) -> str:
    normalized = re.sub(r"\\(?:up)?pi\b", "p", title or "", flags=re.IGNORECASE)
    normalized = re.sub(r"\\(?:up)?lambda\b", "l", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\\[A-Za-z]+\b", "", normalized)
    normalized = re.sub(r"\blambda\b", "l", normalized, flags=re.IGNORECASE)
    normalized = normalized.translate(str.maketrans({"π": "p", "λ": "l", "×": "x"}))
    normalized = unicodedata.normalize("NFKC", normalized)
    normalized = unicodedata.normalize("NFKD", normalized)
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"[\W_]+", "", normalized.casefold(), flags=re.UNICODE)


def merge_cvf_records(records: list[dict], official_records: list[dict]) -> list[dict]:
    title_counts = Counter(
        key for record in official_records
        if (key := normalize_cvf_title(record.get("paper_name", "")))
    )
    eligible_titles = {key for key, count in title_counts.items() if count == 1}
    merged = []
    url_index = {}
    title_index = {}

    for original in [*records, *official_records]:
        record = original
        url = record.get("paper_url") or ""
        lookup_url = canonicalize_cvf_url(url)
        title_key = normalize_cvf_title(record.get("paper_name", ""))
        prior = url_index.get(lookup_url) if lookup_url else None
        title_match = False
        if prior is None and title_key in eligible_titles:
            prior = title_index.get(title_key)
            title_match = prior is not None
        if prior is not None:
            previous_url = prior.get("paper_url") or ""
            _merge_paper_record(prior, record)
            if title_match:
                if is_doi_url(url):
                    prior["paper_url"] = url
                elif is_doi_url(previous_url):
                    prior["paper_url"] = previous_url
                else:
                    prior["paper_url"] = lookup_url or canonicalize_cvf_url(previous_url)
            if previous_url:
                url_index[canonicalize_cvf_url(previous_url)] = prior
            if url:
                url_index[lookup_url] = prior
            continue

        if lookup_url:
            record["paper_url"] = lookup_url
        merged.append(record)
        if lookup_url:
            url_index[lookup_url] = record
        if title_key in eligible_titles:
            title_index[title_key] = record
    return merged


def search_from_thecvf(url, name, res, concurrency=DEFAULT_CONCURRENCY):
    papers = collect_cvf_metadata(url)
    from scripts.cvf_abstract import fetch_cvf_abstract

    def _fetch(paper):
        try:
            return fetch_cvf_abstract(paper["paper_url"])
        except Exception as exc:
            print(f"[!] cvf-abs miss {paper['paper_url']}: {exc}")
            return ""

    workers = max(1, concurrency)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_fetch, paper): paper for paper in papers}
        for future in as_completed(futures):
            paper = futures[future]
            paper["paper_abstract"] = future.result().strip()
    res[name] = merge_cvf_records(res.get(name, []), papers)
    return res

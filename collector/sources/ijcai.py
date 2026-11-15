from __future__ import annotations

import os
import re
import threading
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from collector.http import HEADERS, SESSION
from collector.merge import _merge_paper_record
from collector.sources.miccai import is_doi_url, normalize_miccai_title

IJCAI_HOST = "ijcai.org"
IJCAI_PROCEEDINGS_INDEX = "https://ijcai.org/all_proceedings/"
DEFAULT_CONCURRENCY = 4
IJCAI_TIMEOUT = (10, 60)
_MAIN_LISTING_RE = re.compile(r"^/proceedings/(\d{4})(?:-(\d+))?/?$")
_PAPER_PATH_RE = re.compile(r"^/proceedings/(\d{4})/(\d+)/?$")


class IJCAICollectionError(RuntimeError):
    def __init__(self, failures, papers):
        self.failures = failures
        self.papers = papers
        details = "; ".join(f"{url}: {error}" for url, error in failures[:3])
        super().__init__(f"{len(failures)} IJCAI detail page(s) failed: {details}")


def canonicalize_ijcai_url(url: str) -> str:
    if not (url or "").strip():
        return ""
    parsed = urlsplit((url or "").strip())
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    match = _PAPER_PATH_RE.fullmatch(path.rstrip("/"))
    if match:
        path = f"/proceedings/{match.group(1)}/{int(match.group(2))}"
    elif path != "/":
        path = path.rstrip("/")
    return urlunsplit(("https", host, path, "", ""))


def is_ijcai_paper_url(url: str) -> bool:
    parsed = urlsplit(canonicalize_ijcai_url(url))
    return parsed.netloc == IJCAI_HOST and _PAPER_PATH_RE.fullmatch(parsed.path) is not None


def parse_ijcai_proceedings_index(html: str, base_url: str = IJCAI_PROCEEDINGS_INDEX) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    entries = []
    seen = set()
    for anchor in soup.find_all("a", href=True):
        url = canonicalize_ijcai_url(urljoin(base_url, anchor["href"]))
        parsed = urlsplit(url)
        match = _MAIN_LISTING_RE.fullmatch(parsed.path) if parsed.netloc == IJCAI_HOST else None
        if match is None or url in seen:
            continue
        seen.add(url)
        entries.append({"name": f"IJCAI{match.group(1)}", "url": url})
    return entries


def parse_ijcai_listing(html: str, listing_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    papers = []
    seen = set()
    for row in soup.select("div.paper-row"):
        anchor = row.select_one("div.paper-row__text a.title[href]")
        if anchor is None:
            continue
        detail_url = canonicalize_ijcai_url(urljoin(listing_url, anchor["href"]))
        title = " ".join(anchor.get_text(" ", strip=True).split())
        if not title or not is_ijcai_paper_url(detail_url) or detail_url in seen:
            continue
        seen.add(detail_url)
        authors = []
        for author in row.select("div.paper-row__text div.authors a"):
            name = " ".join(author.get_text(" ", strip=True).split())
            if name and name not in authors:
                authors.append(name)
        paper_url = detail_url
        for link in row.select("div.paper-row__text div.details a[href]"):
            href = urljoin(detail_url, link["href"].strip())
            if is_doi_url(href):
                paper_url = href
                break
        papers.append({
            "paper_name": title,
            "paper_url": paper_url,
            "paper_authors": authors,
            "paper_abstract": "",
            "paper_code": "#",
            "_detail_url": detail_url,
        })
    if not papers:
        raise ValueError(f"No IJCAI paper entries found at {listing_url}")
    return papers


def parse_ijcai_detail(html: str, detail_url: str, listing_record: Optional[dict] = None) -> dict:
    from papervault.services.abstract_fetchers.ijcai import extract_ijcai_abstract

    soup = BeautifulSoup(html, "html.parser")
    fallback = listing_record or {}
    title_node = soup.select_one("h1.article__title")
    title = " ".join(title_node.get_text(" ", strip=True).split()) if title_node else ""
    if not title:
        title = fallback.get("paper_name", "")
    if not title:
        raise ValueError(f"Missing IJCAI paper title at {detail_url}")

    authors = []
    for anchor in soup.select('a[href*="/authors/"]'):
        name = " ".join(anchor.get_text(" ", strip=True).split())
        if name and name not in authors:
            authors.append(name)
    if not authors:
        authors = list(fallback.get("paper_authors") or [])

    paper_url = fallback.get("paper_url") or canonicalize_ijcai_url(detail_url)
    doi_node = soup.select_one('meta[name="citation_doi"][content]')
    if doi_node is not None:
        paper_url = f"https://doi.org/{doi_node['content'].strip()}"
    else:
        for anchor in soup.find_all("a", href=True):
            href = urljoin(detail_url, anchor["href"].strip())
            if is_doi_url(href):
                paper_url = href
                break

    return {
        "paper_name": title,
        "paper_url": paper_url,
        "paper_authors": authors,
        "paper_abstract": extract_ijcai_abstract(soup),
        "paper_code": fallback.get("paper_code") or "#",
    }


def parse_ijcai_json(content: str, detail_url: str, listing_record: Optional[dict] = None) -> dict:
    import json

    data = json.loads(content)
    fallback = listing_record or {}
    if data.get("type") != "ScholarlyArticle":
        raise ValueError(f"Unexpected IJCAI JSON type at {detail_url}")
    title = " ".join((data.get("title") or fallback.get("paper_name") or "").split())
    if not title:
        raise ValueError(f"Missing IJCAI paper title at {detail_url}")
    authors = []
    for author in data.get("authors") or []:
        name = " ".join((author.get("name") or "").split())
        if name and name not in authors:
            authors.append(name)
    if not authors:
        authors = list(fallback.get("paper_authors") or [])
    doi = (data.get("doi") or "").strip()
    paper_url = f"https://doi.org/{doi}" if doi else fallback.get("paper_url") or canonicalize_ijcai_url(detail_url)
    return {
        "paper_name": title,
        "paper_url": paper_url,
        "paper_authors": authors,
        "paper_abstract": " ".join((data.get("abstract") or "").split()),
        "paper_code": fallback.get("paper_code") or "#",
    }


def _snapshot_path(root: Path, url: str) -> Path:
    path = urlsplit(url).path.strip("/")
    parts = path.split("/") if path else ["index"]
    if any(not re.fullmatch(r"[A-Za-z0-9._-]+", part) for part in parts):
        raise ValueError(f"Unsafe IJCAI snapshot path for {url}")
    if not parts[-1].endswith(".json"):
        parts[-1] += ".html"
    return root.joinpath(*parts)


def _get_content(
    url: str,
    *,
    snapshot_dir: Optional[Path] = None,
    refresh: bool = False,
    allow_network: bool = True,
) -> str:
    snapshot = _snapshot_path(snapshot_dir, url) if snapshot_dir is not None else None
    if snapshot is not None and snapshot.exists() and not refresh:
        return snapshot.read_text(encoding="utf-8")
    if not allow_network:
        raise FileNotFoundError(f"No local IJCAI metadata snapshot for {url}")
    response = SESSION.get(url, headers=HEADERS, timeout=IJCAI_TIMEOUT)
    response.raise_for_status()
    response.encoding = "utf-8"
    content = response.text
    if snapshot is not None:
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        tmp = snapshot.with_name(f"{snapshot.name}.{threading.get_ident()}.tmp")
        try:
            tmp.write_text(content, encoding="utf-8")
            os.replace(tmp, snapshot)
        finally:
            tmp.unlink(missing_ok=True)
    return content


def _is_main_cache_url(url: str) -> bool:
    lower = (url or "").lower()
    host = urlsplit(lower).netloc
    if host == "doi.org":
        return bool(urlsplit(lower).path.strip("/"))
    if host == "arxiv.org":
        return urlsplit(lower).path.startswith("/abs/")
    if host == "web.archive.org":
        return (
            "ijcai.org/proceedings/" in lower
            or "aaai.org/ocs/index.php/ijcai/ijcai" in lower
        )
    if host in {"www.booksonline.iospress.nl", "booksonline.iospress.nl"}:
        return True
    if is_ijcai_paper_url(lower):
        return True
    if host in {"ijcai.org", "www.ijcai.org"}:
        return re.fullmatch(
            r"/(?:proceedings/\d{2}/papers/[^/]+\.pdf|abstract/\d{2}/\d+)",
            urlsplit(lower).path,
        ) is not None
    return False


def _author_keys(authors) -> set[str]:
    keys = set()
    for author in authors or []:
        expanded = str(author).casefold().translate(str.maketrans({
            "ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
        }))
        normalized = unicodedata.normalize("NFKD", expanded)
        normalized = "".join(char for char in normalized if not unicodedata.combining(char))
        words = re.findall(r"[\w]+", normalized, flags=re.UNICODE)
        if words:
            keys.add(words[-1])
    return keys


def normalize_ijcai_title(title: str) -> str:
    normalized = unicodedata.normalize("NFKD", normalize_miccai_title(title))
    return "".join(char for char in normalized if not unicodedata.combining(char))


def merge_ijcai_records(records: list[dict], official_records: list[dict]) -> list[dict]:
    unique_official = {}
    for record in official_records:
        key = canonicalize_ijcai_url(record.get("paper_url", "")) or normalize_ijcai_title(record.get("paper_name", ""))
        unique_official.setdefault(key, record)
    official_records = list(unique_official.values())
    title_counts = Counter(
        key for record in official_records
        if (key := normalize_ijcai_title(record.get("paper_name", "")))
    )
    eligible_titles = {key for key, count in title_counts.items() if count == 1}
    merged = []
    url_index = {}
    title_index = {}

    for record in [*records, *official_records]:
        url = record.get("paper_url") or ""
        lookup_url = canonicalize_ijcai_url(url)
        title_key = normalize_ijcai_title(record.get("paper_name", ""))
        prior = url_index.get(lookup_url) if lookup_url else None
        title_match = False
        if prior is None and title_key in eligible_titles and _is_main_cache_url(url):
            candidate = title_index.get(title_key)
            if candidate is not None:
                old_authors = _author_keys(candidate.get("paper_authors"))
                new_authors = _author_keys(record.get("paper_authors"))
                if not old_authors or not new_authors or old_authors & new_authors:
                    prior = candidate
                    title_match = True
        if prior is not None:
            previous_url = prior.get("paper_url") or ""
            _merge_paper_record(prior, record)
            if title_match:
                if is_doi_url(url):
                    prior["paper_url"] = url
                elif is_doi_url(previous_url):
                    prior["paper_url"] = previous_url
                else:
                    prior["paper_url"] = lookup_url or canonicalize_ijcai_url(previous_url)
            if previous_url:
                url_index[canonicalize_ijcai_url(previous_url)] = prior
            if url:
                url_index[lookup_url] = prior
            continue

        if is_ijcai_paper_url(lookup_url):
            record["paper_url"] = lookup_url
        merged.append(record)
        if lookup_url:
            url_index[lookup_url] = record
        if title_key in eligible_titles and _is_main_cache_url(record.get("paper_url", "")):
            title_index[title_key] = record
    return merged


def collect_ijcai_papers(
    listing_url: str,
    *,
    concurrency: int = DEFAULT_CONCURRENCY,
    limit: Optional[int] = None,
    snapshot_dir: Optional[Path] = None,
    cached_records: Optional[list[dict]] = None,
    refresh: bool = False,
    allow_network: bool = True,
    strict: bool = True,
) -> list[dict]:
    html = _get_content(
        listing_url, snapshot_dir=snapshot_dir, refresh=refresh, allow_network=allow_network
    )
    papers = parse_ijcai_listing(html, listing_url)
    if limit is not None:
        papers = papers[:max(0, limit)]

    cached_by_title = {}
    for record in cached_records or []:
        key = normalize_ijcai_title(record.get("paper_name", ""))
        if key and _is_main_cache_url(record.get("paper_url", "")):
            cached_by_title.setdefault(key, []).append(record)
    pending = []
    results = {}
    failures = []
    for index, paper in enumerate(papers):
        key = normalize_ijcai_title(paper["paper_name"])
        matches = cached_by_title.get(key, [])
        if len(matches) == 1 and (matches[0].get("paper_abstract") or "").strip():
            paper.pop("_detail_url", None)
            results[index] = paper
        else:
            pending.append((index, paper))

    def _fetch(index, paper):
        detail_url = paper["_detail_url"]

        def _from_html(json_error=None):
            try:
                content = _get_content(
                    detail_url,
                    snapshot_dir=snapshot_dir,
                    refresh=refresh,
                    allow_network=allow_network,
                )
                return parse_ijcai_detail(content, detail_url, paper)
            except Exception as html_error:
                if json_error is None:
                    raise
                raise RuntimeError(f"JSON: {json_error}; HTML: {html_error}") from html_error

        try:
            content = _get_content(
                detail_url + ".json",
                snapshot_dir=snapshot_dir,
                refresh=refresh,
                allow_network=allow_network,
            )
            parsed = parse_ijcai_json(content, detail_url, paper)
        except Exception as json_error:
            return index, _from_html(json_error)
        if parsed["paper_abstract"]:
            return index, parsed
        html_parsed = _from_html()
        return index, html_parsed if html_parsed["paper_abstract"] else parsed

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as executor:
        futures = {executor.submit(_fetch, index, paper): (index, paper) for index, paper in pending}
        for future in as_completed(futures):
            index, paper = futures[future]
            detail_url = paper["_detail_url"]
            try:
                result_index, parsed = future.result()
                results[result_index] = parsed
            except Exception as exc:
                failures.append((detail_url, str(exc)))
                paper.pop("_detail_url", None)
                results[index] = paper

    ordered = [results[index] for index in range(len(papers))]
    if failures and strict:
        raise IJCAICollectionError(failures, ordered)
    return ordered


def search_from_ijcai(url, name, res):
    res.setdefault(name, [])
    res[name].extend(collect_ijcai_papers(url))
    return res

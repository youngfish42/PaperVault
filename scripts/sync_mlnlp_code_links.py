"""Bidirectional code-link sync with MLNLP-World/Top-AI-Conferences-Paper-with-Code.

Only editions linked from the upstream README's supported-conference table are
processed. Matching is deliberately conservative: stable paper identifiers
first, then unique exact normalized titles within the same conference edition.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data_artifacts import ensure_cache_local, sync_cache_artifacts  # noqa: E402

CACHE_FILE = Path("cache/cache.jsonl.gz")
BACKUP_FILE = Path("cache/cache.jsonl.gz.mlnlp.bak")
EXPECTED_EDITIONS = 60
SUPPORTED_VENUES = {
    "ACL": "ACL",
    "EMNLP": "EMNLP",
    "NAACL": "NAACL",
    "COLING": "COLING",
    "AAAI": "AAAI",
    "IJCAI": "IJCAI",
    "ICLR": "ICLR",
    "NeurIPS": "NIPS",
    "ICML": "ICML",
}
DIRECT_CODE_HOSTS = {"github.com", "gitlab.com", "bitbucket.org"}
BLOCKED_ICLR_REPOS = {
    "https://github.com/mlii0117/ffa-ir",
    "https://github.com/a4bio/opencpd",
}
MAX_SAMPLES = 50


@dataclass(frozen=True)
class Edition:
    venue: str
    year: int
    path: Path

    @property
    def conf(self) -> str:
        return f"{SUPPORTED_VENUES[self.venue]}{self.year}"


@dataclass
class ExternalPaper:
    title: str
    paper_url: str
    code_urls: list[str]
    position: int

    @property
    def code_url(self) -> str:
        return self.code_urls[0] if self.code_urls else ""


def _clean_url(value: str) -> str:
    value = html.unescape(str(value or "")).strip()
    match = re.search(r"\[[^\]]*\]\(\s*(https?://[^)\s]+)\s*\)", value)
    if match:
        value = match.group(1)
    else:
        match = re.search(r"https?://[^\s<>\]]+", value)
        value = match.group(0) if match else ""
    return value.strip().strip("<>").rstrip(".,;:'\")]}>")


def _clean_title(value: str) -> str:
    value = html.unescape(str(value or ""))
    value = re.sub(r"\s+", " ", value).strip()
    return value.replace(r"\|", "|").strip()


def normalize_title(title: str) -> str:
    title = unicodedata.normalize("NFKC", _clean_title(title)).casefold()
    title = re.sub(r"\\[a-z]+\*?(?:\[[^\]]*\])?", "", title)
    return "".join(char for char in title if char.isalnum())


def normalize_repo_url(url: str) -> str:
    cleaned = _clean_url(url)
    if not cleaned:
        return ""
    parsed = urlparse(cleaned)
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = re.sub(r"/+$", "", parsed.path or "")
    if host == "github.com":
        parts = [part for part in path.split("/") if part]
        if len(parts) >= 2:
            path = "/" + "/".join(parts[:2])
    return f"https://{host}{path}".casefold()


def classify_code_url(url: str) -> tuple[str, str]:
    """Return (clean URL, rejection reason); exactly one value is non-empty."""
    cleaned = _clean_url(url)
    if not cleaned or cleaned == "#":
        return "", "empty"
    try:
        parsed = urlparse(cleaned)
    except ValueError:
        return "", "malformed"
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if parsed.scheme not in {"http", "https"} or not host:
        return "", "malformed"
    path = parsed.path or ""
    parts = [part for part in path.split("/") if part]
    if host in DIRECT_CODE_HOSTS and len(parts) >= 2:
        return cleaned, ""
    if host == "anonymous.4open.science" and parts and parts[0] in {"r", "w"}:
        return cleaned, ""
    if host in {"paperswithcode.com", "www.paperswithcode.com", "catalyzex.com", "www.catalyzex.com"}:
        return "", "aggregator"
    if host in {"huggingface.co", "hf.co"}:
        return "", "model_or_dataset"
    if host.endswith("github.io") or host == "github.io":
        return "", "project_page"
    if host in {"youtu.be", "youtube.com", "www.youtube.com"}:
        return "", "video"
    return "", "non_repository"


def normalize_paper_url(url: str) -> str:
    cleaned = _clean_url(url)
    if not cleaned:
        return ""
    try:
        parsed = urlparse(cleaned)
    except ValueError:
        return ""
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if not host or parsed.scheme not in {"http", "https"}:
        return ""
    path = unquote(parsed.path or "").rstrip("/")
    if host in {
        "openaccess.thecvf.com",
        "papers.nips.cc",
        "proceedings.mlr.press",
        "arxiv.org",
        "openreview.net",
        "jmlr.org",
    }:
        path = path.lower()
    if host == "openreview.net":
        match = re.search(r"(?:^|[?&])id=([A-Za-z0-9_-]+)", parsed.query)
        if match:
            return f"https://{host}{path}?id={match.group(1)}"
    return f"https://{host}{path}"


def paper_identity_keys(url: str) -> list[tuple[str, str]]:
    cleaned = _clean_url(url)
    if not cleaned:
        return []
    try:
        parsed = urlparse(cleaned)
    except ValueError:
        return []
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = unquote(parsed.path or "")
    keys: list[tuple[str, str]] = []

    if host == "openreview.net":
        match = re.search(r"(?:^|[?&])id=([A-Za-z0-9_-]+)", parsed.query)
        if match:
            keys.append(("openreview", match.group(1)))
    if host in {"aclanthology.org", "aclweb.org"}:
        anthology_id = path.rstrip("/").rsplit("/", 1)[-1]
        anthology_id = re.sub(r"\.pdf$", "", anthology_id, flags=re.IGNORECASE)
        if re.fullmatch(r"(?:\d{4}\.[a-z0-9-]+\.\d+|[A-Za-z]\d{2}-\d{4})", anthology_id):
            keys.append(("acl", anthology_id.casefold()))
    if host == "arxiv.org":
        match = re.match(r"^/(?:abs|pdf)/(.+)$", path, flags=re.IGNORECASE)
        if match:
            arxiv_id = match.group(1).strip("/")
            arxiv_id = re.sub(r"\.pdf$", "", arxiv_id, flags=re.IGNORECASE)
            arxiv_id = re.sub(r"v\d+$", "", arxiv_id, flags=re.IGNORECASE)
            if arxiv_id:
                keys.append(("arxiv", arxiv_id.casefold()))
    if host == "doi.org" and path.strip("/"):
        keys.append(("doi", path.strip("/").casefold()))
    if host in {"ojs.aaai.org", "aaai.org"}:
        match = re.search(r"/article/view/(\d+)", path, flags=re.IGNORECASE)
        if match:
            keys.append(("aaai", match.group(1)))
    if host == "ijcai.org":
        match = re.search(
            r"/proceedings/(\d{4})/(\d+)(?:\.pdf)?/?$", path, flags=re.IGNORECASE
        )
        if match:
            keys.append(("ijcai", f"{match.group(1)}:{int(match.group(2))}"))
    normalized = normalize_paper_url(cleaned)
    if normalized:
        keys.append(("url", normalized))
    return keys


def load_manifest(repo: Path) -> list[Edition]:
    readme = (repo / "README.md").read_text(encoding="utf-8")
    section_match = re.search(r"^## .*已支持会议\s*$\n(.*?)(?=^## )", readme, re.MULTILINE | re.DOTALL)
    if not section_match:
        raise ValueError("Cannot locate the supported-conference table in README.md")
    editions = []
    seen = set()
    for line in section_match.group(1).splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if not cells or cells[0] not in SUPPORTED_VENUES:
            continue
        venue = cells[0]
        for cell in cells[1:]:
            match = re.fullmatch(r"\[(\d{4})\]\((https://github\.com/[^)]+/blob/master/([^)]+))\)", cell)
            if not match:
                continue
            year = int(match.group(1))
            relative = Path(unquote(match.group(3)))
            path = (repo / relative).resolve()
            try:
                path.relative_to(repo.resolve())
            except ValueError as exc:
                raise ValueError(f"Manifest path escapes repository: {relative}") from exc
            if relative.parts[:2] != (venue, str(year)) or not path.is_file():
                raise ValueError(f"Unsupported or missing manifest target: {relative}")
            key = (venue, year)
            if key in seen:
                raise ValueError(f"Duplicate manifest edition: {venue} {year}")
            seen.add(key)
            editions.append(Edition(venue, year, path))
    if len(editions) != EXPECTED_EDITIONS:
        raise ValueError(f"Expected {EXPECTED_EDITIONS} supported editions, found {len(editions)}")
    return sorted(editions, key=lambda edition: (edition.venue, edition.year))


def _markdown_urls(value: str) -> list[str]:
    urls = []
    seen = set()
    for match in re.finditer(r"https?://[^\s<>\])]+", value):
        url = _clean_url(match.group(0))
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def parse_markdown(path: Path) -> list[ExternalPaper]:
    lines = path.read_text(encoding="utf-8").splitlines()
    entries: list[ExternalPaper] = []
    current_title = ""
    paper_url = ""
    code_urls: list[str] = []
    position = 0

    def flush() -> None:
        nonlocal current_title, paper_url, code_urls, position
        if current_title and paper_url:
            position += 1
            entries.append(ExternalPaper(current_title, paper_url, code_urls, position))
        current_title = ""
        paper_url = ""
        code_urls = []

    def next_nonempty(index: int) -> str:
        for following in lines[index + 1 :]:
            if following.strip():
                return following
        return ""

    for index, line in enumerate(lines):
        heading = re.match(r"^#{3,4}\s+(.+?)\s*$", line)
        if heading:
            flush()
            current_title = _clean_title(heading.group(1))
            continue
        linked_title = re.fullmatch(r"\[([^\]]+)\]\((https?://[^)]+)\)", line.strip())
        if linked_title:
            flush()
            current_title = _clean_title(linked_title.group(1))
            paper_url = _clean_url(linked_title.group(2))
            continue
        paper_pattern = r"\s*[-*+]\s*(?:论文链接|Paper Link)\s*[:：]\s*(.+)"
        if line.strip() and not line.strip().startswith(("|", "-", "*", "+", "#")) and next_nonempty(index):
            if re.match(paper_pattern, next_nonempty(index), re.IGNORECASE):
                flush()
                current_title = _clean_title(line)
                continue
        paper_match = re.match(paper_pattern, line, re.IGNORECASE)
        if current_title and paper_match:
            urls = _markdown_urls(paper_match.group(1))
            if urls:
                paper_url = urls[0]
            continue
        code_match = re.match(
            r"\s*[-*+]\s*(?:代码链接|资源链接|Code Link)\s*[:：]\s*(.+)",
            line,
            re.IGNORECASE,
        )
        if current_title and code_match:
            code_urls.extend(_markdown_urls(code_match.group(1)))
            continue
        if not line.strip().startswith("|"):
            continue
        cells = [_clean_title(cell) for cell in re.split(r"(?<!\\)\|", line.strip())[1:-1]]
        if len(cells) < 3 or cells[0].casefold() in {"title", "论文标题"}:
            continue
        if all(re.fullmatch(r":?-+:?", cell.replace(" ", "")) for cell in cells):
            continue
        row_paper = _markdown_urls(cells[1])
        row_code = _markdown_urls(cells[2])
        if cells[0] and row_paper:
            flush()
            position += 1
            entries.append(ExternalPaper(cells[0], row_paper[0], row_code, position))
    flush()
    if not entries:
        raise ValueError(f"No supported paper entries parsed from non-empty manifest file: {path}")
    return entries


def load_papers(cache_path: Path) -> list[dict]:
    papers = []
    with gzip.open(cache_path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                papers.append(json.loads(line))
    return papers


def write_papers_atomic(cache_path: Path, papers: list[dict]) -> None:
    temporary = cache_path.with_name(cache_path.name + ".tmp")
    with gzip.open(temporary, "wt", encoding="utf-8") as handle:
        for paper in papers:
            handle.write(json.dumps(paper, ensure_ascii=False) + "\n")
    count = 0
    with gzip.open(temporary, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                json.loads(line)
                count += 1
    if count != len(papers):
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"Cache verification failed: expected {len(papers)}, got {count}")
    os.replace(temporary, cache_path)


def _source_commit(repo: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return ""
    return result.stdout.strip()


def _cache_digest(cache_path: Path) -> str:
    return hashlib.sha256(cache_path.read_bytes()).hexdigest()


def _new_stats(direction: str) -> dict:
    return {
        "direction": direction,
        "editions": 0,
        "parsed_external": 0,
        "scanned_local": 0,
        "matched": 0,
        "filled": 0,
        "added": 0,
        "already_present": 0,
        "already_same_code": 0,
        "conflicts": 0,
        "unmatched": 0,
        "ambiguous": 0,
        "rejected_code": 0,
        "quarantined": 0,
        "by_conf": Counter(),
        "by_method": Counter(),
        "rejections": Counter(),
        "samples": defaultdict(list),
    }


def _sample(stats: dict, kind: str, value: dict) -> None:
    if len(stats["samples"][kind]) < MAX_SAMPLES:
        stats["samples"][kind].append(value)


def _build_indices(records: list[dict], url_key: str) -> tuple[dict, dict]:
    identities = defaultdict(list)
    titles = defaultdict(list)
    for index, record in enumerate(records):
        for key in paper_identity_keys(record.get(url_key) or ""):
            identities[key].append(index)
        title = normalize_title(record.get("paper_name") or record.get("title") or "")
        if title:
            titles[title].append(index)
    return identities, titles


def _external_records(entries: list[ExternalPaper]) -> list[dict]:
    return [
        {"title": entry.title, "paper_url": entry.paper_url, "entry": entry}
        for entry in entries
    ]


def match_external(
    entry: ExternalPaper,
    local_records: list[dict],
    identities: dict,
    titles: dict,
    external_titles: dict,
) -> tuple[Optional[int], str, str]:
    for key in paper_identity_keys(entry.paper_url):
        matches = identities.get(key, [])
        if len(matches) == 1:
            return matches[0], key[0], ""
        if len(matches) > 1:
            return None, key[0], "multiple_local_identity_matches"
    title = normalize_title(entry.title)
    if title and len(external_titles.get(title, [])) == 1:
        matches = titles.get(title, [])
        if len(matches) == 1:
            return matches[0], "title", ""
        if len(matches) > 1:
            return None, "title", "multiple_local_title_matches"
    return None, "", "unmatched"


def _non_code_projection(papers: list[dict]) -> list[dict]:
    return [{key: value for key, value in paper.items() if key != "paper_code"} for paper in papers]


def import_records(
    papers: list[dict],
    manifest: list[Edition],
    dry_run: bool = True,
    rejected_urls: Optional[set[str]] = None,
) -> tuple[list[dict], dict]:
    stats = _new_stats("import")
    stats["editions"] = len(manifest)
    rejected_urls = rejected_urls or set()
    updated = [dict(paper) for paper in papers]
    local_by_conf = defaultdict(list)
    for index, paper in enumerate(updated):
        local_by_conf[paper.get("conf") or ""].append((index, paper))
    stats["scanned_local"] = sum(len(local_by_conf.get(edition.conf, [])) for edition in manifest)

    for edition in manifest:
        entries = parse_markdown(edition.path)
        stats["parsed_external"] += len(entries)
        indexed = local_by_conf.get(edition.conf, [])
        local_records = [paper for _, paper in indexed]
        global_indices = [index for index, _ in indexed]
        identities, titles = _build_indices(local_records, "paper_url")
        _, external_titles = _build_indices(_external_records(entries), "paper_url")

        for entry in entries:
            local_index, method, error = match_external(
                entry, local_records, identities, titles, external_titles
            )
            if local_index is None:
                stats["unmatched" if error == "unmatched" else "ambiguous"] += 1
                _sample(stats, "unmatched" if error == "unmatched" else "ambiguous", {
                    "conf": edition.conf,
                    "title": entry.title,
                    "paper_url": entry.paper_url,
                    "reason": error,
                })
                continue
            stats["matched"] += 1
            paper = updated[global_indices[local_index]]
            valid_codes = []
            for raw_code in entry.code_urls:
                code, reason = classify_code_url(raw_code)
                if code in rejected_urls:
                    code, reason = "", "not_found"
                if code:
                    valid_codes.append(code)
                else:
                    stats["rejected_code"] += 1
                    stats["rejections"][reason] += 1
                    _sample(stats, "rejected_code", {
                        "conf": edition.conf,
                        "title": entry.title,
                        "code_url": raw_code,
                        "reason": reason,
                    })
            if not valid_codes:
                continue
            code = valid_codes[0]
            existing = (paper.get("paper_code") or "").strip()
            sample = {
                "conf": edition.conf,
                "title": paper.get("paper_name"),
                "paper_url": paper.get("paper_url"),
                "external_code": code,
                "local_code": existing,
                "method": method,
            }
            if existing and existing != "#":
                if any(normalize_repo_url(existing) == normalize_repo_url(item) for item in valid_codes):
                    stats["already_same_code"] += 1
                else:
                    stats["conflicts"] += 1
                    _sample(stats, "conflicts", sample)
                continue
            if len({normalize_repo_url(item) for item in valid_codes}) > 1:
                stats["ambiguous"] += 1
                sample["reason"] = "multiple_external_codes"
                _sample(stats, "ambiguous", sample)
                continue
            paper["paper_code"] = code
            stats["filled"] += 1
            stats["by_conf"][edition.conf] += 1
            stats["by_method"][method] += 1
            _sample(stats, "filled", sample)

    if _non_code_projection(papers) != _non_code_projection(updated):
        raise AssertionError("Import changed a field other than paper_code")
    if dry_run:
        return papers, stats
    return updated, stats


def repair_records(
    papers: list[dict],
    manifest: list[Edition],
    dry_run: bool = True,
    rejected_urls: Optional[set[str]] = None,
) -> tuple[list[dict], dict]:
    """Repair only the two demonstrated ICLR mass-assignment clusters."""
    stats = _new_stats("repair")
    stats.update({"affected": 0, "replaced": 0, "cleared": 0, "kept": 0, "changes": []})
    rejected_urls = rejected_urls or set()
    updated = [dict(paper) for paper in papers]
    local_by_conf = defaultdict(list)
    for index, paper in enumerate(updated):
        local_by_conf[paper.get("conf") or ""].append((index, paper))

    for edition in (item for item in manifest if item.venue == "ICLR"):
        indexed = local_by_conf.get(edition.conf, [])
        local_records = [paper for _, paper in indexed]
        _, local_titles = _build_indices(local_records, "paper_url")
        entries = parse_markdown(edition.path)
        external_records = _external_records(entries)
        external_identities, external_titles = _build_indices(external_records, "paper_url")
        stats["parsed_external"] += len(entries)
        stats["scanned_local"] += len(local_records)

        for global_index, paper in indexed:
            existing = (paper.get("paper_code") or "").strip()
            repo = normalize_repo_url(existing)
            if repo not in BLOCKED_ICLR_REPOS:
                continue
            stats["affected"] += 1
            change = {
                "_index": global_index,
                "conf": edition.conf,
                "title": paper.get("paper_name"),
                "paper_url": paper.get("paper_url"),
                "old_code": existing,
                "new_code": "",
                "action": "clear",
                "method": "",
            }
            if repo in (paper.get("paper_abstract") or "").casefold():
                change.update(action="keep", new_code=existing, method="abstract")
                stats["kept"] += 1
                stats["by_method"]["abstract"] += 1
                stats["changes"].append(change)
                continue

            external_matches = set()
            method = ""
            for key in paper_identity_keys(paper.get("paper_url") or ""):
                matches = external_identities.get(key, [])
                if matches:
                    external_matches.update(matches)
                    method = key[0]
            if not external_matches:
                title = normalize_title(paper.get("paper_name") or "")
                if title and len(local_titles.get(title, [])) == 1:
                    matches = external_titles.get(title, [])
                    if matches:
                        external_matches.update(matches)
                        method = "title"
            change["method"] = method
            if not external_matches:
                stats["cleared"] += 1
                stats["by_method"]["unmatched"] += 1
                stats["changes"].append(change)
                _sample(stats, "cleared", change)
                continue

            valid_codes = []
            for external_index in sorted(external_matches):
                for raw_code in external_records[external_index]["entry"].code_urls:
                    code, _ = classify_code_url(raw_code)
                    if code and code not in rejected_urls and code not in valid_codes:
                        valid_codes.append(code)
            if not valid_codes:
                stats["cleared"] += 1
                stats["by_method"]["no_valid_external_code"] += 1
                stats["changes"].append(change)
                _sample(stats, "cleared", change)
                continue
            if any(normalize_repo_url(code) == repo for code in valid_codes):
                change.update(action="keep", new_code=existing)
                stats["kept"] += 1
                stats["by_method"][f"external_{method}"] += 1
                stats["changes"].append(change)
                continue
            if len({normalize_repo_url(code) for code in valid_codes}) > 1:
                stats["cleared"] += 1
                stats["by_method"]["ambiguous_external_code"] += 1
                stats["changes"].append(change)
                _sample(stats, "cleared", change)
                continue

            change.update(action="replace", new_code=valid_codes[0])
            stats["replaced"] += 1
            stats["by_conf"][edition.conf] += 1
            stats["by_method"][method] += 1
            stats["changes"].append(change)
            _sample(stats, "replaced", change)

    if _non_code_projection(papers) != _non_code_projection(updated):
        raise AssertionError("Repair changed a field other than paper_code")
    for change in stats["changes"]:
        if change["action"] != "keep":
            updated[change["_index"]]["paper_code"] = change["new_code"] or "#"
    if dry_run:
        return papers, stats
    return updated, stats


def _anomaly_groups(local_records: list[dict], conf: str) -> set[str]:
    by_repo = defaultdict(list)
    for paper in local_records:
        code, reason = classify_code_url(paper.get("paper_code") or "")
        if code:
            by_repo[normalize_repo_url(code)].append(paper)
    quarantined = set()
    total = max(1, len(local_records))
    for repo, records in by_repo.items():
        distinct_titles = {normalize_title(paper.get("paper_name") or "") for paper in records}
        if conf.startswith("ICLR") and repo in BLOCKED_ICLR_REPOS:
            quarantined.add(repo)
        elif len(records) >= 20 and len(distinct_titles) >= 20 and len(records) / total >= 0.05:
            quarantined.add(repo)
    return quarantined


def _local_matches_external(
    paper: dict,
    external_identities: dict,
    external_titles: dict,
    local_identities: dict,
    local_titles: dict,
) -> tuple[bool, str]:
    for key in paper_identity_keys(paper.get("paper_url") or ""):
        if external_identities.get(key):
            return True, key[0]
        if len(local_identities.get(key, [])) > 1:
            return False, "multiple_local_identity_matches"
    title = normalize_title(paper.get("paper_name") or "")
    if not title:
        return False, "missing_title"
    if len(local_titles.get(title, [])) > 1:
        return False, "multiple_local_title_matches"
    if external_titles.get(title):
        return True, "title"
    return False, ""


def export_records(
    papers: list[dict],
    manifest: list[Edition],
    dry_run: bool = True,
    rejected_urls: Optional[set[str]] = None,
    exclusions: Optional[set[tuple[str, str, str]]] = None,
) -> tuple[dict[str, list[dict]], dict]:
    stats = _new_stats("export")
    stats["editions"] = len(manifest)
    rejected_urls = rejected_urls or set()
    exclusions = exclusions or set()
    local_by_conf = defaultdict(list)
    for paper in papers:
        local_by_conf[paper.get("conf") or ""].append(paper)
    stats["scanned_local"] = sum(len(local_by_conf.get(edition.conf, [])) for edition in manifest)
    additions: dict[str, list[dict]] = {}

    for edition in manifest:
        entries = parse_markdown(edition.path)
        stats["parsed_external"] += len(entries)
        external_records = _external_records(entries)
        external_identities, external_titles = _build_indices(external_records, "paper_url")
        local_records = local_by_conf.get(edition.conf, [])
        local_identities, local_titles = _build_indices(local_records, "paper_url")
        quarantined = _anomaly_groups(local_records, edition.conf)
        for repo in sorted(quarantined):
            _sample(stats, "quarantined_groups", {"conf": edition.conf, "repo": repo})
        edition_additions = []

        for paper in local_records:
            exists, method = _local_matches_external(
                paper, external_identities, external_titles, local_identities, local_titles
            )
            if exists:
                stats["already_present"] += 1
                stats["by_method"][method] += 1
                continue
            if method.startswith("multiple_local_"):
                stats["ambiguous"] += 1
                _sample(stats, "ambiguous", {
                    "conf": edition.conf,
                    "title": paper.get("paper_name"),
                    "reason": method,
                })
                continue
            code, reason = classify_code_url(paper.get("paper_code") or "")
            if code in rejected_urls:
                code, reason = "", "not_found"
            if not code:
                if (paper.get("paper_code") or "").strip() not in {"", "#"}:
                    stats["rejected_code"] += 1
                    stats["rejections"][reason] += 1
                    _sample(stats, "rejected_code", {
                        "conf": edition.conf,
                        "title": paper.get("paper_name"),
                        "code_url": paper.get("paper_code"),
                        "reason": reason,
                    })
                continue
            repo = normalize_repo_url(code)
            exclusion_key = (edition.conf, normalize_title(paper.get("paper_name") or ""), repo)
            if exclusion_key in exclusions:
                stats["rejected_code"] += 1
                stats["rejections"]["manual_review"] += 1
                _sample(stats, "rejected_code", {
                    "conf": edition.conf,
                    "title": paper.get("paper_name"),
                    "code_url": code,
                    "reason": "manual_review",
                })
                continue
            if repo in quarantined:
                stats["quarantined"] += 1
                _sample(stats, "quarantined", {
                    "conf": edition.conf,
                    "title": paper.get("paper_name"),
                    "code_url": code,
                })
                continue
            if not paper.get("paper_name") or not paper.get("paper_url"):
                stats["ambiguous"] += 1
                continue
            stats["unmatched"] += 1
            addition = {
                "conf": edition.conf,
                "title": str(paper["paper_name"]).strip(),
                "paper_url": _clean_url(paper["paper_url"]),
                "code_url": code,
            }
            edition_additions.append(addition)
            stats["added"] += 1
            stats["by_conf"][edition.conf] += 1
            _sample(stats, "added", addition)
        if edition_additions:
            additions[edition.conf] = edition_additions
    return additions, stats


def _markdown_destination(url: str) -> str:
    return url.replace(" ", "%20").replace("(", "%28").replace(")", "%29")


def render_additions(additions: list[dict]) -> str:
    lines = [
        "## PaperVault 补充",
        "",
        "<!-- Source: https://github.com/youngfish42/PaperVault -->",
        "",
    ]
    for addition in additions:
        title = re.sub(r"\s+", " ", addition["title"]).strip()
        paper_url = _markdown_destination(addition["paper_url"])
        code_url = _markdown_destination(addition["code_url"])
        lines.extend([
            f"#### {title}",
            "",
            f"- 论文链接：[{paper_url}]({paper_url})",
            f"- 代码链接：[{code_url}]({code_url})",
            "",
        ])
    return "\n".join(lines)


def apply_additions(manifest: list[Edition], additions: dict[str, list[dict]]) -> None:
    for edition in manifest:
        items = additions.get(edition.conf)
        if not items:
            continue
        with edition.path.open("rb") as handle:
            original = handle.read()
        newline = "\r\n" if b"\r\n" in original else "\n"
        prefix = "" if original.endswith((b"\n", b"\r")) else newline
        rendered = prefix + newline + render_additions(items).replace("\n", newline)
        with edition.path.open("ab") as handle:
            handle.write(rendered.encode("utf-8"))


def check_urls(urls: Iterable[str], workers: int = 12) -> dict:
    import requests

    unique = sorted({url for url in urls if url})

    def check(url: str) -> tuple[str, str]:
        try:
            response = requests.get(
                url,
                timeout=(5, 12),
                allow_redirects=True,
                stream=True,
                headers={"User-Agent": "PaperVault-code-link-sync/1.0"},
            )
            status = response.status_code
            response.close()
            if status in {404, 410}:
                return url, "not_found"
            if status in {401, 403, 429} or status >= 500:
                return url, "unverified"
            return url, "reachable"
        except requests.RequestException:
            return url, "unverified"

    results = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for url, status in executor.map(check, unique):
            results[url] = status
    return results


def _jsonable(stats: dict) -> dict:
    output = dict(stats)
    for key in ("by_conf", "by_method", "rejections"):
        output[key] = dict(output[key])
    output["samples"] = dict(output["samples"])
    return output


def write_report(path: Path, stats: dict, repo: Path, cache_path: Path, additions: Optional[dict] = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _jsonable(stats)
    payload.update({
        "source_repository": "https://github.com/MLNLP-World/Top-AI-Conferences-Paper-with-Code",
        "source_commit": _source_commit(repo),
        "cache_path": str(cache_path),
        "cache_sha256": _cache_digest(cache_path),
    })
    if additions is not None:
        payload["additions"] = additions
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _selected_manifest(repo: Path, venues: Optional[list[str]]) -> list[Edition]:
    manifest = load_manifest(repo)
    if not venues:
        return manifest
    selected = set(venues)
    unknown = selected - set(SUPPORTED_VENUES)
    if unknown:
        raise ValueError(f"Unsupported venues: {sorted(unknown)}")
    return [edition for edition in manifest if edition.venue in selected]


def _require_sync_baseline(args: argparse.Namespace, parent_commit: Optional[str]) -> None:
    if args.dry_run or args.no_sync:
        return
    if args.offline:
        raise RuntimeError("Refusing to upload from --offline mode; pass --no-sync for local-only writes")
    if not parent_commit:
        raise RuntimeError(
            "Cannot establish the Hugging Face parent commit; refusing to upload a stale cache. "
            "Retry online or pass --no-sync for a local-only write"
        )


def run_import(args: argparse.Namespace) -> dict:
    if args.offline:
        os.environ["PAPERVAULT_OFFLINE"] = "1"
    cache_path = args.cache.resolve()
    _, parent_commit = ensure_cache_local(cache_path, refresh=True)
    _require_sync_baseline(args, parent_commit)
    manifest = _selected_manifest(args.repo.resolve(), args.venue)
    papers = load_papers(cache_path)
    updated, stats = import_records(papers, manifest, dry_run=False)
    if args.check_links:
        checks = {}
        rejected = set()
        while True:
            candidates = [
                updated[index]["paper_code"]
                for index in range(len(papers))
                if updated[index].get("paper_code") != papers[index].get("paper_code")
                and updated[index].get("paper_code") not in checks
            ]
            if not candidates:
                break
            new_checks = check_urls(candidates)
            checks.update(new_checks)
            new_rejected = {
                url for url, status in new_checks.items() if status == "not_found"
            } - rejected
            if not new_rejected:
                break
            rejected.update(new_rejected)
            updated, stats = import_records(
                papers, manifest, dry_run=False, rejected_urls=rejected
            )
        stats["url_checks"] = Counter(checks.values())
        stats["url_check_details"] = checks
    if not args.dry_run and stats["filled"]:
        shutil.copy2(cache_path, BACKUP_FILE)
        write_papers_atomic(cache_path, updated)
        if not args.no_sync:
            sync_cache_artifacts(
                cache_path=cache_path,
                commit_message="Backfill code links from MLNLP conference lists",
                progress_path=None,
            )
    write_report(args.report, stats, args.repo.resolve(), cache_path)
    print(json.dumps(_jsonable(stats), ensure_ascii=True, indent=2))
    return stats


def run_repair(args: argparse.Namespace) -> dict:
    if args.offline:
        os.environ["PAPERVAULT_OFFLINE"] = "1"
    cache_path = args.cache.resolve()
    _, parent_commit = ensure_cache_local(cache_path, refresh=True)
    _require_sync_baseline(args, parent_commit)
    manifest = _selected_manifest(args.repo.resolve(), ["ICLR"])
    papers = load_papers(cache_path)
    updated, stats = repair_records(papers, manifest, dry_run=False)
    if args.check_links:
        checks = {}
        rejected = set()
        while True:
            candidates = [
                change["new_code"]
                for change in stats["changes"]
                if change["action"] == "replace" and change["new_code"] not in checks
            ]
            if not candidates:
                break
            new_checks = check_urls(candidates)
            checks.update(new_checks)
            new_rejected = {
                url for url, status in new_checks.items() if status == "not_found"
            } - rejected
            if not new_rejected:
                break
            rejected.update(new_rejected)
            updated, stats = repair_records(
                papers, manifest, dry_run=False, rejected_urls=rejected
            )
        stats["url_checks"] = Counter(checks.values())
        stats["url_check_details"] = checks
    if not args.dry_run and (stats["replaced"] or stats["cleared"]):
        shutil.copy2(cache_path, BACKUP_FILE)
        write_papers_atomic(cache_path, updated)
        if not args.no_sync:
            sync_cache_artifacts(
                cache_path=cache_path,
                commit_message="Repair anomalous ICLR code link assignments",
                progress_path=None,
            )
    write_report(args.report, stats, args.repo.resolve(), cache_path)
    print(json.dumps(_jsonable(stats), ensure_ascii=True, indent=2))
    return stats


def load_exclusions(path: Optional[Path]) -> set[tuple[str, str, str]]:
    if path is None:
        return set()
    rows = json.loads(path.read_text(encoding="utf-8"))
    return {
        (row["conf"], normalize_title(row["title"]), normalize_repo_url(row["code_url"]))
        for row in rows
    }


def run_export(args: argparse.Namespace) -> dict:
    if args.offline:
        os.environ["PAPERVAULT_OFFLINE"] = "1"
    cache_path = args.cache.resolve()
    ensure_cache_local(cache_path, refresh=True)
    manifest = _selected_manifest(args.repo.resolve(), args.venue)
    papers = load_papers(cache_path)
    exclusions = load_exclusions(args.exclusions)
    additions, stats = export_records(
        papers, manifest, dry_run=args.dry_run, exclusions=exclusions
    )
    if args.check_links:
        urls = [item["code_url"] for items in additions.values() for item in items]
        checks = check_urls(urls)
        rejected = {url for url, status in checks.items() if status == "not_found"}
        if rejected:
            additions, stats = export_records(
                papers,
                manifest,
                dry_run=args.dry_run,
                rejected_urls=rejected,
                exclusions=exclusions,
            )
        stats["url_checks"] = Counter(checks.values())
        stats["url_check_details"] = checks
    if not args.dry_run:
        apply_additions(manifest, additions)
    write_report(args.report, stats, args.repo.resolve(), cache_path, additions=additions)
    print(json.dumps(_jsonable(stats), ensure_ascii=True, indent=2))
    return stats


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("import", "export", "repair"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--repo", type=Path, required=True, help="Local MLNLP repository clone")
        subparser.add_argument("--cache", type=Path, default=CACHE_FILE)
        subparser.add_argument("--report", type=Path, required=True)
        subparser.add_argument("--venue", action="append", choices=sorted(SUPPORTED_VENUES))
        subparser.add_argument("--dry-run", action="store_true")
        subparser.add_argument("--offline", action="store_true", help="Do not refresh from Hugging Face")
        subparser.add_argument("--no-sync", action="store_true", help="Do not upload the cache after import")
        subparser.add_argument("--check-links", action="store_true")
        subparser.add_argument("--exclusions", type=Path, help="JSON manual-review exclusions for export")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "import":
        run_import(args)
    elif args.command == "export":
        run_export(args)
    else:
        run_repair(args)


if __name__ == "__main__":
    main()

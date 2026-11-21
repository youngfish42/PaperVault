"""Backfill empty abstracts and code links from the Papers with Code API.

The batch lookup endpoint resolves up to 500 papers by arXiv ID or an exact
alphanumeric title fingerprint. Repository details are fetched only for matched
papers that advertise at least one repository.
"""

from __future__ import annotations

import argparse
import fnmatch
import gzip
import hashlib
import json
import os
import random
import re
import shutil
import sys
import time
import unicodedata
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from collector.frontmatter import is_frontmatter_record
from collector.url_types import classify_paper_url
from data_artifacts import ensure_cache_local, ensure_progress_local, sync_cache_artifacts
from papervault.services.abstract_fetchers.base import MIN_ABSTRACT_CHARS
from scripts.fetch_abstracts import clean_abstract

CACHE_FILE = ROOT / "cache" / "cache.jsonl.gz"
BACKUP_FILE = ROOT / "cache" / "cache.jsonl.gz.pwc.bak"
PROGRESS_FILE = ROOT / "cache" / "pwc_backfill_progress.jsonl.gz"
PROGRESS_SCHEMA = "pwc_backfill_progress/v1"
API_BASE = "https://paperswithcode.co/api/v1"
MAX_BATCH_SIZE = 500
DEFAULT_MIN_INTERVAL = 1.0
DEFAULT_MAX_RETRIES = 3
HEADERS = {
    "User-Agent": "PaperVault-PWC-Backfill/1.0 (+https://github.com/youngfish42/PaperVault)"
}


class PwcError(RuntimeError):
    reason = "network"


class PwcTransientError(PwcError):
    def __init__(self, reason: str = "network") -> None:
        super().__init__(reason)
        self.reason = reason


class PwcPermanentError(PwcError):
    reason = "invalid_response"


def missing_abstract(paper: dict) -> bool:
    return not str(paper.get("paper_abstract") or "").strip()


def missing_code(paper: dict) -> bool:
    return str(paper.get("paper_code") or "#").strip() in ("", "#")


def extract_arxiv_id(url: str) -> Optional[str]:
    try:
        parsed = urlparse(str(url or "").strip())
    except ValueError:
        return None
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host != "arxiv.org":
        return None
    match = re.match(r"^/(?:abs|pdf)/(.+)$", parsed.path or "", flags=re.IGNORECASE)
    if not match:
        return None
    arxiv_id = match.group(1).strip("/")
    arxiv_id = re.sub(r"\.pdf$", "", arxiv_id, flags=re.IGNORECASE)
    arxiv_id = re.sub(r"v\d+$", "", arxiv_id, flags=re.IGNORECASE)
    return arxiv_id or None


def title_fingerprint(title: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(title or "")).casefold()
    return "".join(char for char in normalized if char.isalnum())


def paper_key(paper: dict) -> str:
    identity = "\0".join(
        (
            str(paper.get("conf") or ""),
            str(paper.get("paper_url") or ""),
            str(paper.get("paper_name") or "") if not paper.get("paper_url") else "",
        )
    )
    return hashlib.sha1(identity.encode("utf-8")).hexdigest()


def make_lookup_entry(paper: dict) -> Optional[dict]:
    title = str(paper.get("paper_name") or "").strip()
    if not title:
        return None
    key = paper_key(paper)
    arxiv_id = extract_arxiv_id(str(paper.get("paper_url") or ""))
    reference = {"key": key, "titles": [title]}
    if arxiv_id:
        reference["arxiv_id"] = arxiv_id
    return {"key": key, "paper": paper, "arxiv_id": arxiv_id, "reference": reference}


def validate_match(entry: dict, result: dict) -> Optional[dict]:
    if not isinstance(result, dict) or result.get("key") != entry["key"]:
        return None
    paper = result.get("paper")
    if not isinstance(paper, dict) or not str(paper.get("id") or "").strip():
        return None
    matched_by = result.get("matched_by")
    if matched_by == "arxiv_id":
        local_id = str(entry.get("arxiv_id") or "").casefold()
        remote_id = str(paper.get("arxiv_id") or "").casefold()
        if local_id and remote_id == local_id:
            return paper
        return None
    if matched_by == "title":
        local_title = entry["paper"].get("paper_name") or ""
        if title_fingerprint(paper.get("title") or "") == title_fingerprint(local_title):
            return paper
    return None


def select_repository(repositories: object) -> str:
    if not isinstance(repositories, list):
        return ""
    candidates = []
    for position, repository in enumerate(repositories):
        if not isinstance(repository, dict):
            continue
        raw_url = str(repository.get("url") or "").strip()
        try:
            parsed = urlparse(raw_url)
        except ValueError:
            continue
        host = (parsed.netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]
        parts = [part for part in (parsed.path or "").split("/") if part]
        if parsed.scheme not in ("http", "https") or host != "github.com" or len(parts) < 2:
            continue
        owner, name = parts[0], parts[1]
        name = re.sub(r"\.git$", "", name, flags=re.IGNORECASE)
        if not owner or not name:
            continue
        url = f"https://github.com/{owner}/{name}"
        try:
            stars = max(0, int(repository.get("num_stars") or 0))
        except (TypeError, ValueError):
            stars = 0
        official = repository.get("is_official") is True
        candidates.append((0 if official else 1, -stars, url, position))
    if not candidates:
        return ""
    return min(candidates)[2]


class PwcClient:
    def __init__(
        self,
        session: Optional[requests.Session] = None,
        *,
        min_interval: float = DEFAULT_MIN_INTERVAL,
        max_retries: int = DEFAULT_MAX_RETRIES,
        sleep_func=time.sleep,
        jitter_func=random.uniform,
    ) -> None:
        self.session = session or requests.Session()
        self.session.headers.update(HEADERS)
        self.min_interval = max(0.0, min_interval)
        self.max_retries = max(1, max_retries)
        self.sleep_func = sleep_func
        self.jitter_func = jitter_func
        self._last_request = 0.0

    def _wait_for_slot(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if self._last_request and elapsed < self.min_interval:
            self.sleep_func(self.min_interval - elapsed)
        self._last_request = time.monotonic()

    def _request(self, method: str, path: str, *, allow_404: bool = False, **kwargs):
        last_error = "network"
        for attempt in range(1, self.max_retries + 1):
            self._wait_for_slot()
            try:
                response = self.session.request(
                    method,
                    API_BASE + path,
                    timeout=(10, 45),
                    **kwargs,
                )
                if response.status_code == 404 and allow_404:
                    return None
                if response.status_code == 200:
                    try:
                        return response.json()
                    except (TypeError, ValueError) as exc:
                        last_error = "invalid_response"
                        if attempt == self.max_retries:
                            raise PwcTransientError(last_error) from exc
                elif response.status_code == 429 or response.status_code >= 500:
                    last_error = "rate_limited" if response.status_code == 429 else "network"
                else:
                    detail = ""
                    try:
                        detail = str(response.json().get("detail") or "")
                    except (TypeError, ValueError, AttributeError):
                        pass
                    raise PwcPermanentError(
                        f"PWC HTTP {response.status_code}: {detail[:300]}"
                    )
                if attempt < self.max_retries:
                    backoff = min(60.0, 2.0 ** (attempt - 1))
                    retry_after = 0.0
                    try:
                        retry_after = max(0.0, float(response.headers.get("Retry-After") or 0))
                    except (TypeError, ValueError):
                        pass
                    self.sleep_func(max(backoff, retry_after) + self.jitter_func(0, 0.25))
            except PwcPermanentError:
                raise
            except requests.RequestException as exc:
                last_error = "timeout" if isinstance(exc, requests.Timeout) else "network"
                if attempt == self.max_retries:
                    raise PwcTransientError(last_error) from exc
                self.sleep_func(min(60.0, 2.0 ** (attempt - 1)) + self.jitter_func(0, 0.25))
        raise PwcTransientError(last_error)

    def lookup(self, references: list[dict]) -> dict:
        if not 1 <= len(references) <= MAX_BATCH_SIZE:
            raise ValueError("PWC lookup batches must contain 1..500 references")
        data = self._request("POST", "/papers/lookup", json={"references": references})
        if not isinstance(data, dict) or not isinstance(data.get("references"), list):
            raise PwcTransientError("invalid_response")
        return data

    def repositories(self, paper_id: str) -> list:
        data = self._request("GET", f"/papers/{paper_id}/repositories", allow_404=True)
        if data is None:
            return []
        if not isinstance(data, list):
            raise PwcTransientError("invalid_response")
        return data


class ProgressLedger:
    def __init__(self, path: Path = PROGRESS_FILE) -> None:
        self.path = Path(path)
        self.records: dict[str, dict] = {}
        self.dirty: dict[str, dict] = {}
        self.physical_lines = 0
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        with gzip.open(self.path, "rt", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                self.physical_lines += 1
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("_meta"):
                    continue
                key = record.get("key")
                if key:
                    self.records[str(key)] = record

    def update(self, key: str, record: dict) -> None:
        payload = dict(record, key=key)
        self.records[key] = payload
        self.dirty[key] = payload

    def _write(self, path: Path, records: list[dict], mode: str, include_meta: bool) -> None:
        with gzip.open(path, mode, encoding="utf-8") as handle:
            if include_meta:
                handle.write(json.dumps({"_meta": True, "schema": PROGRESS_SCHEMA}) + "\n")
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def save(self) -> None:
        if not self.dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        is_new = not self.path.exists()
        dirty_records = list(self.dirty.values())
        self._write(self.path, dirty_records, "wt" if is_new else "at", is_new)
        self.physical_lines += len(dirty_records) + (1 if is_new else 0)
        self.dirty.clear()
        live = len(self.records)
        if live and self.physical_lines > max(1000, live * 2):
            tmp_path = self.path.with_name(self.path.name + ".tmp")
            self._write(tmp_path, list(self.records.values()), "wt", True)
            os.replace(tmp_path, self.path)
            self.physical_lines = live + 1


def load_cache(path: Path = CACHE_FILE) -> list[dict]:
    papers = []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                papers.append(json.loads(line))
    return papers


def save_cache_atomic(
    path: Path, papers: list[dict], backup_path: Optional[Path] = None
) -> None:
    if backup_path is None:
        backup_path = BACKUP_FILE
    if not backup_path.exists():
        shutil.copy2(path, backup_path)
    tmp_path = path.with_name(path.name + ".tmp")
    with gzip.open(tmp_path, "wt", encoding="utf-8") as handle:
        for paper in papers:
            handle.write(json.dumps(paper, ensure_ascii=False) + "\n")
    os.replace(tmp_path, path)


def _conf_matches(conf: str, patterns: Optional[str]) -> bool:
    if not patterns:
        return True
    return any(
        fnmatch.fnmatchcase(conf.casefold(), pattern.strip().casefold())
        for pattern in patterns.split(",")
        if pattern.strip()
    )


def collect_targets(
    papers: list[dict],
    progress: dict[str, dict],
    *,
    conf: Optional[str] = None,
    year: Optional[str] = None,
    retry_failed: bool = False,
    force: bool = False,
    max_failed_attempts: int = 3,
) -> list[dict]:
    candidates = [paper for paper in papers if missing_abstract(paper) or missing_code(paper)]
    candidates.sort(key=lambda paper: 0 if missing_abstract(paper) else 1)
    targets = []
    for paper in candidates:
        if not str(paper.get("paper_name") or "").strip():
            continue
        if classify_paper_url(str(paper.get("paper_url") or "")) == "venue-index":
            continue
        if is_frontmatter_record(paper):
            continue
        if not _conf_matches(str(paper.get("conf") or ""), conf):
            continue
        if year and year != "all":
            match = re.search(r"\d{4}", str(paper.get("conf") or ""))
            if not match or match.group(0) != str(year):
                continue
        key = paper_key(paper)
        previous = progress.get(key)
        if not force:
            if retry_failed and previous is None:
                continue
            if previous:
                if not retry_failed and previous.get("terminal"):
                    continue
                if not retry_failed and int(previous.get("attempts") or 0) >= max_failed_attempts:
                    continue
        targets.append(paper)
    return targets


def _progress_record(
    entry: dict,
    *,
    paper: Optional[dict],
    matched_by: Optional[str],
    abstract_status: str,
    code_status: str,
    terminal: bool,
    reason: str,
    attempts: int,
) -> dict:
    return {
        "conf": entry["paper"].get("conf") or "",
        "url": entry["paper"].get("paper_url") or "",
        "pwc_id": str((paper or {}).get("id") or ""),
        "matched_by": matched_by or "",
        "abstract_status": abstract_status,
        "code_status": code_status,
        "terminal": terminal,
        "reason": reason,
        "attempts": attempts,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


def run(args) -> dict:
    started = time.monotonic()
    dry_run = bool(args.dry_run)
    offline = bool(args.offline)
    if not offline and not dry_run:
        ensure_cache_local(CACHE_FILE, refresh=True)
        ensure_progress_local(PROGRESS_FILE, refresh=True)
    papers = load_cache(CACHE_FILE)
    ledger = ProgressLedger(PROGRESS_FILE)
    targets = collect_targets(
        papers,
        ledger.records,
        conf=args.conf,
        year=args.year,
        retry_failed=args.retry_failed,
        force=args.force,
        max_failed_attempts=args.max_failed_attempts,
    )
    if args.max_papers is not None:
        targets = targets[: max(0, args.max_papers)]
    print(f"[*] PWC targets: {len(targets)} (cache records: {len(papers)})")
    stats = {"processed": 0, "matched": 0, "abstracts": 0, "code": 0, "failed": 0}
    if not targets:
        return stats

    client = PwcClient(min_interval=args.min_interval, max_retries=args.max_retries)
    chunk_size = min(MAX_BATCH_SIZE, max(1, args.chunk_size))
    stop = False
    for offset in range(0, len(targets), chunk_size):
        if stop or (args.soft_timeout and time.monotonic() - started >= args.soft_timeout):
            break
        entries = [make_lookup_entry(paper) for paper in targets[offset : offset + chunk_size]]
        entries = [entry for entry in entries if entry is not None]
        if not entries:
            continue
        try:
            response = client.lookup([entry["reference"] for entry in entries])
        except PwcPermanentError as exc:
            print(f"[!] PWC permanent batch failure; recording attempts and continuing: {exc}")
            for entry in entries:
                previous = ledger.records.get(entry["key"], {})
                attempts = int(previous.get("attempts") or 0) + 1
                ledger.update(
                    entry["key"],
                    _progress_record(
                        entry,
                        paper=None,
                        matched_by=None,
                        abstract_status="failed",
                        code_status="failed",
                        terminal=False,
                        reason="invalid_response",
                        attempts=attempts,
                    ),
                )
                stats["processed"] += 1
                stats["failed"] += 1
                if dry_run:
                    print(f"  [invalid_response] {(entry['paper'].get('paper_name') or '')[:80]}")
            if not dry_run:
                ledger.save()
            continue
        except PwcTransientError as exc:
            print(f"[!] PWC batch failed without advancing paper attempts: {exc}")
            break
        results = {}
        for result in response["references"]:
            if isinstance(result, dict) and result.get("key"):
                results[str(result["key"])] = result

        cache_changed = False
        for entry in entries:
            if args.soft_timeout and time.monotonic() - started >= args.soft_timeout:
                stop = True
                break
            paper = entry["paper"]
            previous = ledger.records.get(entry["key"], {})
            attempts = int(previous.get("attempts") or 0)
            result = results.get(entry["key"])
            remote = validate_match(entry, result or {})
            if remote is None:
                terminal = result is not None
                if not terminal:
                    reason = "invalid_response"
                elif isinstance((result or {}).get("paper"), dict):
                    reason = "identity_mismatch"
                else:
                    reason = "not_found"
                attempts += 0 if terminal else 1
                record = _progress_record(
                    entry,
                    paper=None,
                    matched_by=None,
                    abstract_status="unavailable" if terminal else "failed",
                    code_status="unavailable" if terminal else "failed",
                    terminal=terminal,
                    reason=reason,
                    attempts=attempts,
                )
                ledger.update(entry["key"], record)
                stats["processed"] += 1
                stats["failed"] += 0 if terminal else 1
                if dry_run:
                    print(f"  [{reason}] {(paper.get('paper_name') or '')[:80]}")
                continue

            stats["matched"] += 1
            abstract_status = "not_needed"
            code_status = "not_needed"
            reasons = []
            transient = False
            if missing_abstract(paper):
                abstract = clean_abstract(str(remote.get("abstract") or ""))
                if len(abstract) >= MIN_ABSTRACT_CHARS:
                    paper["paper_abstract"] = abstract
                    abstract_status = "filled"
                    stats["abstracts"] += 1
                    cache_changed = True
                else:
                    abstract_status = "unavailable"
                    reasons.append("empty_abstract")

            if missing_code(paper):
                try:
                    count = max(0, int(remote.get("code_repository_count") or 0))
                except (TypeError, ValueError):
                    count = 0
                if count == 0:
                    code_status = "unavailable"
                    reasons.append("no_repository")
                else:
                    try:
                        repositories = client.repositories(str(remote["id"]))
                        code_url = select_repository(repositories)
                        if code_url:
                            paper["paper_code"] = code_url
                            code_status = "filled"
                            stats["code"] += 1
                            cache_changed = True
                        else:
                            code_status = "unavailable"
                            reasons.append("no_repository")
                    except PwcError as exc:
                        code_status = "failed"
                        reasons.append(exc.reason)
                        transient = True

            if transient:
                attempts += 1
                stats["failed"] += 1
            terminal = not transient and all(
                status in ("filled", "unavailable", "not_needed")
                for status in (abstract_status, code_status)
            )
            reason = ",".join(reasons)
            ledger.update(
                entry["key"],
                _progress_record(
                    entry,
                    paper=remote,
                    matched_by=(result or {}).get("matched_by"),
                    abstract_status=abstract_status,
                    code_status=code_status,
                    terminal=terminal,
                    reason=reason,
                    attempts=attempts,
                ),
            )
            stats["processed"] += 1
            if dry_run:
                print(
                    f"  [matched:{(result or {}).get('matched_by')}] "
                    f"abstract={abstract_status} code={code_status} "
                    f"{(paper.get('paper_name') or '')[:60]}"
                )

        if not dry_run:
            if cache_changed:
                save_cache_atomic(CACHE_FILE, papers)
            ledger.save()
        if args.soft_timeout and time.monotonic() - started >= args.soft_timeout:
            stop = True

    if not dry_run and stats["processed"] and not offline and not args.no_sync:
        sync_cache_artifacts(
            cache_path=CACHE_FILE,
            progress_path=PROGRESS_FILE,
            commit_message="Update PaperVault data artifacts after Papers with Code backfill",
        )
    print(
        "[*] PWC done: processed={processed}, matched={matched}, "
        "abstracts={abstracts}, code={code}, failures={failed}".format(**stats)
    )
    return stats


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--conf", help="Conference name or comma-separated fnmatch patterns")
    parser.add_argument("--year", help="Conference year, or 'all' (default)")
    parser.add_argument("--chunk-size", type=int, default=500, help="Lookup batch size, capped at 500")
    parser.add_argument("--max-papers", type=int)
    parser.add_argument("--soft-timeout", type=float)
    parser.add_argument("--max-failed-attempts", type=int, default=3)
    parser.add_argument("--min-interval", type=float, default=DEFAULT_MIN_INTERVAL)
    parser.add_argument("--max-retries", type=int, default=DEFAULT_MAX_RETRIES)
    retry = parser.add_mutually_exclusive_group()
    retry.add_argument("--retry-failed", action="store_true", help="Retry previously attempted records")
    retry.add_argument("--force", action="store_true", help="Retry all records that still need metadata")
    parser.add_argument("--dry-run", action="store_true", help="Use the local cache; do not write or sync")
    parser.add_argument("--offline", action="store_true", help="Skip HF download and upload")
    parser.add_argument("--no-sync", action="store_true", help="Skip only the final HF upload")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _parser().parse_args(argv)
    if args.chunk_size < 1 or args.chunk_size > MAX_BATCH_SIZE:
        _parser().error(f"--chunk-size must be between 1 and {MAX_BATCH_SIZE}")
    if args.max_papers is not None and args.max_papers < 0:
        _parser().error("--max-papers must be non-negative")
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

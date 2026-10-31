"""SQLite-backed paper repository and search service.

The Hugging Face JSONL.gz artifact remains the canonical interchange format,
but it is a poor online query store: parsing the whole corpus into Python
objects used several GiB of resident memory and every search scanned the
entire corpus. ``PaperRepository`` now materialises a derived SQLite/FTS5
database beside the cache at startup and opens short-lived read-only
connections for requests.

The database records a source fingerprint and schema version. An unchanged
cache is therefore an O(1) startup check; a changed cache is rebuilt into a
temporary file and atomically swapped into place so readers never observe a
partially-built index.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, TextIO, Tuple

from data_artifacts import ensure_cache_local

logger = logging.getLogger("papervault.papers")

_YEAR_RE = re.compile(r"\d{4}")
_TRAILING_YEAR_RE = re.compile(r"\d{4}(.*)$")
_WS_RE = re.compile(r"\s+")

_SEARCH_SCHEMA_VERSION = 2
_INSERT_BATCH_SIZE = 2_000
_FTS_TRIGRAM_MIN_CHARS = 3


@dataclass(slots=True)
class Paper:
    id: str
    conf: str
    year: str
    title: str
    title_format: str
    url: Optional[str]
    authors: List[str]
    abstract: Optional[str]
    code: Optional[str]
    # Retained for compatibility with callers that construct ``Paper``
    # directly. Database-backed result objects are only created for the
    # requested page / id batch, so these derived strings no longer consume
    # corpus-sized resident memory.
    abstract_lower: Optional[str] = None
    authors_joined_lower: str = ""


@dataclass(frozen=True, slots=True)
class ConferenceStats:
    name: str
    total: int
    years: Dict[str, int]


@dataclass
class PaperRepository:
    cache_path: Path
    refresh_on_load: bool = True
    db_path: Optional[Path] = None

    _loaded: bool = field(default=False, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def __post_init__(self) -> None:
        self.cache_path = Path(self.cache_path)
        if self.db_path is None:
            self.db_path = self.cache_path.with_name("papers.sqlite3")
        else:
            self.db_path = Path(self.db_path)

    @property
    def database_path(self) -> Path:
        assert self.db_path is not None
        return self.db_path

    def ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            ensure_cache_local(
                str(self.cache_path),
                refresh=self.refresh_on_load,
            )
            self._ensure_database()
            self._loaded = True

    def reload(self) -> None:
        """Refresh the source artifact and atomically rebuild the search DB."""

        with self._lock:
            ensure_cache_local(
                str(self.cache_path),
                refresh=self.refresh_on_load,
            )
            self._ensure_database(force=True)
            self._loaded = True

    def _source_fingerprint(self) -> Tuple[str, str]:
        try:
            stat = self.cache_path.stat()
        except FileNotFoundError:
            return ("-1", "-1")
        return (str(stat.st_size), str(stat.st_mtime_ns))

    def _ensure_database(self, *, force: bool = False) -> None:
        fingerprint = self._source_fingerprint()
        if not force and self._database_is_current(fingerprint):
            logger.info("Reusing SQLite paper index at %s", self.database_path)
            return

        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.database_path.with_name(
            f".{self.database_path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            rows, dropped = self._build_database(temp_path, fingerprint)
            os.replace(temp_path, self.database_path)
        finally:
            # The normal path moved the file already. On a parse / disk error,
            # remove only this uniquely-named incomplete build artifact.
            temp_path.unlink(missing_ok=True)

        logger.info(
            "Built SQLite paper index with %d papers (%d duplicate rows dropped) at %s",
            rows,
            dropped,
            self.database_path,
        )

    def _database_is_current(self, fingerprint: Tuple[str, str]) -> bool:
        if not self.database_path.is_file():
            return False
        try:
            with self._connect(readonly=True, ensure=False) as conn:
                version = conn.execute("PRAGMA user_version").fetchone()[0]
                if version != _SEARCH_SCHEMA_VERSION:
                    return False
                metadata = dict(conn.execute("SELECT key, value FROM metadata"))
                required = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type IN ('table', 'view')"
                    )
                }
        except (OSError, sqlite3.Error):
            return False

        return (
            {"papers", "paper_fts", "metadata"}.issubset(required)
            and metadata.get("source_size") == fingerprint[0]
            and metadata.get("source_mtime_ns") == fingerprint[1]
        )

    def _open_cache(self) -> TextIO:
        if not self.cache_path.exists():
            # Offline tests and fresh installations legitimately start with an
            # empty cache. Build a valid empty database instead of keeping a
            # special in-memory repository path alive.
            from io import StringIO

            return StringIO("")
        if self.cache_path.name.endswith(".gz"):
            return gzip.open(self.cache_path, "rt", encoding="utf-8")
        return self.cache_path.open("r", encoding="utf-8")

    def _build_database(
        self,
        temp_path: Path,
        fingerprint: Tuple[str, str],
    ) -> Tuple[int, int]:
        conn = sqlite3.connect(str(temp_path))
        try:
            conn.executescript(
                f"""
                PRAGMA journal_mode=OFF;
                PRAGMA synchronous=OFF;
                PRAGMA temp_store=FILE;
                PRAGMA user_version={_SEARCH_SCHEMA_VERSION};

                CREATE TABLE metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                ) WITHOUT ROWID;

                CREATE TABLE papers (
                    rowid INTEGER PRIMARY KEY,
                    id TEXT NOT NULL UNIQUE,
                    conf TEXT NOT NULL,
                    year INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT,
                    authors_json TEXT NOT NULL,
                    authors_text TEXT NOT NULL,
                    abstract TEXT,
                    code TEXT
                );

                CREATE VIRTUAL TABLE paper_fts USING fts5(
                    title,
                    abstract,
                    authors,
                    content='',
                    tokenize='trigram'
                );
                """
            )

            seen_ids: set[str] = set()
            paper_batch: List[Tuple[object, ...]] = []
            fts_batch: List[Tuple[object, ...]] = []
            rowid = 0
            dropped = 0

            with self._open_cache() as source:
                for line_num, line in enumerate(source, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        raw = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise ValueError(
                            f"Malformed JSON on line {line_num} of "
                            f"{self.cache_path}: {exc}"
                        ) from exc

                    conf_key = raw.get("conf")
                    if not isinstance(conf_key, str):
                        raise ValueError(
                            f"Missing or invalid 'conf' field on line {line_num} "
                            f"of {self.cache_path}"
                        )
                    year_match = _YEAR_RE.search(conf_key)
                    if year_match is None:
                        logger.warning("Skip conf without year in key: %s", conf_key)
                        continue

                    year = year_match.group()
                    conf_name = _TRAILING_YEAR_RE.sub("", conf_key).strip().upper()
                    title = str(raw.get("paper_name") or "")
                    pid = hashlib.sha1(
                        f"{conf_name}|{year}|{title}".encode("utf-8")
                    ).hexdigest()[:16]
                    if pid in seen_ids:
                        dropped += 1
                        continue
                    seen_ids.add(pid)

                    authors_value = raw.get("paper_authors") or []
                    if not isinstance(authors_value, list):
                        authors_value = [str(authors_value)]
                    authors = [str(author) for author in authors_value]
                    authors_text = " ".join(authors)
                    abstract_value = raw.get("paper_abstract")
                    abstract = (
                        str(abstract_value) if abstract_value is not None else None
                    )
                    url_value = raw.get("paper_url")
                    code_value = raw.get("paper_code")

                    rowid += 1
                    paper_batch.append(
                        (
                            rowid,
                            pid,
                            conf_name,
                            int(year),
                            title,
                            str(url_value) if url_value is not None else None,
                            json.dumps(authors, ensure_ascii=False),
                            authors_text,
                            abstract,
                            str(code_value) if code_value is not None else None,
                        )
                    )
                    fts_batch.append(
                        (
                            rowid,
                            _normalize(title),
                            _normalize(abstract),
                            _normalize(authors_text),
                        )
                    )

                    if len(paper_batch) >= _INSERT_BATCH_SIZE:
                        self._flush_build_batch(conn, paper_batch, fts_batch)

            self._flush_build_batch(conn, paper_batch, fts_batch)
            conn.executescript(
                """
                CREATE INDEX idx_papers_conf ON papers(conf);
                CREATE INDEX idx_papers_year ON papers(year);
                CREATE INDEX idx_papers_conf_year ON papers(conf, year);
                CREATE INDEX idx_papers_title ON papers(title COLLATE NOCASE);
                """
            )
            conn.executemany(
                "INSERT INTO metadata(key, value) VALUES (?, ?)",
                (
                    ("source_size", fingerprint[0]),
                    ("source_mtime_ns", fingerprint[1]),
                    ("paper_count", str(rowid)),
                    ("duplicate_count", str(dropped)),
                ),
            )
            conn.commit()
            conn.execute("PRAGMA optimize")
            return rowid, dropped
        except sqlite3.OperationalError as exc:
            if "fts5" in str(exc).lower():
                raise RuntimeError(
                    "SQLite FTS5 support is required to build the PaperVault "
                    "search index."
                ) from exc
            raise
        finally:
            conn.close()

    @staticmethod
    def _flush_build_batch(
        conn: sqlite3.Connection,
        paper_batch: List[Tuple[object, ...]],
        fts_batch: List[Tuple[object, ...]],
    ) -> None:
        if not paper_batch:
            return
        conn.executemany(
            """
            INSERT INTO papers(
                rowid, id, conf, year, title, url, authors_json,
                authors_text, abstract, code
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            paper_batch,
        )
        conn.executemany(
            "INSERT INTO paper_fts(rowid, title, abstract, authors) "
            "VALUES (?, ?, ?, ?)",
            fts_batch,
        )
        paper_batch.clear()
        fts_batch.clear()

    def _connect(
        self,
        *,
        readonly: bool = True,
        ensure: bool = True,
    ) -> sqlite3.Connection:
        if ensure:
            self.ensure_loaded()
        if readonly:
            uri = f"{self.database_path.resolve().as_uri()}?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=10.0)
        else:
            conn = sqlite3.connect(str(self.database_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON" if readonly else "PRAGMA query_only=OFF")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    def paper_count(self) -> int:
        with self._connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0])

    def conference_count(self) -> int:
        with self._connect() as conn:
            return int(
                conn.execute("SELECT COUNT(DISTINCT conf) FROM papers").fetchone()[0]
            )

    def conference_stats(self) -> List[ConferenceStats]:
        grouped: Dict[str, Dict[str, int]] = {}
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT conf, CAST(year AS TEXT) AS year, COUNT(*) AS count
                FROM papers
                GROUP BY conf, year
                ORDER BY conf, year
                """
            )
            for row in rows:
                grouped.setdefault(row["conf"], {})[row["year"]] = row["count"]
        return [
            ConferenceStats(name=name, total=sum(years.values()), years=years)
            for name, years in grouped.items()
        ]

    def all_papers(self) -> List[Paper]:
        """Compatibility accessor; not used by online request hot paths."""

        with self._connect() as conn:
            return [self._row_to_paper(row) for row in conn.execute(_PAPER_SELECT)]

    def get_by_id(self, paper_id: str) -> Optional[Paper]:
        with self._connect() as conn:
            row = conn.execute(
                f"{_PAPER_SELECT} WHERE p.id = ?",
                (paper_id,),
            ).fetchone()
        return self._row_to_paper(row) if row is not None else None

    def confs(self) -> Dict[str, List[Paper]]:
        """Compatibility grouping; API statistics use ``conference_stats``."""

        grouped: Dict[str, List[Paper]] = {}
        for paper in self.all_papers():
            grouped.setdefault(paper.conf, []).append(paper)
        return grouped

    def support_confs(self) -> List[str]:
        with self._connect() as conn:
            return [
                row[0]
                for row in conn.execute("SELECT DISTINCT conf FROM papers ORDER BY conf")
            ]

    @staticmethod
    def _row_to_paper(row: sqlite3.Row) -> Paper:
        authors = json.loads(row["authors_json"])
        abstract = row["abstract"]
        return Paper(
            id=row["id"],
            conf=row["conf"],
            year=str(row["year"]),
            title=row["title"],
            title_format=_normalize(row["title"]),
            url=row["url"],
            authors=authors,
            abstract=abstract,
            code=row["code"],
            abstract_lower=abstract.lower() if abstract else None,
            authors_joined_lower=_normalize(row["authors_text"]),
        )

    def search(self, criteria: "SearchCriteria") -> Tuple[List[Paper], int]:
        query_value = _normalize(criteria.query) if criteria.query else ""
        if query_value == "#":
            query_value = ""
        query_tokens = query_value.split() if query_value else []
        author_value = _normalize(criteria.author) if criteria.author else ""
        author_tokens = author_value.split() if author_value else []

        use_title = criteria.field in ("title", "any") and bool(query_tokens)
        use_abstract = use_title
        use_author = criteria.field in ("author", "any") and bool(query_tokens)

        # The trigram tokenizer preserves the legacy arbitrary-substring
        # semantics while still narrowing the candidate set for terms of at
        # least three characters. FTS5 cannot match shorter trigram terms, so
        # those are deliberately left to the exact ``instr`` filters below.
        query_fts_tokens = _fts_eligible_tokens(query_tokens)
        author_fts_tokens = _fts_eligible_tokens(author_tokens)

        fts_parts: List[str] = []
        if query_fts_tokens:
            field_parts = []
            if use_title:
                field_parts.append(
                    _fts_field_expression("title", query_fts_tokens, "AND")
                )
            if use_abstract:
                field_parts.append(
                    _fts_field_expression("abstract", query_fts_tokens, "AND")
                )
            if use_author:
                field_parts.append(
                    _fts_field_expression("authors", query_fts_tokens, "AND")
                )
            fts_parts.append(f"({' OR '.join(field_parts)})")
        # The independent author filter is OR-based. It is safe to narrow via
        # FTS only when every alternative is trigram-eligible; otherwise a row
        # matching only a short token would be excluded before ``instr`` can
        # validate it.
        if author_fts_tokens and len(author_fts_tokens) == len(author_tokens):
            fts_parts.append(
                _fts_field_expression("authors", author_fts_tokens, "OR")
            )
        fts_query = " AND ".join(fts_parts)

        filters: List[str] = []
        filter_params: List[object] = []
        if fts_query:
            filters.append("paper_fts MATCH ?")
            filter_params.append(fts_query)

        if criteria.confs:
            confs = sorted({conf.upper() for conf in criteria.confs})
            placeholders = ", ".join("?" for _ in confs)
            filters.append(f"p.conf IN ({placeholders})")
            filter_params.extend(confs)
        if criteria.since is not None:
            filters.append("p.year >= ?")
            filter_params.append(criteria.since)
        if criteria.until is not None:
            filters.append("p.year <= ?")
            filter_params.append(criteria.until)

        if author_tokens:
            author_checks, params = _substring_any_sql("p.authors_text", author_tokens)
            filters.append(author_checks)
            filter_params.extend(params)

        relevance_sql = "0"
        relevance_params: List[object] = []
        if query_tokens:
            full_fields: List[str] = []
            score_fields: List[str] = []
            for enabled, column, weight in (
                (use_title, "p.title", 3),
                (use_abstract, "p.abstract", 1),
                (use_author, "p.authors_text", 2),
            ):
                if not enabled:
                    continue
                full_sql, full_params = _substring_all_sql(column, query_tokens)
                full_fields.append(full_sql)
                filter_params.extend(full_params)

                hits_sql, hits_params = _substring_hits_sql(column, query_tokens)
                score_fields.append(f"({hits_sql}) * {weight}")
                relevance_params.extend(hits_params)
            filters.append(f"({' OR '.join(full_fields)})")
            relevance_sql = " + ".join(score_fields)

        from_sql = "papers AS p"
        if fts_query:
            from_sql += " JOIN paper_fts ON paper_fts.rowid = p.rowid"
        where_sql = f" WHERE {' AND '.join(filters)}" if filters else ""

        count_sql = f"SELECT COUNT(*) FROM {from_sql}{where_sql}"
        with self._connect() as conn:
            total = int(conn.execute(count_sql, filter_params).fetchone()[0])

            sort_column, direction = _sql_sort(criteria.sort)
            order_parts = []
            if query_tokens:
                order_parts.append("relevance_score DESC")
            order_parts.append(f"{sort_column} {direction}")
            if query_tokens and fts_query:
                # Preserve the public weighted-ranking + user-sort contract;
                # BM25 resolves only otherwise-equal rows within that order.
                order_parts.append("fts_rank ASC")
            order_parts.append("p.id ASC")

            select_extras = f", ({relevance_sql}) AS relevance_score"
            if fts_query:
                select_extras += ", bm25(paper_fts, 3.0, 1.0, 2.0) AS fts_rank"
            else:
                select_extras += ", 0.0 AS fts_rank"

            offset = (criteria.page - 1) * criteria.size
            page_sql = (
                f"SELECT {_PAPER_COLUMNS}{select_extras} FROM {from_sql}{where_sql} "
                f"ORDER BY {', '.join(order_parts)} LIMIT ? OFFSET ?"
            )
            page_params = [
                *relevance_params,
                *filter_params,
                criteria.size,
                offset,
            ]
            rows = conn.execute(page_sql, page_params).fetchall()
        return [self._row_to_paper(row) for row in rows], total


@dataclass(slots=True)
class SearchCriteria:
    query: Optional[str]
    field: str  # title | author | any
    confs: List[str]
    since: Optional[int]
    until: Optional[int]
    author: Optional[str]
    sort: str  # -year | year | -title | title | -conf | conf
    page: int
    size: int


_PAPER_COLUMNS = (
    "p.id, p.conf, p.year, p.title, p.url, p.authors_json, "
    "p.authors_text, p.abstract, p.code"
)
_PAPER_SELECT = f"SELECT {_PAPER_COLUMNS} FROM papers AS p"


def _normalize(value: Optional[str]) -> str:
    if not value:
        return ""
    return _WS_RE.sub(" ", value).strip().lower().replace("-", " ")


def _fts_quote(token: str) -> str:
    return f'"{token.replace(chr(34), chr(34) * 2)}"'


def _fts_eligible_tokens(tokens: List[str]) -> List[str]:
    return [token for token in tokens if len(token) >= _FTS_TRIGRAM_MIN_CHARS]


def _fts_field_expression(field_name: str, tokens: List[str], operator: str) -> str:
    joined = f" {operator} ".join(_fts_quote(token) for token in tokens)
    return f"{field_name} : ({joined})"


def _normalised_column_sql(column: str) -> str:
    return f"lower(replace(coalesce({column}, ''), '-', ' '))"


def _substring_all_sql(column: str, tokens: List[str]) -> Tuple[str, List[object]]:
    expression = _normalised_column_sql(column)
    return (
        "(" + " AND ".join(f"instr({expression}, ?) > 0" for _ in tokens) + ")",
        list(tokens),
    )


def _substring_any_sql(column: str, tokens: List[str]) -> Tuple[str, List[object]]:
    expression = _normalised_column_sql(column)
    return (
        "(" + " OR ".join(f"instr({expression}, ?) > 0" for _ in tokens) + ")",
        list(tokens),
    )


def _substring_hits_sql(column: str, tokens: List[str]) -> Tuple[str, List[object]]:
    expression = _normalised_column_sql(column)
    parts = [
        f"CASE WHEN instr({expression}, ?) > 0 THEN 1 ELSE 0 END"
        for _ in tokens
    ]
    return " + ".join(parts), list(tokens)


def _sql_sort(sort: str) -> Tuple[str, str]:
    descending = sort.startswith("-")
    key = sort[1:] if descending else sort
    column = {
        "year": "p.year",
        "conf": "p.conf COLLATE NOCASE",
        "title": "p.title COLLATE NOCASE",
    }[key]
    return column, "DESC" if descending else "ASC"


# Compatibility helpers retained for external scripts and focused unit tests.
def _count_token_hits(haystack: str, tokens: List[str]) -> int:
    if not tokens or not haystack:
        return 0
    return sum(1 for token in tokens if token in haystack)


def _author_score_joined(joined_lower: str, tokens: List[str]) -> int:
    return _count_token_hits(joined_lower, tokens)


def _author_score(authors: Iterable[str], needle: str) -> int:
    if not needle:
        return 0
    joined = " ".join(author.lower().replace("-", " ") for author in authors)
    return _count_token_hits(joined, needle.split())


def _author_matches(authors: Iterable[str], needle: str) -> bool:
    return _author_score(authors, needle) > 0


def _title_matches(paper: Paper, query: str) -> bool:
    if not query:
        return False
    return _count_token_hits(paper.title_format, query.split()) > 0


def search_papers(
    repo: PaperRepository,
    criteria: SearchCriteria,
) -> Tuple[List[Paper], int]:
    return repo.search(criteria)

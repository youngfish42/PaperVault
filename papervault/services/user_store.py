"""Per-user saved search queries backed by a dedicated SQLite database.

The papers index (``papers.sqlite3``) is read-only and atomically rebuilt,
so mutable user data lives in a separate ``users.sqlite3`` file. The store
is exposed via ``current_app.extensions["user_store"]`` — mirroring how
``PaperRepository`` is mounted on ``current_app.extensions`` — but attached
lazily on first use so ``app.py`` needs no extra wiring.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import current_app

# Sentinel distinguishing "last_count not provided" from an explicit None
# (which clears the stored count) in ``update_query``.
_UNSET = object()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS saved_queries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_key TEXT NOT NULL,
    name TEXT NOT NULL,
    dsl TEXT NOT NULL,
    last_count INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_saved_queries_user_key
    ON saved_queries (user_key);
"""

_MOUNT_LOCK = threading.Lock()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class UserStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self._lock = threading.Lock()
        self._initialized = False

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            conn = self._connect()
            try:
                # WAL is persistent once set, so it lives here instead of on
                # the per-call connect path.
                conn.execute("PRAGMA journal_mode=WAL")
                conn.executescript(_SCHEMA)
                conn.commit()
            finally:
                conn.close()
            self._initialized = True

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": row["id"],
            "name": row["name"],
            "dsl": row["dsl"],
            "last_count": row["last_count"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_queries(self, user_key: str) -> List[Dict[str, Any]]:
        self._ensure_schema()
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM saved_queries WHERE user_key = ? "
                "ORDER BY updated_at DESC, id DESC",
                (user_key,),
            ).fetchall()
        finally:
            conn.close()
        return [self._row_to_dict(row) for row in rows]

    def create_query(
        self,
        user_key: str,
        name: str,
        dsl: str,
        last_count: Optional[int],
        max_per_user: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """Insert a saved query; returns the created row.

        When ``max_per_user`` is given, the quota check runs inside the write
        lock (together with the INSERT) so concurrent requests cannot both
        pass it; returns ``None`` when the user is already at the cap.
        """

        self._ensure_schema()
        now = _utc_now()
        with self._lock:
            conn = self._connect()
            try:
                if max_per_user is not None:
                    count = conn.execute(
                        "SELECT COUNT(*) AS n FROM saved_queries "
                        "WHERE user_key = ?",
                        (user_key,),
                    ).fetchone()["n"]
                    if int(count) >= max_per_user:
                        return None
                cur = conn.execute(
                    "INSERT INTO saved_queries "
                    "(user_key, name, dsl, last_count, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (user_key, name, dsl, last_count, now, now),
                )
                conn.commit()
                row = conn.execute(
                    "SELECT * FROM saved_queries WHERE id = ?",
                    (cur.lastrowid,),
                ).fetchone()
            finally:
                conn.close()
        return self._row_to_dict(row)

    def update_query(
        self,
        user_key: str,
        query_id: int,
        *,
        name: Optional[str] = None,
        dsl: Optional[str] = None,
        last_count: Any = _UNSET,
    ) -> Optional[Dict[str, Any]]:
        """Update a row owned by ``user_key``; ``None`` when not found/owned.

        ``last_count`` uses the ``_UNSET`` sentinel so callers can set the
        column to NULL explicitly while omitting it leaves it untouched.
        ``updated_at`` is always bumped.
        """

        self._ensure_schema()
        assignments: List[str] = []
        params: List[Any] = []
        if name is not None:
            assignments.append("name = ?")
            params.append(name)
        if dsl is not None:
            assignments.append("dsl = ?")
            params.append(dsl)
        if last_count is not _UNSET:
            assignments.append("last_count = ?")
            params.append(last_count)
        assignments.append("updated_at = ?")
        params.append(_utc_now())
        params.extend([query_id, user_key])

        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    f"UPDATE saved_queries SET {', '.join(assignments)} "
                    "WHERE id = ? AND user_key = ?",
                    params,
                )
                conn.commit()
                if cur.rowcount == 0:
                    return None
                row = conn.execute(
                    "SELECT * FROM saved_queries WHERE id = ?",
                    (query_id,),
                ).fetchone()
            finally:
                conn.close()
        return self._row_to_dict(row) if row is not None else None

    def delete_query(self, user_key: str, query_id: int) -> bool:
        self._ensure_schema()
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "DELETE FROM saved_queries WHERE id = ? AND user_key = ?",
                    (query_id, user_key),
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    def count_queries(self, user_key: str) -> int:
        self._ensure_schema()
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM saved_queries WHERE user_key = ?",
                (user_key,),
            ).fetchone()
        finally:
            conn.close()
        return int(row["n"])


def get_user_store() -> UserStore:
    """Return the per-app ``UserStore``, creating and mounting it lazily.

    The db path resolves to ``settings.user_db_path`` or, when unset, a
    ``users.sqlite3`` file beside ``settings.cache_path`` (the same
    derivation philosophy ``PaperRepository`` uses for ``papers.sqlite3``).
    """

    store = current_app.extensions.get("user_store")
    if store is None:
        with _MOUNT_LOCK:
            store = current_app.extensions.get("user_store")
            if store is None:
                settings = current_app.extensions["settings"]
                db_path = settings.user_db_path or (
                    settings.cache_path.parent / "users.sqlite3"
                )
                store = UserStore(Path(db_path))
                current_app.extensions["user_store"] = store
    return store

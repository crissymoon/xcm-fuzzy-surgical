"""SQLite backed snapshots of file contents.

Every write made by the editor can record the
previous on-disk text in a local SQLite database first. That gives the user a
safety net when an edit goes wrong, without scattering backup files next to
their source tree.

The store is deliberately defensive: a missing database, a read-only home
directory or a locked file only disables history. Saving source files must
never depend on it.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import sys
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Union

DEFAULT_PER_FILE_LIMIT = 25
DEFAULT_MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_SNAPSHOT_BYTES = 4 * 1024 * 1024
APP_DIR_NAME = "fuzzy-surgical"
DATABASE_NAME = "history.sqlite3"

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    digest TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    size INTEGER NOT NULL,
    original_size INTEGER NOT NULL,
    compressed INTEGER NOT NULL DEFAULT 1,
    content BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snapshots_path ON snapshots(path, id DESC);
"""


@dataclass(frozen=True)
class Revision:
    """Metadata for one stored snapshot, without its content."""

    id: int
    path: str
    reason: str
    stored_size: int
    original_size: int
    created_at: float
    digest: str
    compressed: bool


def default_history_path() -> Path:
    """Return the per-user database location for the current platform."""
    override = os.environ.get("FUZZY_SURGICAL_HISTORY")
    if override:
        return Path(override).expanduser()

    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return Path(base) / APP_DIR_NAME / DATABASE_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIR_NAME / DATABASE_NAME
    base = os.environ.get("XDG_DATA_HOME")
    if not base:
        base = os.path.join(os.path.expanduser("~"), ".local", "share")
    return Path(base) / APP_DIR_NAME / DATABASE_NAME


def resolve_path(file_path: Union[str, os.PathLike]) -> str:
    """Resolve a path without raising on broken links."""
    try:
        return os.path.realpath(os.fspath(file_path))
    except (OSError, TypeError, ValueError):
        return str(file_path)


class SnapshotStore:
    """Keeps the last good content of edited files in SQLite."""

    def __init__(
        self,
        database_path: Optional[Union[str, os.PathLike]] = None,
        per_file_limit: int = DEFAULT_PER_FILE_LIMIT,
        max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
        enabled: bool = True,
    ) -> None:
        self.database_path = (
            Path(database_path).expanduser() if database_path else default_history_path()
        )
        self.per_file_limit = max(1, int(per_file_limit))
        self.max_total_bytes = max(0, int(max_total_bytes))
        self.enabled = bool(enabled)
        self.error = ""
        self._connection: Optional[sqlite3.Connection] = None
        if self.enabled:
            self._connect()

    @property
    def available(self) -> bool:
        return self.enabled and self._connection is not None

    def _connect(self) -> None:
        try:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(str(self.database_path), timeout=5.0)
            connection.execute("PRAGMA busy_timeout=5000")
            try:
                connection.execute("PRAGMA journal_mode=WAL")
            except sqlite3.Error:
                pass
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.executescript(SCHEMA)
            connection.commit()
        except (sqlite3.Error, OSError) as exc:
            self.error = "History is unavailable: " + (str(exc) or exc.__class__.__name__)
            self.enabled = False
            self._connection = None
            return
        self._connection = connection

    def close(self) -> None:
        connection = self._connection
        self._connection = None
        if connection is None:
            return
        try:
            connection.commit()
        except sqlite3.Error:
            pass
        try:
            connection.close()
        except sqlite3.Error:
            pass

    def record_current(
        self,
        file_path: Union[str, os.PathLike],
        reason: str = "save",
    ) -> Optional[Revision]:
        """Snapshot the current contents of file_path, if it exists on disk."""
        if not self.available:
            return None
        target = resolve_path(file_path)
        try:
            with open(target, "rb") as handle:
                data = handle.read(MAX_SNAPSHOT_BYTES + 1)
        except OSError:
            return None
        if len(data) > MAX_SNAPSHOT_BYTES:
            self.error = "That file is too large to snapshot. The save still works."
            return None
        return self.record_bytes(target, data, reason=reason)

    def record_text(
        self,
        file_path: Union[str, os.PathLike],
        text: str,
        reason: str = "save",
    ) -> Optional[Revision]:
        """Snapshot text as it was before an edit."""
        if not self.available:
            return None
        return self.record_bytes(
            file_path,
            text.encode("utf-8", "surrogateescape"),
            reason=reason,
        )

    def record_bytes(
        self,
        file_path: Union[str, os.PathLike],
        data: bytes,
        reason: str = "save",
    ) -> Optional[Revision]:
        """Store one snapshot unless it duplicates the newest one."""
        if not self.available:
            return None
        if len(data) > MAX_SNAPSHOT_BYTES:
            self.error = "That file is too large to snapshot. The save still works."
            return None

        target = resolve_path(file_path)
        digest = hashlib.sha256(data).hexdigest()
        connection = self._connection
        if connection is None:
            return None

        try:
            previous = connection.execute(
                "SELECT digest FROM snapshots WHERE path = ? ORDER BY id DESC LIMIT 1",
                (target,),
            ).fetchone()
            if previous is not None and previous[0] == digest:
                return None

            compressed = zlib.compress(data, 6)
            created_at = time.time()
            cursor = connection.execute(
                "INSERT INTO snapshots "
                "(path, digest, reason, created_at, size, original_size, compressed, content) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    target,
                    digest,
                    reason[:80],
                    created_at,
                    len(compressed),
                    len(data),
                    1,
                    sqlite3.Binary(compressed),
                ),
            )
            connection.commit()
            snapshot_id = int(cursor.lastrowid or 0)
            self._prune(connection, target)
            connection.commit()
        except (sqlite3.Error, zlib.error) as exc:
            self.error = "Could not store a snapshot: " + (str(exc) or exc.__class__.__name__)
            return None

        self.error = ""
        return Revision(
            id=snapshot_id,
            path=target,
            reason=reason[:80],
            stored_size=len(compressed),
            original_size=len(data),
            created_at=created_at,
            digest=digest,
            compressed=True,
        )

    def list_revisions(
        self,
        file_path: Union[str, os.PathLike],
        limit: int = 50,
    ) -> List[Revision]:
        """Return the newest snapshots for one file."""
        if not self.available:
            return []
        target = resolve_path(file_path)
        limit = max(1, min(int(limit), 500))
        try:
            rows = self._connection.execute(
                "SELECT id, path, reason, size, original_size, created_at, digest, compressed "
                "FROM snapshots WHERE path = ? ORDER BY id DESC LIMIT ?",
                (target, limit),
            ).fetchall()
        except sqlite3.Error as exc:
            self.error = "Could not read history: " + (str(exc) or exc.__class__.__name__)
            return []
        return [
            Revision(
                id=int(row[0]),
                path=str(row[1]),
                reason=str(row[2]),
                stored_size=int(row[3]),
                original_size=int(row[4]),
                created_at=float(row[5]),
                digest=str(row[6]),
                compressed=bool(row[7]),
            )
            for row in rows
        ]

    def read_bytes(self, snapshot_id: int) -> Optional[bytes]:
        """Return the raw text stored in a snapshot."""
        if not self.available:
            return None
        try:
            row = self._connection.execute(
                "SELECT content, compressed FROM snapshots WHERE id = ?",
                (int(snapshot_id),),
            ).fetchone()
        except sqlite3.Error as exc:
            self.error = "Could not read the snapshot: " + (str(exc) or exc.__class__.__name__)
            return None
        if row is None:
            return None
        data = bytes(row[0])
        if row[1]:
            try:
                return zlib.decompress(data)
            except zlib.error as exc:
                self.error = "The snapshot is damaged: " + (str(exc) or "zlib error")
                return None
        return data

    def read_text(self, snapshot_id: int) -> Optional[str]:
        """Return a snapshot decoded the same way the editors read files."""
        data = self.read_bytes(snapshot_id)
        if data is None:
            return None
        return data.decode("utf-8", "surrogateescape")

    def count(self, file_path: Optional[Union[str, os.PathLike]] = None) -> int:
        if not self.available:
            return 0
        try:
            if file_path is None:
                row = self._connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()
            else:
                row = self._connection.execute(
                    "SELECT COUNT(*) FROM snapshots WHERE path = ?",
                    (resolve_path(file_path),),
                ).fetchone()
        except sqlite3.Error:
            return 0
        return int(row[0]) if row else 0

    def _prune(self, connection: sqlite3.Connection, target: str) -> None:
        keep_ids = [
            int(row[0])
            for row in connection.execute(
                "SELECT id FROM snapshots WHERE path = ? ORDER BY id DESC LIMIT ?",
                (target, self.per_file_limit),
            ).fetchall()
        ]
        if keep_ids:
            placeholders = ",".join("?" for _ in keep_ids)
            connection.execute(
                f"DELETE FROM snapshots WHERE path = ? AND id NOT IN ({placeholders})",
                (target, *keep_ids),
            )

        total = 0
        remove: List[int] = []
        rows = connection.execute("SELECT id, size FROM snapshots ORDER BY id DESC").fetchall()
        for position, (snapshot_id, stored_size) in enumerate(rows):
            total += int(stored_size)
            if total > self.max_total_bytes and position > 0:
                # Always keep the newest snapshot, even if it alone is large.
                remove.append(int(snapshot_id))
        if remove:
            placeholders = ",".join("?" for _ in remove)
            connection.execute(
                f"DELETE FROM snapshots WHERE id IN ({placeholders})",
                tuple(remove),
            )

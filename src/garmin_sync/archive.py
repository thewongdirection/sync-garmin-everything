"""SQLite store: raw Garmin payloads (source of truth) plus the per-sink push ledger.

Raw payloads are archived verbatim so that normalization is a pure, re-runnable
function of the archive: mapping bugs can be fixed and re-applied without calling
Garmin again. All SQL is parameterized.

Each raw row remembers *when* it was fetched and *which span of days* the fetch covered
(``span_end``). Together these decide whether a row is final or must be fetched again
(see :func:`garmin_sync.pull.is_final`), so a day archived while it was still in progress
is never mistaken for a finished one.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from garmin_sync.paths import PRIVATE_FILE_MODE, ensure_private_dir

SCHEMA_VERSION = 2
# PRAGMA cannot take bound parameters; the value is a module constant, never input.
_SET_VERSION = f"PRAGMA user_version = {SCHEMA_VERSION}"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS raw (
    endpoint   TEXT NOT NULL,
    key        TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    span_end   TEXT NOT NULL DEFAULT '',
    payload    TEXT NOT NULL,
    PRIMARY KEY (endpoint, key)
);
CREATE TABLE IF NOT EXISTS ledger (
    sink         TEXT NOT NULL,
    external_id  TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    pushed_at    TEXT NOT NULL,
    remote_ref   TEXT,
    PRIMARY KEY (sink, external_id)
);
"""

Clock = Callable[[], datetime]


@dataclass(frozen=True)
class LedgerEntry:
    content_hash: str
    pushed_at: str
    remote_ref: str | None


@dataclass(frozen=True)
class RawMeta:
    """Provenance of an archived payload."""

    fetched_at: datetime  # aware UTC
    span_end: str  # last ISO date the fetch covered; '' when unknown (treated as not final)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class Archive:
    """Thin wrapper over the SQLite file. Create with :meth:`open`."""

    def __init__(self, conn: sqlite3.Connection, clock: Clock = _utc_now) -> None:
        self._conn = conn
        self._clock = clock

    @classmethod
    def open(cls, path: Path, clock: Clock = _utc_now) -> Archive:
        ensure_private_dir(path.parent)
        conn = sqlite3.connect(path)
        path.chmod(PRIVATE_FILE_MODE)  # tighten even a pre-existing, laxer file
        conn.executescript(_SCHEMA)
        _migrate(conn)
        return cls(conn, clock)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Archive:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    @contextmanager
    def _cursor(self) -> Iterator[sqlite3.Cursor]:
        with self._conn, closing(self._conn.cursor()) as cur:
            yield cur

    # -- raw payloads -----------------------------------------------------------------

    def put_raw(
        self, endpoint: str, key: str, payload: Any, *, span_end: str | None = None
    ) -> None:
        """Store ``payload``; ``span_end`` is the last day it covers (defaults to ``key``)."""
        covered = span_end if span_end is not None else key
        with self._cursor() as cur:
            cur.execute(
                "INSERT OR REPLACE INTO raw (endpoint, key, fetched_at, span_end, payload)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    endpoint,
                    key,
                    self._clock().isoformat(timespec="seconds"),
                    covered,
                    json.dumps(payload, separators=(",", ":")),
                ),
            )

    def has_raw(self, endpoint: str, key: str) -> bool:
        return self.raw_meta(endpoint, key) is not None

    def raw_meta(self, endpoint: str, key: str) -> RawMeta | None:
        with self._cursor() as cur:
            cur.execute(
                "SELECT fetched_at, span_end FROM raw WHERE endpoint = ? AND key = ?",
                (endpoint, key),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return RawMeta(fetched_at=datetime.fromisoformat(row[0]), span_end=row[1])

    def get_raw(self, endpoint: str, key: str) -> Any | None:
        with self._cursor() as cur:
            cur.execute("SELECT payload FROM raw WHERE endpoint = ? AND key = ?", (endpoint, key))
            row = cur.fetchone()
        return json.loads(row[0]) if row else None

    def iter_raw(self, endpoint: str, key_from: str, key_to: str) -> Iterator[tuple[str, Any]]:
        """Payloads for ``endpoint`` with keys in ``[key_from, key_to]`` (ISO dates sort)."""
        with self._cursor() as cur:
            cur.execute(
                "SELECT key, payload FROM raw"
                " WHERE endpoint = ? AND key >= ? AND key <= ? ORDER BY key",
                (endpoint, key_from, key_to),
            )
            rows = cur.fetchall()
        for key, payload in rows:
            yield key, json.loads(payload)

    def iter_raw_by_span_end(
        self, endpoint: str, span_from: str, span_to: str
    ) -> Iterator[tuple[str, Any]]:
        """Payloads whose ``span_end`` day lies in ``[span_from, span_to]`` (or is unknown).

        Used for id-keyed rows (activities), whose key says nothing about the day.
        """
        with self._cursor() as cur:
            cur.execute(
                "SELECT key, payload FROM raw WHERE endpoint = ?"
                " AND (span_end = '' OR (span_end >= ? AND span_end <= ?)) ORDER BY key",
                (endpoint, span_from, span_to),
            )
            rows = cur.fetchall()
        for key, payload in rows:
            yield key, json.loads(payload)

    def latest_key(self, endpoint: str) -> str | None:
        with self._cursor() as cur:
            cur.execute("SELECT MAX(key) FROM raw WHERE endpoint = ?", (endpoint,))
            row = cur.fetchone()
        return row[0] if row else None

    def raw_counts(self) -> dict[str, int]:
        with self._cursor() as cur:
            cur.execute("SELECT endpoint, COUNT(*) FROM raw GROUP BY endpoint ORDER BY endpoint")
            return dict(cur.fetchall())

    # -- push ledger ------------------------------------------------------------------

    def ledger_get(self, sink: str, external_id: str) -> LedgerEntry | None:
        with self._cursor() as cur:
            cur.execute(
                "SELECT content_hash, pushed_at, remote_ref FROM ledger"
                " WHERE sink = ? AND external_id = ?",
                (sink, external_id),
            )
            row = cur.fetchone()
        return LedgerEntry(*row) if row else None

    def ledger_put(
        self, sink: str, external_id: str, content_hash: str, remote_ref: str | None = None
    ) -> None:
        with self._cursor() as cur:
            cur.execute(
                "INSERT OR REPLACE INTO ledger"
                " (sink, external_id, content_hash, pushed_at, remote_ref)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    sink,
                    external_id,
                    content_hash,
                    self._clock().isoformat(timespec="seconds"),
                    remote_ref,
                ),
            )

    def ledger_counts(self) -> dict[str, int]:
        with self._cursor() as cur:
            cur.execute("SELECT sink, COUNT(*) FROM ledger GROUP BY sink ORDER BY sink")
            return dict(cur.fetchall())


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring an older archive up to :data:`SCHEMA_VERSION` without losing data."""
    (version,) = conn.execute("PRAGMA user_version").fetchone()
    if version < 2:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(raw)")}
        if "span_end" not in columns:
            conn.execute("ALTER TABLE raw ADD COLUMN span_end TEXT NOT NULL DEFAULT ''")
    conn.execute(_SET_VERSION)
    conn.commit()

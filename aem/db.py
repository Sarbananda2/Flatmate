"""SQLite persistence layer.

Design notes
------------
* One connection per unit of work. SQLite handles a single-operator local app
  comfortably and a short-lived connection keeps transaction boundaries obvious.
* ``transaction()`` is the only sanctioned way to write. It opens an explicit
  ``BEGIN IMMEDIATE``, commits on success, and rolls back on any exception, so a
  failed request can never leave a half-written expense behind.
* ``PRAGMA foreign_keys = ON`` is set on every connection; SQLite defaults it
  off per connection, and the application's referential integrity depends on it.
* ``PRAGMA journal_mode = WAL`` lets a read (a dashboard refresh) proceed while
  a write is in flight.
"""
from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from .errors import AemError

DEFAULT_TIMEOUT_SECONDS = 15.0


def new_id(prefix: str) -> str:
    """Opaque, sortable-enough identifier. Stable across restarts."""
    return f"{prefix}_{uuid.uuid4().hex}"


def now_timestamp(value: datetime | None = None) -> str:
    """Timezone-aware ISO-8601 timestamp to the second."""
    return (value or datetime.now().astimezone()).isoformat(timespec="seconds")


def _configure(conn: sqlite3.Connection, *, read_only: bool) -> None:
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 15000")
    if not read_only:
        try:
            conn.execute("PRAGMA journal_mode = WAL")
        except sqlite3.DatabaseError:
            # A read-only filesystem or a database opened by an older SQLite
            # can refuse the journal change; the app still works in delete mode.
            pass
        conn.execute("PRAGMA synchronous = FULL")


@contextmanager
def connect(db_path: str | Path, *, read_only: bool = False) -> Iterator[sqlite3.Connection]:
    """Open a configured connection and always close it.

    `isolation_level=None` turns off the sqlite3 module's implicit transaction
    handling so this module controls every ``BEGIN``/``COMMIT`` explicitly.
    """
    conn = sqlite3.connect(str(db_path), timeout=DEFAULT_TIMEOUT_SECONDS,
                           isolation_level=None)
    try:
        _configure(conn, read_only=read_only)
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction(db_path: str | Path) -> Iterator[sqlite3.Connection]:
    """Run a write unit of work atomically.

    Everything inside the block commits together or not at all, which is what
    makes a partially saved expense impossible.
    """
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")


@contextmanager
def nested(conn: sqlite3.Connection, name: str = "sp") -> Iterator[sqlite3.Connection]:
    """Savepoint, for a service that needs its own rollback point inside a
    transaction opened by its caller."""
    savepoint = f"{name}_{uuid.uuid4().hex[:8]}"
    conn.execute(f"SAVEPOINT {savepoint}")
    try:
        yield conn
    except BaseException:
        conn.execute(f"ROLLBACK TO {savepoint}")
        conn.execute(f"RELEASE {savepoint}")
        raise
    conn.execute(f"RELEASE {savepoint}")


def rows_to_dicts(rows: Sequence[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


Params = Sequence[Any] | Mapping[str, Any]


def _bind(params: Params) -> Any:
    """Accept either positional (?) or named (:name) parameters."""
    return dict(params) if isinstance(params, Mapping) else tuple(params)


def fetch_one(conn: sqlite3.Connection, sql: str, params: Params = ()) -> dict[str, Any] | None:
    row = conn.execute(sql, _bind(params)).fetchone()
    return dict(row) if row is not None else None


def fetch_all(conn: sqlite3.Connection, sql: str, params: Params = ()) -> list[dict[str, Any]]:
    return rows_to_dicts(conn.execute(sql, _bind(params)).fetchall())


def scalar(conn: sqlite3.Connection, sql: str, params: Params = (), default: Any = None) -> Any:
    row = conn.execute(sql, _bind(params)).fetchone()
    if row is None or row[0] is None:
        return default
    return row[0]


class DatabaseError(AemError):
    code = "database_error"
    http_status = 500

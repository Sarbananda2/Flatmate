"""The SQLite schema history, kept for the one-off import into PostgreSQL.

The application no longer runs on SQLite. These are the six migrations it
shipped with, and the runner that applied them, preserved so that an
installation which stopped at an older schema can still be brought to the final
SQLite schema before its data is copied across.

The importer only ever runs this against a temporary copy of the household's
database file, never the file itself.

Rules the runner follows (unchanged from when it was the live path):

* A migration is applied exactly once, inside one transaction with the others
  pending, in ascending order.
* A database created by the original prototype (it has an ``apartment`` table
  but no ``schema_migration`` table) is *adopted* at version 1: the baseline is
  marked applied without running it.
* Nothing here drops a table or a column.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from ....ids import now_timestamp
from ...errors import MigrationError

MIGRATIONS_DIR = Path(__file__).parent
_FILENAME_RE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")

#: The SQLite schema version the PostgreSQL baseline was written from.
FINAL_VERSION = 6


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    path: Path

    @property
    def sql(self) -> str:
        return self.path.read_text(encoding="utf-8")

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()[:32]


def split_statements(sql: str) -> list[str]:
    """Split a migration script into individual statements.

    Splitting on ``;`` is wrong because a ``CREATE TRIGGER`` body contains
    semicolons, so this uses SQLite's own completeness test to find real
    statement boundaries.
    """
    statements: list[str] = []
    buffer = ""
    for line in sql.splitlines(keepends=True):
        stripped = line.strip()
        if not buffer and (not stripped or stripped.startswith("--")):
            continue
        buffer += line
        if sqlite3.complete_statement(buffer):
            statements.append(buffer.strip())
            buffer = ""
    leftover = buffer.strip()
    if leftover:
        raise MigrationError("Migration script ends with an incomplete statement.",
                             details={"fragment": leftover[:200]})
    return statements


def discover() -> list[Migration]:
    found: list[Migration] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        match = _FILENAME_RE.match(path.name)
        if not match:
            raise MigrationError(f"Migration filename {path.name!r} must look like 0001_name.sql.")
        found.append(Migration(int(match.group(1)), match.group(2), path))
    return found


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(row[1] == column for row in conn.execute(f"PRAGMA table_info({table})"))


def current_version(conn: sqlite3.Connection) -> int:
    if not _table_exists(conn, "schema_migration"):
        return 1 if _table_exists(conn, "apartment") else 0
    row = conn.execute("SELECT MAX(version) FROM schema_migration").fetchone()
    return int(row[0] or 0)


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Bring a SQLite database up to :data:`FINAL_VERSION`.

    ``conn`` must have been opened with ``isolation_level=None``. Returns a log
    of what was done; on failure everything is rolled back.
    """
    migrations = discover()
    log: list[str] = []
    pre_existing_prototype = (not _table_exists(conn, "schema_migration")
                              and _table_exists(conn, "apartment"))
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS schema_migration (
                   version INTEGER PRIMARY KEY,
                   name TEXT NOT NULL,
                   checksum TEXT NOT NULL,
                   applied_at TEXT NOT NULL,
                   adopted INTEGER NOT NULL DEFAULT 0 CHECK (adopted IN (0, 1))
               )""")
        if pre_existing_prototype:
            # The prototype shipped one forward-compatible tweak outside any
            # migration: payment.expense_id. Add it before adopting, so the
            # adopted baseline really matches 0001.
            if not _column_exists(conn, "payment", "expense_id"):
                conn.execute("ALTER TABLE payment ADD COLUMN expense_id TEXT")
                log.append("added payment.expense_id to the pre-existing schema")
            baseline = next(m for m in migrations if m.version == 1)
            conn.execute(
                "INSERT OR IGNORE INTO schema_migration "
                "(version, name, checksum, applied_at, adopted) VALUES (?,?,?,?,1)",
                (baseline.version, baseline.name, baseline.checksum, now_timestamp()))
            log.append("adopted the prototype database at version 1 (baseline not re-run)")
        applied = {row[0] for row in conn.execute("SELECT version FROM schema_migration")}
        for migration in migrations:
            if migration.version in applied:
                continue
            for statement in split_statements(migration.sql):
                conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_migration (version, name, checksum, applied_at, adopted) "
                "VALUES (?,?,?,?,0)",
                (migration.version, migration.name, migration.checksum, now_timestamp()))
            log.append(f"applied {migration.version:04d}_{migration.name}")
    except sqlite3.Error as exc:
        conn.execute("ROLLBACK")
        raise MigrationError(f"SQLite migration failed and was rolled back: {exc}") from exc
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
    return log

"""Versioned, forward-only SQLite migrations.

Rules this runner follows:

* A migration is a numbered ``NNNN_name.sql`` file in this package. It is
  applied exactly once, inside its own transaction, in ascending order.
* Applied versions are recorded in ``schema_migration`` with a checksum, so a
  migration that is edited after being applied is reported rather than silently
  re-applied or ignored.
* A database created by the original prototype (it has an ``apartment`` table
  but no ``schema_migration`` table) is *adopted* at version 1: the baseline is
  marked applied without running it, so existing residents, expenses,
  allocations, payments, and statements are preserved untouched.
* Nothing here drops a table or a column. A database is never deleted to
  resolve a migration problem.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from ..db import connect, now_timestamp
from ..errors import AemError

MIGRATIONS_DIR = Path(__file__).parent
_FILENAME_RE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")


class MigrationError(AemError):
    code = "migration_error"
    http_status = 500


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

    ``sqlite3.executescript`` cannot be used: it commits any open transaction
    first, which would defeat per-migration atomicity. Splitting on ``;`` is
    wrong because a ``CREATE TRIGGER`` body contains semicolons, so this uses
    SQLite's own completeness test to find real statement boundaries.
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
    versions = [m.version for m in found]
    if len(set(versions)) != len(versions):
        raise MigrationError("Duplicate migration version numbers found.")
    return found


def _ensure_version_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS schema_migration (
               version INTEGER PRIMARY KEY,
               name TEXT NOT NULL,
               checksum TEXT NOT NULL,
               applied_at TEXT NOT NULL,
               adopted INTEGER NOT NULL DEFAULT 0 CHECK (adopted IN (0, 1))
           )"""
    )


def _applied(conn: sqlite3.Connection) -> dict[int, dict]:
    return {row["version"]: dict(row)
            for row in conn.execute("SELECT * FROM schema_migration").fetchall()}


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(row[1] == column for row in conn.execute(f"PRAGMA table_info({table})"))


def current_version(db_path: str | Path) -> int:
    with connect(db_path, read_only=True) as conn:
        if not _table_exists(conn, "schema_migration"):
            return 1 if _table_exists(conn, "apartment") else 0
        row = conn.execute("SELECT MAX(version) FROM schema_migration").fetchone()
        return int(row[0] or 0)


def migrate(db_path: str | Path, *, verbose: bool = False) -> list[str]:
    """Bring the database at `db_path` up to the latest version.

    Returns a human-readable log of what was done. Safe to call repeatedly;
    calling it on an up-to-date database does nothing.
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    migrations = discover()
    log: list[str] = []

    with connect(path) as conn:
        pre_existing_prototype = (not _table_exists(conn, "schema_migration")
                                  and _table_exists(conn, "apartment"))
        conn.execute("BEGIN IMMEDIATE")
        try:
            _ensure_version_table(conn)
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
                    (baseline.version, baseline.name, baseline.checksum, now_timestamp()),
                )
                log.append("adopted the existing prototype database at version 1 "
                           "(baseline not re-run; no data touched)")
            applied = _applied(conn)
            for migration in migrations:
                record = applied.get(migration.version)
                if record is not None:
                    if not record["adopted"] and record["checksum"] != migration.checksum:
                        raise MigrationError(
                            f"Migration {migration.version:04d}_{migration.name} changed after it "
                            "was applied. Add a new migration instead of editing an applied one.",
                            details={"version": migration.version,
                                     "recorded_checksum": record["checksum"],
                                     "file_checksum": migration.checksum})
                    continue
                for statement in split_statements(migration.sql):
                    conn.execute(statement)
                conn.execute(
                    "INSERT INTO schema_migration (version, name, checksum, applied_at, adopted) "
                    "VALUES (?,?,?,?,0)",
                    (migration.version, migration.name, migration.checksum, now_timestamp()),
                )
                log.append(f"applied {migration.version:04d}_{migration.name}")
        except sqlite3.Error as exc:
            conn.execute("ROLLBACK")
            raise MigrationError(f"Migration failed and was rolled back: {exc}") from exc
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")

    if not log:
        log.append(f"database already at version {current_version(path)}")
    if verbose:
        for line in log:
            print(f"[migrate] {line}")
    return log


def initialize_database(db_path: str | Path, schema_path: str | Path | None = None) -> None:
    """Create or upgrade the application database.

    `schema_path` is accepted for compatibility with the prototype's signature
    and is ignored: the schema now comes from the versioned migrations so that
    an existing database is upgraded rather than redefined.
    """
    migrate(db_path)

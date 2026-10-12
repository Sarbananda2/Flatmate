"""Versioned, forward-only PostgreSQL migrations.

Rules this runner follows:

* A migration is a numbered ``NNNN_name.sql`` file in ``migrations/``. It is
  applied exactly once, in ascending order, inside its own transaction together
  with the row that records it -- so a migration either happened and is
  recorded, or did not happen at all. PostgreSQL DDL is transactional, which is
  what makes that true for schema changes too.
* Applied versions are recorded in ``schema_migration`` with a checksum, so a
  migration that is edited after being applied is reported rather than silently
  re-applied or ignored.
* Only one process migrates at a time: the runner holds an advisory lock, so
  two app instances starting together do not race.
* Nothing here drops a table or a column. A database is never deleted to
  resolve a migration problem.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import psycopg
import psycopg.sql

from ...ids import now_timestamp
from ..errors import MigrationError

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_FILENAME_RE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")

# Arbitrary, fixed keys for pg_advisory_xact_lock. "AEM" then a purpose byte.
MIGRATION_LOCK_KEY = 0x41454D01
WRITE_LOCK_KEY = 0x41454D02


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


def latest_version() -> int:
    return max((m.version for m in discover()), default=0)


def _version_table_exists(conn: psycopg.Connection) -> bool:
    return conn.execute("SELECT to_regclass('schema_migration') IS NOT NULL").fetchone()[0]


def current_version(conn: psycopg.Connection) -> int:
    """The newest applied migration, or 0 for a database with no schema yet."""
    if not _version_table_exists(conn):
        return 0
    return int(conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migration").fetchone()[0])


# Tables the runtime role may add to but never rewrite. The triggers already
# refuse UPDATE and DELETE on them; withholding the privilege means the runtime
# role is turned away before a trigger is even consulted.
APPEND_ONLY_TABLES = ("audit_log", "statement_revision", "ledger_adjustment", "rent_receipt",
                      "household_deposit_transaction")


def grant_runtime_access(conn: psycopg.Connection, role: str) -> list[str]:
    """Give the runtime role exactly what serving requests needs, in this schema.

    Run by the migration role, which owns the tables. The runtime role gets
    USAGE on the schema and row-level access to the tables -- nothing that
    changes structure: no CREATE, no ownership, no TRUNCATE, no TRIGGER, no
    REFERENCES. Within that:

    * ``schema_migration`` is read-only to it (it checks the version, never
      records one);
    * the append-only tables are SELECT and INSERT only.

    Default privileges are set as well, so a table added by a later migration
    is covered when that migration runs. Safe to repeat.
    """
    schema = conn.execute("SELECT current_schema()").fetchone()[0]
    ident = psycopg.sql.Identifier
    statements = [
        "GRANT USAGE ON SCHEMA {schema} TO {role}",
        "REVOKE ALL ON ALL TABLES IN SCHEMA {schema} FROM {role}",
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {schema} TO {role}",
        "REVOKE INSERT, UPDATE, DELETE ON {schema}.schema_migration FROM {role}",
        *("REVOKE UPDATE, DELETE ON {schema}." + table + " FROM {role}"
          for table in APPEND_ONLY_TABLES
          if conn.execute("SELECT to_regclass(%s) IS NOT NULL", (table,)).fetchone()[0]),
        # Identity columns need nothing here; this lets pg_dump, run as the
        # runtime role, read where each sequence has got to.
        "GRANT SELECT ON ALL SEQUENCES IN SCHEMA {schema} TO {role}",
        "ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {role}",
        "ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} GRANT SELECT ON SEQUENCES TO {role}",
    ]
    try:
        with conn.transaction():
            for statement in statements:
                conn.execute(psycopg.sql.SQL(statement).format(
                    schema=ident(schema), role=ident(role)))
    except psycopg.Error as exc:
        raise MigrationError(
            f"Could not grant runtime access to role {role!r}: {str(exc).strip()}") from exc
    return [f"granted runtime access on schema {schema} to {role}"]


def migrate(conn: psycopg.Connection, *, target_version: int | None = None) -> list[str]:
    """Bring the database up to the latest version, or to ``target_version``.

    ``conn`` must be an autocommit connection, so that each ``transaction()``
    block below is a real transaction of its own. Returns a human-readable log. Safe to call repeatedly; on an up-to-date database it
    does nothing.
    """
    migrations = discover()
    log: list[str] = []
    try:
        with conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (MIGRATION_LOCK_KEY,))
            conn.execute(
                """CREATE TABLE IF NOT EXISTS schema_migration (
                       version INTEGER PRIMARY KEY,
                       name TEXT NOT NULL,
                       checksum TEXT NOT NULL,
                       applied_at TEXT NOT NULL
                   )""")
        for migration in migrations:
            if target_version is not None and migration.version > target_version:
                break
            with conn.transaction():
                conn.execute("SELECT pg_advisory_xact_lock(%s)", (MIGRATION_LOCK_KEY,))
                record = conn.execute(
                    "SELECT checksum FROM schema_migration WHERE version = %s",
                    (migration.version,)).fetchone()
                if record is not None:
                    if record[0] != migration.checksum:
                        raise MigrationError(
                            f"Migration {migration.version:04d}_{migration.name} changed after it "
                            "was applied. Add a new migration instead of editing an applied one.",
                            details={"version": migration.version,
                                     "recorded_checksum": record[0],
                                     "file_checksum": migration.checksum})
                    continue
                # No parameters, so the script is sent as written: it may hold
                # many statements, and a '%' in it is just a percent sign.
                conn.execute(migration.sql)
                conn.execute(
                    "INSERT INTO schema_migration (version, name, checksum, applied_at) "
                    "VALUES (%s,%s,%s,%s)",
                    (migration.version, migration.name, migration.checksum, now_timestamp()))
                log.append(f"applied {migration.version:04d}_{migration.name}")
    except psycopg.Error as exc:
        raise MigrationError(f"Migration failed and was rolled back: {exc}".strip()) from exc

    if not log:
        log.append(f"database already at version {current_version(conn)}")
    return log

"""One-off import of an existing SQLite database into PostgreSQL.

This is an operational tool, run explicitly by the operator
(``python scripts/manage.py import-sqlite``). The application never calls it,
and nothing imports or migrates a SQLite file automatically.

What it guarantees
------------------

* **The source is never written.** The SQLite file is opened read-only and
  copied, with SQLite's own backup API, to a temporary file; everything else
  works on that copy. The source file's SHA-256 is taken before and after and
  the two must match.
* **The target must be empty.** If any application table in the PostgreSQL
  database holds a row, the import refuses. It never merges into, or
  overwrites, existing records.
* **All or nothing.** Every row is copied inside one PostgreSQL transaction.
  If anything fails -- a value of the wrong type, a broken reference, a count
  or total that does not match -- the transaction rolls back and the target is
  left exactly as it was.
* **Nothing is reinterpreted.** IDs, dates, timestamps, JSON documents, and
  receipt files are copied byte for byte; paise stay integers; SQLite's
  ``rowid`` becomes ``row_seq``, so records keep their insertion order.
* **It is checked.** Before committing, every table is read back and compared
  with the source row by row and column by column, in order, and every foreign
  key is validated.
* **No guard is switched off.** The import runs as the application's ordinary
  role, with every trigger and constraint in force, so the schema's own checks
  (a payment applied beyond its amount, for example) are applied to the
  imported history too.

One thing needs care because the guards stay on. A closed statement's
allocations cannot be inserted: the trigger that protects closed statements
forbids exactly that. So statements are first loaded as drafts, their expenses
and allocations follow, and each statement is then given its real status in a
single update -- which the guards allow, since a draft may be finalized or
closed. The verification afterwards compares the final rows, status included.
"""
from __future__ import annotations

import hashlib
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql as pg_sql

from ..errors import PersistenceError
from ..postgres import migrate as pg_migrations
from ..postgres.adapter import APPLICATION_TABLES, translate
from ..postgres.config import PostgresConfig
from . import legacy_migrations

#: The PostgreSQL schema version whose tables mirror the final SQLite schema.
BASELINE_VERSION = 1

# SQLite tables that are bookkeeping, not household data.
_IGNORED_SOURCE_TABLES = {"schema_migration"}

_INTEGER_TYPES = {"smallint", "integer", "bigint"}

# Loaded as a draft and given its real status once its expenses are in.
_STATEMENT_TABLE = "monthly_statement"
_DRAFT = "draft"
_BATCH = 500


class ImportRefused(PersistenceError):
    """The import was not attempted, or was rolled back. Nothing was changed."""

    code = "import_refused"
    http_status = 409


@dataclass
class TableResult:
    table: str
    source_rows: int
    target_rows: int
    columns_compared: int


@dataclass
class ImportReport:
    source: Path
    target: str
    source_sha256: str
    source_schema_version: int
    committed: bool = False
    log: list[str] = field(default_factory=list)
    tables: list[TableResult] = field(default_factory=list)

    @property
    def total_rows(self) -> int:
        return sum(result.target_rows for result in self.tables)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _snapshot_source(source: Path, workdir: Path) -> Path:
    """A private, consistent copy of the SQLite database."""
    copy = workdir / "source-copy.sqlite"
    try:
        origin = sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise ImportRefused(f"Could not open {source} read-only: {exc}") from exc
    try:
        target = sqlite3.connect(str(copy))
        try:
            origin.backup(target)
        finally:
            target.close()
    except sqlite3.Error as exc:
        raise ImportRefused(f"Could not read {source}: {exc}") from exc
    finally:
        origin.close()
    return copy


def _prepare_copy(copy: Path, log: list[str]) -> tuple[sqlite3.Connection, int]:
    """Check the copy, bring it to the final SQLite schema, and open it."""
    conn = sqlite3.connect(str(copy), isolation_level=None)
    conn.row_factory = sqlite3.Row
    check = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if check != "ok":
        conn.close()
        raise ImportRefused(
            "The SQLite database fails its own integrity check; it was not imported.",
            details={"integrity_check": check})
    conn.execute("PRAGMA foreign_keys = ON")
    original_version = legacy_migrations.current_version(conn)
    if original_version == 0:
        conn.close()
        raise ImportRefused("That file is not an Apartment Expense Manager database "
                            "(it has no apartment table).")
    if original_version > legacy_migrations.FINAL_VERSION:
        conn.close()
        raise ImportRefused(
            f"The SQLite database is at schema version {original_version}, newer than "
            f"the version {legacy_migrations.FINAL_VERSION} this importer understands.")
    if original_version < legacy_migrations.FINAL_VERSION:
        for line in legacy_migrations.migrate(conn):
            log.append(f"temporary copy: {line}")
    broken = conn.execute("PRAGMA foreign_key_check").fetchall()
    if broken:
        conn.close()
        raise ImportRefused(
            "The SQLite database has rows that refer to records that do not exist; "
            "it was not imported.",
            details={"foreign_key_violations": [tuple(row) for row in broken[:20]]})
    return conn, original_version


def _source_tables(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )} - _IGNORED_SOURCE_TABLES


def _target_columns(pg: psycopg.Connection[Any], table: str) -> dict[str, str]:
    rows = pg.execute(
        """SELECT column_name, data_type FROM information_schema.columns
            WHERE table_schema = current_schema() AND table_name = %s
            ORDER BY ordinal_position""", (table,)).fetchall()
    return {name: data_type for name, data_type in rows}


def _digest_rows(rows: Any) -> str:
    """A digest of rows in order, distinguishing every value and its type."""
    digest = hashlib.sha256()
    for row in rows:
        for value in row:
            if value is None:
                token = b"N"
            elif isinstance(value, (bytes, memoryview)):
                token = b"B" + hashlib.sha256(bytes(value)).digest()
            elif isinstance(value, float):
                token = b"F" + repr(value).encode()
            elif isinstance(value, int):
                token = b"I" + str(value).encode()
            else:
                token = b"T" + str(value).encode("utf-8")
            digest.update(len(token).to_bytes(8, "big") + token)
        digest.update(b"\x00row")
    return digest.hexdigest()


def _check_value(table: str, column: str, data_type: str, value: Any, rowid: int) -> Any:
    """Return ``value`` as it should be stored, or refuse.

    SQLite stores whatever it is given; PostgreSQL columns are typed. A value
    that does not fit its column is reported, never coerced.
    """
    if value is None:
        return None
    problem = None
    if data_type in _INTEGER_TYPES:
        if isinstance(value, bool) or not isinstance(value, int):
            problem = "is not a whole number"
    elif data_type == "double precision":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            problem = "is not a number"
        else:
            value = float(value)
    elif data_type == "bytea":
        if not isinstance(value, (bytes, memoryview)):
            problem = "is not binary data"
        else:
            value = bytes(value)
    else:
        if not isinstance(value, str):
            problem = "is not text"
        elif "\x00" in value:
            problem = "contains a NUL character, which PostgreSQL text cannot hold"
    if problem:
        raise ImportRefused(
            f"{table}.{column} in row {rowid} {problem}. Nothing was imported.",
            details={"table": table, "column": column, "rowid": rowid,
                     "sqlite_type": type(value).__name__, "postgres_type": data_type})
    return value


def _table_columns(source: sqlite3.Connection, pg: psycopg.Connection[Any],
                   table: str) -> tuple[list[str], dict[str, str]]:
    target_columns = _target_columns(pg, table)
    source_columns = [row[1] for row in source.execute(f'PRAGMA table_info("{table}")')]
    unknown = [name for name in source_columns if name not in target_columns]
    if unknown:
        raise ImportRefused(
            f"The SQLite table {table} has columns the PostgreSQL schema does not: "
            f"{', '.join(unknown)}. Nothing was imported.",
            details={"table": table, "columns": unknown})
    return source_columns, target_columns


def _copy_table(source: sqlite3.Connection, pg: psycopg.Connection[Any],
                table: str) -> dict[str, str]:
    """Insert every row of `table`. Returns the real status of each statement
    that was loaded as a draft (empty for every other table)."""
    columns, target_columns = _table_columns(source, pg, table)
    insert = pg_sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
        pg_sql.Identifier(table),
        pg_sql.SQL(", ").join(pg_sql.Identifier(name) for name in [*columns, "row_seq"]),
        pg_sql.SQL(", ").join(pg_sql.Placeholder() for _ in range(len(columns) + 1)))
    select = 'SELECT rowid AS "__rowid", {} FROM "{}" ORDER BY rowid'.format(
        ", ".join(f'"{name}"' for name in columns), table)

    deferred_status: dict[str, str] = {}
    batch: list[tuple[Any, ...]] = []
    with pg.cursor() as cursor:
        for row in source.execute(select):
            rowid = row["__rowid"]
            values = {name: _check_value(table, name, target_columns[name], row[name], rowid)
                      for name in columns}
            if table == _STATEMENT_TABLE and values["status"] != _DRAFT:
                deferred_status[values["statement_id"]] = values["status"]
                values["status"] = _DRAFT
            batch.append(tuple(values[name] for name in columns) + (rowid,))
            if len(batch) >= _BATCH:
                cursor.executemany(insert, batch)
                batch.clear()
        if batch:
            cursor.executemany(insert, batch)

    # Continue numbering after the imported rows.
    pg.execute(
        pg_sql.SQL("SELECT setval(pg_get_serial_sequence(%s, 'row_seq'), "
                   "COALESCE((SELECT MAX(row_seq) FROM {}), 0) + 1, false)").format(
            pg_sql.Identifier(table)), (table,))
    return deferred_status


def _verify_table(source: sqlite3.Connection, pg: psycopg.Connection[Any],
                  table: str) -> TableResult:
    """Compare every column of every row, in insertion order."""
    columns, _ = _table_columns(source, pg, table)
    source_rows = int(source.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
    target_rows = int(pg.execute(pg_sql.SQL("SELECT COUNT(*) FROM {}").format(
        pg_sql.Identifier(table))).fetchone()[0])
    if target_rows != source_rows:
        raise ImportRefused(
            f"{table}: {source_rows} rows in SQLite but {target_rows} in PostgreSQL.",
            details={"table": table})
    source_digest = _digest_rows(tuple(row) for row in source.execute(
        'SELECT rowid, {} FROM "{}" ORDER BY rowid'.format(
            ", ".join(f'"{name}"' for name in columns), table)))
    target_digest = _digest_rows(pg.execute(
        pg_sql.SQL("SELECT row_seq, {} FROM {} ORDER BY row_seq").format(
            pg_sql.SQL(", ").join(pg_sql.Identifier(name) for name in columns),
            pg_sql.Identifier(table))))
    if source_digest != target_digest:
        raise ImportRefused(
            f"{table}: the rows in PostgreSQL do not match the rows in SQLite. "
            "Nothing was imported.", details={"table": table})
    return TableResult(table, source_rows, target_rows, len(columns))


def import_sqlite(source: Path, config: PostgresConfig, *,
                  dry_run: bool = False) -> ImportReport:
    """Copy every record from the SQLite file at ``source`` into PostgreSQL.

    ``config`` is the *migration* role's connection: the import creates the
    schema if it is missing, so it runs as the role that owns it, and grants
    the runtime role (``config.grant_to``) its access afterwards.

    With ``dry_run`` the whole import is performed and verified, then rolled
    back, so the operator can see it would succeed without changing anything.
    Raises :class:`ImportRefused` (or another ``PersistenceError``) if the
    import cannot be done safely; in that case the target is unchanged.
    """
    source = Path(source).expanduser()
    if not source.is_file():
        raise ImportRefused(f"There is no SQLite database at {source}.")
    checksum_before = _sha256(source)
    report = ImportReport(source=source, target=config.describe(),
                          source_sha256=checksum_before, source_schema_version=0)

    workdir = Path(tempfile.mkdtemp(prefix="aem-sqlite-import-"))
    sqlite_conn: sqlite3.Connection | None = None
    try:
        copy = _snapshot_source(source, workdir)
        sqlite_conn, report.source_schema_version = _prepare_copy(copy, report.log)
        extra = _source_tables(sqlite_conn) - set(APPLICATION_TABLES)
        if extra:
            raise ImportRefused(
                "The SQLite database has tables this importer does not know: "
                f"{', '.join(sorted(extra))}. Nothing was imported.",
                details={"tables": sorted(extra)})
        present = [table for table in APPLICATION_TABLES
                   if table in _source_tables(sqlite_conn)]

        try:
            pg = psycopg.connect(autocommit=True, **config.connection_kwargs())
        except psycopg.Error as exc:
            raise translate(exc) from exc
        with pg:
            try:
                if config.schema:
                    pg.execute(pg_sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(
                        pg_sql.Identifier(config.schema)))
                version = pg_migrations.current_version(pg)
                if version > BASELINE_VERSION:
                    raise ImportRefused(
                        f"The PostgreSQL database is already at schema version {version}. "
                        f"Import into a new, empty database: the importer loads data at "
                        f"version {BASELINE_VERSION} and then upgrades it.")
                if version:
                    occupied = {}
                    for table in APPLICATION_TABLES:
                        count = int(pg.execute(pg_sql.SQL("SELECT COUNT(*) FROM {}").format(
                            pg_sql.Identifier(table))).fetchone()[0])
                        if count:
                            occupied[table] = count
                    if occupied:
                        raise ImportRefused(
                            "The PostgreSQL database already contains data. The import only "
                            "runs into an empty database and never merges records.",
                            details={"rows_present": occupied})
                for line in pg_migrations.migrate(pg, target_version=BASELINE_VERSION):
                    report.log.append(f"postgresql: {line}")
                if config.grant_to and config.grant_to != config.user:
                    pg_migrations.grant_runtime_access(pg, config.grant_to)

                with pg.transaction(force_rollback=dry_run):
                    pg.execute("SELECT pg_advisory_xact_lock(%s)",
                               (pg_migrations.WRITE_LOCK_KEY,))
                    # Re-check under the lock: nobody may have written meanwhile.
                    for table in APPLICATION_TABLES:
                        if pg.execute(pg_sql.SQL("SELECT 1 FROM {} LIMIT 1").format(
                                pg_sql.Identifier(table))).fetchone():
                            raise ImportRefused(
                                "The PostgreSQL database received data while the import was "
                                "starting. Nothing was imported.", details={"table": table})
                    # Tables refer to one another (and to themselves), so foreign
                    # keys are checked together at the end rather than row by row.
                    pg.execute("SET CONSTRAINTS ALL DEFERRED")
                    statement_status: dict[str, str] = {}
                    for table in present:
                        statement_status.update(_copy_table(sqlite_conn, pg, table))
                    for statement_id, status in statement_status.items():
                        pg.execute(
                            "UPDATE monthly_statement SET status = %s WHERE statement_id = %s",
                            (status, statement_id))
                    # Validate every foreign key now, inside the transaction.
                    pg.execute("SET CONSTRAINTS ALL IMMEDIATE")
                    for table in present:
                        report.tables.append(_verify_table(sqlite_conn, pg, table))
                    if _sha256(source) != checksum_before:
                        raise ImportRefused(
                            "The SQLite file changed while it was being imported (is the old "
                            "app still running against it?). Nothing was imported; stop "
                            "whatever is writing to it and run the import again.")
                report.committed = not dry_run
                if report.committed:
                    for line in pg_migrations.migrate(pg):
                        report.log.append(f"postgresql: {line}")
                    if config.grant_to and config.grant_to != config.user:
                        for line in pg_migrations.grant_runtime_access(pg, config.grant_to):
                            report.log.append(f"postgresql: {line}")
            except psycopg.Error as exc:
                raise translate(exc) from exc
    finally:
        if sqlite_conn is not None:
            sqlite_conn.close()
        shutil.rmtree(workdir, ignore_errors=True)
    return report

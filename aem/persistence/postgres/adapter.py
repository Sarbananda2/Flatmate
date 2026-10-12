"""PostgreSQL adapter: the driver, connections, transactions, and error mapping.

This is the only module in the application that imports a database driver (the
migration runner and the one-off SQLite importer aside). It turns a
:class:`PostgresConfig` into a :class:`~aem.persistence.database.Database`:

* connections come from a small pool, opened on first use;
* ``transaction()`` is one PostgreSQL transaction, committed on success and
  rolled back on any exception, so a failed request can never leave a
  half-written expense behind;
* write transactions are serialized with an advisory lock. The application
  reads a balance and then writes against it; under SQLite ``BEGIN IMMEDIATE``
  guaranteed nobody else wrote in between, and this keeps that guarantee;
* ``read()`` is a read-only REPEATABLE READ transaction, so a report built from
  several queries sees one consistent snapshot;
* every driver exception is translated into an ``aem.persistence`` error.

Two roles are involved, and this class keeps them apart. Everything a request
does -- every unit of work, every status check -- runs as the *runtime* role,
which owns nothing and cannot change the schema. Only :meth:`migrate` uses the
*migration* role, which owns the tables; a serving process is constructed
without that role's settings and so cannot migrate at all.

It connects to whatever endpoint it is configured with. It never starts or
stops a PostgreSQL server: locally that is the development launcher's job, and
in AWS the database is managed elsewhere.
"""
from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from decimal import Decimal
from typing import Any, Iterator

import psycopg
from psycopg import errors as pg_errors
from psycopg import sql as pg_sql
from psycopg.adapt import Dumper, Loader
from psycopg_pool import ConnectionPool, PoolTimeout

from ..errors import (
    CheckViolation,
    DatabaseUnavailableError,
    ForeignKeyViolation,
    GuardViolation,
    IntegrityViolation,
    MigrationError,
    PersistenceError,
    UniqueViolation,
)
from ..session import Params, Row
from ..unit_of_work import UnitOfWork
from . import migrate as migrations
from .config import PostgresConfig

logger = logging.getLogger("aem.persistence")

# How long a writer waits for the write lock (or any other lock) before giving
# up. SQLite's busy timeout was the same 15 seconds.
LOCK_TIMEOUT = "15s"

# Raised by the schema's guard triggers as 'marker: explanation'. The marker
# selects the API error code; an unknown marker is an ordinary database error.
GUARD_CODES = {
    "closed_statement": "closed_statement",
    "locked_statement": "locked_statement",
    "append_only": "append_only_record",
    "overapplied_payment": "payment_overapplied",
    "overapplied_charge": "charge_overapplied",
}

REFUSED = "The database refused the change and nothing was saved."

# Tables whose row counts say whether the database holds any household data.
APPLICATION_TABLES = (
    "apartment", "resident", "expense_definition", "monthly_statement", "expense_instance",
    "expense_component", "allocation", "smart_plug_reading", "payment", "opening_balance",
    "payment_application", "ledger_adjustment", "statement_revision", "electricity_bill",
    "audit_log", "app_setting", "rent_lease_config", "rent_cycle", "rent_receipt",
    "rent_landlord_receipt", "rent_lease_term",
)


class _BoolAsIntegerDumper(Dumper):
    """Send Python ``bool`` as 0/1. The schema's flags are INTEGER columns."""

    oid = psycopg.adapters.types["int2"].oid

    def dump(self, obj: bool) -> bytes:
        return b"1" if obj else b"0"


class _FloatAsLiteralDumper(Dumper):
    """Send Python ``float`` untyped, so the column decides what it is.

    Sent as a typed float, PostgreSQL would silently round ``4500000.5`` into a
    BIGINT paise column. Sent as an untyped literal, a float bound for an
    integer column is refused outright ("invalid input syntax for type
    bigint"), which is what the SQLite schema's ``typeof(...) = 'integer'``
    checks did; the two DOUBLE PRECISION columns accept it as before.
    """

    def dump(self, obj: float) -> bytes:
        return repr(obj).encode()


class _NumericLoader(Loader):
    """Read NUMERIC as ``int`` when it is whole.

    No column is NUMERIC, but ``SUM()`` over BIGINT paise is. Money must reach
    the application as ``int``, never ``Decimal`` or ``float``.
    """

    def load(self, data: Any) -> int | Decimal:
        text = bytes(data).decode()
        try:
            return int(text)
        except ValueError:
            return Decimal(text)


def _row_factory(cursor: psycopg.Cursor[Any]):
    """Rows as dicts, without the ``row_seq`` bookkeeping column."""
    description = cursor.description
    if description is None:
        def no_result(values: Any) -> Row:
            raise psycopg.InterfaceError("the last operation didn't produce a result")
        return no_result
    names = [column.name for column in description]
    keep = [index for index, name in enumerate(names) if name != "row_seq"]

    def make(values: Any) -> Row:
        return {names[index]: values[index] for index in keep}
    return make


def register_value_adapters(conn: psycopg.Connection[Any]) -> None:
    """How Python values cross to PostgreSQL and back, for any connection."""
    conn.adapters.register_dumper(bool, _BoolAsIntegerDumper)
    conn.adapters.register_dumper(float, _FloatAsLiteralDumper)
    conn.adapters.register_loader("numeric", _NumericLoader)


def _configure(conn: psycopg.Connection[Any]) -> None:
    register_value_adapters(conn)
    conn.row_factory = _row_factory


def translate(exc: psycopg.Error) -> PersistenceError:
    """The ``aem.persistence`` error for a driver exception."""
    diag = getattr(exc, "diag", None)
    message = (getattr(diag, "message_primary", None) or str(exc)).strip()
    constraint = getattr(diag, "constraint_name", None)

    if isinstance(exc, pg_errors.RaiseException):
        marker, separator, explanation = message.partition(": ")
        if separator and marker in GUARD_CODES:
            return GuardViolation(explanation, marker=marker, code=GUARD_CODES[marker])
        return PersistenceError(REFUSED, details={"detail": message})
    details = {"detail": message}
    if isinstance(exc, pg_errors.UniqueViolation):
        return UniqueViolation(REFUSED, constraint=constraint, details=details)
    if isinstance(exc, pg_errors.ForeignKeyViolation):
        return ForeignKeyViolation(REFUSED, constraint=constraint, details=details)
    if isinstance(exc, (pg_errors.CheckViolation, pg_errors.NotNullViolation)):
        return CheckViolation(REFUSED, constraint=constraint, details=details)
    if isinstance(exc, pg_errors.IntegrityError):
        return IntegrityViolation(REFUSED, constraint=constraint, details=details)
    if isinstance(exc, pg_errors.LockNotAvailable):
        return PersistenceError(
            "Another change is still being saved. Try again in a moment.",
            code="database_busy", details=details)
    if isinstance(exc, (psycopg.OperationalError, psycopg.InterfaceError)) and (
            diag is None or getattr(diag, "sqlstate", None) is None):
        # No SQLSTATE: the server never answered. Connection refused, lost, or timed out.
        return DatabaseUnavailableError(
            "The database could not be reached.", details={"detail": message})
    return PersistenceError(REFUSED, details=details)


class PostgresSession:
    """One open transaction on one connection. See :class:`aem.persistence.session.Session`."""

    def __init__(self, conn: psycopg.Connection[Any], *, log_sql: bool = False) -> None:
        self._conn = conn
        self._log_sql = log_sql

    def _run(self, sql: str, params: Params) -> psycopg.Cursor[Any]:
        if self._log_sql:
            logger.debug("sql: %s", " ".join(sql.split()))
        try:
            # Always bind, even with nothing to bind, so '%%' means '%' everywhere.
            return self._conn.execute(sql, params if params is not None else ())
        except psycopg.Error as exc:
            raise translate(exc) from exc

    def execute(self, sql: str, params: Params = ()) -> int:
        return self._run(sql, params).rowcount

    def fetch_one(self, sql: str, params: Params = ()) -> Row | None:
        return self._run(sql, params).fetchone()

    def fetch_all(self, sql: str, params: Params = ()) -> list[Row]:
        return self._run(sql, params).fetchall()

    def scalar(self, sql: str, params: Params = (), default: Any = None) -> Any:
        row = self._run(sql, params).fetchone()
        if not row:
            return default
        value = next(iter(row.values()))
        return default if value is None else value

    @contextmanager
    def savepoint(self) -> Iterator[None]:
        try:
            with self._conn.transaction():
                yield
        except psycopg.Error as exc:
            raise translate(exc) from exc


class PostgresDatabase:
    """A PostgreSQL database. See :class:`aem.persistence.database.Database`."""

    def __init__(self, config: PostgresConfig, *,
                 migration_config: PostgresConfig | None = None,
                 log_sql: bool = False) -> None:
        """``config`` is the runtime role's connection. ``migration_config`` is
        the schema owner's, and is passed only by migration tooling."""
        self._config = config
        self._migration_config = migration_config
        self._log_sql = log_sql
        self._pool: ConnectionPool | None = None
        self._pool_lock = threading.Lock()

    # -- connections ------------------------------------------------------- #

    def _get_pool(self) -> ConnectionPool:
        with self._pool_lock:
            if self._pool is None:
                pool = ConnectionPool(
                    kwargs=self._config.connection_kwargs(),
                    min_size=self._config.pool_min_size,
                    max_size=max(self._config.pool_min_size, self._config.pool_max_size),
                    configure=_configure,
                    timeout=self._config.connect_timeout_seconds,
                    name="aem",
                    open=False,
                )
                pool.open()
                self._pool = pool
            return self._pool

    @contextmanager
    def _connection(self) -> Iterator[psycopg.Connection[Any]]:
        pool = self._get_pool()
        try:
            conn = pool.getconn()
        except PoolTimeout as exc:
            raise DatabaseUnavailableError(
                f"The database at {self.describe()} could not be reached. Locally, start it "
                "with ./scripts/dev.sh or ./scripts/local_postgres.sh start.") from exc
        try:
            yield conn
        finally:
            # The pool rolls back anything still open before reusing the connection.
            pool.putconn(conn)

    def _direct_connection(self, *, autocommit: bool,
                           config: PostgresConfig | None = None) -> psycopg.Connection[Any]:
        """A connection outside the pool: status checks as the runtime role, or
        migrations as whichever role ``config`` names."""
        try:
            return psycopg.connect(autocommit=autocommit,
                                   **(config or self._config).connection_kwargs())
        except psycopg.Error as exc:
            raise translate(exc) from exc

    # -- units of work ----------------------------------------------------- #

    @contextmanager
    def transaction(self) -> Iterator[UnitOfWork]:
        with self._connection() as conn:
            session = PostgresSession(conn, log_sql=self._log_sql)
            try:
                session.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
                session.execute("SELECT pg_advisory_xact_lock(%s)", (migrations.WRITE_LOCK_KEY,))
                yield UnitOfWork(session)
                conn.commit()
            except psycopg.Error as exc:
                conn.rollback()
                raise translate(exc) from exc
            except BaseException:
                conn.rollback()
                raise

    @contextmanager
    def read(self) -> Iterator[UnitOfWork]:
        with self._connection() as conn:
            session = PostgresSession(conn, log_sql=self._log_sql)
            try:
                session.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                yield UnitOfWork(session)
            finally:
                conn.rollback()

    # -- administration ---------------------------------------------------- #

    def describe(self) -> str:
        return self._config.describe()

    def migrate(self, *, target_version: int | None = None) -> list[str]:
        """Apply pending migrations as the migration role, then (re)grant the
        runtime role its access. The runtime role cannot do this."""
        config = self._migration_config
        if config is None:
            raise MigrationError(
                "Migrations run as the schema's owner, and this process was not given that "
                "role's settings. Run 'python scripts/manage.py init' with "
                "AEM_DB_MIGRATION_USER and AEM_DB_MIGRATION_PASSWORD set.")
        with self._direct_connection(autocommit=True, config=config) as conn:
            if config.schema:
                conn.execute(
                    pg_sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(
                        pg_sql.Identifier(config.schema)))
            log = migrations.migrate(conn, target_version=target_version)
            if config.grant_to and config.grant_to != config.user:
                grants = migrations.grant_runtime_access(conn, config.grant_to)
                # Not news when nothing else happened.
                if not log[0].startswith("database already at version"):
                    log.extend(grants)
            return log

    def require_current_schema(self) -> None:
        """Raise unless the schema is at the version this code expects.

        For tools that write data but must not, and cannot, change the schema.
        """
        version, latest = self.schema_version(), migrations.latest_version()
        if version != latest:
            raise MigrationError(
                f"The database schema is at version {version}; this code needs {latest}. "
                "Run 'python scripts/manage.py init' (as the migration role) first.")

    def schema_version(self) -> int:
        with self._direct_connection(autocommit=True) as conn:
            return migrations.current_version(conn)

    def table_counts(self) -> dict[str, int]:
        """Row counts for every application table that exists."""
        counts: dict[str, int] = {}
        with self._direct_connection(autocommit=True) as conn:
            for table in APPLICATION_TABLES:
                if conn.execute("SELECT to_regclass(%s) IS NOT NULL", (table,)).fetchone()[0]:
                    counts[table] = int(conn.execute(
                        pg_sql.SQL("SELECT COUNT(*) FROM {}").format(
                            pg_sql.Identifier(table))).fetchone()[0])
        return counts

    def status(self) -> dict[str, Any]:
        """Reachability and version facts. Never raises; reports ``error`` instead."""
        latest = migrations.latest_version()
        report: dict[str, Any] = {
            "target": self.describe(), "reachable": False, "schema_version": 0,
            "latest_schema_version": latest, "server_version": None, "collation": None,
            "invalid_constraints": None, "role": None, "role_is_privileged": None,
            "role_owns_tables": None, "role_can_change_schema": None,
            "trigger_guard_installed": None, "disabled_triggers": None, "error": None,
        }
        try:
            with self._direct_connection(autocommit=True) as conn:
                report["reachable"] = True
                report["server_version"] = conn.execute("SHOW server_version").fetchone()[0]
                report["collation"] = conn.execute(
                    "SELECT datcollate FROM pg_database WHERE datname = current_database()"
                ).fetchone()[0]
                report["schema_version"] = migrations.current_version(conn)
                # The application should connect as an ordinary role: one that
                # cannot switch off triggers or constraint checks for its session.
                report["role"], report["role_is_privileged"] = conn.execute(
                    """SELECT rolname, rolsuper OR rolreplication OR rolbypassrls
                         FROM pg_roles WHERE rolname = current_user""").fetchone()
                # The runtime role should own nothing and be unable to create,
                # alter, or truncate anything in the application's schema.
                report["role_owns_tables"] = int(conn.execute(
                    """SELECT COUNT(*) FROM pg_class c
                         JOIN pg_namespace n ON n.oid = c.relnamespace
                        WHERE n.nspname = current_schema() AND c.relkind IN ('r', 'S', 'v')
                          AND pg_get_userbyid(c.relowner) = current_user""").fetchone()[0])
                report["role_can_change_schema"] = bool(conn.execute(
                    """SELECT has_schema_privilege(current_user, current_schema(), 'CREATE')
                           OR has_database_privilege(current_user, current_database(), 'CREATE')
                           OR pg_get_userbyid(
                                (SELECT nspowner FROM pg_namespace
                                  WHERE nspname = current_schema())) = current_user
                           OR EXISTS (
                                SELECT 1 FROM pg_class c
                                  JOIN pg_namespace n ON n.oid = c.relnamespace
                                 WHERE n.nspname = current_schema() AND c.relkind = 'r'
                                   AND (has_table_privilege(current_user, c.oid, 'TRUNCATE')
                                     OR has_table_privilege(current_user, c.oid, 'TRIGGER')
                                     OR has_table_privilege(current_user, c.oid, 'REFERENCES')))
                    """).fetchone()[0])
                report["trigger_guard_installed"] = bool(conn.execute(
                    """SELECT EXISTS (SELECT 1 FROM pg_event_trigger
                                       WHERE evtname = 'aem_triggers_stay_enabled'
                                         AND evtenabled <> 'D')""").fetchone()[0])
                report["disabled_triggers"] = int(conn.execute(
                    """SELECT COUNT(*) FROM pg_trigger t
                         JOIN pg_class c ON c.oid = t.tgrelid
                         JOIN pg_namespace n ON n.oid = c.relnamespace
                        WHERE n.nspname = current_schema() AND NOT t.tgisinternal
                          AND t.tgenabled NOT IN ('O', 'A')""").fetchone()[0])
                # A constraint left NOT VALID is not being enforced for old rows.
                report["invalid_constraints"] = int(conn.execute(
                    """SELECT COUNT(*) FROM pg_constraint c
                         JOIN pg_namespace n ON n.oid = c.connamespace
                        WHERE n.nspname = current_schema() AND NOT c.convalidated"""
                ).fetchone()[0])
        except (PersistenceError, psycopg.Error) as exc:
            report["error"] = getattr(exc, "message", None) or str(exc).strip()
            if isinstance(exc, PersistenceError) and exc.details.get("detail"):
                report["error"] = f"{report['error']} ({exc.details['detail']})"
        return report

    def close(self) -> None:
        with self._pool_lock:
            if self._pool is not None:
                self._pool.close()
                self._pool = None

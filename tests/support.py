"""PostgreSQL plumbing for the test suite.

Every test gets its own database: a throwaway schema, freshly migrated, inside
the dedicated *test* database (``aem_test`` by default, created by
``scripts/local_postgres.sh``). Nothing here can reach the household's real
database -- :func:`test_config` refuses any database whose name does not end in
``_test``.

The suite uses the same two roles the application does. The *migration* role
owns each test schema: it creates it, migrates it, and drops it. Everything a
test then does through a ``Database`` runs as the *runtime* role, which owns
nothing and cannot change the schema -- so the tests exercise the services with
exactly the privileges they have in production.

Two kinds of access are provided:

* :func:`new_database` returns the same ``Database`` handle the application
  uses. Tests pass it to services exactly as the API layer does.
* :func:`connect` opens a raw connection to the same schema, for the tests
  that assert on stored rows directly or try to break a rule behind the
  services' backs (the trigger tests). It connects as the runtime role unless
  ``as_owner=True``, which the few tests that change schema objects, or that
  need to reach a trigger the runtime role is not even permitted to approach,
  ask for. It runs in autocommit mode, so a statement the database refuses does
  not poison the statements after it. Rows can be read by position
  (``row[0]``) or by column name (``row["x"]``), and ``dict(row)`` works. SQL
  uses PostgreSQL placeholders: ``%s``.

:data:`DatabaseRefusal` is what a refused raw statement raises; its text
carries the guard's ``marker: explanation`` message, so
``pytest.raises(DatabaseRefusal, match="closed_statement")`` reads as before.
"""
from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import Any, Iterator

import psycopg
from psycopg import sql as pg_sql

from aem.config import get_settings
from aem.persistence.postgres import PostgresConfig, PostgresDatabase
from aem.persistence.postgres.adapter import register_value_adapters

#: Raised by a raw statement the database refuses (constraint or guard trigger).
DatabaseRefusal = psycopg.Error

_TEST_SUFFIX = "_test"


class Row(tuple):
    """A result row readable by index or by column name."""

    _names: tuple[str, ...]

    def __new__(cls, names: tuple[str, ...], values: Any) -> "Row":
        row = super().__new__(cls, values)
        row._names = names
        return row

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, str):
            return super().__getitem__(self._names.index(key))
        return super().__getitem__(key)

    def keys(self) -> tuple[str, ...]:
        return self._names


def _row_factory(cursor: psycopg.Cursor[Any]):
    description = cursor.description
    if description is None:
        return lambda values: values
    names = [column.name for column in description]
    # Like the application's own sessions, leave out the row_seq bookkeeping
    # column so that ``SELECT *`` is exactly the record.
    keep = [index for index, name in enumerate(names) if name != "row_seq"]
    kept = tuple(names[index] for index in keep)
    return lambda values: Row(kept, [values[index] for index in keep])


@dataclass(frozen=True)
class TestTarget:
    """The test database, as each of the two roles reaches it."""

    __test__ = False  # not a test class, despite the name

    runtime: PostgresConfig
    owner: PostgresConfig


def _current_database(config: PostgresConfig) -> str:
    try:
        with psycopg.connect(autocommit=True, **config.connection_kwargs()) as conn:
            return conn.execute("SELECT current_database()").fetchone()[0]
    except psycopg.Error as exc:
        raise RuntimeError(
            f"The test database at {config.describe()} could not be reached as "
            f"{config.user} ({str(exc).strip()}). Start the project's cluster with "
            "./scripts/local_postgres.sh start, which also creates the test database "
            "and both roles."
        ) from exc


def test_config() -> TestTarget:
    """Connection settings for the test database (no schema selected yet)."""
    settings = get_settings()
    base, owner = settings.database, settings.migration_database
    if owner is None:
        raise RuntimeError(
            "The tests create and drop schemas, which takes the migration role, and its "
            "password is not configured. Set AEM_DB_MIGRATION_PASSWORD (./scripts/"
            "local_postgres.sh start generates it into .env.local).")
    url = os.environ.get("AEM_TEST_DATABASE_URL")
    owner_url = os.environ.get("AEM_TEST_MIGRATION_DATABASE_URL")
    name = os.environ.get("AEM_TEST_DB_NAME") or f"{base.name}{_TEST_SUFFIX}"
    if (base.url and not url) or (owner.url and not owner_url):
        raise RuntimeError(
            "AEM_DATABASE_URL is set, so the tests cannot derive a test database from it. "
            "Set AEM_TEST_DATABASE_URL and AEM_TEST_MIGRATION_DATABASE_URL to a database "
            "whose name ends in '_test'.")
    target = TestTarget(
        runtime=replace(base, name=name, url=url, schema=None,
                        pool_min_size=0, pool_max_size=4),
        owner=replace(owner, name=name, url=owner_url, schema=None))
    for config in (target.runtime, target.owner):
        actual = _current_database(config)
        if not actual.endswith(_TEST_SUFFIX):
            raise RuntimeError(
                f"Refusing to run tests against database {actual!r}: its name does not end "
                f"in {_TEST_SUFFIX!r}. Tests create and drop schemas; they only ever run in "
                "a database set aside for them.")
    return target


test_config.__test__ = False  # not a test, despite the name


def new_database(target: TestTarget | None = None, *, migrated: bool = True) -> PostgresDatabase:
    """A database of its own: a new schema in the test database.

    The schema is created (and, unless ``migrated=False``, migrated) by the
    migration role; the handle returned serves as the runtime role. Pass
    ``migrated=False`` for an empty one (no tables), as the migration and
    import tests need. Release it with :func:`drop_database`.
    """
    target = target or test_config()
    schema = f"t_{uuid.uuid4().hex[:20]}"
    with psycopg.connect(autocommit=True, **target.owner.connection_kwargs()) as conn:
        conn.execute(pg_sql.SQL("CREATE SCHEMA {}").format(pg_sql.Identifier(schema)))
    database = PostgresDatabase(replace(target.runtime, schema=schema),
                                migration_config=replace(target.owner, schema=schema))
    if migrated:
        database.migrate()
    return database


def config_of(database: PostgresDatabase) -> PostgresConfig:
    """The migration role's settings for a test database (schema included):
    what the importer, which creates tables, is given."""
    return database._migration_config


def runtime_config_of(database: PostgresDatabase) -> PostgresConfig:
    """The runtime role's settings for a test database (schema included)."""
    return database._config


def drop_database(database: PostgresDatabase) -> None:
    """Close a test database and remove its schema."""
    config = config_of(database)
    database.close()
    if not config.schema or not config.schema.startswith("t_"):
        raise RuntimeError("drop_database only removes schemas created by new_database.")
    base = replace(config, schema=None)
    with psycopg.connect(autocommit=True, **base.connection_kwargs()) as conn:
        conn.execute(pg_sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
            pg_sql.Identifier(config.schema)))


@contextmanager
def connect(database: PostgresDatabase, *, read_only: bool = False,
            as_owner: bool = False) -> Iterator[psycopg.Connection[Any]]:
    """A raw autocommit connection to a test database's schema.

    As the runtime role by default -- the role the application serves as --
    or, with ``as_owner=True``, as the migration role that owns the tables.
    ``read_only`` is accepted for the readability of the tests that only look;
    it makes the session refuse writes.
    """
    config = config_of(database) if as_owner else runtime_config_of(database)
    conn = psycopg.connect(autocommit=True, **config.connection_kwargs())
    try:
        # The same value handling the application's own connections use.
        register_value_adapters(conn)
        conn.row_factory = _row_factory
        if read_only:
            conn.execute("SET default_transaction_read_only = on")
        yield conn
    finally:
        conn.close()


# ----------------------------------------------------------- signing in --

# The household admin the API tests act as.
ADMIN = {"full_name": "Asha Rao", "email": "asha@example.com",
         "password": "correct horse battery"}


def sign_in(client: Any) -> dict[str, Any]:
    """Sign a test client in as :data:`ADMIN`: signing up on a database nobody
    has signed up to yet, logging in on one that has them. The client keeps
    the session cookie, so every later request is made as the admin."""
    if client.get("/api/auth/status").json()["setup_required"]:
        response = client.post("/api/auth/signup", json=ADMIN)
    else:
        response = client.post("/api/auth/login",
                               json={"email": ADMIN["email"], "password": ADMIN["password"]})
    assert response.status_code in (200, 201), response.text
    return response.json()["user"]

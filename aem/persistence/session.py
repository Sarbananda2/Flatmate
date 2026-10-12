"""The narrow interface repositories use to reach the database.

A :class:`Session` is one open transaction. It is created and owned by a
database adapter (see :mod:`aem.persistence.postgres`), handed to repositories
by the :class:`~aem.persistence.unit_of_work.UnitOfWork`, and never given to a
service or a route.

SQL passed to a session is PostgreSQL, with psycopg placeholders: ``%s`` for a
positional parameter and ``%(name)s`` for a named one. A literal percent sign
is always written ``%%``, whether or not the statement has parameters.

Rows come back as plain ``dict`` objects keyed by column name. The bookkeeping
column ``row_seq`` (insertion order, see the schema) is left out of every row
so that ``SELECT *`` returns exactly the record's own fields; select it under
another name (``row_seq AS seq``) on the rare occasion a caller needs it.
"""
from __future__ import annotations

from typing import Any, ContextManager, Mapping, Protocol, Sequence

Params = Sequence[Any] | Mapping[str, Any]
Row = dict[str, Any]


class Session(Protocol):
    def execute(self, sql: str, params: Params = ()) -> int:
        """Run a statement and return the number of rows it affected."""

    def fetch_one(self, sql: str, params: Params = ()) -> Row | None:
        """The first row, or ``None``."""

    def fetch_all(self, sql: str, params: Params = ()) -> list[Row]:
        """Every row."""

    def scalar(self, sql: str, params: Params = (), default: Any = None) -> Any:
        """The first column of the first row; ``default`` if absent or NULL."""

    def savepoint(self) -> ContextManager[None]:
        """A rollback point inside the transaction.

        PostgreSQL aborts the whole transaction when a statement fails. Code
        that expects a statement might fail and means to carry on afterwards
        must run it inside a savepoint.
        """

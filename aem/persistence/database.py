"""The database, as the application sees it.

:class:`Database` is what a route hands to a service. It can open a unit of
work and report on itself; it exposes no connection, cursor, or SQL. The one
implementation is :class:`aem.persistence.postgres.PostgresDatabase`.
"""
from __future__ import annotations

from typing import Any, ContextManager, Protocol

from .unit_of_work import UnitOfWork


class Database(Protocol):
    def transaction(self) -> ContextManager[UnitOfWork]:
        """A read-write unit of work.

        Commits when the block ends and rolls back if it raises. Write
        transactions run one at a time, so a service can read a balance and
        act on it without another writer changing it in between.
        """

    def read(self) -> ContextManager[UnitOfWork]:
        """A read-only unit of work over one consistent snapshot."""

    def describe(self) -> str:
        """Where the data lives, with no secret in it. Safe to log and show."""

    def status(self) -> dict[str, Any]:
        """Reachability, schema version, and basic integrity facts."""

    def migrate(self) -> list[str]:
        """Apply pending schema migrations; returns a log of what was done."""

    def schema_version(self) -> int:
        """The newest applied migration, or 0 for an empty database."""

    def close(self) -> None:
        """Release connections. The database server itself is not touched."""

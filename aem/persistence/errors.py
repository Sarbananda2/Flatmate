"""Errors the persistence layer raises.

Nothing above the persistence layer sees a database driver's exception. The
adapter translates every driver error into one of these, so a service that
needs to react to a duplicate key catches :class:`UniqueViolation` and never
imports a driver to do it.

These deliberately do *not* derive from :class:`aem.errors.AemError`. An
``AemError`` is an expected, operator-facing outcome that services and routers
catch and report; a persistence error is the database refusing something, and
it must not be swallowed by an ``except AemError`` written for the former.
"""
from __future__ import annotations

from typing import Any


class PersistenceError(Exception):
    """The database refused an operation, or could not be reached."""

    code = "database_error"
    http_status = 500

    def __init__(self, message: str, *, code: str | None = None,
                 details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        self.details: dict[str, Any] = dict(details or {})

    def __str__(self) -> str:
        """The database's own message when there is one.

        Services put ``str(error)`` into the message they show the operator
        ("Could not save expense: ..."), and the specific reason is more use
        there than the generic one.
        """
        return str(self.details.get("detail") or self.message)


class DatabaseUnavailableError(PersistenceError):
    """No connection could be made to the configured database."""

    code = "database_unavailable"
    http_status = 503


class IntegrityViolation(PersistenceError):
    """A declared constraint rejected the change.

    ``constraint`` names the constraint when the database reported one.
    """

    def __init__(self, message: str, *, constraint: str | None = None,
                 code: str | None = None, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, code=code, details=details)
        self.constraint = constraint


class UniqueViolation(IntegrityViolation):
    """A primary key or unique constraint already holds this value."""


class ForeignKeyViolation(IntegrityViolation):
    """The row refers to, or is referred to by, another row."""


class CheckViolation(IntegrityViolation):
    """A CHECK or NOT NULL constraint rejected the value."""


class GuardViolation(PersistenceError):
    """A schema guard refused the change.

    The schema's triggers protect accounting history -- closed statements, the
    audit trail, applied payments -- and raise messages of the form
    ``marker: explanation``. ``marker`` is that leading word and ``code`` is the
    API error code it maps to.
    """

    http_status = 409

    def __init__(self, message: str, *, marker: str, code: str | None = None,
                 details: dict[str, Any] | None = None) -> None:
        super().__init__(message, code=code, details=details)
        self.marker = marker


class MigrationError(PersistenceError):
    code = "migration_error"

"""Persistence boundary.

    route -> service -> unit of work / repositories -> PostgreSQL adapter -> PostgreSQL

Services import from here (and nowhere deeper): the :class:`Database` they are
given, the :class:`UnitOfWork` they open on it, and the errors it can raise.
SQL lives in :mod:`aem.persistence.repositories`; the driver, connections, and
transactions live in :mod:`aem.persistence.postgres`.
"""
from .database import Database
from .errors import (
    CheckViolation,
    DatabaseUnavailableError,
    ForeignKeyViolation,
    GuardViolation,
    IntegrityViolation,
    MigrationError,
    PersistenceError,
    UniqueViolation,
)
from .unit_of_work import UnitOfWork

__all__ = [
    "CheckViolation", "Database", "DatabaseUnavailableError", "ForeignKeyViolation",
    "GuardViolation", "IntegrityViolation", "MigrationError", "PersistenceError",
    "UniqueViolation", "UnitOfWork",
]

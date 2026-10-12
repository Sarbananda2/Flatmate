"""Compatibility import path for the electricity expense workflow.

The workflow moved to ``aem.services.electricity``, and database setup moved to
the versioned migrations behind the persistence boundary: ``initialize_database``
takes a ``Database`` (``aem.persistence.Database``) and creates *or upgrades*
the schema of the configured PostgreSQL database, preserving existing data.

``ElectricityWorkflowError`` is ``aem.errors.AemError``, the base class of every
expected error, so existing ``except`` blocks keep working.
"""
import _root  # noqa: F401  (makes ``aem`` importable)

from aem.errors import AemError as ElectricityWorkflowError  # noqa: F401
from aem.services.electricity import (  # noqa: F401
    create_electricity_expense,
    get_electricity_expense,
)


def initialize_database(db, schema_path=None):
    """Make sure the schema is current. ``schema_path`` is accepted and ignored.

    Creating or upgrading the schema takes the migration role, which owns it.
    A handle that serves as the runtime role only -- what the terminal CLIs
    here use -- passes when the schema is already current, and otherwise raises
    saying to run ``scripts/manage.py init``.
    """
    from aem.persistence.postgres.migrate import latest_version

    if db.schema_version() != latest_version():
        db.migrate()


__all__ = [
    "ElectricityWorkflowError", "create_electricity_expense",
    "get_electricity_expense", "initialize_database",
]

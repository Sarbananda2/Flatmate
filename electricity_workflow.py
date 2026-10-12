"""Compatibility import path for the electricity expense workflow.

The workflow moved to ``aem.services.electricity``, and database setup moved to
the versioned migrations in ``aem.migrations``: ``initialize_database`` now
creates *or upgrades* the database, preserving existing data.

``ElectricityWorkflowError`` is ``aem.errors.AemError``, the base class of every
expected error, so existing ``except`` blocks keep working.
"""
from aem.errors import AemError as ElectricityWorkflowError  # noqa: F401
from aem.migrations import initialize_database  # noqa: F401
from aem.services.electricity import (  # noqa: F401
    create_electricity_expense,
    get_electricity_expense,
)

__all__ = [
    "ElectricityWorkflowError", "create_electricity_expense",
    "get_electricity_expense", "initialize_database",
]

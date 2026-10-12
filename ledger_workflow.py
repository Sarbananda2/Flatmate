"""Compatibility import path for the general ledger workflow.

The workflow was split into focused services under ``aem.services`` when the
application grew an API and a UI. Every function below is the same code, under
the same name, with the same behaviour -- the services simply accept extra
keyword arguments for the features added since.

``LedgerWorkflowError`` is now ``aem.errors.AemError``, the base class of every
expected error the application raises, so existing ``except``/``assertRaises``
blocks keep catching what they caught before.
"""
from aem.errors import AemError as LedgerWorkflowError  # noqa: F401
from aem.services.adjustments import record_adjustment  # noqa: F401
from aem.services.definitions import (  # noqa: F401
    create_expense_definition,
    list_expense_definitions,
)
from aem.services.expenses import create_expense  # noqa: F401
from aem.services.payments import record_payment  # noqa: F401
from aem.services.reconciliation import reconcile_pooled_settlement  # noqa: F401
from aem.services.statements import (  # noqa: F401
    close_expired_statements,
    create_monthly_statement,
    finalize_statement,
    statement_snapshot,
)

__all__ = [
    "LedgerWorkflowError", "close_expired_statements", "create_expense",
    "create_expense_definition", "create_monthly_statement", "finalize_statement",
    "list_expense_definitions", "reconcile_pooled_settlement", "record_adjustment",
    "record_payment", "statement_snapshot",
]

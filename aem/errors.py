"""Error types shared by the engines, services, and API layer."""
from __future__ import annotations

from typing import Any


class AemError(ValueError):
    """Base class for every expected, operator-facing error.

    `code` is a stable machine-readable identifier used by the API layer so the
    frontend can react without string matching. `details` carries structured
    context (record IDs, expected/actual totals) for the operator.
    """

    code = "aem_error"
    http_status = 400

    def __init__(self, message: str, *, code: str | None = None,
                 details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        self.details: dict[str, Any] = dict(details or {})

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


class ValidationError(AemError):
    code = "validation_error"
    http_status = 422


class NotFoundError(AemError):
    code = "not_found"
    http_status = 404


class AuthenticationError(AemError):
    """No signed-in user, or credentials that do not match one."""

    code = "authentication_required"
    http_status = 401


class ConflictError(AemError):
    """The request is well-formed but conflicts with current state."""

    code = "conflict"
    http_status = 409


class ImmutableRecordError(ConflictError):
    """A finalized or closed record cannot be changed this way."""

    code = "immutable_record"
    http_status = 409


class ReconciliationError(AemError):
    """The ledger cannot be reconciled without operator intervention.

    This is deliberately an error rather than a silently balanced result: the
    application never invents a balancing entry.
    """

    code = "reconciliation_error"
    http_status = 409

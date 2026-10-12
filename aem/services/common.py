"""Lookups and guards shared by every service."""
from __future__ import annotations

from typing import Any

from ..errors import ConflictError, ImmutableRecordError, NotFoundError, ValidationError
from ..persistence import UnitOfWork

POOL_ACCOUNT_ID = "__pool__"

ALLOCATION_METHODS = ("equal", "weighted", "custom", "measured", "host_guest")
EXPENSE_KINDS = ("general", "electricity")
PAYMENT_KINDS = ("to_pool", "provider_advance", "refund", "adjustment")
PAYMENT_MODES = ("upi", "bank_transfer", "cash", "other")
STATEMENT_STATUSES = ("draft", "finalized_correction_window", "closed")
RECURRENCES = ("none", "monthly")


def require_text(value: Any, label: str, *, max_length: int = 500) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{label} is required.", details={"field": label})
    text = value.strip()
    if len(text) > max_length:
        raise ValidationError(f"{label} must be at most {max_length} characters.",
                              details={"field": label, "max_length": max_length})
    return text


def optional_text(value: Any, label: str, *, max_length: int = 2000) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError(f"{label} must be text.", details={"field": label})
    text = value.strip()
    if not text:
        return None
    if len(text) > max_length:
        raise ValidationError(f"{label} must be at most {max_length} characters.",
                              details={"field": label, "max_length": max_length})
    return text


def get_apartment(uow: UnitOfWork, apartment_id: str) -> dict[str, Any]:
    apartment = uow.apartments.get(apartment_id)
    if apartment is None:
        raise NotFoundError("Apartment not found.",
                            details={"apartment_id": apartment_id})
    return apartment


def apartment_exists(uow: UnitOfWork, apartment_id: str) -> bool:
    return uow.apartments.exists(apartment_id)


def list_residents(uow: UnitOfWork, apartment_id: str, *,
                   active_only: bool = False) -> list[dict[str, Any]]:
    return uow.residents.list_for_apartment(apartment_id, active_only=active_only)


def resident_ids(uow: UnitOfWork, apartment_id: str, *,
                 active_only: bool = False) -> list[str]:
    """Resident IDs, sorted. Sorting makes every downstream split deterministic."""
    return sorted(uow.residents.ids_for_apartment(apartment_id, active_only=active_only))


def get_resident(uow: UnitOfWork, resident_id: str,
                 apartment_id: str | None = None) -> dict[str, Any]:
    resident = uow.residents.get(resident_id)
    if resident is None:
        raise NotFoundError("Resident not found.", details={"resident_id": resident_id})
    if apartment_id is not None and resident["apartment_id"] != apartment_id:
        raise ValidationError("Resident must belong to this apartment.",
                              details={"resident_id": resident_id,
                                       "apartment_id": apartment_id})
    return resident


def get_statement(uow: UnitOfWork, statement_id: str,
                  apartment_id: str | None = None) -> dict[str, Any]:
    statement = uow.statements.get(statement_id)
    if statement is None:
        raise NotFoundError("Statement not found.", details={"statement_id": statement_id})
    if apartment_id is not None and statement["apartment_id"] != apartment_id:
        raise ValidationError("Statement must belong to this apartment.",
                              details={"statement_id": statement_id})
    return statement


def get_expense(uow: UnitOfWork, expense_id: str,
                apartment_id: str | None = None) -> dict[str, Any]:
    expense = uow.expenses.get(expense_id)
    if expense is None:
        raise NotFoundError("Expense not found.", details={"expense_id": expense_id})
    if apartment_id is not None and expense.get("apartment_id") not in (None, apartment_id):
        raise ValidationError("Expense must belong to this apartment.",
                              details={"expense_id": expense_id})
    return expense


def require_draft_statement(uow: UnitOfWork, statement_id: str,
                            apartment_id: str) -> dict[str, Any]:
    """Guard used when attaching a new expense to a statement."""
    statement = get_statement(uow, statement_id, apartment_id)
    if statement["status"] != "draft":
        raise ConflictError("Expenses can only be added to a draft statement.",
                            code="statement_not_draft",
                            details={"statement_id": statement_id,
                                     "status": statement["status"]})
    return statement


def statement_edit_mode(uow: UnitOfWork, statement_id: str | None) -> str:
    """How an edit to a record in this statement must be handled.

    ``"free"``      — draft, or not attached to any statement: edit directly.
    ``"revision"``  — finalized and inside the correction window: the edit is
                      allowed but must be recorded as a statement revision with
                      a reason.
    ``"locked"``    — closed: the record is immutable; use a ledger adjustment.
    """
    if not statement_id:
        return "free"
    statement = get_statement(uow, statement_id)
    if statement["status"] == "draft":
        return "free"
    if statement["status"] == "finalized_correction_window":
        return "revision"
    return "locked"


def assert_not_locked(uow: UnitOfWork, statement_id: str | None,
                      what: str = "record") -> str:
    mode = statement_edit_mode(uow, statement_id)
    if mode == "locked":
        raise ImmutableRecordError(
            f"This {what} belongs to a closed statement and cannot be changed. "
            "Record a ledger adjustment instead.",
            code="closed_statement",
            details={"statement_id": statement_id})
    return mode


def paginate(limit: Any, offset: Any, *, default_limit: int = 50,
             max_limit: int = 500) -> tuple[int, int]:
    try:
        resolved_limit = default_limit if limit is None else int(limit)
        resolved_offset = 0 if offset is None else int(offset)
    except (TypeError, ValueError):
        raise ValidationError("limit and offset must be integers.") from None
    if resolved_limit < 1 or resolved_limit > max_limit:
        raise ValidationError(f"limit must be between 1 and {max_limit}.",
                              details={"field": "limit", "max": max_limit})
    if resolved_offset < 0:
        raise ValidationError("offset cannot be negative.", details={"field": "offset"})
    return resolved_limit, resolved_offset

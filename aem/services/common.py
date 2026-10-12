"""Lookups and guards shared by every service."""
from __future__ import annotations

import sqlite3
from typing import Any

from ..errors import ConflictError, ImmutableRecordError, NotFoundError, ValidationError

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


def get_apartment(conn: sqlite3.Connection, apartment_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM apartment WHERE apartment_id = ?",
                       (apartment_id,)).fetchone()
    if row is None:
        raise NotFoundError("Apartment not found.",
                            details={"apartment_id": apartment_id})
    return dict(row)


def apartment_exists(conn: sqlite3.Connection, apartment_id: str) -> bool:
    return conn.execute("SELECT 1 FROM apartment WHERE apartment_id = ?",
                        (apartment_id,)).fetchone() is not None


def list_residents(conn: sqlite3.Connection, apartment_id: str, *,
                   active_only: bool = False) -> list[dict[str, Any]]:
    sql = "SELECT * FROM resident WHERE apartment_id = ?"
    if active_only:
        sql += " AND is_active = 1"
    sql += " ORDER BY display_name, resident_id"
    return [dict(row) for row in conn.execute(sql, (apartment_id,)).fetchall()]


def resident_ids(conn: sqlite3.Connection, apartment_id: str, *,
                 active_only: bool = False) -> list[str]:
    """Resident IDs, sorted. Sorting makes every downstream split deterministic."""
    sql = "SELECT resident_id FROM resident WHERE apartment_id = ?"
    if active_only:
        sql += " AND is_active = 1"
    return sorted(row[0] for row in conn.execute(sql, (apartment_id,)).fetchall())


def get_resident(conn: sqlite3.Connection, resident_id: str,
                 apartment_id: str | None = None) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM resident WHERE resident_id = ?",
                       (resident_id,)).fetchone()
    if row is None:
        raise NotFoundError("Resident not found.", details={"resident_id": resident_id})
    resident = dict(row)
    if apartment_id is not None and resident["apartment_id"] != apartment_id:
        raise ValidationError("Resident must belong to this apartment.",
                              details={"resident_id": resident_id,
                                       "apartment_id": apartment_id})
    return resident


def get_statement(conn: sqlite3.Connection, statement_id: str,
                  apartment_id: str | None = None) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM monthly_statement WHERE statement_id = ?",
                       (statement_id,)).fetchone()
    if row is None:
        raise NotFoundError("Statement not found.", details={"statement_id": statement_id})
    statement = dict(row)
    if apartment_id is not None and statement["apartment_id"] != apartment_id:
        raise ValidationError("Statement must belong to this apartment.",
                              details={"statement_id": statement_id})
    return statement


def get_expense(conn: sqlite3.Connection, expense_id: str,
                apartment_id: str | None = None) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM expense_instance WHERE expense_id = ?",
                       (expense_id,)).fetchone()
    if row is None:
        raise NotFoundError("Expense not found.", details={"expense_id": expense_id})
    expense = dict(row)
    if apartment_id is not None and expense.get("apartment_id") not in (None, apartment_id):
        raise ValidationError("Expense must belong to this apartment.",
                              details={"expense_id": expense_id})
    return expense


def require_draft_statement(conn: sqlite3.Connection, statement_id: str,
                            apartment_id: str) -> dict[str, Any]:
    """Guard used when attaching a new expense to a statement."""
    statement = get_statement(conn, statement_id, apartment_id)
    if statement["status"] != "draft":
        raise ConflictError("Expenses can only be added to a draft statement.",
                            code="statement_not_draft",
                            details={"statement_id": statement_id,
                                     "status": statement["status"]})
    return statement


def statement_edit_mode(conn: sqlite3.Connection, statement_id: str | None) -> str:
    """How an edit to a record in this statement must be handled.

    ``"free"``      — draft, or not attached to any statement: edit directly.
    ``"revision"``  — finalized and inside the correction window: the edit is
                      allowed but must be recorded as a statement revision with
                      a reason.
    ``"locked"``    — closed: the record is immutable; use a ledger adjustment.
    """
    if not statement_id:
        return "free"
    statement = get_statement(conn, statement_id)
    if statement["status"] == "draft":
        return "free"
    if statement["status"] == "finalized_correction_window":
        return "revision"
    return "locked"


def assert_not_locked(conn: sqlite3.Connection, statement_id: str | None,
                      what: str = "record") -> str:
    mode = statement_edit_mode(conn, statement_id)
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

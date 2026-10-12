"""Signed ledger adjustments.

An adjustment is the only way to correct a closed statement. It is a signed
amount against one resident:

* **positive** credits the resident (the pool owes them more);
* **negative** debits the resident (they owe the pool more).

Adjustments are append-only. A mistaken adjustment is corrected by a reversing
adjustment that points at the original through ``reverses_adjustment_id``, so
the original statement stays exactly as it was finalized and the correction is
visible as its own event. Each adjustment therefore affects balances exactly
once, and its effect can be traced to the row that caused it.
"""
from __future__ import annotations

from typing import Any

from .. import db
from ..db import connect, new_id, now_timestamp, transaction
from ..errors import ConflictError, NotFoundError, ValidationError
from ..periods import parse_iso_date
from . import audit
from .common import get_apartment, get_resident, get_statement, paginate, require_text


def record_adjustment(
    db_path, *, apartment_id: str, resident_id: str, amount_paise: int, reason: str,
    created_by: str, statement_id: str | None = None, adjustment_id: str | None = None,
    created_at: str | None = None, reverses_adjustment_id: str | None = None,
    effective_date: str | None = None,
) -> str:
    """Record a signed correction: positive credits the resident; negative debits."""
    if isinstance(amount_paise, bool) or not isinstance(amount_paise, int) or amount_paise == 0:
        raise ValidationError("Adjustment must be a non-zero integer number of paise.",
                              details={"field": "amount_paise"})
    if not isinstance(reason, str) or not reason.strip() \
            or not isinstance(created_by, str) or not created_by.strip():
        raise ValidationError("Adjustment reason and created_by are required.")
    adjustment_id = adjustment_id or new_id("adjustment")
    timestamp = created_at or now_timestamp()
    effective = effective_date or timestamp[:10]
    parse_iso_date(effective, "effective_date")
    with transaction(db_path) as conn:
        get_apartment(conn, apartment_id)
        get_resident(conn, resident_id, apartment_id)
        if statement_id:
            get_statement(conn, statement_id, apartment_id)
        if reverses_adjustment_id:
            original = db.fetch_one(
                conn, "SELECT * FROM ledger_adjustment WHERE adjustment_id=?",
                (reverses_adjustment_id,))
            if original is None:
                raise NotFoundError("The adjustment being reversed does not exist.",
                                    details={"reverses_adjustment_id": reverses_adjustment_id})
            if original["resident_id"] != resident_id:
                raise ValidationError(
                    "A reversing adjustment must apply to the same resident as the original.",
                    details={"reverses_adjustment_id": reverses_adjustment_id})
            existing = db.scalar(
                conn, "SELECT COUNT(*) FROM ledger_adjustment WHERE reverses_adjustment_id=?",
                (reverses_adjustment_id,), default=0)
            if existing:
                raise ConflictError("That adjustment has already been reversed.",
                                    code="adjustment_already_reversed",
                                    details={"reverses_adjustment_id": reverses_adjustment_id})
            if amount_paise != -original["amount_paise"]:
                raise ValidationError(
                    "A reversing adjustment must be the exact negative of the original.",
                    code="reversal_amount_mismatch",
                    details={"original_paise": original["amount_paise"],
                             "expected_paise": -original["amount_paise"],
                             "provided_paise": amount_paise})
        conn.execute(
            """INSERT INTO ledger_adjustment
               (adjustment_id, statement_id, resident_id, amount_paise, reason, created_at,
                created_by, reverses_adjustment_id, effective_date)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (adjustment_id, statement_id, resident_id, amount_paise, reason.strip(),
             timestamp, created_by.strip(), reverses_adjustment_id, effective))
        audit.record(conn, entity_type="ledger_adjustment", entity_id=adjustment_id,
                     action="create", actor=created_by, apartment_id=apartment_id,
                     reason=reason.strip(),
                     new_value={"resident_id": resident_id, "amount_paise": amount_paise,
                                "statement_id": statement_id,
                                "effective_date": effective,
                                "reverses_adjustment_id": reverses_adjustment_id})
    return adjustment_id


def reverse_adjustment(db_path, adjustment_id: str, *, apartment_id: str, reason: str,
                       created_by: str = "operator") -> str:
    """Create the exact negative of an existing adjustment."""
    reason = require_text(reason, "reason", max_length=2000)
    with connect(db_path, read_only=True) as conn:
        original = db.fetch_one(conn, "SELECT * FROM ledger_adjustment WHERE adjustment_id=?",
                                (adjustment_id,))
        if original is None:
            raise NotFoundError("Adjustment not found.", details={"adjustment_id": adjustment_id})
    return record_adjustment(
        db_path, apartment_id=apartment_id, resident_id=original["resident_id"],
        amount_paise=-original["amount_paise"],
        reason=f"Reversal of {adjustment_id}: {reason}", created_by=created_by,
        statement_id=original["statement_id"], reverses_adjustment_id=adjustment_id)


def list_adjustments(db_path, *, apartment_id: str, resident_id: str | None = None,
                     statement_id: str | None = None, limit: int | None = 100,
                     offset: int | None = 0) -> dict[str, Any]:
    limit, offset = paginate(limit, offset)
    clauses = ["r.apartment_id = ?"]
    params: list[Any] = [apartment_id]
    if resident_id:
        clauses.append("la.resident_id = ?")
        params.append(resident_id)
    if statement_id:
        clauses.append("la.statement_id = ?")
        params.append(statement_id)
    where = " AND ".join(clauses)
    base = f"""
        FROM ledger_adjustment la
        JOIN resident r ON r.resident_id = la.resident_id
        LEFT JOIN monthly_statement s ON s.statement_id = la.statement_id
        WHERE {where}
    """
    with connect(db_path, read_only=True) as conn:
        get_apartment(conn, apartment_id)
        total = int(conn.execute(f"SELECT COUNT(*) {base}", tuple(params)).fetchone()[0])
        rows = db.fetch_all(
            conn,
            f"""SELECT la.*, r.display_name, s.settlement_month, s.status AS statement_status,
                       (SELECT rv.adjustment_id FROM ledger_adjustment rv
                         WHERE rv.reverses_adjustment_id = la.adjustment_id) AS reversed_by
                  {base}
                ORDER BY la.effective_date DESC, la.created_at DESC, la.rowid DESC
                LIMIT ? OFFSET ?""", params + [limit, offset])
    return {"items": rows, "total": total, "limit": limit, "offset": offset}

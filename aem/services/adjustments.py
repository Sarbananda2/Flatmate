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

from ..errors import ConflictError, NotFoundError, ValidationError
from ..ids import new_id, now_timestamp
from ..periods import parse_iso_date
from ..persistence import Database
from . import audit
from .common import get_apartment, get_resident, get_statement, paginate, require_text


def record_adjustment(
    db: Database, *, apartment_id: str, resident_id: str, amount_paise: int, reason: str,
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
    with db.transaction() as uow:
        get_apartment(uow, apartment_id)
        get_resident(uow, resident_id, apartment_id)
        if statement_id:
            get_statement(uow, statement_id, apartment_id)
        if reverses_adjustment_id:
            original = uow.adjustments.get(reverses_adjustment_id)
            if original is None:
                raise NotFoundError("The adjustment being reversed does not exist.",
                                    details={"reverses_adjustment_id": reverses_adjustment_id})
            if original["resident_id"] != resident_id:
                raise ValidationError(
                    "A reversing adjustment must apply to the same resident as the original.",
                    details={"reverses_adjustment_id": reverses_adjustment_id})
            existing = uow.adjustments.count_reversals_of(reverses_adjustment_id)
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
        uow.adjustments.insert_adjustment(
            adjustment_id=adjustment_id, statement_id=statement_id, resident_id=resident_id,
            amount_paise=amount_paise, reason=reason.strip(), created_at=timestamp,
            created_by=created_by.strip(), reverses_adjustment_id=reverses_adjustment_id,
            effective_date=effective)
        audit.record(uow, entity_type="ledger_adjustment", entity_id=adjustment_id,
                     action="create", actor=created_by, apartment_id=apartment_id,
                     reason=reason.strip(),
                     new_value={"resident_id": resident_id, "amount_paise": amount_paise,
                                "statement_id": statement_id,
                                "effective_date": effective,
                                "reverses_adjustment_id": reverses_adjustment_id})
    return adjustment_id


def reverse_adjustment(db: Database, adjustment_id: str, *, apartment_id: str, reason: str,
                       created_by: str = "operator") -> str:
    """Create the exact negative of an existing adjustment."""
    reason = require_text(reason, "reason", max_length=2000)
    with db.read() as uow:
        original = uow.adjustments.get(adjustment_id)
        if original is None:
            raise NotFoundError("Adjustment not found.", details={"adjustment_id": adjustment_id})
    return record_adjustment(
        db, apartment_id=apartment_id, resident_id=original["resident_id"],
        amount_paise=-original["amount_paise"],
        reason=f"Reversal of {adjustment_id}: {reason}", created_by=created_by,
        statement_id=original["statement_id"], reverses_adjustment_id=adjustment_id)


def list_adjustments(db: Database, *, apartment_id: str, resident_id: str | None = None,
                     statement_id: str | None = None, limit: int | None = 100,
                     offset: int | None = 0) -> dict[str, Any]:
    limit, offset = paginate(limit, offset)
    with db.read() as uow:
        get_apartment(uow, apartment_id)
        total = uow.adjustments.count_for_apartment(
            apartment_id=apartment_id, resident_id=resident_id, statement_id=statement_id)
        rows = uow.adjustments.list_for_apartment(
            apartment_id=apartment_id, resident_id=resident_id, statement_id=statement_id,
            limit=limit, offset=offset)
    return {"items": rows, "total": total, "limit": limit, "offset": offset}

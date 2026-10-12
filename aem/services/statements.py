"""Monthly statements: draft, finalize, correct, close.

Lifecycle
---------

``draft``
    Everything is editable. Expenses can be attached and detached, allocations
    recalculated, recurring expenses generated.

``finalized_correction_window``
    A snapshot has been taken and stored. The window is 72 hours by default --
    the agreed operational reading of "three days" -- and during it the operator
    may still correct the data, but every correction must carry a reason and is
    written as a numbered, immutable ``statement_revision``.

``closed``
    The snapshot is immutable, enforced by the service layer and by database
    triggers. Later corrections are signed ledger adjustments against the
    resident, linked to this statement; the original statement is never
    rewritten.

The snapshot is the statement's record of what was true when it was finalized.
It stores its own ``as_of_date`` and a SHA-256 ``snapshot_hash`` so it can be
re-read and verified byte-for-byte afterwards.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any

from ..errors import ConflictError, ImmutableRecordError, NotFoundError, ValidationError
from ..ids import new_id, now_timestamp
from ..periods import month_end, month_range, normalize_month
from ..persistence import Database, IntegrityViolation, UnitOfWork
from . import audit
from .common import (
    get_apartment,
    get_statement,
    optional_text,
    paginate,
    require_text,
    resident_ids,
)
from .reconciliation import carry_forward, reconcile
from .validation import SEVERITY_ERROR

DEFAULT_CORRECTION_WINDOW_HOURS = 72
SNAPSHOT_VERSION = 2

LEGACY_WARNING = (
    "Net positions reconcile the full recorded apartment ledger across months. The "
    "__pool__ account represents cash held by or owed to the collection pool. Link "
    "provider advances to the expense they fund whenever that expense also records a "
    "payer, to avoid double counting.")


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def snapshot_hash(payload: dict[str, Any]) -> str:
    """Hash of the snapshot with its own hash field removed."""
    without = {k: v for k, v in payload.items() if k != "snapshot_hash"}
    return hashlib.sha256(_canonical(without).encode("utf-8")).hexdigest()


def create_monthly_statement(db: Database, *, apartment_id: str, settlement_month: str,
                             statement_id: str | None = None, created_at: str | None = None,
                             correction_window_hours: int = DEFAULT_CORRECTION_WINDOW_HOURS,
                             notes: str | None = None, actor: str = "operator") -> str:
    try:
        settlement_month = normalize_month(settlement_month, "settlement_month")
    except ValidationError:
        raise ValidationError("settlement_month must use YYYY-MM format.",
                              details={"field": "settlement_month"}) from None
    if correction_window_hours < 0:
        raise ValidationError("correction_window_hours cannot be negative.",
                              details={"field": "correction_window_hours"})
    statement_id = statement_id or new_id("statement")
    timestamp = created_at or now_timestamp()
    with db.transaction() as uow:
        get_apartment(uow, apartment_id)
        existing = uow.statements.find_for_month(apartment_id, settlement_month)
        if existing:
            raise ConflictError(
                f"A {existing['status']} statement already exists for {settlement_month}.",
                code="statement_exists",
                details={"statement_id": existing["statement_id"],
                         "settlement_month": settlement_month,
                         "status": existing["status"]})
        try:
            uow.statements.insert_draft(
                statement_id=statement_id, apartment_id=apartment_id,
                settlement_month=settlement_month, created_at=timestamp,
                correction_window_hours=int(correction_window_hours),
                notes=optional_text(notes, "notes"), updated_at=timestamp)
        except IntegrityViolation as exc:
            raise ValidationError(f"Could not create statement: {exc}") from exc
        audit.record(uow, entity_type="statement", entity_id=statement_id, action="create",
                     actor=actor, apartment_id=apartment_id,
                     new_value={"settlement_month": settlement_month, "status": "draft"})
    return statement_id


def attach_eligible_expenses(db: Database, statement_id: str, *, actor: str = "operator",
                             expense_ids: list[str] | None = None) -> dict[str, Any]:
    """Attach unattached expenses whose settlement period is this statement's month.

    This is how a statement collects its contents: by each expense's configured
    period rule, not by transaction date. A September electricity bill entered in
    October under the ``previous_month`` rule settles in October and is picked up
    by the October statement.
    """
    with db.transaction() as uow:
        statement = get_statement(uow, statement_id)
        if statement["status"] != "draft":
            raise ConflictError("Expenses can only be added to a draft statement.",
                                code="statement_not_draft",
                                details={"statement_id": statement_id,
                                         "status": statement["status"]})
        if expense_ids is not None:
            if not expense_ids:
                return {"statement_id": statement_id, "attached": [], "attached_count": 0}
        rows = uow.statements.unattached_expenses(
            statement["apartment_id"], statement["settlement_month"],
            expense_ids=expense_ids)
        for row in rows:
            uow.statements.attach_expense(expense_id=row["expense_id"],
                                          statement_id=statement_id,
                                          updated_at=now_timestamp())
            audit.record(uow, entity_type="expense", entity_id=row["expense_id"],
                         action="attach_to_statement", actor=actor,
                         apartment_id=statement["apartment_id"],
                         field_name="statement_id", old_value=None, new_value=statement_id)
        if rows:
            audit.record(uow, entity_type="statement", entity_id=statement_id,
                         action="attach_expenses", actor=actor,
                         apartment_id=statement["apartment_id"],
                         new_value={"expense_ids": [r["expense_id"] for r in rows]})
        return {"statement_id": statement_id, "attached": rows, "attached_count": len(rows)}


def detach_expense(db: Database, statement_id: str, expense_id: str, *,
                   actor: str = "operator") -> dict[str, Any]:
    with db.transaction() as uow:
        statement = get_statement(uow, statement_id)
        if statement["status"] != "draft":
            raise ConflictError("Expenses can only be removed from a draft statement.",
                                code="statement_not_draft",
                                details={"statement_id": statement_id,
                                         "status": statement["status"]})
        row = uow.statements.expense_attachment(expense_id)
        if row is None:
            raise NotFoundError("Expense not found.", details={"expense_id": expense_id})
        if row["statement_id"] != statement_id:
            raise ValidationError("That expense is not attached to this statement.",
                                  details={"expense_id": expense_id})
        uow.statements.detach_expense(expense_id=expense_id, updated_at=now_timestamp())
        audit.record(uow, entity_type="expense", entity_id=expense_id,
                     action="detach_from_statement", actor=actor,
                     apartment_id=statement["apartment_id"], field_name="statement_id",
                     old_value=statement_id, new_value=None)
        return {"statement_id": statement_id, "expense_id": expense_id, "detached": True}


def _statement_expenses(uow: UnitOfWork, statement_id: str,
                        residents: list[str]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    expenses = uow.statements.expenses_for(statement_id)
    expense_data: list[dict[str, Any]] = []
    charges = {rid: 0 for rid in residents}
    for expense in expenses:
        allocations = uow.statements.allocations_with_settled(expense["expense_id"])
        allocation_data = []
        for row in allocations:
            charges[row["resident_id"]] = charges.get(row["resident_id"], 0) + row["amount_paise"]
            allocation_data.append({
                "allocation_id": row["allocation_id"],
                "resident_id": row["resident_id"],
                "amount_paise": row["amount_paise"],
                "settled_paise": int(row["settled_paise"]),
                "outstanding_paise": row["amount_paise"] - int(row["settled_paise"]),
            })
        linked_advances = uow.statements.linked_provider_advances(expense["expense_id"])
        expense_data.append({
            "expense_id": expense["expense_id"],
            "description": expense["description"],
            "amount_paise": expense["amount_paise"],
            "expense_date": expense["expense_date"],
            "expense_period": expense["expense_period"],
            "service_period": expense["service_period"],
            "settlement_period": expense["settlement_period"],
            "period_rule": expense["period_rule"],
            "due_date": expense["due_date"],
            "expense_kind": expense["expense_kind"],
            "allocation_method": expense["allocation_method"],
            "category": expense["category"],
            "definition_name": expense["definition_name"],
            "provider_name": expense["provider_name"],
            "reference": expense["reference"],
            "payer_resident_id": expense["payer_resident_id"],
            "payer_display_name": expense["payer_display_name"],
            "generated_from_recurrence": bool(expense["generated_from_recurrence"]),
            "linked_provider_advances": linked_advances,
            "funding_source": ("linked_provider_advance" if linked_advances
                               else "payer_resident" if expense["payer_resident_id"]
                               else "household_pool"),
            "allocations": [{"resident_id": a["resident_id"], "amount_paise": a["amount_paise"]}
                            for a in allocation_data],
            "allocation_details": allocation_data,
        })
    return expense_data, charges


def statement_snapshot(db: Database, statement_id: str, *,
                       as_of_date: str | None = None) -> dict[str, Any]:
    """Build the statement view: its own expenses plus full cross-month reconciliation."""
    with db.read() as uow:
        statement = get_statement(uow, statement_id)
        apartment_id = statement["apartment_id"]
        residents = resident_ids(uow, apartment_id)
        resident_rows = uow.statements.resident_names(apartment_id)
        expense_data, statement_charges = _statement_expenses(uow, statement_id, residents)
        revisions = uow.statements.revision_summaries(statement_id)
        adjustments = uow.statements.adjustments_for(statement_id)
        payments_in_month = uow.statements.active_payments_in_month(
            apartment_id, statement["settlement_month"])
        movement = carry_forward(uow, apartment_id, statement["settlement_month"],
                                 as_of_date=as_of_date, statement_charges=statement_charges)

    reconciliation = reconcile(db, apartment_id=apartment_id, as_of_date=as_of_date)
    statement_total = sum(e["amount_paise"] for e in expense_data)
    snapshot = {
        "snapshot_version": SNAPSHOT_VERSION,
        "statement_id": statement_id,
        "apartment_id": apartment_id,
        "settlement_month": statement["settlement_month"],
        "status": statement["status"],
        "created_at": statement["created_at"],
        "finalized_at": statement["finalized_at"],
        "correction_deadline": statement["correction_deadline"],
        "closed_at": statement["closed_at"],
        "correction_window_hours": statement["correction_window_hours"],
        "generated_at": now_timestamp(),
        "as_of_date": as_of_date,
        "residents": resident_rows,
        "expenses": expense_data,
        "expense_count": len(expense_data),
        "statement_total_paise": statement_total,
        "statement_charges_paise_by_resident": statement_charges,
        "charges_paise_by_resident": statement_charges,
        "payments_in_month": payments_in_month,
        "adjustments": adjustments,
        "carry_forward_breakdown": movement["residents"],
        "carry_forward_totals": movement["totals"],
        "revisions": revisions,
        "net_positions_paise_by_resident": {
            rid: value for rid, value in
            reconciliation["net_positions_paise_by_account"].items()
            if rid != reconciliation["pool_account_id"]},
        "provider_advances_paise_by_resident":
            reconciliation["provider_credits_paise_by_resident"],
        "reconciliation": reconciliation,
        **reconciliation,
        "warning": LEGACY_WARNING,
    }
    snapshot["snapshot_hash"] = snapshot_hash(snapshot)
    return snapshot


def preview_statement(db: Database, statement_id: str) -> dict[str, Any]:
    """The draft review screen: the snapshot plus what is still missing.

    ``candidate_expenses`` are expenses whose settlement period is this month
    but which are not attached yet. ``blocking_issues`` are the accounting
    errors that must be resolved before finalizing.
    """
    snapshot = statement_snapshot(db, statement_id)
    with db.read() as uow:
        statement = get_statement(uow, statement_id)
        candidates = uow.statements.candidate_expenses(
            statement["apartment_id"], statement["settlement_month"])
    issues = snapshot["validation"]["issues"]
    blocking = [i for i in issues if i["severity"] == SEVERITY_ERROR]
    return {
        **snapshot,
        "candidate_expenses": candidates,
        "candidate_expense_total_paise": sum(c["amount_paise"] for c in candidates),
        "blocking_issues": blocking,
        "can_finalize": statement["status"] == "draft" and not blocking,
        "finalize_blocked_reason": (
            None if statement["status"] == "draft" and not blocking
            else "This statement is not a draft."
            if statement["status"] != "draft"
            else "Resolve the listed accounting errors first."),
    }


def record_revision(uow: UnitOfWork, *, statement_id: str, reason: str,
                    created_by: str, change_note: str | None = None,
                    snapshot_payload: dict[str, Any] | None = None,
                    now: datetime | None = None) -> dict[str, Any]:
    """Append a numbered revision. Called inside an open transaction.

    The revision stores the *reason* and the change note. The statement's own
    ``snapshot_json`` is refreshed separately by :func:`refresh_snapshot` once
    the transaction that changed the data has committed, so the revision can
    never record a snapshot of data that was then rolled back.
    """
    statement = get_statement(uow, statement_id)
    if statement["status"] != "finalized_correction_window":
        raise ConflictError("Only a statement inside its correction window can be revised.",
                            code="statement_not_in_window",
                            details={"statement_id": statement_id,
                                     "status": statement["status"]})
    deadline = statement["correction_deadline"]
    moment = now or datetime.now().astimezone()
    if deadline and moment > datetime.fromisoformat(deadline):
        raise ImmutableRecordError(
            "The 72-hour correction window has expired. Record a ledger adjustment instead.",
            code="correction_window_expired",
            details={"statement_id": statement_id, "correction_deadline": deadline})
    next_number = int(uow.statements.next_revision_number(statement_id))
    revision_id = new_id("revision")
    payload = snapshot_payload or {"pending": True, "change_note": change_note}
    uow.statements.append_revision(
        revision_id=revision_id, statement_id=statement_id, revision_number=next_number,
        snapshot_json=_canonical(payload), reason=reason.strip(),
        created_at=now_timestamp(), created_by=created_by)
    audit.record(uow, entity_type="statement", entity_id=statement_id, action="revise",
                 actor=created_by, apartment_id=statement["apartment_id"], reason=reason,
                 new_value={"revision_id": revision_id, "revision_number": next_number,
                            "change_note": change_note})
    return {"revision_id": revision_id, "revision_number": next_number,
            "statement_id": statement_id}


def refresh_snapshot(db: Database, statement_id: str, *, actor: str = "operator",
                     reason: str | None = None,
                     now: datetime | None = None) -> dict[str, Any]:
    """Re-take the snapshot of a statement inside its correction window."""
    snapshot = statement_snapshot(db, statement_id)
    with db.transaction() as uow:
        statement = get_statement(uow, statement_id)
        if statement["status"] == "closed":
            raise ImmutableRecordError("A closed statement's snapshot is immutable.",
                                       code="closed_statement",
                                       details={"statement_id": statement_id})
        if statement["status"] != "finalized_correction_window":
            raise ConflictError("Only a finalized statement has a snapshot to refresh.",
                                code="statement_not_finalized",
                                details={"statement_id": statement_id,
                                         "status": statement["status"]})
        snapshot["status"] = statement["status"]
        snapshot["finalized_at"] = statement["finalized_at"]
        snapshot["correction_deadline"] = statement["correction_deadline"]
        snapshot["snapshot_hash"] = snapshot_hash(snapshot)
        uow.statements.store_snapshot(statement_id=statement_id,
                                      snapshot_json=_canonical(snapshot),
                                      updated_at=now_timestamp())
        latest = uow.statements.latest_revision(statement_id)
        if latest:
            # statement_revision is append-only, so the refreshed snapshot is
            # recorded as its own revision rather than overwriting the last one.
            record_revision(uow, statement_id=statement_id,
                            reason=reason or "snapshot refreshed after a correction",
                            created_by=actor, change_note="snapshot refreshed",
                            snapshot_payload=snapshot, now=now)
        audit.record(uow, entity_type="statement", entity_id=statement_id,
                     action="refresh_snapshot", actor=actor,
                     apartment_id=statement["apartment_id"], reason=reason,
                     new_value={"snapshot_hash": snapshot["snapshot_hash"]})
    return snapshot


def revise_statement(db: Database, statement_id: str, *, reason: str, actor: str = "operator",
                     change_note: str | None = None,
                     now: datetime | None = None) -> dict[str, Any]:
    """Record an explicit correction during the window and re-take the snapshot."""
    reason = require_text(reason, "reason", max_length=2000)
    with db.transaction() as uow:
        revision = record_revision(uow, statement_id=statement_id, reason=reason,
                                   created_by=actor, change_note=change_note, now=now)
    snapshot = refresh_snapshot(db, statement_id, actor=actor, reason=reason, now=now)
    return {**revision, "snapshot": snapshot}


def finalize_statement(db: Database, *, statement_id: str, finalized_by: str,
                       now: datetime | None = None,
                       correction_window_hours: int | None = None,
                       require_clean: bool = False) -> dict[str, Any]:
    """Finalize a statement snapshot; it becomes closed after the correction window."""
    if not isinstance(finalized_by, str) or not finalized_by.strip():
        raise ValidationError("finalized_by is required.", details={"field": "finalized_by"})
    if correction_window_hours is not None and correction_window_hours < 0:
        raise ValidationError("correction_window_hours cannot be negative.",
                              details={"field": "correction_window_hours"})
    now = now or datetime.now().astimezone()
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValidationError("Finalization time must be timezone-aware.",
                              details={"field": "now"})

    with db.read() as uow:
        statement = get_statement(uow, statement_id)
        if statement["status"] != "draft":
            raise ConflictError("Only draft statements can be finalized.",
                                code="statement_not_draft",
                                details={"statement_id": statement_id,
                                         "status": statement["status"]})
    window = (correction_window_hours if correction_window_hours is not None
              else statement["correction_window_hours"] or DEFAULT_CORRECTION_WINDOW_HOURS)
    snapshot = statement_snapshot(db, statement_id)
    blocking = [i for i in snapshot["validation"]["issues"] if i["severity"] == SEVERITY_ERROR]
    if blocking and require_clean:
        raise ConflictError(
            "This statement has unresolved accounting errors and was not finalized.",
            code="statement_has_errors", details={"issues": blocking})

    snapshot["status"] = "finalized_correction_window"
    snapshot["finalized_by"] = finalized_by.strip()
    snapshot["finalized_at"] = now.isoformat(timespec="seconds")
    snapshot["correction_deadline"] = (now + timedelta(hours=int(window))).isoformat(
        timespec="seconds")
    snapshot["correction_window_hours"] = int(window)
    snapshot["snapshot_hash"] = snapshot_hash(snapshot)

    with db.transaction() as uow:
        row = uow.statements.status_and_apartment(statement_id)
        if row is None:
            raise NotFoundError("Statement not found.", details={"statement_id": statement_id})
        if row["status"] != "draft":
            raise ConflictError("Only draft statements can be finalized.",
                                code="statement_not_draft",
                                details={"statement_id": statement_id,
                                         "status": row["status"]})
        uow.statements.mark_finalized(
            statement_id=statement_id, finalized_at=snapshot["finalized_at"],
            correction_deadline=snapshot["correction_deadline"],
            snapshot_json=_canonical(snapshot), finalized_by=snapshot["finalized_by"],
            correction_window_hours=int(window), updated_at=now_timestamp())
        uow.statements.append_revision(
            revision_id=new_id("revision"), statement_id=statement_id, revision_number=1,
            snapshot_json=_canonical(snapshot), reason="initial_finalization",
            created_at=snapshot["finalized_at"], created_by=snapshot["finalized_by"])
        audit.record(uow, entity_type="statement", entity_id=statement_id,
                     action="finalize", actor=finalized_by,
                     apartment_id=row["apartment_id"],
                     old_value={"status": "draft"},
                     new_value={"status": "finalized_correction_window",
                                "correction_deadline": snapshot["correction_deadline"],
                                "statement_total_paise": snapshot["statement_total_paise"],
                                "snapshot_hash": snapshot["snapshot_hash"]})
    return snapshot


def close_expired_statements(db: Database, *, now: datetime | None = None,
                             actor: str = "system") -> list[str]:
    """Close finalized statements whose correction window has elapsed."""
    now = now or datetime.now().astimezone()
    closed: list[str] = []
    with db.transaction() as uow:
        rows = uow.statements.in_correction_window()
        for row in rows:
            if now >= datetime.fromisoformat(row["correction_deadline"]):
                uow.statements.mark_closed(
                    statement_id=row["statement_id"],
                    closed_at=now.isoformat(timespec="seconds"), closed_by=actor,
                    updated_at=now_timestamp())
                audit.record(uow, entity_type="statement", entity_id=row["statement_id"],
                             action="close", actor=actor, apartment_id=row["apartment_id"],
                             old_value={"status": "finalized_correction_window"},
                             new_value={"status": "closed",
                                        "correction_deadline": row["correction_deadline"]})
                closed.append(row["statement_id"])
    return closed


def close_statement(db: Database, statement_id: str, *, actor: str = "operator",
                    now: datetime | None = None, force: bool = False) -> dict[str, Any]:
    """Close one statement. Without ``force`` the window must already have elapsed."""
    now = now or datetime.now().astimezone()
    with db.transaction() as uow:
        statement = get_statement(uow, statement_id)
        if statement["status"] == "closed":
            raise ConflictError("That statement is already closed.",
                                code="statement_already_closed",
                                details={"statement_id": statement_id})
        if statement["status"] != "finalized_correction_window":
            raise ConflictError("Only a finalized statement can be closed.",
                                code="statement_not_finalized",
                                details={"statement_id": statement_id,
                                         "status": statement["status"]})
        deadline = statement["correction_deadline"]
        if not force and deadline and now < datetime.fromisoformat(deadline):
            raise ConflictError(
                "The correction window has not elapsed yet. Closing early requires an "
                "explicit confirmation.",
                code="correction_window_open",
                details={"statement_id": statement_id, "correction_deadline": deadline})
        uow.statements.mark_closed(
            statement_id=statement_id, closed_at=now.isoformat(timespec="seconds"),
            closed_by=actor, updated_at=now_timestamp())
        audit.record(uow, entity_type="statement", entity_id=statement_id, action="close",
                     actor=actor, apartment_id=statement["apartment_id"],
                     reason="closed early by operator" if not deadline or now < datetime.fromisoformat(deadline) else None,
                     old_value={"status": statement["status"]},
                     new_value={"status": "closed", "forced": bool(force)})
        return {"statement_id": statement_id, "status": "closed"}


def read_statement(db: Database, statement_id: str) -> dict[str, Any]:
    with db.read() as uow:
        statement = get_statement(uow, statement_id)
        statement["revisions"] = uow.statements.revision_summaries(statement_id)
        statement["expense_count"] = int(uow.statements.expense_count(statement_id))
        statement["statement_total_paise"] = int(
            uow.statements.expense_total_paise(statement_id))
        statement["has_snapshot"] = statement["snapshot_json"] is not None
        statement.pop("snapshot_json", None)
        return statement


def read_finalized_snapshot(db: Database, statement_id: str) -> dict[str, Any]:
    """The stored snapshot exactly as it was saved, with its hash verified.

    ``hash_matches`` being false means the stored bytes no longer hash to the
    value recorded inside them -- the snapshot was tampered with outside the
    application. It is reported rather than corrected.
    """
    with db.read() as uow:
        statement = get_statement(uow, statement_id)
        if not statement["snapshot_json"]:
            raise NotFoundError(
                "This statement has no stored snapshot. Only finalized statements have one.",
                details={"statement_id": statement_id, "status": statement["status"]})
        stored = json.loads(statement["snapshot_json"])
    recorded = stored.get("snapshot_hash")
    return {"statement_id": statement_id, "status": statement["status"],
            "snapshot": stored, "snapshot_hash": recorded,
            "hash_matches": recorded == snapshot_hash(stored)}


def list_statement_revisions(db: Database, statement_id: str) -> list[dict[str, Any]]:
    with db.read() as uow:
        get_statement(uow, statement_id)
        rows = uow.statements.revisions(statement_id)
    for row in rows:
        try:
            row["snapshot"] = json.loads(row.pop("snapshot_json"))
        except (json.JSONDecodeError, TypeError):
            row["snapshot"] = None
    return rows


def list_statements(db: Database, *, apartment_id: str, status: str | None = None,
                    limit: int | None = 50, offset: int | None = 0) -> dict[str, Any]:
    limit, offset = paginate(limit, offset)
    with db.read() as uow:
        get_apartment(uow, apartment_id)
        total = int(uow.statements.count_for_apartment(apartment_id, status=status))
        rows = uow.statements.list_for_apartment(apartment_id, status=status,
                                                 limit=limit, offset=offset)
    now = datetime.now().astimezone()
    for row in rows:
        row["has_snapshot"] = bool(row["has_snapshot"])
        deadline = row["correction_deadline"]
        row["correction_window_open"] = bool(
            row["status"] == "finalized_correction_window" and deadline
            and now < datetime.fromisoformat(deadline))
        row["correction_hours_remaining"] = (
            round((datetime.fromisoformat(deadline) - now).total_seconds() / 3600, 1)
            if row["correction_window_open"] else 0.0)
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


def available_months(db: Database, apartment_id: str) -> list[str]:
    """Months that have any recorded activity, newest first."""
    with db.read() as uow:
        get_apartment(uow, apartment_id)
        months = set(uow.statements.expense_settlement_periods(apartment_id))
        months |= set(uow.statements.statement_months(apartment_id))
        months |= set(uow.statements.payment_months(apartment_id))
    months.discard(None)
    if not months:
        return [now_timestamp()[:7]]
    ordered = sorted(months)
    return sorted(month_range(ordered[0], max(ordered[-1], now_timestamp()[:7])), reverse=True)

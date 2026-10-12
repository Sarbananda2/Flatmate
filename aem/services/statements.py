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
import sqlite3
from datetime import datetime, timedelta
from typing import Any

from .. import db
from ..db import connect, new_id, now_timestamp, transaction
from ..errors import ConflictError, ImmutableRecordError, NotFoundError, ValidationError
from ..periods import month_end, month_range, normalize_month
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


def create_monthly_statement(db_path, *, apartment_id: str, settlement_month: str,
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
    with transaction(db_path) as conn:
        get_apartment(conn, apartment_id)
        existing = conn.execute(
            "SELECT statement_id, status FROM monthly_statement "
            "WHERE apartment_id=? AND settlement_month=?",
            (apartment_id, settlement_month)).fetchone()
        if existing:
            raise ConflictError(
                f"A {existing['status']} statement already exists for {settlement_month}.",
                code="statement_exists",
                details={"statement_id": existing["statement_id"],
                         "settlement_month": settlement_month,
                         "status": existing["status"]})
        try:
            conn.execute(
                """INSERT INTO monthly_statement
                   (statement_id, apartment_id, settlement_month, status, created_at,
                    correction_window_hours, notes, updated_at)
                   VALUES (?,?,?,'draft',?,?,?,?)""",
                (statement_id, apartment_id, settlement_month, timestamp,
                 int(correction_window_hours), optional_text(notes, "notes"), timestamp))
        except sqlite3.IntegrityError as exc:
            raise ValidationError(f"Could not create statement: {exc}") from exc
        audit.record(conn, entity_type="statement", entity_id=statement_id, action="create",
                     actor=actor, apartment_id=apartment_id,
                     new_value={"settlement_month": settlement_month, "status": "draft"})
    return statement_id


def attach_eligible_expenses(db_path, statement_id: str, *, actor: str = "operator",
                             expense_ids: list[str] | None = None) -> dict[str, Any]:
    """Attach unattached expenses whose settlement period is this statement's month.

    This is how a statement collects its contents: by each expense's configured
    period rule, not by transaction date. A September electricity bill entered in
    October under the ``previous_month`` rule settles in October and is picked up
    by the October statement.
    """
    with transaction(db_path) as conn:
        statement = get_statement(conn, statement_id)
        if statement["status"] != "draft":
            raise ConflictError("Expenses can only be added to a draft statement.",
                                code="statement_not_draft",
                                details={"statement_id": statement_id,
                                         "status": statement["status"]})
        params: list[Any] = [statement["apartment_id"], statement["settlement_month"]]
        sql = ("SELECT expense_id, description, amount_paise FROM expense_instance "
               "WHERE apartment_id=? AND settlement_period=? AND statement_id IS NULL")
        if expense_ids is not None:
            if not expense_ids:
                return {"statement_id": statement_id, "attached": [], "attached_count": 0}
            placeholders = ",".join("?" for _ in expense_ids)
            sql += f" AND expense_id IN ({placeholders})"
            params.extend(expense_ids)
        rows = db.fetch_all(conn, sql + " ORDER BY expense_date, rowid", params)
        for row in rows:
            conn.execute("UPDATE expense_instance SET statement_id=?, updated_at=? "
                         "WHERE expense_id=?",
                         (statement_id, now_timestamp(), row["expense_id"]))
            audit.record(conn, entity_type="expense", entity_id=row["expense_id"],
                         action="attach_to_statement", actor=actor,
                         apartment_id=statement["apartment_id"],
                         field_name="statement_id", old_value=None, new_value=statement_id)
        if rows:
            audit.record(conn, entity_type="statement", entity_id=statement_id,
                         action="attach_expenses", actor=actor,
                         apartment_id=statement["apartment_id"],
                         new_value={"expense_ids": [r["expense_id"] for r in rows]})
        return {"statement_id": statement_id, "attached": rows, "attached_count": len(rows)}


def detach_expense(db_path, statement_id: str, expense_id: str, *,
                   actor: str = "operator") -> dict[str, Any]:
    with transaction(db_path) as conn:
        statement = get_statement(conn, statement_id)
        if statement["status"] != "draft":
            raise ConflictError("Expenses can only be removed from a draft statement.",
                                code="statement_not_draft",
                                details={"statement_id": statement_id,
                                         "status": statement["status"]})
        row = conn.execute("SELECT statement_id FROM expense_instance WHERE expense_id=?",
                           (expense_id,)).fetchone()
        if row is None:
            raise NotFoundError("Expense not found.", details={"expense_id": expense_id})
        if row[0] != statement_id:
            raise ValidationError("That expense is not attached to this statement.",
                                  details={"expense_id": expense_id})
        conn.execute("UPDATE expense_instance SET statement_id=NULL, updated_at=? "
                     "WHERE expense_id=?", (now_timestamp(), expense_id))
        audit.record(conn, entity_type="expense", entity_id=expense_id,
                     action="detach_from_statement", actor=actor,
                     apartment_id=statement["apartment_id"], field_name="statement_id",
                     old_value=statement_id, new_value=None)
        return {"statement_id": statement_id, "expense_id": expense_id, "detached": True}


def _statement_expenses(conn: sqlite3.Connection, statement_id: str,
                        residents: list[str]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    expenses = conn.execute(
        """SELECT e.*, d.name AS definition_name, d.category AS category,
                  r.display_name AS payer_display_name
             FROM expense_instance e
             LEFT JOIN expense_definition d
                    ON d.expense_definition_id = e.expense_definition_id
             LEFT JOIN resident r ON r.resident_id = e.payer_resident_id
            WHERE e.statement_id = ?
            ORDER BY e.expense_date, e.expense_id""", (statement_id,)).fetchall()
    expense_data: list[dict[str, Any]] = []
    charges = {rid: 0 for rid in residents}
    for expense in expenses:
        allocations = conn.execute(
            """SELECT a.resident_id, a.amount_paise, a.allocation_id,
                      COALESCE((SELECT SUM(pa.amount_paise) FROM payment_application pa
                                 JOIN payment p ON p.payment_id = pa.payment_id
                                WHERE pa.allocation_id = a.allocation_id
                                  AND p.status = 'active'), 0) AS settled_paise
                 FROM allocation a WHERE a.expense_id = ?
                ORDER BY a.resident_id""", (expense["expense_id"],)).fetchall()
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
        linked_advances = db.fetch_all(
            conn,
            """SELECT payment_id, payer_resident_id, amount_paise, payment_date
                 FROM payment WHERE expense_id = ? AND payment_kind = 'provider_advance'
                   AND status = 'active' ORDER BY payment_date, rowid""",
            (expense["expense_id"],))
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


def statement_snapshot(db_path, statement_id: str, *,
                       as_of_date: str | None = None) -> dict[str, Any]:
    """Build the statement view: its own expenses plus full cross-month reconciliation."""
    with connect(db_path, read_only=True) as conn:
        statement = get_statement(conn, statement_id)
        apartment_id = statement["apartment_id"]
        residents = resident_ids(conn, apartment_id)
        resident_rows = db.fetch_all(
            conn, "SELECT resident_id, display_name, is_active FROM resident "
                  "WHERE apartment_id=? ORDER BY display_name", (apartment_id,))
        expense_data, statement_charges = _statement_expenses(conn, statement_id, residents)
        revisions = db.fetch_all(
            conn, "SELECT revision_id, revision_number, reason, created_at, created_by "
                  "FROM statement_revision WHERE statement_id=? ORDER BY revision_number",
            (statement_id,))
        adjustments = db.fetch_all(
            conn, "SELECT * FROM ledger_adjustment WHERE statement_id=? "
                  "ORDER BY COALESCE(effective_date, created_at), rowid", (statement_id,))
        payments_in_month = db.fetch_all(
            conn,
            """SELECT p.*, r.display_name AS payer_display_name
                 FROM payment p JOIN resident r ON r.resident_id = p.payer_resident_id
                WHERE p.apartment_id = ? AND substr(p.payment_date, 1, 7) = ?
                  AND p.status = 'active'
                ORDER BY p.payment_date, p.rowid""",
            (apartment_id, statement["settlement_month"]))
        movement = carry_forward(conn, apartment_id, statement["settlement_month"],
                                 as_of_date=as_of_date, statement_charges=statement_charges)

    reconciliation = reconcile(db_path, apartment_id=apartment_id, as_of_date=as_of_date)
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


def preview_statement(db_path, statement_id: str) -> dict[str, Any]:
    """The draft review screen: the snapshot plus what is still missing.

    ``candidate_expenses`` are expenses whose settlement period is this month
    but which are not attached yet. ``blocking_issues`` are the accounting
    errors that must be resolved before finalizing.
    """
    snapshot = statement_snapshot(db_path, statement_id)
    with connect(db_path, read_only=True) as conn:
        statement = get_statement(conn, statement_id)
        candidates = db.fetch_all(
            conn,
            """SELECT e.expense_id, e.description, e.amount_paise, e.expense_date,
                      e.settlement_period, e.service_period, e.expense_kind,
                      e.payer_resident_id
                 FROM expense_instance e
                WHERE e.apartment_id = ? AND e.settlement_period = ?
                  AND e.statement_id IS NULL
                ORDER BY e.expense_date, e.rowid""",
            (statement["apartment_id"], statement["settlement_month"]))
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


def record_revision(conn: sqlite3.Connection, *, statement_id: str, reason: str,
                    created_by: str, change_note: str | None = None,
                    snapshot_payload: dict[str, Any] | None = None,
                    now: datetime | None = None) -> dict[str, Any]:
    """Append a numbered revision. Called inside an open transaction.

    The revision stores the *reason* and the change note. The statement's own
    ``snapshot_json`` is refreshed separately by :func:`refresh_snapshot` once
    the transaction that changed the data has committed, so the revision can
    never record a snapshot of data that was then rolled back.
    """
    statement = get_statement(conn, statement_id)
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
    next_number = int(db.scalar(
        conn, "SELECT COALESCE(MAX(revision_number), 0) + 1 FROM statement_revision "
              "WHERE statement_id=?", (statement_id,), default=1))
    revision_id = new_id("revision")
    payload = snapshot_payload or {"pending": True, "change_note": change_note}
    conn.execute(
        """INSERT INTO statement_revision
           (revision_id, statement_id, revision_number, snapshot_json, reason,
            created_at, created_by)
           VALUES (?,?,?,?,?,?,?)""",
        (revision_id, statement_id, next_number, _canonical(payload), reason.strip(),
         now_timestamp(), created_by))
    audit.record(conn, entity_type="statement", entity_id=statement_id, action="revise",
                 actor=created_by, apartment_id=statement["apartment_id"], reason=reason,
                 new_value={"revision_id": revision_id, "revision_number": next_number,
                            "change_note": change_note})
    return {"revision_id": revision_id, "revision_number": next_number,
            "statement_id": statement_id}


def refresh_snapshot(db_path, statement_id: str, *, actor: str = "operator",
                     reason: str | None = None,
                     now: datetime | None = None) -> dict[str, Any]:
    """Re-take the snapshot of a statement inside its correction window."""
    snapshot = statement_snapshot(db_path, statement_id)
    with transaction(db_path) as conn:
        statement = get_statement(conn, statement_id)
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
        conn.execute("UPDATE monthly_statement SET snapshot_json=?, updated_at=? "
                     "WHERE statement_id=?",
                     (_canonical(snapshot), now_timestamp(), statement_id))
        latest = db.fetch_one(
            conn, "SELECT revision_id FROM statement_revision WHERE statement_id=? "
                  "ORDER BY revision_number DESC LIMIT 1", (statement_id,))
        if latest:
            # statement_revision is append-only, so the refreshed snapshot is
            # recorded as its own revision rather than overwriting the last one.
            record_revision(conn, statement_id=statement_id,
                            reason=reason or "snapshot refreshed after a correction",
                            created_by=actor, change_note="snapshot refreshed",
                            snapshot_payload=snapshot, now=now)
        audit.record(conn, entity_type="statement", entity_id=statement_id,
                     action="refresh_snapshot", actor=actor,
                     apartment_id=statement["apartment_id"], reason=reason,
                     new_value={"snapshot_hash": snapshot["snapshot_hash"]})
    return snapshot


def revise_statement(db_path, statement_id: str, *, reason: str, actor: str = "operator",
                     change_note: str | None = None,
                     now: datetime | None = None) -> dict[str, Any]:
    """Record an explicit correction during the window and re-take the snapshot."""
    reason = require_text(reason, "reason", max_length=2000)
    with transaction(db_path) as conn:
        revision = record_revision(conn, statement_id=statement_id, reason=reason,
                                   created_by=actor, change_note=change_note, now=now)
    snapshot = refresh_snapshot(db_path, statement_id, actor=actor, reason=reason, now=now)
    return {**revision, "snapshot": snapshot}


def finalize_statement(db_path, *, statement_id: str, finalized_by: str,
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

    with connect(db_path, read_only=True) as conn:
        statement = get_statement(conn, statement_id)
        if statement["status"] != "draft":
            raise ConflictError("Only draft statements can be finalized.",
                                code="statement_not_draft",
                                details={"statement_id": statement_id,
                                         "status": statement["status"]})
    window = (correction_window_hours if correction_window_hours is not None
              else statement["correction_window_hours"] or DEFAULT_CORRECTION_WINDOW_HOURS)
    snapshot = statement_snapshot(db_path, statement_id)
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

    with transaction(db_path) as conn:
        row = conn.execute("SELECT status, apartment_id FROM monthly_statement "
                           "WHERE statement_id=?", (statement_id,)).fetchone()
        if row is None:
            raise NotFoundError("Statement not found.", details={"statement_id": statement_id})
        if row["status"] != "draft":
            raise ConflictError("Only draft statements can be finalized.",
                                code="statement_not_draft",
                                details={"statement_id": statement_id,
                                         "status": row["status"]})
        conn.execute(
            """UPDATE monthly_statement SET status='finalized_correction_window',
                   finalized_at=?, correction_deadline=?, snapshot_json=?, finalized_by=?,
                   correction_window_hours=?, updated_at=?
               WHERE statement_id=?""",
            (snapshot["finalized_at"], snapshot["correction_deadline"],
             _canonical(snapshot), snapshot["finalized_by"], int(window),
             now_timestamp(), statement_id))
        conn.execute(
            """INSERT INTO statement_revision
               (revision_id, statement_id, revision_number, snapshot_json, reason,
                created_at, created_by)
               VALUES (?,?,1,?,?,?,?)""",
            (new_id("revision"), statement_id, _canonical(snapshot),
             "initial_finalization", snapshot["finalized_at"], snapshot["finalized_by"]))
        audit.record(conn, entity_type="statement", entity_id=statement_id,
                     action="finalize", actor=finalized_by,
                     apartment_id=row["apartment_id"],
                     old_value={"status": "draft"},
                     new_value={"status": "finalized_correction_window",
                                "correction_deadline": snapshot["correction_deadline"],
                                "statement_total_paise": snapshot["statement_total_paise"],
                                "snapshot_hash": snapshot["snapshot_hash"]})
    return snapshot


def close_expired_statements(db_path, *, now: datetime | None = None,
                             actor: str = "system") -> list[str]:
    """Close finalized statements whose correction window has elapsed."""
    now = now or datetime.now().astimezone()
    closed: list[str] = []
    with transaction(db_path) as conn:
        rows = conn.execute(
            """SELECT statement_id, apartment_id, correction_deadline
                 FROM monthly_statement
                WHERE status='finalized_correction_window'
                  AND correction_deadline IS NOT NULL""").fetchall()
        for row in rows:
            if now >= datetime.fromisoformat(row["correction_deadline"]):
                conn.execute(
                    "UPDATE monthly_statement SET status='closed', closed_at=?, closed_by=?, "
                    "updated_at=? WHERE statement_id=?",
                    (now.isoformat(timespec="seconds"), actor, now_timestamp(),
                     row["statement_id"]))
                audit.record(conn, entity_type="statement", entity_id=row["statement_id"],
                             action="close", actor=actor, apartment_id=row["apartment_id"],
                             old_value={"status": "finalized_correction_window"},
                             new_value={"status": "closed",
                                        "correction_deadline": row["correction_deadline"]})
                closed.append(row["statement_id"])
    return closed


def close_statement(db_path, statement_id: str, *, actor: str = "operator",
                    now: datetime | None = None, force: bool = False) -> dict[str, Any]:
    """Close one statement. Without ``force`` the window must already have elapsed."""
    now = now or datetime.now().astimezone()
    with transaction(db_path) as conn:
        statement = get_statement(conn, statement_id)
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
        conn.execute(
            "UPDATE monthly_statement SET status='closed', closed_at=?, closed_by=?, "
            "updated_at=? WHERE statement_id=?",
            (now.isoformat(timespec="seconds"), actor, now_timestamp(), statement_id))
        audit.record(conn, entity_type="statement", entity_id=statement_id, action="close",
                     actor=actor, apartment_id=statement["apartment_id"],
                     reason="closed early by operator" if not deadline or now < datetime.fromisoformat(deadline) else None,
                     old_value={"status": statement["status"]},
                     new_value={"status": "closed", "forced": bool(force)})
        return {"statement_id": statement_id, "status": "closed"}


def read_statement(db_path, statement_id: str) -> dict[str, Any]:
    with connect(db_path, read_only=True) as conn:
        statement = get_statement(conn, statement_id)
        statement["revisions"] = db.fetch_all(
            conn, "SELECT revision_id, revision_number, reason, created_at, created_by "
                  "FROM statement_revision WHERE statement_id=? ORDER BY revision_number",
            (statement_id,))
        statement["expense_count"] = int(db.scalar(
            conn, "SELECT COUNT(*) FROM expense_instance WHERE statement_id=?",
            (statement_id,), default=0))
        statement["statement_total_paise"] = int(db.scalar(
            conn, "SELECT COALESCE(SUM(amount_paise), 0) FROM expense_instance "
                  "WHERE statement_id=?", (statement_id,), default=0))
        statement["has_snapshot"] = statement["snapshot_json"] is not None
        statement.pop("snapshot_json", None)
        return statement


def read_finalized_snapshot(db_path, statement_id: str) -> dict[str, Any]:
    """The stored snapshot exactly as it was saved, with its hash verified.

    ``hash_matches`` being false means the stored bytes no longer hash to the
    value recorded inside them -- the snapshot was tampered with outside the
    application. It is reported rather than corrected.
    """
    with connect(db_path, read_only=True) as conn:
        statement = get_statement(conn, statement_id)
        if not statement["snapshot_json"]:
            raise NotFoundError(
                "This statement has no stored snapshot. Only finalized statements have one.",
                details={"statement_id": statement_id, "status": statement["status"]})
        stored = json.loads(statement["snapshot_json"])
    recorded = stored.get("snapshot_hash")
    return {"statement_id": statement_id, "status": statement["status"],
            "snapshot": stored, "snapshot_hash": recorded,
            "hash_matches": recorded == snapshot_hash(stored)}


def list_statement_revisions(db_path, statement_id: str) -> list[dict[str, Any]]:
    with connect(db_path, read_only=True) as conn:
        get_statement(conn, statement_id)
        rows = db.fetch_all(
            conn, "SELECT * FROM statement_revision WHERE statement_id=? "
                  "ORDER BY revision_number", (statement_id,))
    for row in rows:
        try:
            row["snapshot"] = json.loads(row.pop("snapshot_json"))
        except (json.JSONDecodeError, TypeError):
            row["snapshot"] = None
    return rows


def list_statements(db_path, *, apartment_id: str, status: str | None = None,
                    limit: int | None = 50, offset: int | None = 0) -> dict[str, Any]:
    limit, offset = paginate(limit, offset)
    clauses = ["s.apartment_id = ?"]
    params: list[Any] = [apartment_id]
    if status:
        clauses.append("s.status = ?")
        params.append(status)
    where = " AND ".join(clauses)
    with connect(db_path, read_only=True) as conn:
        get_apartment(conn, apartment_id)
        total = int(conn.execute(
            f"SELECT COUNT(*) FROM monthly_statement s WHERE {where}",
            tuple(params)).fetchone()[0])
        rows = db.fetch_all(
            conn,
            f"""SELECT s.statement_id, s.apartment_id, s.settlement_month, s.status,
                       s.created_at, s.finalized_at, s.correction_deadline, s.closed_at,
                       s.finalized_by, s.correction_window_hours, s.notes,
                       s.snapshot_json IS NOT NULL AS has_snapshot,
                       (SELECT COUNT(*) FROM expense_instance e
                         WHERE e.statement_id = s.statement_id) AS expense_count,
                       (SELECT COALESCE(SUM(e.amount_paise), 0) FROM expense_instance e
                         WHERE e.statement_id = s.statement_id) AS statement_total_paise,
                       (SELECT COUNT(*) FROM statement_revision sr
                         WHERE sr.statement_id = s.statement_id) AS revision_count
                  FROM monthly_statement s WHERE {where}
                 ORDER BY s.settlement_month DESC LIMIT ? OFFSET ?""",
            params + [limit, offset])
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


def available_months(db_path, apartment_id: str) -> list[str]:
    """Months that have any recorded activity, newest first."""
    with connect(db_path, read_only=True) as conn:
        get_apartment(conn, apartment_id)
        months = {row[0] for row in conn.execute(
            "SELECT DISTINCT settlement_period FROM expense_instance WHERE apartment_id=? "
            "AND settlement_period IS NOT NULL", (apartment_id,)).fetchall()}
        months |= {row[0] for row in conn.execute(
            "SELECT DISTINCT settlement_month FROM monthly_statement WHERE apartment_id=?",
            (apartment_id,)).fetchall()}
        months |= {row[0] for row in conn.execute(
            "SELECT DISTINCT substr(payment_date, 1, 7) FROM payment WHERE apartment_id=?",
            (apartment_id,)).fetchall()}
    months.discard(None)
    if not months:
        return [now_timestamp()[:7]]
    ordered = sorted(months)
    return sorted(month_range(ordered[0], max(ordered[-1], now_timestamp()[:7])), reverse=True)

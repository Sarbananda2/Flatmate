"""Electricity bills: a dedicated module over the standalone electricity engine.

What is authoritative, and what is not
--------------------------------------

The **actual utility bill** and its itemised charges are authoritative. The
engine allocates the bill that arrived; it never reconstructs a tariff from kWh
and never estimates the total. Smart-plug readings are *allocation weights*
supplied by the operator, nothing more.

Guests are not billed residents. Guest electricity is charged to the host by
including the guest's kWh in the host's own smart-plug reading before the
calculation runs -- so a guest never becomes a debtor, and the host carries the
cost.

Precision and rounding
----------------------

kWh readings are stored as whole watt-hours, which supports three decimal
places. Money stays integer paise throughout. When a component's allocation
leaves remainder paise, the engine's largest-remainder rule assigns them with
the bill payer placed **last** in the tie-break, so the payer is not the one
charged the spare paisa.

Periods
-------

``bill_period_start``/``bill_period_end`` are the utility's own consumption
window. ``settlement_period`` is the statement month that charges the bill, and
it is separate: a September bill under a ``previous_month`` rule settles in
October.

Corrections
-----------

A saved calculation is never silently changed. ``recalculate_electricity_expense``
refuses once the statement is closed, requires a reason inside the correction
window, and keeps the superseded calculation in the audit trail.
"""
from __future__ import annotations

import json
import sqlite3
from decimal import Decimal
from typing import Any, Mapping, Sequence

from .. import db
from ..db import connect, new_id, now_timestamp, transaction
from ..engine.electricity import ElectricityCalculationError, calculate_electricity
from ..errors import ConflictError, NotFoundError, ValidationError
from ..money import ensure_paise, kwh_to_wh, wh_to_kwh_string
from ..periods import (
    default_due_date,
    month_end,
    month_start,
    normalize_month,
    parse_iso_date,
    resolve_periods,
)
from . import audit
from .common import (
    assert_not_locked,
    get_apartment,
    get_expense,
    optional_text,
    require_draft_statement,
    resident_ids,
)

ENGINE_METHODS = ("usage", "equal", "custom")

# The database schema names usage-based allocation "measured".
_SCHEMA_METHOD = {"usage": "measured", "equal": "equal", "custom": "custom"}
_ENGINE_METHOD = {"measured": "usage", "equal": "equal", "custom": "custom"}


def _schema_method(method: str) -> str:
    return _SCHEMA_METHOD[method]


def _normalize_readings(readings: Sequence[Mapping[str, Any]], *, expense_date: str,
                        period_start: str | None, period_end: str | None
                        ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not isinstance(readings, Sequence) or isinstance(readings, (str, bytes)):
        raise ValidationError("smart_plug_readings must be a list.",
                              details={"field": "smart_plug_readings"})
    normalized: list[dict[str, Any]] = []
    engine_readings: list[dict[str, Any]] = []
    for index, reading in enumerate(readings):
        if not isinstance(reading, Mapping):
            raise ValidationError(f"Reading {index} must be an object.",
                                  details={"field": f"smart_plug_readings[{index}]"})
        resident_id = reading.get("resident_id")
        if not isinstance(resident_id, str) or not resident_id:
            raise ValidationError(f"Reading {index} requires resident_id.",
                                  details={"field": f"smart_plug_readings[{index}].resident_id"})
        energy_wh = kwh_to_wh(reading.get("kwh"), f"Reading {index} kwh")
        normalized.append({
            "reading_id": reading.get("reading_id") or new_id("reading"),
            "resident_id": resident_id,
            "device_label": reading.get("device_label") or "Smart plug",
            "period_start": reading.get("period_start") or period_start or expense_date,
            "period_end": reading.get("period_end") or period_end or expense_date,
            "energy_wh": energy_wh,
            "source_note": reading.get("source_note"),
        })
        engine_readings.append({"resident_id": resident_id,
                                "kwh": str(Decimal(energy_wh) / Decimal(1000))})
    return normalized, engine_readings


def calculate_preview(db_path, *, apartment_id: str, total_amount_paise: int,
                      smart_plug_readings: Sequence[Mapping[str, Any]],
                      components: Sequence[Mapping[str, Any]] | None = None,
                      payer_resident_id: str | None = None,
                      expense_date: str | None = None) -> dict[str, Any]:
    """Run the engine without saving: the review screen before committing."""
    ensure_paise(total_amount_paise, "total_amount_paise")
    reference_date = expense_date or now_timestamp()[:10]
    parse_iso_date(reference_date, "expense_date")
    with connect(db_path, read_only=True) as conn:
        get_apartment(conn, apartment_id)
        residents = resident_ids(conn, apartment_id)
        if not residents:
            raise ValidationError("No residents are registered for this apartment.",
                                  code="no_residents")
        names = {row["resident_id"]: row["display_name"] for row in conn.execute(
            "SELECT resident_id, display_name FROM resident WHERE apartment_id=?",
            (apartment_id,)).fetchall()}
    normalized, engine_readings = _normalize_readings(
        smart_plug_readings, expense_date=reference_date, period_start=None, period_end=None)
    unknown = sorted({r["resident_id"] for r in normalized} - set(residents))
    if unknown:
        raise ValidationError(
            f"Reading references resident {unknown[0]!r}, who does not belong to this apartment.",
            details={"unknown_resident_ids": unknown})
    if payer_resident_id is not None and payer_resident_id not in residents:
        raise ValidationError("payer_resident_id must belong to this apartment.",
                              details={"field": "payer_resident_id"})

    bill: dict[str, Any] = {"total_amount_paise": total_amount_paise}
    if components is not None:
        bill["components"] = list(components)
    try:
        result = calculate_electricity({
            "residents": residents,
            "smart_plug_readings": engine_readings,
            "payer_resident_id": payer_resident_id,
            "bill": bill,
        })
    except ElectricityCalculationError as exc:
        raise ValidationError(str(exc), code="electricity_calculation_error") from exc

    usage_wh: dict[str, int] = {rid: 0 for rid in residents}
    for reading in normalized:
        usage_wh[reading["resident_id"]] += reading["energy_wh"]
    total_wh = sum(usage_wh.values())
    payload = result.to_dict()
    payload["residents"] = [
        {"resident_id": rid, "display_name": names.get(rid, rid),
         "energy_wh": usage_wh[rid], "kwh": wh_to_kwh_string(usage_wh[rid]),
         "usage_share_percent": (round(usage_wh[rid] * 100 / total_wh, 2)
                                 if total_wh else 0.0),
         "amount_paise": next(a["amount_paise"] for a in payload["allocations"]
                              if a["resident_id"] == rid),
         "is_payer": rid == payer_resident_id}
        for rid in residents
    ]
    payload["total_energy_wh"] = total_wh
    payload["total_kwh"] = wh_to_kwh_string(total_wh)
    payload["reconciles"] = (
        sum(a["amount_paise"] for a in payload["allocations"]) == total_amount_paise
        and sum(c["amount_paise"] for c in payload["components"]) == total_amount_paise)
    payload["readings"] = normalized
    payload["rounding_rule"] = (
        "Remainder paise are assigned by largest remainder; the bill payer is placed last "
        "in the tie-break, so the payer is not charged the spare paisa.")
    return payload


def create_electricity_expense(
    db_path, *, apartment_id: str, expense_id: str, description: str, expense_date: str,
    expense_period: str | None = None, total_amount_paise: int,
    smart_plug_readings: Sequence[Mapping[str, Any]],
    components: Sequence[Mapping[str, Any]] | None = None,
    payer_resident_id: str | None = None, provider_name: str = "Electricity provider",
    expense_definition_id: str | None = None, statement_id: str | None = None,
    created_at: str | None = None,
    # Added after the prototype:
    bill_period_start: str | None = None, bill_period_end: str | None = None,
    settlement_period: str | None = None, service_period: str | None = None,
    period_rule: str | None = None, bill_number: str | None = None,
    notes: str | None = None, due_date: str | None = None, actor: str = "operator",
):
    """Calculate and persist an electricity expense in a single transaction.

    Each reading mapping requires resident_id and kwh. Optional fields:
    reading_id, device_label, period_start, period_end, source_note.
    Component mappings use the electricity engine's component contract.

    All money fields are integer paise. On any error, the transaction rolls
    back so no partial expense/component/allocation/reading records remain.
    """
    if not expense_id or not str(expense_id).strip():
        raise ValidationError("expense_id is required.", details={"field": "expense_id"})
    if not description or not str(description).strip():
        raise ValidationError("description is required.", details={"field": "description"})
    if not apartment_id or not str(apartment_id).strip():
        raise ValidationError("apartment_id is required.", details={"field": "apartment_id"})
    if not expense_date:
        # The accounting periods can come from the definition's period rule, so
        # only the transaction date is strictly required.
        raise ValidationError("expense_date and expense_period are required.",
                              details={"field": "expense_date"})
    if isinstance(total_amount_paise, bool) or not isinstance(total_amount_paise, int) \
            or total_amount_paise < 0:
        raise ValidationError(
            "total_amount_paise must be a non-negative integer number of paise.",
            details={"field": "total_amount_paise"})
    parse_iso_date(expense_date, "expense_date")

    timestamp = created_at or now_timestamp()
    normalized, engine_readings = _normalize_readings(
        smart_plug_readings, expense_date=expense_date,
        period_start=bill_period_start, period_end=bill_period_end)

    with transaction(db_path) as conn:
        get_apartment(conn, apartment_id)
        residents = resident_ids(conn, apartment_id)
        if not residents:
            raise ValidationError("No residents are registered for this apartment.",
                                  code="no_residents")
        if payer_resident_id is not None and payer_resident_id not in residents:
            raise ValidationError("payer_resident_id must belong to this apartment.",
                                  details={"field": "payer_resident_id",
                                           "value": payer_resident_id})
        for reading in normalized:
            if reading["resident_id"] not in residents:
                raise ValidationError(
                    f"Reading references resident {reading['resident_id']!r}, who does not "
                    "belong to this apartment.",
                    details={"resident_id": reading["resident_id"]})
        if expense_definition_id:
            definition = conn.execute(
                "SELECT apartment_id FROM expense_definition WHERE expense_definition_id = ?",
                (expense_definition_id,)).fetchone()
            if not definition or definition["apartment_id"] != apartment_id:
                raise ValidationError("expense_definition_id must belong to this apartment.",
                                      details={"field": "expense_definition_id"})
        if statement_id:
            statement = conn.execute(
                "SELECT apartment_id, status FROM monthly_statement WHERE statement_id = ?",
                (statement_id,)).fetchone()
            if not statement or statement["apartment_id"] != apartment_id:
                raise ValidationError("statement_id must belong to this apartment.",
                                      details={"field": "statement_id"})
            if statement["status"] != "draft":
                raise ConflictError(
                    "Electricity expenses can only be added to a draft statement.",
                    code="statement_not_draft",
                    details={"statement_id": statement_id, "status": statement["status"]})

        rule = period_rule
        if rule is None and expense_definition_id:
            row = conn.execute(
                "SELECT period_rule FROM expense_definition WHERE expense_definition_id=?",
                (expense_definition_id,)).fetchone()
            rule = row[0] if row else None
        fallback = None
        if expense_period:
            try:
                fallback = normalize_month(expense_period, "expense_period")
            except ValidationError:
                fallback = None
        if rule is None:
            rule = "custom"
        if rule == "custom":
            resolved = resolve_periods(
                "custom", expense_date, service_period=service_period,
                settlement_period=settlement_period or fallback or expense_date[:7])
        else:
            resolved = resolve_periods(rule, expense_date, service_period=service_period,
                                       settlement_period=settlement_period)
        resolved_due = due_date or default_due_date(resolved.settlement_period)
        parse_iso_date(resolved_due, "due_date")

        if statement_id:
            statement_month = conn.execute(
                "SELECT settlement_month FROM monthly_statement WHERE statement_id=?",
                (statement_id,)).fetchone()[0]
            if statement_month != resolved.settlement_period:
                raise ValidationError(
                    "This bill settles in a different month from the statement it is being "
                    "attached to.",
                    code="settlement_period_mismatch",
                    details={"statement_settlement_month": statement_month,
                             "bill_settlement_period": resolved.settlement_period})

        bill: dict[str, Any] = {"total_amount_paise": total_amount_paise}
        if components is not None:
            bill["components"] = list(components)
        try:
            result = calculate_electricity({
                "residents": residents,
                "smart_plug_readings": engine_readings,
                "payer_resident_id": payer_resident_id,
                "bill": bill,
            })
        except ElectricityCalculationError as exc:
            raise ValidationError(str(exc), code="electricity_calculation_error") from exc

        # The engine already proves the component and resident totals reconcile;
        # re-check here so persistence can never store a total it did not verify.
        if sum(c.amount_paise for c in result.components) != total_amount_paise:
            raise ValidationError(
                "Bill components do not equal the actual bill total.",
                code="component_total_mismatch")
        if sum(a.amount_paise for a in result.allocations) != total_amount_paise:
            raise ValidationError(
                "Resident allocations do not equal the actual bill total.",
                code="allocation_total_mismatch")

        period_start = bill_period_start or month_start(resolved.service_period).isoformat()
        period_end = bill_period_end or month_end(resolved.service_period).isoformat()
        parse_iso_date(period_start, "bill_period_start")
        parse_iso_date(period_end, "bill_period_end")
        if period_end < period_start:
            raise ValidationError("bill_period_end cannot be before bill_period_start.",
                                  details={"field": "bill_period_end"})

        try:
            conn.execute(
                """INSERT INTO expense_instance
                   (expense_id, expense_definition_id, statement_id, description,
                    amount_paise, expense_date, expense_period, payer_resident_id,
                    provider_name, is_settled, created_at, service_period,
                    settlement_period, period_rule, due_date, allocation_method,
                    expense_kind, apartment_id, reference, notes,
                    generated_from_recurrence, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,0,?,?,?,?,?,'measured','electricity',?,?,?,0,?)""",
                (str(expense_id).strip(), expense_definition_id, statement_id,
                 str(description).strip(), total_amount_paise, expense_date,
                 expense_period or resolved.settlement_period, payer_resident_id,
                 provider_name, timestamp, resolved.service_period,
                 resolved.settlement_period, resolved.rule, resolved_due, apartment_id,
                 optional_text(bill_number, "bill_number", max_length=120),
                 optional_text(notes, "notes"), timestamp))
            for component in result.components:
                db_component_id = new_id("ec")
                conn.execute(
                    """INSERT INTO expense_component
                       (component_id, expense_id, name, amount_paise, allocation_method,
                        host_resident_id)
                       VALUES (?,?,?,?,?,NULL)""",
                    (db_component_id, str(expense_id).strip(), component.name,
                     component.amount_paise, _schema_method(component.allocation_method)))
                for allocation in component.allocations:
                    # A zero share of a non-zero component is a real measured
                    # result, so electricity allocations are written in full --
                    # including zeros -- and the stored rows reconcile
                    # component by component to the actual bill.
                    basis = {
                        "method": component.allocation_method,
                        "component_id": component.component_id,
                        "usage_kwh_by_resident": result.breakdown["usage_kwh_by_resident"],
                        "payer_resident_id": payer_resident_id,
                    }
                    conn.execute(
                        """INSERT INTO allocation
                           (allocation_id, expense_id, component_id, resident_id,
                            amount_paise, allocation_basis_json)
                           VALUES (?,?,?,?,?,?)""",
                        (new_id("alloc"), str(expense_id).strip(), db_component_id,
                         allocation.resident_id, allocation.amount_paise,
                         json.dumps(basis, sort_keys=True)))
            for reading in normalized:
                conn.execute(
                    """INSERT INTO smart_plug_reading
                       (reading_id, resident_id, expense_id, device_label, period_start,
                        period_end, energy_wh, source_note)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (reading["reading_id"], reading["resident_id"], str(expense_id).strip(),
                     reading["device_label"], reading["period_start"], reading["period_end"],
                     reading["energy_wh"], reading["source_note"]))
            conn.execute(
                """INSERT INTO electricity_bill
                   (electricity_bill_id, expense_id, bill_period_start, bill_period_end,
                    settlement_period, bill_number, provider_name, total_amount_paise,
                    calculation_json, notes, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (new_id("ebill"), str(expense_id).strip(), period_start, period_end,
                 resolved.settlement_period,
                 optional_text(bill_number, "bill_number", max_length=120), provider_name,
                 total_amount_paise, json.dumps(result.to_dict(), sort_keys=True),
                 optional_text(notes, "notes"), timestamp))
        except sqlite3.Error as exc:
            raise ValidationError(f"Could not persist electricity expense: {exc}") from exc

        audit.record(conn, entity_type="expense", entity_id=str(expense_id).strip(),
                     action="create", actor=actor, apartment_id=apartment_id,
                     new_value={"expense_kind": "electricity",
                                "total_amount_paise": total_amount_paise,
                                "settlement_period": resolved.settlement_period,
                                "service_period": resolved.service_period,
                                "bill_period": [period_start, period_end],
                                "payer_resident_id": payer_resident_id,
                                "allocations": {a.resident_id: a.amount_paise
                                                for a in result.allocations}})
    return result


def recalculate_electricity_expense(db_path, expense_id: str, *, reason: str,
                                    actor: str = "operator", **changes: Any) -> dict[str, Any]:
    """Replace a saved electricity calculation, auditably.

    The previous calculation is written to the audit trail before being
    replaced, the statement's edit rules are enforced, and a correction inside
    the 72-hour window is recorded as a statement revision. A closed statement
    is refused outright: that correction belongs in a ledger adjustment.
    """
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("A reason is required to change a saved electricity "
                              "calculation.", details={"field": "reason"})
    from . import statements  # local import avoids a cycle with snapshots

    with connect(db_path, read_only=True) as conn:
        expense = get_expense(conn, expense_id)
        if expense["expense_kind"] != "electricity":
            raise ValidationError("That expense is not an electricity bill.",
                                  details={"expense_id": expense_id})
        bill = db.fetch_one(conn, "SELECT * FROM electricity_bill WHERE expense_id=?",
                            (expense_id,))
        readings = db.fetch_all(
            conn, "SELECT * FROM smart_plug_reading WHERE expense_id=? ORDER BY rowid",
            (expense_id,))
        mode = assert_not_locked(conn, expense["statement_id"], "electricity bill")
    if mode == "revision" and not reason:
        raise ValidationError("A reason is required during the correction window.",
                              details={"field": "reason"})

    payload = {
        "apartment_id": expense["apartment_id"],
        "expense_id": expense_id,
        "description": changes.get("description", expense["description"]),
        "expense_date": changes.get("expense_date", expense["expense_date"]),
        "total_amount_paise": changes.get("total_amount_paise", expense["amount_paise"]),
        "payer_resident_id": changes.get("payer_resident_id", expense["payer_resident_id"]),
        "provider_name": changes.get("provider_name", expense["provider_name"]
                                     or "Electricity provider"),
        "expense_definition_id": changes.get("expense_definition_id",
                                             expense["expense_definition_id"]),
        "statement_id": changes.get("statement_id", expense["statement_id"]),
        "settlement_period": changes.get("settlement_period", expense["settlement_period"]),
        "service_period": changes.get("service_period", expense["service_period"]),
        "period_rule": changes.get("period_rule", expense["period_rule"]),
        "due_date": changes.get("due_date", expense["due_date"]),
        "bill_period_start": changes.get("bill_period_start",
                                         bill["bill_period_start"] if bill else None),
        "bill_period_end": changes.get("bill_period_end",
                                       bill["bill_period_end"] if bill else None),
        "bill_number": changes.get("bill_number", bill["bill_number"] if bill else None),
        "notes": changes.get("notes", expense["notes"]),
        "components": changes.get("components"),
        "smart_plug_readings": changes.get("smart_plug_readings", [
            {"resident_id": r["resident_id"],
             "kwh": wh_to_kwh_string(r["energy_wh"]),
             "device_label": r["device_label"],
             "period_start": r["period_start"], "period_end": r["period_end"],
             "source_note": r["source_note"]} for r in readings]),
        "created_at": expense["created_at"],
        "actor": actor,
    }
    if payload["components"] is None and bill:
        stored = json.loads(bill["calculation_json"])
        payload["components"] = [
            {"id": c["component_id"], "name": c["name"], "amount_paise": c["amount_paise"],
             "allocation_method": c["allocation_method"]}
            for c in stored["components"]]

    with transaction(db_path) as conn:
        settled = int(db.scalar(
            conn,
            """SELECT COALESCE(SUM(pa.amount_paise), 0) FROM payment_application pa
                 JOIN allocation a ON a.allocation_id = pa.allocation_id
                WHERE a.expense_id = ?""", (expense_id,), default=0))
        if settled:
            raise ConflictError(
                "Payments have already been applied to this bill's charges. Reverse them "
                "first, or record a ledger adjustment instead.",
                code="allocations_have_payments",
                details={"expense_id": expense_id, "applied_paise": settled})
        linked_payment_ids = [row["payment_id"] for row in db.fetch_all(
            conn, "SELECT payment_id FROM payment WHERE expense_id=? ORDER BY rowid",
            (expense_id,))]
        previous = db.fetch_one(conn, "SELECT * FROM electricity_bill WHERE expense_id=?",
                                (expense_id,))
        audit.record(conn, entity_type="expense", entity_id=expense_id,
                     action="supersede_electricity_calculation", actor=actor,
                     apartment_id=expense["apartment_id"], reason=reason,
                     old_value={"amount_paise": expense["amount_paise"],
                                "calculation": json.loads(previous["calculation_json"])
                                if previous else None})
        if linked_payment_ids:
            # Detach the funding links so the expense row can be replaced, then
            # restore exactly those links once the new row exists.
            conn.execute("UPDATE payment SET expense_id=NULL WHERE expense_id=?", (expense_id,))
        conn.execute("DELETE FROM allocation WHERE expense_id=?", (expense_id,))
        conn.execute("DELETE FROM smart_plug_reading WHERE expense_id=?", (expense_id,))
        conn.execute("DELETE FROM electricity_bill WHERE expense_id=?", (expense_id,))
        conn.execute("DELETE FROM expense_component WHERE expense_id=?", (expense_id,))
        conn.execute("DELETE FROM expense_instance WHERE expense_id=?", (expense_id,))

    create_electricity_expense(db_path, **payload)
    if linked_payment_ids:
        with transaction(db_path) as conn:
            for payment_id in linked_payment_ids:
                conn.execute("UPDATE payment SET expense_id=? WHERE payment_id=?",
                             (expense_id, payment_id))
    if mode == "revision":
        with transaction(db_path) as conn:
            statements.record_revision(
                conn, statement_id=expense["statement_id"], reason=reason, created_by=actor,
                change_note=f"electricity bill {expense_id} recalculated")
        statements.refresh_snapshot(db_path, expense["statement_id"], actor=actor,
                                    reason=reason)
    return get_electricity_expense(db_path, expense_id)


def get_electricity_expense(db_path, expense_id: str) -> dict[str, Any] | None:
    """Read a saved electricity expense with components, readings, and allocations."""
    with connect(db_path, read_only=True) as conn:
        expense = db.fetch_one(conn, "SELECT * FROM expense_instance WHERE expense_id = ?",
                               (expense_id,))
        if expense is None:
            return None
        components = db.fetch_all(
            conn, "SELECT * FROM expense_component WHERE expense_id = ? ORDER BY rowid",
            (expense_id,))
        readings = db.fetch_all(
            conn, "SELECT * FROM smart_plug_reading WHERE expense_id = ? ORDER BY rowid",
            (expense_id,))
        allocations = db.fetch_all(
            conn,
            """SELECT a.*, c.name AS component_name, r.display_name
                 FROM allocation a
                 LEFT JOIN expense_component c ON c.component_id = a.component_id
                 LEFT JOIN resident r ON r.resident_id = a.resident_id
                WHERE a.expense_id = ? ORDER BY c.rowid, a.resident_id""", (expense_id,))
        bill = db.fetch_one(conn, "SELECT * FROM electricity_bill WHERE expense_id=?",
                            (expense_id,))
    for reading in readings:
        reading["kwh"] = wh_to_kwh_string(reading["energy_wh"])
    resident_totals: dict[str, int] = {}
    for row in allocations:
        row["allocation_basis"] = json.loads(row["allocation_basis_json"]) \
            if row.get("allocation_basis_json") else None
        resident_totals[row["resident_id"]] = resident_totals.get(row["resident_id"], 0) \
            + row["amount_paise"]
    result = {
        "expense": expense,
        "components": components,
        "readings": readings,
        "allocations": allocations,
        "bill": bill,
        "calculation": json.loads(bill["calculation_json"]) if bill else None,
        "resident_totals_paise": resident_totals,
        "reconciles": sum(resident_totals.values()) == expense["amount_paise"]
                      and sum(c["amount_paise"] for c in components) == expense["amount_paise"],
    }
    return result


def list_electricity_bills(db_path, apartment_id: str, *, limit: int = 50,
                           offset: int = 0) -> dict[str, Any]:
    with connect(db_path, read_only=True) as conn:
        get_apartment(conn, apartment_id)
        total = int(db.scalar(
            conn, "SELECT COUNT(*) FROM expense_instance WHERE apartment_id=? "
                  "AND expense_kind='electricity'", (apartment_id,), default=0))
        rows = db.fetch_all(
            conn,
            """SELECT e.expense_id, e.description, e.amount_paise, e.expense_date,
                      e.service_period, e.settlement_period, e.payer_resident_id,
                      e.statement_id, e.provider_name,
                      r.display_name AS payer_display_name,
                      s.status AS statement_status, s.settlement_month,
                      b.bill_period_start, b.bill_period_end, b.bill_number,
                      (SELECT COALESCE(SUM(sp.energy_wh), 0) FROM smart_plug_reading sp
                        WHERE sp.expense_id = e.expense_id) AS total_energy_wh,
                      (SELECT COUNT(*) FROM expense_component c
                        WHERE c.expense_id = e.expense_id) AS component_count
                 FROM expense_instance e
                 LEFT JOIN electricity_bill b ON b.expense_id = e.expense_id
                 LEFT JOIN resident r ON r.resident_id = e.payer_resident_id
                 LEFT JOIN monthly_statement s ON s.statement_id = e.statement_id
                WHERE e.apartment_id = ? AND e.expense_kind = 'electricity'
                ORDER BY e.expense_date DESC, e.rowid DESC LIMIT ? OFFSET ?""",
            (apartment_id, int(limit), int(offset)))
    for row in rows:
        row["total_kwh"] = wh_to_kwh_string(row["total_energy_wh"])
    return {"items": rows, "total": total, "limit": int(limit), "offset": int(offset)}


def consumption_history(db_path, apartment_id: str) -> list[dict[str, Any]]:
    """Per-resident kWh and paise, by service period: the consumption report."""
    with connect(db_path, read_only=True) as conn:
        get_apartment(conn, apartment_id)
        rows = db.fetch_all(
            conn,
            """SELECT e.service_period, sp.resident_id, r.display_name,
                      SUM(sp.energy_wh) AS energy_wh,
                      (SELECT COALESCE(SUM(a.amount_paise), 0) FROM allocation a
                        WHERE a.expense_id = e.expense_id
                          AND a.resident_id = sp.resident_id) AS amount_paise
                 FROM smart_plug_reading sp
                 JOIN expense_instance e ON e.expense_id = sp.expense_id
                 JOIN resident r ON r.resident_id = sp.resident_id
                WHERE e.apartment_id = ? AND e.expense_kind = 'electricity'
                GROUP BY e.service_period, sp.resident_id
                ORDER BY e.service_period DESC, r.display_name""", (apartment_id,))
    for row in rows:
        row["kwh"] = wh_to_kwh_string(row["energy_wh"])
    return rows

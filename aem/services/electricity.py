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
from decimal import Decimal
from typing import Any, Mapping, Sequence

from ..engine.electricity import ElectricityCalculationError, calculate_electricity
from ..errors import ConflictError, NotFoundError, ValidationError
from ..ids import new_id, now_timestamp
from ..money import ensure_paise, format_inr, kwh_to_wh, wh_to_kwh_string
from ..periods import (
    default_due_date,
    month_end,
    month_start,
    normalize_month,
    parse_iso_date,
    resolve_periods,
)
from ..persistence import Database, PersistenceError, UnitOfWork
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


def calculate_preview(db: Database, *, apartment_id: str, total_amount_paise: int,
                      smart_plug_readings: Sequence[Mapping[str, Any]],
                      components: Sequence[Mapping[str, Any]] | None = None,
                      payer_resident_id: str | None = None,
                      expense_date: str | None = None) -> dict[str, Any]:
    """Run the engine without saving: the review screen before committing."""
    ensure_paise(total_amount_paise, "total_amount_paise")
    reference_date = expense_date or now_timestamp()[:10]
    parse_iso_date(reference_date, "expense_date")
    with db.read() as uow:
        get_apartment(uow, apartment_id)
        residents = resident_ids(uow, apartment_id, active_only=True)
        if not residents:
            raise ValidationError("No active residents are registered for this apartment.",
                                  code="no_residents")
        names = {row["resident_id"]: row["display_name"]
                 for row in uow.electricity.resident_names(apartment_id)}
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


def create_electricity_expense(db: Database, **fields: Any):
    """Calculate and persist an electricity expense in a single transaction.

    Required: apartment_id, expense_id, description, expense_date,
    total_amount_paise and smart_plug_readings. Each reading mapping requires
    resident_id and kwh. Optional fields: reading_id, device_label,
    period_start, period_end, source_note. Component mappings use the
    electricity engine's component contract.

    All money fields are integer paise. On any error, the transaction rolls
    back so no partial expense/component/allocation/reading records remain.
    """
    with db.transaction() as uow:
        return _create_electricity_expense_in(uow, **fields)


def _plan_electricity_expense(
    uow: UnitOfWork, *, apartment_id: str, expense_id: str, description: str,
    expense_date: str,
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
    participants: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Check and calculate an electricity bill without writing anything: what
    :func:`_insert_electricity_expense` or :func:`_rewrite_electricity_expense_in`
    then writes."""
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

    get_apartment(uow, apartment_id)
    if participants is not None:
        residents = sorted(participants)
    else:
        residents = resident_ids(uow, apartment_id, active_only=True)
    if not residents:
        raise ValidationError("No active residents are registered for this apartment.",
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
        definition = uow.electricity.definition_apartment(expense_definition_id)
        if not definition or definition["apartment_id"] != apartment_id:
            raise ValidationError("expense_definition_id must belong to this apartment.",
                                  details={"field": "expense_definition_id"})
    if statement_id:
        statement = uow.electricity.statement_scope(statement_id)
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
        rule = uow.electricity.definition_period_rule(expense_definition_id)
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
        statement_month = uow.electricity.statement_settlement_month(statement_id)
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

    return {
        "apartment_id": apartment_id, "expense_id": str(expense_id).strip(),
        "expense_definition_id": expense_definition_id, "statement_id": statement_id,
        "description": str(description).strip(), "total_amount_paise": total_amount_paise,
        "expense_date": expense_date,
        "expense_period": expense_period or resolved.settlement_period,
        "payer_resident_id": payer_resident_id, "provider_name": provider_name,
        "timestamp": timestamp, "resolved": resolved, "due_date": resolved_due,
        "bill_number": bill_number, "notes": notes, "result": result,
        "readings": normalized, "period_start": period_start, "period_end": period_end,
        "actor": actor,
    }

def _create_electricity_expense_in(uow: UnitOfWork, **fields: Any):
    """Calculate and persist an electricity expense inside the caller's transaction.

    The bill is shared between the apartment's currently active residents.
    ``participants`` overrides that for a correction, which keeps the residents
    the bill was originally shared between even if one has since moved out.
    """
    plan = _plan_electricity_expense(uow, **fields)
    _insert_electricity_expense(uow, plan)
    return plan["result"]


def _allocation_basis_json(plan: Mapping[str, Any], component: Any) -> str:
    return json.dumps({
        "method": component.allocation_method,
        "component_id": component.component_id,
        "usage_kwh_by_resident": plan["result"].breakdown["usage_kwh_by_resident"],
        "payer_resident_id": plan["payer_resident_id"],
    }, sort_keys=True)


def _insert_readings_and_bill(uow: UnitOfWork, plan: Mapping[str, Any]) -> None:
    for reading in plan["readings"]:
        uow.electricity.insert_reading(
            reading_id=reading["reading_id"], resident_id=reading["resident_id"],
            expense_id=plan["expense_id"], device_label=reading["device_label"],
            period_start=reading["period_start"], period_end=reading["period_end"],
            energy_wh=reading["energy_wh"], source_note=reading["source_note"])
    uow.electricity.insert_bill(
        electricity_bill_id=new_id("ebill"), expense_id=plan["expense_id"],
        bill_period_start=plan["period_start"], bill_period_end=plan["period_end"],
        settlement_period=plan["resolved"].settlement_period,
        bill_number=optional_text(plan["bill_number"], "bill_number", max_length=120),
        provider_name=plan["provider_name"], total_amount_paise=plan["total_amount_paise"],
        calculation_json=json.dumps(plan["result"].to_dict(), sort_keys=True),
        notes=optional_text(plan["notes"], "notes"), created_at=plan["timestamp"])


def _audit_allocations(plan: Mapping[str, Any]) -> dict[str, Any]:
    resolved = plan["resolved"]
    return {"expense_kind": "electricity",
            "total_amount_paise": plan["total_amount_paise"],
            "settlement_period": resolved.settlement_period,
            "service_period": resolved.service_period,
            "bill_period": [plan["period_start"], plan["period_end"]],
            "payer_resident_id": plan["payer_resident_id"],
            "allocations": {a.resident_id: a.amount_paise
                            for a in plan["result"].allocations}}


def _insert_electricity_expense(uow: UnitOfWork, plan: Mapping[str, Any]) -> None:
    """Write a planned bill: the expense, its components and charges, the
    readings, and the bill itself."""
    resolved, result = plan["resolved"], plan["result"]
    try:
        uow.electricity.insert_expense(
            expense_id=plan["expense_id"],
            expense_definition_id=plan["expense_definition_id"],
            statement_id=plan["statement_id"], description=plan["description"],
            amount_paise=plan["total_amount_paise"], expense_date=plan["expense_date"],
            expense_period=plan["expense_period"],
            payer_resident_id=plan["payer_resident_id"], provider_name=plan["provider_name"],
            created_at=plan["timestamp"], service_period=resolved.service_period,
            settlement_period=resolved.settlement_period, period_rule=resolved.rule,
            due_date=plan["due_date"], apartment_id=plan["apartment_id"],
            reference=optional_text(plan["bill_number"], "bill_number", max_length=120),
            notes=optional_text(plan["notes"], "notes"), updated_at=plan["timestamp"])
        for component in result.components:
            db_component_id = new_id("ec")
            uow.electricity.insert_component(
                component_id=db_component_id, expense_id=plan["expense_id"],
                name=component.name, amount_paise=component.amount_paise,
                allocation_method=_schema_method(component.allocation_method))
            for allocation in component.allocations:
                # A zero share of a non-zero component is a real measured
                # result, so electricity allocations are written in full --
                # including zeros -- and the stored rows reconcile
                # component by component to the actual bill.
                uow.electricity.insert_allocation(
                    allocation_id=new_id("alloc"), expense_id=plan["expense_id"],
                    component_id=db_component_id, resident_id=allocation.resident_id,
                    amount_paise=allocation.amount_paise,
                    allocation_basis_json=_allocation_basis_json(plan, component))
        _insert_readings_and_bill(uow, plan)
    except PersistenceError as exc:
        raise ValidationError(f"Could not persist electricity expense: {exc}") from exc

    audit.record(uow, entity_type="expense", entity_id=plan["expense_id"], action="create",
                 actor=plan["actor"], apartment_id=plan["apartment_id"],
                 new_value=_audit_allocations(plan))


def _rewrite_electricity_expense_in(uow: UnitOfWork, plan: Mapping[str, Any]) -> None:
    """Recalculate a bill in place: the expense, its components and its charges
    are changed rather than replaced, so the payments applied to a charge stay
    with it -- a resident who has paid part of their share sees the rest still owed.

    Refused when it would take a charge below what has been paid against it
    (``electricity_share_below_paid``), or the bill below what residents have
    paid the provider for it (``electricity_below_disbursed``).
    """
    expense_id, result = plan["expense_id"], plan["result"]
    old_components = uow.electricity.components_for_expense(expense_id)
    charges = uow.electricity.charges_for_expense(expense_id)
    by_component: dict[str | None, dict[str, dict[str, Any]]] = {}
    for row in charges:
        by_component.setdefault(row["component_id"], {})[row["resident_id"]] = row

    # What each existing charge becomes: its component is matched by position.
    new_amount: dict[str, int] = {}
    for index, old in enumerate(old_components):
        new = result.components[index] if index < len(result.components) else None
        amounts = {a.resident_id: a.amount_paise for a in new.allocations} if new else {}
        for rid, row in by_component.get(old["component_id"], {}).items():
            new_amount[row["allocation_id"]] = amounts.get(rid, 0)
    names = {row["resident_id"]: row["display_name"] or row["resident_id"] for row in charges}
    paid: dict[str, int] = {}
    for row in charges:
        paid[row["resident_id"]] = paid.get(row["resident_id"], 0) + row["settled_paise"]
    owed = {a.resident_id: a.amount_paise for a in result.allocations}
    for rid, amount in sorted(paid.items()):
        if amount > owed.get(rid, 0):
            raise ConflictError(
                f"{names[rid]} has already paid {format_inr(amount)} towards this electricity "
                f"bill, more than this would leave their share at "
                f"({format_inr(owed.get(rid, 0))}).", code="electricity_share_below_paid",
                details={"resident_id": rid, "settled_paise": amount,
                         "share_paise": owed.get(rid, 0)})
    for row in charges:
        if row["settled_paise"] > new_amount.get(row["allocation_id"], 0):
            raise ConflictError(
                f"{names[row['resident_id']]}'s payment is applied to a part of this bill "
                "that this would take below it. Recalculate without moving money between "
                "the bill's parts, or reverse the payment first.",
                code="electricity_share_below_paid",
                details={"resident_id": row["resident_id"],
                         "allocation_id": row["allocation_id"],
                         "settled_paise": row["settled_paise"],
                         "share_paise": new_amount.get(row["allocation_id"], 0)})
    advanced = uow.electricity.linked_advance_total_paise(expense_id)
    if advanced > plan["total_amount_paise"]:
        raise ConflictError(
            f"Residents have already paid the provider {format_inr(advanced)} for this bill, "
            f"more than this would leave it at ({format_inr(plan['total_amount_paise'])}).",
            code="electricity_below_disbursed",
            details={"advanced_paise": advanced,
                     "total_amount_paise": plan["total_amount_paise"]})

    resolved = plan["resolved"]
    uow.electricity.update_expense(
        expense_id, description=plan["description"], amount_paise=plan["total_amount_paise"],
        expense_date=plan["expense_date"], expense_period=plan["expense_period"],
        payer_resident_id=plan["payer_resident_id"], provider_name=plan["provider_name"],
        service_period=resolved.service_period, settlement_period=resolved.settlement_period,
        period_rule=resolved.rule, due_date=plan["due_date"],
        reference=optional_text(plan["bill_number"], "bill_number", max_length=120),
        notes=optional_text(plan["notes"], "notes"), updated_at=now_timestamp())
    # Charges with no component of their own (from before components were kept)
    # have nothing to follow; none of them has been paid, as checked above.
    for row in by_component.get(None, {}).values():
        uow.electricity.delete_allocation(row["allocation_id"])
    for index, component in enumerate(result.components):
        if index < len(old_components):
            component_id = old_components[index]["component_id"]
            uow.electricity.update_component(
                component_id, name=component.name, amount_paise=component.amount_paise,
                allocation_method=_schema_method(component.allocation_method))
        else:
            component_id = new_id("ec")
            uow.electricity.insert_component(
                component_id=component_id, expense_id=expense_id, name=component.name,
                amount_paise=component.amount_paise,
                allocation_method=_schema_method(component.allocation_method))
        existing = by_component.get(component_id, {})
        for allocation in component.allocations:
            row = existing.pop(allocation.resident_id, None)
            if row is not None:
                uow.electricity.update_allocation(
                    row["allocation_id"], amount_paise=allocation.amount_paise,
                    allocation_basis_json=_allocation_basis_json(plan, component))
            else:
                uow.electricity.insert_allocation(
                    allocation_id=new_id("alloc"), expense_id=expense_id,
                    component_id=component_id, resident_id=allocation.resident_id,
                    amount_paise=allocation.amount_paise,
                    allocation_basis_json=_allocation_basis_json(plan, component))
        for row in existing.values():
            uow.electricity.delete_allocation(row["allocation_id"])
    for old in old_components[len(result.components):]:
        for row in by_component.get(old["component_id"], {}).values():
            uow.electricity.delete_allocation(row["allocation_id"])
        uow.electricity.delete_component(old["component_id"])
    uow.electricity.delete_readings(expense_id)
    uow.electricity.delete_bill(expense_id)
    _insert_readings_and_bill(uow, plan)
    audit.record(uow, entity_type="expense", entity_id=expense_id, action="recalculate",
                 actor=plan["actor"], apartment_id=plan["apartment_id"],
                 new_value=_audit_allocations(plan))


def recalculate_electricity_expense(db: Database, expense_id: str, *, reason: str,
                                    actor: str = "operator", **changes: Any) -> dict[str, Any]:
    """Replace a saved electricity calculation, auditably.

    The previous calculation is written to the audit trail before being
    replaced, the statement's edit rules are enforced, and a correction inside
    the 72-hour window is recorded as a statement revision. A closed statement
    is refused outright: that correction belongs in a ledger adjustment.

    While the month is open (no statement, or a draft one) the bill is
    recalculated in place, so payments residents have made towards it stay with
    their charges: a bill that goes up leaves them the difference still owed. It
    is refused if it would take a charge below what has been paid against it, or
    the bill below what residents have paid the provider for it. In the
    correction window a bill with payments applied is still refused.
    """
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("A reason is required to change a saved electricity "
                              "calculation.", details={"field": "reason"})
    from . import statements  # local import avoids a cycle with snapshots

    with db.read() as uow:
        expense = get_expense(uow, expense_id)
        if expense["expense_kind"] != "electricity":
            raise ValidationError("That expense is not an electricity bill.",
                                  details={"expense_id": expense_id})
        bill = uow.electricity.bill_for_expense(expense_id)
        readings = uow.electricity.readings_for_expense(expense_id)
        allocations = uow.electricity.allocations_for_expense(expense_id)
        mode = assert_not_locked(uow, expense["statement_id"], "electricity bill")
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
        # A correction keeps the residents the bill was shared between, so an
        # old bill still reconciles after one of them has moved out.
        "participants": sorted({a["resident_id"] for a in allocations}) or None,
    }
    if payload["components"] is None and bill:
        stored = json.loads(bill["calculation_json"])
        payload["components"] = [
            {"id": c["component_id"], "name": c["name"], "amount_paise": c["amount_paise"],
             "allocation_method": c["allocation_method"],
             # A custom split has no formula to re-run; carry its amounts over.
             **({"allocations_paise": {a["resident_id"]: a["amount_paise"]
                                       for a in c["allocations"]}}
                if c["allocation_method"] == "custom" else {})}
            for c in stored["components"]]

    if mode == "free":
        with db.transaction() as uow:
            audit.record(uow, entity_type="expense", entity_id=expense_id,
                         action="supersede_electricity_calculation", actor=actor,
                         apartment_id=expense["apartment_id"], reason=reason,
                         old_value={"amount_paise": expense["amount_paise"],
                                    "calculation": json.loads(bill["calculation_json"])
                                    if bill else None})
            _rewrite_electricity_expense_in(uow, _plan_electricity_expense(uow, **payload))
        return get_electricity_expense(db, expense_id)

    with db.transaction() as uow:
        settled = uow.electricity.applied_paise_for_expense(expense_id)
        if settled:
            raise ConflictError(
                "Payments have already been applied to this bill's charges. Reverse them "
                "first, or record a ledger adjustment instead.",
                code="allocations_have_payments",
                details={"expense_id": expense_id, "applied_paise": settled})
        linked_payment_ids = uow.electricity.linked_payment_ids(expense_id)
        previous = uow.electricity.bill_for_expense(expense_id)
        audit.record(uow, entity_type="expense", entity_id=expense_id,
                     action="supersede_electricity_calculation", actor=actor,
                     apartment_id=expense["apartment_id"], reason=reason,
                     old_value={"amount_paise": expense["amount_paise"],
                                "calculation": json.loads(previous["calculation_json"])
                                if previous else None})
        if linked_payment_ids:
            # Detach the funding links so the expense row can be replaced, then
            # restore exactly those links once the new row exists.
            uow.electricity.detach_payments(expense_id)
        uow.electricity.delete_allocations(expense_id)
        uow.electricity.delete_readings(expense_id)
        uow.electricity.delete_bill(expense_id)
        uow.electricity.delete_components(expense_id)
        uow.electricity.delete_expense(expense_id)

        # The replacement is written in this same transaction: if it is refused
        # for any reason, the deletions above roll back and the saved bill stays.
        _create_electricity_expense_in(uow, **payload)
        for payment_id in linked_payment_ids:
            uow.electricity.attach_payment(payment_id, expense_id)
        if mode == "revision":
            statements.record_revision(
                uow, statement_id=expense["statement_id"], reason=reason, created_by=actor,
                change_note=f"electricity bill {expense_id} recalculated")
    if mode == "revision":
        statements.refresh_snapshot(db, expense["statement_id"], actor=actor,
                                    reason=reason)
    return get_electricity_expense(db, expense_id)


def get_electricity_expense(db: Database, expense_id: str) -> dict[str, Any] | None:
    """Read a saved electricity expense with components, readings, and allocations."""
    with db.read() as uow:
        expense = uow.expenses.get(expense_id)
        if expense is None:
            return None
        components = uow.electricity.components_for_expense(expense_id)
        readings = uow.electricity.readings_for_expense(expense_id)
        allocations = uow.electricity.allocations_for_expense(expense_id)
        bill = uow.electricity.bill_for_expense(expense_id)
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


def list_electricity_bills(db: Database, apartment_id: str, *, limit: int = 50,
                           offset: int = 0) -> dict[str, Any]:
    with db.read() as uow:
        get_apartment(uow, apartment_id)
        total = uow.electricity.count_bills(apartment_id)
        rows = uow.electricity.list_bills(apartment_id, limit=int(limit),
                                          offset=int(offset))
    for row in rows:
        row["total_kwh"] = wh_to_kwh_string(row["total_energy_wh"])
    return {"items": rows, "total": total, "limit": int(limit), "offset": int(offset)}


def consumption_history(db: Database, apartment_id: str) -> list[dict[str, Any]]:
    """Per-resident kWh and paise, by service period: the consumption report."""
    with db.read() as uow:
        get_apartment(uow, apartment_id)
        rows = uow.electricity.consumption_by_period(apartment_id)
    for row in rows:
        row["kwh"] = wh_to_kwh_string(row["energy_wh"])
    return rows

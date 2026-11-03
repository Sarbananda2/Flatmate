"""Electricity bills: a dedicated module over the standalone electricity engine.

Each month's electricity is entered on the Electricity page as the flat's two
bills -- the grid's (UPPCL) and the diesel generator's (DG) -- and each
flatmate's AC: everyone pays their own AC and an equal share of the rest, into
the House Kitty, which pays UPPCL and DG (see "the month's bill" below). A bill
can still be recorded, and corrected, split by smart-plug readings instead, as
the rest of this docstring describes.

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

from ..engine.electricity import (
    ElectricityCalculationError,
    calculate_consolidated_electricity,
    calculate_electricity,
)
from ..errors import ConflictError, NotFoundError, ValidationError
from ..ids import new_id, now_timestamp
from ..money import ensure_paise, format_inr, kwh_to_wh, wh_to_kwh_string
from ..periods import (
    default_due_date,
    month_end,
    month_start,
    normalize_month,
    month_label,
    parse_iso_date,
    resolve_periods,
    shift_month,
)
from ..persistence import Database, PersistenceError, UnitOfWork
from . import (
    audit,
    bill_shares,
    definitions,
    deletions,
    expenses,
    payments,
    resident_payouts,
    service_bills,
)
from .common import (
    assert_not_locked,
    get_apartment,
    get_expense,
    get_resident,
    list_residents,
    optional_text,
    require_draft_statement,
    require_text,
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
        if _is_consolidated(bill):
            raise ConflictError(
                "This month's electricity is split by AC and settled through the House "
                "Kitty: change it on the Electricity page.", code="electricity_bill_consolidated",
                details={"expense_id": expense_id})
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


# ======================================================== the month's bill ==
#
# Each month the flat's two bills -- the grid's (UPPCL) and the diesel
# generator's (DG) -- are settled as one, through the House Kitty. Each
# flatmate is responsible for their own AC, and an equal share of the rest
# (:func:`aem.engine.electricity.calculate_consolidated_electricity`).
#
# The month is one ``electricity`` expense with a component per bill, and a
# charge per flatmate for all they owe on it. They pay it into the kitty, and
# the kitty pays UPPCL and DG separately, in one payout or several -- each
# provider a household service of its own, so a payout names it (migration
# 0033). A resident who pays a provider from their own account pays their own
# share through the kitty, and the kitty pays back what they paid beyond it
# (:mod:`aem.services.bill_shares`).
#
# Until the month's statement is finalized, the bills and the AC can be
# entered again: the charges change in place, and what each flatmate paid
# moves with their new share.

PROVIDER_KEYS = ("uppcl", "dg")
DEFAULT_PROVIDER_NAMES = {"uppcl": "UPPCL (Grid)", "dg": "Diesel Generator (DG)"}
#: Where a payout to a provider came from: the kitty, or a resident's own account.
PAYOUT_MODES = ("pool", "resident_advance")
#: What a month's bill row says it is (``electricity_bill.calculation_json``).
CONSOLIDATED = "consolidated"


def _is_consolidated(bill: Mapping[str, Any] | None) -> bool:
    if not bill or not bill.get("calculation_json"):
        return False
    try:
        return json.loads(bill["calculation_json"]).get("model") == CONSOLIDATED
    except (ValueError, AttributeError):
        return False


def _month_providers(uow: UnitOfWork, apartment_id: str) -> dict[str, dict[str, Any]]:
    return {row["provider_key"]: row for row in uow.electricity.live_providers(apartment_id)}


def _month_definition_id(uow: UnitOfWork, apartment_id: str,
                         providers: Mapping[str, Mapping[str, Any]]) -> str | None:
    return (next((row["expense_definition_id"] for row in providers.values()
                  if row["expense_definition_id"]), None)
            or uow.electricity.find_definition_id(apartment_id))


def _month_expense(uow: UnitOfWork, apartment_id: str, definition_id: str | None,
                   month: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """``(the month's bill, an electricity bill recorded for it some other way)``."""
    if not definition_id:
        return None, None
    foreign = None
    for expense in uow.furniture.expenses_for_month(apartment_id, definition_id, month):
        if _is_consolidated(uow.electricity.bill_for_expense(expense["expense_id"])):
            return expense, None
        foreign = foreign or expense
    return None, foreign


def _month_stored_basis(allocations: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    for row in allocations:
        if row.get("allocation_basis_json"):
            try:
                return json.loads(row["allocation_basis_json"])
            except ValueError:
                return {}
    return {}


def _month_participants(uow: UnitOfWork, apartment_id: str,
                        allocations: Sequence[Mapping[str, Any]],
                        basis: Mapping[str, Any]) -> list[str]:
    """Who shares the month: everyone living in the flat, and whoever the bill
    already charges -- a flatmate who moved out keeps the month they were in."""
    everyone = set(resident_ids(uow, apartment_id))
    on_bill = set(basis.get("participants")
                  or [row["resident_id"] for row in allocations if row["amount_paise"]])
    return sorted(set(resident_ids(uow, apartment_id, active_only=True))
                  | (on_bill & everyone))


def _clean_ac(payload: Mapping[str, Any]) -> dict[str, int]:
    """Each flatmate's AC, as sent, in paise."""
    ac = payload.get("ac_allocations") or {}
    if not isinstance(ac, Mapping):
        raise ValidationError("ac_allocations must give each flatmate's AC in paise.",
                              details={"field": "ac_allocations"})
    return {str(rid): ensure_paise(paise, f"AC for {rid}") for rid, paise in ac.items()}


def _month_plan(uow: UnitOfWork, apartment_id: str, participants: Sequence[str],
                bills: Sequence[Mapping[str, Any]], ac: Mapping[str, int]) -> dict[str, Any]:
    """The month split: each flatmate's AC, and an equal share of the rest."""
    names = {row["resident_id"]: row["display_name"] for row in list_residents(uow, apartment_id)}
    if not participants:
        raise ValidationError("Nobody lives in the flat, so there is nobody to share the bill.",
                              code="electricity_no_participants")
    strangers = sorted(rid for rid, paise in ac.items() if rid not in participants and paise)
    if strangers:
        raise ValidationError(
            f"{', '.join(names.get(rid, rid) for rid in strangers)} is not sharing this "
            "month's electricity, so has no AC on it.", code="electricity_ac_not_participant",
            details={"field": "ac_allocations", "resident_ids": strangers})
    total = sum(bill["amount_paise"] for bill in bills)
    total_ac = sum(ac.get(rid, 0) for rid in participants)
    if total_ac > total:
        raise ValidationError(
            f"The AC comes to {format_inr(total_ac)}, more than the {format_inr(total)} the "
            "bills come to.", code="electricity_ac_exceeds_bill",
            details={"field": "ac_allocations", "total_ac_paise": total_ac,
                     "total_paise": total})
    try:
        calc = calculate_consolidated_electricity(
            components=bills, ac_allocations={rid: ac.get(rid, 0) for rid in participants},
            residents=participants)
    except ElectricityCalculationError as exc:
        raise ValidationError(str(exc), code="electricity_split_invalid") from exc
    for share in calc["residents"]:
        share["display_name"] = names.get(share["resident_id"], share["resident_id"])
    return calc


def _month_basis(calc: Mapping[str, Any]) -> dict[str, Any]:
    """Kept with each charge, so the month reads as it was split."""
    amounts = {bill["id"]: bill["amount_paise"] for bill in calc["components"]}
    return {
        "source": "electricity", "method": "custom",
        "uppcl_paise": amounts["uppcl"], "dg_paise": amounts["dg"],
        "ac_allocations": {share["resident_id"]: share["ac_paise"]
                           for share in calc["residents"]},
        "common_remainder_paise": calc["common_remainder_paise"],
        "common_share_paise": calc["common_share_paise"],
        "participants": [share["resident_id"] for share in calc["residents"]],
    }


def _month_shares(calc: Mapping[str, Any]) -> dict[str, int]:
    return {share["resident_id"]: share["responsibility_paise"]
            for share in calc["residents"] if share["responsibility_paise"]}


def _month_provider_label(calc: Mapping[str, Any]) -> str:
    return " + ".join(bill["name"] for bill in calc["components"])


def _month_notes(calc: Mapping[str, Any]) -> str:
    bills = " + ".join(f"{bill['name']} {format_inr(bill['amount_paise'])}"
                       for bill in calc["components"])
    return (f"{bills}. AC by flatmate ({format_inr(calc['total_ac_paise'])}); "
            f"{format_inr(calc['common_remainder_paise'])} shared equally by "
            f"{len(calc['residents'])}.")[:2000]


def _month_calculation_json(calc: Mapping[str, Any]) -> str:
    return json.dumps({"model": CONSOLIDATED, **calc}, sort_keys=True, default=str)


def _month_components(calc: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [{"component_id": new_id("component"), "name": bill["name"],
             "amount_paise": bill["amount_paise"]} for bill in calc["components"]]


def _month_service_period(uow: UnitOfWork, definition_id: str, month: str) -> tuple[str, str]:
    """``(period rule, service period)``: as the household's electricity
    definition says -- by default, last month's usage settles this month."""
    rule = uow.electricity.definition_period_rule(definition_id) or "previous_month"
    return rule, shift_month(month, -1) if rule == "previous_month" else month


def _ensure_providers(uow: UnitOfWork, apartment_id: str, bills: Sequence[Mapping[str, Any]],
                      definition_id: str, timestamp: str,
                      actor: str) -> dict[str, dict[str, Any]]:
    """UPPCL and DG as the flat's providers, by the names the bills give them."""
    providers = _month_providers(uow, apartment_id)
    for bill in bills:
        provider = providers.get(bill["id"])
        if provider is None:
            service_id = new_id("svc")
            uow.electricity.insert_provider(
                service_id=service_id, apartment_id=apartment_id, provider_key=bill["id"],
                provider_name=bill["name"], expense_definition_id=definition_id,
                created_at=timestamp)
            audit.record(uow, entity_type="household_service", entity_id=service_id,
                         action="create", actor=actor, apartment_id=apartment_id,
                         new_value={"service_type": "electricity", "provider_key": bill["id"],
                                    "provider_name": bill["name"]})
        elif provider["provider_name"] != bill["name"]:
            uow.electricity.rename_provider(provider["service_id"], provider_name=bill["name"],
                                            updated_at=timestamp)
            audit.record(uow, entity_type="household_service", entity_id=provider["service_id"],
                         action="update", actor=actor, apartment_id=apartment_id,
                         old_value={"provider_name": provider["provider_name"]},
                         new_value={"provider_name": bill["name"]})
    return _month_providers(uow, apartment_id)


def _record_month(uow: UnitOfWork, apartment_id: str, month: str, calc: Mapping[str, Any],
                  definition_id: str, statement: Mapping[str, Any] | None, timestamp: str,
                  actor: str) -> str:
    """Write the month's bill: one expense, a component per bill, a charge per flatmate."""
    expense_id = new_id("electricity")
    rule, service_period = _month_service_period(uow, definition_id, month)
    shares = _month_shares(calc)
    expenses._create_expense(
        uow, apartment_id=apartment_id, expense_id=expense_id,
        description=f"Electricity for {month_label(month)}",
        amount_paise=calc["total_paise"], expense_date=f"{month}-01",
        allocation_method="custom",
        allocations_paise={rid: shares.get(rid, 0) for rid in resident_ids(uow, apartment_id)},
        allocation_basis=_month_basis(calc), provider_name=_month_provider_label(calc),
        expense_definition_id=definition_id,
        statement_id=statement["statement_id"] if statement else None,
        period_rule=rule, service_period=service_period, settlement_period=month,
        notes=_month_notes(calc), expense_kind="electricity", created_at=timestamp, actor=actor)
    uow.electricity.replace_components(expense_id, _month_components(calc))
    uow.electricity.insert_bill(
        electricity_bill_id=new_id("electricity-bill"), expense_id=expense_id,
        bill_period_start=month_start(service_period).isoformat(),
        bill_period_end=month_end(service_period).isoformat(), settlement_period=month,
        bill_number=None, provider_name=_month_provider_label(calc),
        total_amount_paise=calc["total_paise"], calculation_json=_month_calculation_json(calc),
        notes=None, created_at=timestamp)
    return expense_id


def _rewrite_month(uow: UnitOfWork, apartment_id: str, month: str,
                   expense: Mapping[str, Any], allocations: Sequence[Mapping[str, Any]],
                   basis: Mapping[str, Any], calc: Mapping[str, Any],
                   providers: Mapping[str, Mapping[str, Any]], timestamp: str,
                   actor: str) -> None:
    """Bring the month's bill to new figures. Charges change in place, and what
    each flatmate paid -- into the kitty, or to a provider -- follows their new
    share; refused if the kitty has paid somebody back more than it would then
    owe them."""
    expense_id = expense["expense_id"]
    shares = _month_shares(calc)
    new_basis = _month_basis(calc)
    basis_json = json.dumps(new_basis, sort_keys=True, default=str)
    current = {row["resident_id"]: row for row in allocations}
    components = [(row["name"], row["amount_paise"])
                  for row in uow.electricity.components_for_expense(expense_id)]
    if (expense["amount_paise"] == calc["total_paise"] and basis == new_basis
            and components == [(bill["name"], bill["amount_paise"])
                               for bill in calc["components"]]
            and {rid: row["amount_paise"] for rid, row in current.items()
                 if row["amount_paise"]} == shares):
        return
    name = month_label(month)
    rebalance = bill_shares.plan(
        uow, scope="service", apartment_id=apartment_id, expense_id=expense_id,
        shares=shares, charged=list(current),
        tranches=uow.furniture.service_disbursements(expense_id),
        owed={providers[bill["id"]]["service_id"]: bill["amount_paise"]
              for bill in calc["components"]},
        names={row["resident_id"]: row["display_name"]
               for row in list_residents(uow, apartment_id)},
        what=f"the {name} electricity bill")
    if rebalance.problem is not None:
        raise rebalance.problem
    uow.furniture.update_bill(expense_id, amount_paise=calc["total_paise"],
                              notes=_month_notes(calc), provider_name=_month_provider_label(calc),
                              updated_at=timestamp)
    uow.electricity.replace_components(expense_id, _month_components(calc))

    def set_charge(rid: str, amount: int) -> None:
        row = current.get(rid)
        if row is not None and amount:
            uow.furniture.update_allocation(row["allocation_id"], amount_paise=amount,
                                            allocation_basis_json=basis_json)
        elif row is not None:
            uow.furniture.delete_allocation(row["allocation_id"])
        elif amount:
            uow.expenses.insert_allocation(
                allocation_id=new_id("allocation"), expense_id=expense_id, component_id=None,
                resident_id=rid, amount_paise=amount, allocation_basis_json=basis_json)

    bill_shares.apply(
        uow, rebalance, set_charge=set_charge,
        payee=lambda group: group["tranches"][0]["provider_name"],
        share_note=f"Electricity for {name}",
        advance_note=f"Extra paid for electricity ({name}) to be reimbursed via House Kitty",
        timestamp=timestamp, actor=actor)
    # Every charge reads the month as it now stands, a charge left as it was too.
    for row in uow.furniture.allocations_for_expense(expense_id):
        uow.furniture.update_allocation(row["allocation_id"], amount_paise=row["amount_paise"],
                                        allocation_basis_json=basis_json)
    uow.electricity.update_bill_row(
        expense_id, provider_name=_month_provider_label(calc),
        total_amount_paise=calc["total_paise"], bill_number=None,
        calculation_json=_month_calculation_json(calc), notes=None)
    audit.record(uow, entity_type="expense", entity_id=expense_id, action="sync",
                 actor=actor, apartment_id=apartment_id,
                 old_value={"amount_paise": expense["amount_paise"],
                            "shares": {rid: row["amount_paise"] for rid, row in current.items()},
                            "split": {key: basis.get(key) for key in (
                                "uppcl_paise", "dg_paise", "ac_allocations")}},
                 new_value={"amount_paise": calc["total_paise"], "shares": shares,
                            "split": {key: new_basis[key] for key in (
                                "uppcl_paise", "dg_paise", "ac_allocations")}})


def _month_bills_at(providers: Mapping[str, Mapping[str, Any]],
                    amounts: Mapping[str, int]) -> list[dict[str, Any]]:
    """UPPCL's and DG's bills at these amounts -- ₹0 for one not given -- by
    the names the flat knows them by."""
    return [{"id": key,
             "name": (providers[key]["provider_name"] if key in providers
                      else DEFAULT_PROVIDER_NAMES[key]),
             "amount_paise": amounts.get(key, 0)} for key in PROVIDER_KEYS]


def _recharged(uow: UnitOfWork, apartment_id: str,
               expense: Mapping[str, Any] | None) -> dict[str, int]:
    """What each prepaid meter has been recharged for the month: the sum of its
    top-ups, which is its bill."""
    recharged = {key: 0 for key in PROVIDER_KEYS}
    if expense is None:
        return recharged
    keys = {row["service_id"]: row["provider_key"]
            for row in uow.electricity.live_providers(apartment_id)}
    for tranche in uow.furniture.service_disbursements(expense["expense_id"]):
        recharged[keys.get(tranche["service_id"], "uppcl")] += tranche["amount_paise"]
    return recharged


def _ensure_month_definition(db: Database, apartment_id: str, actor: str) -> str:
    """The household's electricity definition, created if it has none."""
    with db.read() as uow:
        get_apartment(uow, apartment_id)
        definition_id = _month_definition_id(uow, apartment_id,
                                             _month_providers(uow, apartment_id))
        id_taken = definition_id is None and uow.electricity.definition_id_taken("electricity")
    if definition_id is None:
        definition_id = definitions.create_expense_definition(
            db, apartment_id=apartment_id,
            definition_id=new_id("electricity") if id_taken else "electricity",
            name="Electricity", category="Utilities", period_rule="previous_month",
            default_allocation_method="custom", is_recurring=False, recurrence="none",
            sort_order=20, notes="Created by the Electricity screen, where each month's UPPCL "
                                 "and DG bills are entered.", actor=actor)["expense_definition_id"]
    return definition_id


def _write_month(uow: UnitOfWork, apartment_id: str, month: str,
                 bills: Sequence[Mapping[str, Any]], ac: Mapping[str, int], definition_id: str,
                 timestamp: str, actor: str) -> None:
    """Record the month at these bills and AC, or bring its bill to them."""
    statement = uow.furniture.statement_for_month(apartment_id, month)
    if statement is not None and statement["status"] != "draft":
        raise ConflictError(
            f"The {month_label(month)} statement has been finalized, so its electricity "
            "can no longer change.", code="electricity_month_finalized",
            details={"settlement_month": month, "status": statement["status"]})
    expense, foreign = _month_expense(uow, apartment_id, definition_id, month)
    if foreign is not None:
        raise ConflictError(
            f"{month_label(month)}'s electricity is already recorded with smart-plug "
            "readings. Correct that bill instead.", code="electricity_bill_recorded_elsewhere",
            details={"expense_id": foreign["expense_id"]})
    providers = _ensure_providers(uow, apartment_id, bills, definition_id, timestamp, actor)
    allocations = (uow.furniture.allocations_for_expense(expense["expense_id"])
                   if expense else [])
    basis = _month_stored_basis(allocations)
    calc = _month_plan(uow, apartment_id,
                       _month_participants(uow, apartment_id, allocations, basis), bills, ac)
    if expense is None:
        expense_id = _record_month(uow, apartment_id, month, calc, definition_id, statement,
                                   timestamp, actor)
        audit.record(uow, entity_type="expense", entity_id=expense_id, action="generate",
                     actor=actor, apartment_id=apartment_id,
                     new_value={"settlement_month": month, **_month_basis(calc)})
    else:
        _rewrite_month(uow, apartment_id, month, expense, allocations, basis, calc, providers,
                       timestamp, actor)


def record_month_bill(db: Database, apartment_id: str, payload: Mapping[str, Any], *,
                      actor: str = "operator") -> dict[str, Any]:
    """Set a month's AC: what each flatmate's AC cost, taken out of what the
    meters were recharged. The rest is shared equally.

    ``payload``: ``settlement_month`` and ``ac_allocations``, each flatmate's
    AC in paise -- together no more than the month's recharges. The month's
    bills are its recharges, so it needs one logged first (see
    :func:`disburse_month_payout`). What each flatmate paid moves with their
    new share. Refused once the month's statement is finalized. Returns the
    month as :func:`get_month` gives it.
    """
    month = normalize_month(payload.get("settlement_month"), "settlement_month")
    ac = _clean_ac(payload)
    timestamp = now_timestamp()
    definition_id = _ensure_month_definition(db, apartment_id, actor)
    with db.transaction() as uow:
        expense, foreign = _month_expense(uow, apartment_id, definition_id, month)
        if expense is None and foreign is None:
            raise ConflictError(
                f"No meter recharge is logged for {month_label(month)} yet: the AC is taken out "
                "of what was recharged.", code="electricity_no_recharges",
                details={"settlement_month": month})
        _write_month(uow, apartment_id, month,
                     _month_bills_at(_month_providers(uow, apartment_id),
                                     _recharged(uow, apartment_id, expense)),
                     ac, definition_id, timestamp, actor)
        return _month_overview(uow, apartment_id, month)


def preview_month_bill(db: Database, apartment_id: str, settlement_month: str,
                       payload: Mapping[str, Any]) -> dict[str, Any]:
    """What this AC would leave each flatmate responsible for, at the month's
    recharges so far. Nothing is written."""
    month = normalize_month(settlement_month, "month")
    ac = _clean_ac(payload)
    with db.read() as uow:
        get_apartment(uow, apartment_id)
        providers = _month_providers(uow, apartment_id)
        expense, _ = _month_expense(uow, apartment_id,
                                    _month_definition_id(uow, apartment_id, providers), month)
        allocations = (uow.furniture.allocations_for_expense(expense["expense_id"])
                       if expense else [])
        participants = _month_participants(uow, apartment_id, allocations,
                                           _month_stored_basis(allocations))
        bills = _month_bills_at(providers, _recharged(uow, apartment_id, expense))
        return {"settlement_month": month,
                **_month_plan(uow, apartment_id, participants, bills, ac)}


def delete_month_top_up(db: Database, apartment_id: str, settlement_month: str,
                        top_up_id: str, *, actor: str = "operator") -> dict[str, Any]:
    """Remove a meter recharge logged by mistake, and everything recorded with it.

    Its meter's bill comes down by it, and the month is split again; what
    flatmates have paid moves with their shares. The month's last recharge
    takes the month with it -- refused once flatmates have paid towards it.
    Refused once the month's statement is finalized.
    """
    month = normalize_month(settlement_month, "month")
    timestamp = now_timestamp()
    with db.transaction() as uow:
        get_apartment(uow, apartment_id)
        expense, providers = _require_month_bill(uow, apartment_id, month)
        expense_id = expense["expense_id"]
        tranche = uow.furniture.service_disbursement(top_up_id)
        if tranche is None or tranche["expense_id"] != expense_id:
            raise NotFoundError(f"No such recharge for {month_label(month)}.",
                                code="top_up_not_found", details={"top_up_id": top_up_id})
        basis = _month_stored_basis(uow.furniture.allocations_for_expense(expense_id))
        deletions.delete_payout_in(uow, apartment_id=apartment_id, scope="service",
                                   disbursement_id=top_up_id, settlement_month=month,
                                   actor=actor)
        recharged = _recharged(uow, apartment_id, expense)
        if not sum(recharged.values()):
            allocations = uow.furniture.allocations_for_expense(expense_id)
            if (sum(row["settled_paise"] for row in allocations)
                    or uow.furniture.linked_advance_total_paise(expense_id)
                    or uow.payments.kitty_payments_for_bill(expense_id)):
                raise ConflictError(
                    f"Flatmates have paid towards {month_label(month)}'s electricity, so its "
                    "last recharge cannot be removed. Delete their payments first.",
                    code="electricity_month_has_payments",
                    details={"settlement_month": month, "expense_id": expense_id})
            # Nothing recharged, and nothing paid: no month.
            uow.electricity.delete_bill(expense_id)
            uow.expenses.delete_allocations(expense_id)
            uow.expenses.delete_components(expense_id)
            uow.expenses.delete_expense(expense_id)
            audit.record(uow, entity_type="expense", entity_id=expense_id, action="delete",
                         actor=actor, apartment_id=apartment_id,
                         reason="Its last meter recharge was removed.",
                         old_value={"settlement_month": month,
                                    "amount_paise": expense["amount_paise"]})
        else:
            _write_month(uow, apartment_id, month, _month_bills_at(providers, recharged),
                         {rid: int(paise)
                          for rid, paise in (basis.get("ac_allocations") or {}).items()},
                         _month_definition_id(uow, apartment_id, providers), timestamp, actor)
        return _month_overview(uow, apartment_id, month)


def get_month(db: Database, apartment_id: str, settlement_month: str) -> dict[str, Any]:
    """A month's electricity: its bills and AC, how they are split, and where
    paying them stands -- into the kitty, and out to UPPCL and DG."""
    month = normalize_month(settlement_month, "month")
    with db.read() as uow:
        get_apartment(uow, apartment_id)
        return _month_overview(uow, apartment_id, month)


def _month_overview(uow: UnitOfWork, apartment_id: str, month: str) -> dict[str, Any]:
    names = {row["resident_id"]: row["display_name"] for row in list_residents(uow, apartment_id)}
    providers = _month_providers(uow, apartment_id)
    definition_id = _month_definition_id(uow, apartment_id, providers)
    statement = uow.furniture.statement_for_month(apartment_id, month)
    expense, foreign = _month_expense(uow, apartment_id, definition_id, month)
    allocations = uow.furniture.allocations_for_expense(expense["expense_id"]) if expense else []
    basis = _month_stored_basis(allocations)
    participants = _month_participants(uow, apartment_id, allocations, basis)
    funding = service_bills.funding(uow, apartment_id, expense) if expense else None
    components = (uow.electricity.components_for_expense(expense["expense_id"])
                  if expense else [])
    amounts = {key: (basis.get(f"{key}_paise") or 0) for key in PROVIDER_KEYS}
    bills = [{"id": key,
              "name": (providers[key]["provider_name"] if key in providers
                       else DEFAULT_PROVIDER_NAMES[key]),
              "amount_paise": amounts[key]} for key in PROVIDER_KEYS]
    for bill, component in zip(bills, components):
        bill["name"] = component["name"]
    ac = {rid: int(paise) for rid, paise in (basis.get("ac_allocations") or {}).items()}
    total = expense["amount_paise"] if expense else 0
    total_ac = sum(ac.values())
    owing = {entry["recipient_resident_id"]: entry["pending_reimbursement_paise"]
             for entry in (funding["reimbursements"] if funding else [])}

    shares = []
    charged = {rid: row["share_paise"] for rid, row in funding["charges"].items()} \
        if funding else {}
    for rid in sorted(set(participants) | set(charged), key=lambda r: (names.get(r, r), r)):
        owed = charged.get(rid, 0)
        charge = funding["charges"].get(rid) if funding else None
        paid = charge["settled_paise"] if charge else 0
        advanced = funding["advanced"].get(rid, 0) if funding else 0
        covered = min(owed, paid + advanced)
        status = service_bills.share_status(owed, paid, advanced) if funding else None
        shares.append({
            "resident_id": rid, "display_name": names.get(rid, rid),
            "ac_paise": ac.get(rid, 0),
            "common_share_paise": owed - ac.get(rid, 0) if expense else 0,
            # What they are responsible for: paid + still owed, always.
            "total_paise": owed,
            "paid_paise": covered,
            "settled_paise": paid,
            "outstanding_paise": owed - covered,
            "status": status,
            "paid_date": (funding["advanced_on"][rid] if status == "funded_payout"
                          else charge["last_payment_date"] if paid and charge else None),
            "payments": funding["paid_in"].get(rid, []) if funding else [],
            # Above zero the kitty owes them; below, they owe the kitty.
            "net_balance_paise": owing.get(rid, 0) - (owed - covered),
        })

    payouts = []
    tranches = funding["disbursements"] if funding else []
    for bill in bills:
        provider = providers.get(bill["id"])
        own = [tranche for tranche in tranches
               if provider and tranche["service_id"] == provider["service_id"]]
        disbursed = sum(tranche["amount_paise"] for tranche in own)
        payouts.append({
            "provider_key": bill["id"], "provider_name": bill["name"],
            "service_id": provider["service_id"] if provider else None,
            "total_paise": bill["amount_paise"], "disbursed_paise": disbursed,
            "remaining_paise": max(0, bill["amount_paise"] - disbursed),
            "payout_status": ("disbursed" if expense and disbursed >= bill["amount_paise"]
                              else "partially_disbursed" if disbursed else "pending"),
            "disbursements": own,
        })

    # Each recharge as it was made: a flatmate's, whatever went through the kitty
    # with it, or the kitty's own.
    top_ups = []
    if funding is not None:
        active = {row["payment_id"]: row for row in uow.payments.active_payments(apartment_id)}
        keys = {row["service_id"]: key for key, row in providers.items()}
        by_flatmate, by_kitty = resident_payouts.group_payouts("service", funding["tranches"],
                                                               active)
        for group in by_flatmate:
            first = group["tranches"][0]
            top_ups.append({
                "top_up_id": first["disbursement_id"],
                "provider_key": keys.get(first["service_id"]),
                "provider_name": first["provider_name"],
                "amount_paise": group["amount_paise"], "recharged_on": group["disbursed_at"],
                "reference": group["reference"], "paid_by": "flatmate",
                "resident_id": group["resident_id"],
                "resident_name": names.get(group["resident_id"], group["resident_name"]),
                "note": group["note"], "seq": group["seq"]})
        for tranche in by_kitty:
            top_ups.append({
                "top_up_id": tranche["disbursement_id"],
                "provider_key": keys.get(tranche["service_id"]),
                "provider_name": tranche["provider_name"],
                "amount_paise": tranche["amount_paise"], "recharged_on": tranche["disbursed_at"],
                "reference": tranche["disbursement_reference"], "paid_by": "kitty",
                "resident_id": None, "resident_name": None, "note": tranche.get("note"),
                "seq": tranche["seq"]})
        top_ups.sort(key=lambda row: (row["recharged_on"], row["seq"]))
        for row in top_ups:
            del row["seq"]

    live = statement is None or statement["status"] == "draft"
    service_period = (_month_service_period(uow, definition_id, month)[1] if definition_id
                      else shift_month(month, -1))
    return {
        "settlement_month": month,
        "service_period": service_period,
        "participants": [{"resident_id": rid, "display_name": names.get(rid, rid)}
                         for rid in sorted(participants, key=lambda r: (names.get(r, r), r))],
        "components": bills,
        "ac_allocations": ac,
        "total_paise": total,
        "total_ac_paise": total_ac,
        "common_remainder_paise": basis.get("common_remainder_paise", 0) if expense else 0,
        "common_share_paise": basis.get("common_share_paise", 0) if expense else 0,
        "shares": shares,
        "payouts": payouts,
        "top_ups": top_ups,
        "collected_paise": funding["collected_paise"] if funding else 0,
        "collected_count": funding["collected_count"] if funding else 0,
        "reimbursements": funding["reimbursements"] if funding else [],
        "expense": expense,
        "foreign_expense": foreign,
        "statement_status": statement["status"] if statement else None,
        # 'live': recorded, and can be entered again until its statement is
        # finalized; 'frozen': the month's snapshot; 'not_billed': no bill yet;
        # 'recorded_elsewhere': entered with smart-plug readings instead.
        "bill_state": ("recorded_elsewhere" if foreign is not None
                       else ("live" if live else "frozen") if expense else "not_billed"),
        "can_record": live and foreign is None,
        "transactions_open": deletions.month_is_open(uow, apartment_id, month),
    }


# ------------------------------------------------------------ paying it -- #

def _require_month_bill(uow: UnitOfWork, apartment_id: str,
                        month: str) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    providers = _month_providers(uow, apartment_id)
    expense, _ = _month_expense(uow, apartment_id,
                                _month_definition_id(uow, apartment_id, providers), month)
    if expense is None:
        raise ConflictError(f"{month_label(month)}'s electricity is not recorded yet.",
                            code="electricity_bill_not_recorded",
                            details={"settlement_month": month})
    return expense, providers


def record_month_payment(db: Database, apartment_id: str, payload: Mapping[str, Any], *,
                         actor: str = "operator") -> dict[str, Any]:
    """Record a flatmate paying what they owe for a month's electricity into the kitty.

    ``payload``: ``settlement_month``, ``resident_id``, ``amount_paise``,
    ``payment_date``, and optionally ``payment_mode``, ``reference`` and ``note``.
    It settles their share of the month first; anything beyond goes to their
    other charges, oldest first, and what is left stays a credit. A share
    already paid, or covered by their paying a provider, is refused.
    """
    month = normalize_month(payload.get("settlement_month"), "settlement_month")
    resident_id = require_text(payload.get("resident_id"), "resident_id", max_length=64)
    amount = ensure_paise(payload.get("amount_paise"), "amount_paise", allow_zero=False)
    payment_date = parse_iso_date(payload.get("payment_date"), "payment_date").isoformat()
    timestamp = now_timestamp()
    with db.transaction() as uow:
        get_apartment(uow, apartment_id)
        name = get_resident(uow, resident_id, apartment_id)["display_name"]
        expense, _ = _require_month_bill(uow, apartment_id, month)
        charges = uow.furniture.resident_charges_for_expense(expense["expense_id"], resident_id)
        if not charges:
            raise ValidationError(
                f"{name} has no share of {month_label(month)}'s electricity.",
                code="not_an_electricity_participant",
                details={"field": "resident_id", "resident_id": resident_id})
        target = next((charge for charge in charges if charge["outstanding_paise"] > 0), None)
        if target is None:
            raise ConflictError(
                f"{name} has already paid their share of {month_label(month)}'s electricity.",
                code="electricity_share_already_paid", details={"resident_id": resident_id})
        advanced = sum(tranche["amount_paise"]
                       for tranche in uow.furniture.service_disbursements(expense["expense_id"])
                       if tranche["funding_resident_id"] == resident_id)
        if advanced and advanced >= sum(charge["outstanding_paise"] for charge in charges):
            raise ConflictError(
                f"{name} paid a provider {month_label(month)}'s electricity, which already "
                "covers their share; the kitty owes them the rest.",
                code="electricity_share_funded_by_payout", details={"resident_id": resident_id})
        payments._record_payment(
            uow, apartment_id=apartment_id, payment_id=new_id("payment"),
            payer_resident_id=resident_id, amount_paise=amount, payment_date=payment_date,
            payment_kind="to_pool", payee_or_provider=None,
            reference=optional_text(payload.get("reference"), "reference", max_length=200),
            note=(optional_text(payload.get("note"), "note")
                  or f"Electricity for {month_label(month)}"),
            timestamp=timestamp, expense_id=expense["expense_id"], actor=actor,
            target_allocation_id=target["allocation_id"],
            payment_mode=payload.get("payment_mode"))
        return _month_overview(uow, apartment_id, month)


def disburse_month_payout(db: Database, apartment_id: str, payload: Mapping[str, Any], *,
                          actor: str = "operator") -> dict[str, Any]:
    """Record a recharge of the UPPCL or the DG prepaid meter: a top-up.

    The meters are prepaid, topped up as the month goes, so the month's bills
    are what was recharged: each top-up raises its meter's bill by its amount,
    and the month is split again -- each flatmate's AC, and an equal share of
    the rest -- with what everyone has paid moving with their shares (see
    :func:`_rewrite_month`). The first top-up of a month records it. Refused
    once the month's statement is finalized.

    ``payload``: ``settlement_month``, ``provider`` (``"uppcl"`` or ``"dg"``),
    ``disbursement_mode``, ``amount_paise``, ``reference``, ``disbursed_date``,
    and optionally ``funding_resident_id`` and ``notes``.

    ``pool``: the kitty recharged it, out of what flatmates paid in.
    ``resident_advance``: a flatmate recharged it from their own account --
    what covers their own open share goes through the kitty as everyone's
    does, and what they paid beyond it the kitty pays back to them
    (``reimbursements``).
    """
    month = normalize_month(payload.get("settlement_month"), "settlement_month")
    key = payload.get("provider")
    if key not in PROVIDER_KEYS:
        raise ValidationError("provider must be 'uppcl' or 'dg'.",
                              details={"field": "provider", "allowed": list(PROVIDER_KEYS)})
    mode = payload.get("disbursement_mode")
    if mode not in PAYOUT_MODES:
        raise ValidationError("Unsupported disbursement_mode.",
                              details={"field": "disbursement_mode",
                                       "allowed": list(PAYOUT_MODES)})
    reference = require_text(payload.get("reference"), "reference", max_length=200)
    disbursed_date = parse_iso_date(payload.get("disbursed_date"), "disbursed_date").isoformat()
    note = optional_text(payload.get("notes"), "notes", max_length=500)
    if payload.get("amount_paise") is None:
        raise ValidationError("Give the amount recharged.", code="electricity_amount_required",
                              details={"field": "amount_paise"})
    amount_paise = ensure_paise(payload.get("amount_paise"), "amount_paise", allow_zero=False)
    funding_resident_id = payload.get("funding_resident_id")
    if mode == "pool" and funding_resident_id:
        raise ValidationError("A recharge from the kitty has no funding resident.",
                              details={"field": "funding_resident_id"})
    if mode == "resident_advance" and not funding_resident_id:
        raise ValidationError("Choose the flatmate who recharged the meter.",
                              details={"field": "funding_resident_id"})
    timestamp = now_timestamp()
    definition_id = _ensure_month_definition(db, apartment_id, actor)
    with db.transaction() as uow:
        get_apartment(uow, apartment_id)
        expense, _ = _month_expense(uow, apartment_id, definition_id, month)
        # The month's bills are what was recharged: this top-up raises its meter's.
        recharged = _recharged(uow, apartment_id, expense)
        recharged[key] += amount_paise
        basis = (_month_stored_basis(uow.furniture.allocations_for_expense(expense["expense_id"]))
                 if expense else {})
        _write_month(uow, apartment_id, month,
                     _month_bills_at(_month_providers(uow, apartment_id), recharged),
                     {rid: int(paise) for rid, paise in (basis.get("ac_allocations") or {}).items()},
                     definition_id, timestamp, actor)
        expense, providers = _require_month_bill(uow, apartment_id, month)
        provider = providers[key]
        expense_id = expense["expense_id"]
        tranches = uow.furniture.service_disbursements(expense_id)
        amount = amount_paise
        owed = recharged[key]
        remaining = amount
        name = provider["provider_name"]
        advanced = sum(tranche["amount_paise"] for tranche in tranches
                       if tranche["disbursed_by"] == "resident_advance")
        external = uow.furniture.linked_advance_total_paise(expense_id) - advanced
        if external > 0 or expense["payer_resident_id"]:
            raise ConflictError(
                f"{month_label(month)}'s electricity already records a flatmate as having paid "
                "it, so a payout cannot also be recorded here. Correct the expense, or the "
                "payment linked to it, first.", code="electricity_already_funded",
                details={"expense_id": expense_id,
                         "payer_resident_id": expense["payer_resident_id"],
                         "linked_advance_paise": external})

        written: list[dict[str, Any]] = []
        if mode == "pool":
            written.append(dict(disbursement_id=new_id("svcdisb"), amount_paise=amount,
                                disbursed_by="pool", funding_resident_id=None,
                                funding_payment_id=None, note=note))
        else:
            payer = get_resident(uow, funding_resident_id, apartment_id)["display_name"]
            open_charge = next((charge for charge in uow.furniture.resident_charges_for_expense(
                expense_id, funding_resident_id) if charge["outstanding_paise"] > 0), None)
            share_paise = min(amount, open_charge["outstanding_paise"]) if open_charge else 0
            extra_paise = amount - share_paise
            if share_paise:
                # Their own share goes through the kitty, as everyone else's does.
                share_payment_id = new_id("payment")
                payments._record_payment(
                    uow, apartment_id=apartment_id, payment_id=share_payment_id,
                    payer_resident_id=funding_resident_id, amount_paise=share_paise,
                    payment_date=disbursed_date, payment_kind="to_pool",
                    payee_or_provider=None, reference=reference,
                    note=f"Electricity for {month_label(month)}",
                    timestamp=timestamp, expense_id=expense_id, actor=actor,
                    target_allocation_id=open_charge["allocation_id"])
                written.append(dict(disbursement_id=service_bills.share_payout_id(share_payment_id),
                                    amount_paise=share_paise, disbursed_by="pool",
                                    funding_resident_id=None, funding_payment_id=None,
                                    note=f"{payer}'s allotted share via House Kitty"))
            if extra_paise:
                # Only what they paid beyond their share is an advance, which the
                # kitty pays back out of what the others pay in.
                funding_payment_id = new_id("electricity-payout")
                payments._record_payment(
                    uow, apartment_id=apartment_id, payment_id=funding_payment_id,
                    payer_resident_id=funding_resident_id, amount_paise=extra_paise,
                    payment_date=disbursed_date, payment_kind="provider_advance",
                    payee_or_provider=name, reference=reference,
                    note=(f"Extra paid for electricity ({month_label(month)}) to be "
                          "reimbursed via House Kitty"),
                    timestamp=timestamp, expense_id=expense_id, actor=actor)
                written.append(dict(disbursement_id=new_id("svcdisb"), amount_paise=extra_paise,
                                    disbursed_by="resident_advance",
                                    funding_resident_id=funding_resident_id,
                                    funding_payment_id=funding_payment_id,
                                    note=note or (f"Extra above {payer}'s share "
                                                  "(reimbursable via House Kitty)")))
        for tranche in written:
            uow.furniture.insert_service_disbursement(
                service_id=provider["service_id"], expense_id=expense_id,
                apartment_id=apartment_id, settlement_month=month, disbursed_at=disbursed_date,
                disbursement_reference=reference, created_at=timestamp, **tranche)
        audit.record(uow, entity_type="household_service", entity_id=provider["service_id"],
                     action="disburse", actor=actor, apartment_id=apartment_id,
                     new_value={"settlement_month": month, "expense_id": expense_id,
                                "provider": key, "amount_paise": amount, "owed_paise": owed,
                                "remaining_paise": remaining - amount, "disbursed_by": mode,
                                "disbursed_at": disbursed_date,
                                "disbursement_reference": reference,
                                "funding_resident_id": funding_resident_id, "note": note,
                                "tranches": [{k: tranche[k] for k in (
                                    "disbursement_id", "amount_paise", "disbursed_by",
                                    "funding_payment_id")} for tranche in written]})
        return _month_overview(uow, apartment_id, month)


def reimburse_month_from_kitty(db: Database, apartment_id: str, payload: Mapping[str, Any], *,
                               actor: str = "operator") -> dict[str, Any]:
    """Pay back, from the kitty, a flatmate who paid a provider more than their share.

    ``payload``: ``settlement_month``, ``recipient_resident_id``, ``amount_paise``,
    ``payment_date``, and optionally ``reference`` and ``notes``. Never more than
    the kitty holds for the month, nor more than they are still owed."""
    month = normalize_month(payload.get("settlement_month"), "settlement_month")
    recipient = require_text(payload.get("recipient_resident_id"), "recipient_resident_id",
                             max_length=64)
    amount = ensure_paise(payload.get("amount_paise"), "amount_paise", allow_zero=False)
    payment_date = parse_iso_date(payload.get("payment_date"), "payment_date").isoformat()
    timestamp = now_timestamp()
    with db.transaction() as uow:
        get_apartment(uow, apartment_id)
        expense, _ = _require_month_bill(uow, apartment_id, month)
        owing = service_bills.funding(uow, apartment_id, expense)["reimbursements"]
        owed = next((entry for entry in owing
                     if entry["recipient_resident_id"] == recipient), None)
        if owed is None:
            raise NotFoundError(
                f"The kitty owes nobody by that name for {month_label(month)}'s electricity.",
                code="electricity_no_reimbursement",
                details={"field": "recipient_resident_id", "settlement_month": month})
        name = owed["recipient_name"]
        if amount > owed["pending_reimbursement_paise"]:
            raise ValidationError(
                f"That is more than the {format_inr(owed['pending_reimbursement_paise'])} "
                f"still owed to {name} for {month_label(month)}.",
                code="exceeds_pending_reimbursement",
                details={"field": "amount_paise", "amount_paise": amount,
                         "pending_reimbursement_paise": owed["pending_reimbursement_paise"]})
        if amount > owed["kitty_cash_available_paise"]:
            raise ValidationError(
                f"The kitty holds only {format_inr(owed['kitty_cash_available_paise'])} from "
                f"flatmates for this month that has not already gone to UPPCL, the DG, or back "
                f"to {name}.", code="exceeds_kitty_cash",
                details={"field": "amount_paise", "amount_paise": amount,
                         "kitty_cash_available_paise": owed["kitty_cash_available_paise"]})
        payment = payments._record_payment(
            uow, apartment_id=apartment_id,
            payment_id=new_id(service_bills.reimbursement_id_base(expense["expense_id"])),
            payer_resident_id=recipient, amount_paise=amount, payment_date=payment_date,
            payment_kind="refund", payee_or_provider=None,
            reference=optional_text(payload.get("reference"), "reference", max_length=200),
            note=(optional_text(payload.get("notes"), "notes", max_length=500)
                  or f"Reimbursement for electricity for {month_label(month)}, paid to UPPCL "
                     "or the DG on the household's behalf"),
            timestamp=timestamp, expense_id=None, actor=actor)
        audit.record(uow, entity_type="expense", entity_id=expense["expense_id"],
                     action="reimburse", actor=actor, apartment_id=apartment_id,
                     new_value={"settlement_month": month, "recipient_resident_id": recipient,
                                "amount_paise": amount, "payment_id": payment["payment_id"],
                                "payment_date": payment_date})
        return _month_overview(uow, apartment_id, month)

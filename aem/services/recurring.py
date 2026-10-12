"""Recurring expense generation: preview, then explicit confirmation.

Generation never happens as a side effect. ``preview`` reports what *would* be
created for a settlement month and why each definition is or is not eligible;
``generate`` creates only the lines the operator confirmed, with the amount they
confirmed. Actual amounts are free to differ every month -- a definition's
``default_amount_paise`` is a suggestion, not a commitment.

Duplicate protection has two layers:

* ``preview`` marks a definition ``already_exists`` when any expense -- generated
  or entered by hand -- already settles in that month against it;
* a partial unique index on ``(expense_definition_id, settlement_period)`` for
  generated rows makes a duplicate impossible even under a race or a repeated
  request.

Generated expenses stay fully editable while their statement is a draft.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Any, Mapping, Sequence

from ..errors import ConflictError, ValidationError
from ..ids import new_id, now_timestamp
from ..money import ensure_paise
from ..periods import month_end, normalize_month, resolve_periods, shift_month
from ..persistence import Database
from .allocation import plan_allocation
from .common import get_apartment, get_statement, resident_ids
from .expenses import create_expense

STATUS_ELIGIBLE = "eligible"
STATUS_ALREADY_EXISTS = "already_exists"
STATUS_NEEDS_AMOUNT = "needs_amount"
STATUS_NOT_RECURRING = "not_recurring"


def _transaction_date_for(definition: Mapping[str, Any], settlement_period: str) -> str:
    """The transaction date a generated expense gets.

    It must be a date whose period rule maps back to ``settlement_period``, so
    the generated expense lands in the month the operator asked for.
    """
    day = int(definition["day_of_month"] or 1)
    last = month_end(settlement_period).day
    return date(*map(int, settlement_period.split("-")), min(day, last)).isoformat()


def preview(db: Database, *, apartment_id: str, settlement_period: str) -> dict[str, Any]:
    settlement_period = normalize_month(settlement_period, "settlement_period")
    with db.read() as uow:
        get_apartment(uow, apartment_id)
        residents = resident_ids(uow, apartment_id)
        active_residents = resident_ids(uow, apartment_id, active_only=True)
        definitions = uow.recurring.active_definitions(apartment_id)
        existing = {
            row["expense_definition_id"]: row
            for row in uow.recurring.expenses_against_definitions(
                apartment_id, settlement_period)}
        statement = uow.recurring.statement_for_month(apartment_id, settlement_period)

    lines: list[dict[str, Any]] = []
    for definition in definitions:
        definition_id = definition["expense_definition_id"]
        transaction_date = _transaction_date_for(definition, settlement_period)
        periods = resolve_periods(
            definition["period_rule"], transaction_date,
            settlement_period=settlement_period,
            service_period=(shift_month(settlement_period, -1)
                            if definition["period_rule"] == "previous_month" else None))
        participants = json.loads(definition["default_participants_json"]) \
            if definition["default_participants_json"] else active_residents
        weights = json.loads(definition["default_weights_json"]) \
            if definition["default_weights_json"] else None
        method = definition["default_allocation_method"]
        plannable = method if method in ("equal", "weighted") else "equal"
        amount = definition["default_amount_paise"]

        if definition["recurrence"] != "monthly":
            status, reason = STATUS_NOT_RECURRING, "This definition is not set to recur monthly."
        elif definition_id in existing:
            status, reason = STATUS_ALREADY_EXISTS, (
                f"An expense for {settlement_period} already exists against this definition.")
        elif amount is None:
            status, reason = STATUS_NEEDS_AMOUNT, (
                "No default amount is configured, so the amount must be entered before "
                "generating.")
        else:
            status, reason = STATUS_ELIGIBLE, None

        shares: dict[str, int] | None = None
        warnings: list[str] = []
        if amount is not None and participants:
            try:
                computed = plan_allocation(
                    amount_paise=amount, method=plannable, all_residents=residents,
                    participants=None if plannable == "weighted" else participants,
                    weights=weights if plannable == "weighted" else None)
                shares = computed.shares_paise
                warnings = computed.warnings
            except ValidationError as exc:
                status = STATUS_NEEDS_AMOUNT
                reason = exc.message

        lines.append({
            "expense_definition_id": definition_id,
            "name": definition["name"],
            "category": definition["category"],
            "period_rule": definition["period_rule"],
            "recurrence": definition["recurrence"],
            "status": status,
            "reason": reason,
            "suggested_amount_paise": amount,
            "suggested_description": definition["name"],
            "provider_name": definition["default_provider_name"],
            "transaction_date": transaction_date,
            "service_period": periods.service_period,
            "settlement_period": periods.settlement_period,
            "allocation_method": plannable,
            "participants": participants,
            "weights": weights,
            "suggested_shares_paise": shares,
            "warnings": warnings,
            "existing_expense_id": existing.get(definition_id, {}).get("expense_id"),
        })

    eligible = [line for line in lines if line["status"] == STATUS_ELIGIBLE]
    return {
        "apartment_id": apartment_id,
        "settlement_period": settlement_period,
        "statement_id": statement["statement_id"] if statement else None,
        "statement_status": statement["status"] if statement else None,
        "lines": lines,
        "eligible_count": len(eligible),
        "eligible_total_paise": sum(line["suggested_amount_paise"] or 0 for line in eligible),
        "already_exists_count": sum(1 for line in lines
                                    if line["status"] == STATUS_ALREADY_EXISTS),
        "needs_amount_count": sum(1 for line in lines
                                  if line["status"] == STATUS_NEEDS_AMOUNT),
    }


def generate(db: Database, *, apartment_id: str, settlement_period: str,
             lines: Sequence[Mapping[str, Any]], statement_id: str | None = None,
             attach_to_statement: bool = True, actor: str = "operator") -> dict[str, Any]:
    """Create the confirmed recurring expenses. Nothing is created without a line.

    Each line names its ``expense_definition_id`` and the ``amount_paise`` the
    operator confirmed, and may override the description, payer, provider,
    allocation method, participants, and weights.
    """
    settlement_period = normalize_month(settlement_period, "settlement_period")
    if not lines:
        raise ValidationError(
            "No recurring expenses were confirmed, so nothing was generated.",
            code="nothing_to_generate")
    plan = {line["expense_definition_id"]: line for line in preview(
        db, apartment_id=apartment_id, settlement_period=settlement_period)["lines"]}

    with db.read() as uow:
        get_apartment(uow, apartment_id)
        if statement_id:
            statement = get_statement(uow, statement_id, apartment_id)
            if statement["settlement_month"] != settlement_period:
                raise ValidationError(
                    "That statement is for a different settlement month.",
                    details={"statement_id": statement_id,
                             "statement_settlement_month": statement["settlement_month"],
                             "settlement_period": settlement_period})
            if statement["status"] != "draft":
                raise ConflictError("Expenses can only be added to a draft statement.",
                                    code="statement_not_draft",
                                    details={"statement_id": statement_id,
                                             "status": statement["status"]})
        elif attach_to_statement:
            row = uow.recurring.statement_for_month(apartment_id, settlement_period)
            if row and row["status"] == "draft":
                statement_id = row["statement_id"]

    created: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for line in lines:
        definition_id = line.get("expense_definition_id")
        planned = plan.get(definition_id)
        if planned is None:
            raise ValidationError(
                "That expense definition is not active in this apartment.",
                details={"expense_definition_id": definition_id})
        if planned["status"] == STATUS_ALREADY_EXISTS:
            skipped.append({"expense_definition_id": definition_id,
                            "reason": planned["reason"],
                            "existing_expense_id": planned["existing_expense_id"]})
            continue
        if planned["status"] == STATUS_NOT_RECURRING:
            raise ValidationError(planned["reason"],
                                  details={"expense_definition_id": definition_id})
        amount = line.get("amount_paise", planned["suggested_amount_paise"])
        if amount is None:
            raise ValidationError(
                f"An amount is required for '{planned['name']}'.",
                code="amount_required", details={"expense_definition_id": definition_id})
        ensure_paise(amount, f"amount_paise for {definition_id}")
        method = line.get("allocation_method") or planned["allocation_method"]
        participants = line.get("participants", planned["participants"])
        weights = line.get("weights", planned["weights"])
        shares = line.get("allocations_paise")
        expense_id = line.get("expense_id") or new_id(f"rec-{definition_id}")
        try:
            result = create_expense(
                db, apartment_id=apartment_id, expense_id=expense_id,
                description=line.get("description") or planned["suggested_description"],
                amount_paise=amount,
                expense_date=line.get("expense_date") or planned["transaction_date"],
                allocations_paise=shares,
                allocation_method=method if shares is None else "custom",
                participants=participants if method == "equal" else None,
                weights=weights if method == "weighted" else None,
                payer_resident_id=line.get("payer_resident_id"),
                provider_name=line.get("provider_name") or planned["provider_name"],
                expense_definition_id=definition_id,
                statement_id=statement_id,
                period_rule=planned["period_rule"],
                service_period=planned["service_period"],
                settlement_period=settlement_period,
                notes=line.get("notes"), reference=line.get("reference"),
                generated_from_recurrence=True, actor=actor)
        except ConflictError as exc:
            if exc.code == "duplicate_recurring_expense":
                skipped.append({"expense_definition_id": definition_id,
                                "reason": exc.message})
                continue
            raise
        created.append({**result, "expense_definition_id": definition_id,
                        "name": planned["name"]})

    return {
        "apartment_id": apartment_id,
        "settlement_period": settlement_period,
        "statement_id": statement_id,
        "created": created,
        "created_count": len(created),
        "created_total_paise": sum(item["amount_paise"] for item in created),
        "skipped": skipped,
        "skipped_count": len(skipped),
    }

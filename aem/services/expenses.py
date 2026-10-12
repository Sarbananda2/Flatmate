"""General household expenses and their resident allocations.

An expense is three separate facts, and the application keeps them separate
because merging them is how double counting happens:

1. **The household incurred a cost.** ``expense_instance.amount_paise``.
2. **Each resident owes a share of it.** ``allocation`` rows, which must sum to
   the expense amount exactly.
3. **Somebody funded it.** ``payer_resident_id``, or a ``provider_advance``
   payment linked to this expense. Funding is not a second expense.

Writes happen inside one transaction, so a partially saved expense -- an
expense row with no allocations, or allocations that do not add up -- cannot
exist in the database.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Mapping, Sequence

from .. import db
from ..db import connect, new_id, now_timestamp, transaction
from ..errors import ConflictError, NotFoundError, ValidationError
from ..money import ensure_paise
from ..periods import (
    PERIOD_RULES,
    default_due_date,
    normalize_month,
    parse_iso_date,
    resolve_periods,
)
from . import audit
from .allocation import plan_allocation
from .common import (
    ALLOCATION_METHODS,
    EXPENSE_KINDS,
    assert_not_locked,
    get_apartment,
    get_expense,
    optional_text,
    paginate,
    require_draft_statement,
    require_text,
    resident_ids,
)

EDITABLE_FIELDS = ["description", "amount_paise", "expense_date", "service_period",
                   "settlement_period", "due_date", "payer_resident_id", "provider_name",
                   "reference", "notes", "statement_id", "expense_definition_id"]


def _resolve_expense_periods(
    conn: sqlite3.Connection, *, expense_definition_id: str | None, period_rule: str | None,
    expense_date: str, expense_period: str | None, service_period: str | None,
    settlement_period: str | None,
) -> tuple[str, str, str]:
    """Decide (period_rule, service_period, settlement_period) for one expense.

    Precedence: explicit arguments, then the definition's rule, then the
    prototype's behaviour of treating ``expense_period`` as the settlement
    month. That last fallback is what keeps older callers and existing rows
    meaning the same thing after the upgrade.
    """
    rule = period_rule
    if rule is None and expense_definition_id:
        row = conn.execute(
            "SELECT period_rule FROM expense_definition WHERE expense_definition_id=?",
            (expense_definition_id,)).fetchone()
        if row is not None:
            rule = row[0]
    if rule is None:
        rule = "custom"
    if rule not in PERIOD_RULES:
        raise ValidationError(f"Unsupported period_rule: {rule}",
                              details={"field": "period_rule", "allowed": list(PERIOD_RULES)})

    fallback = None
    if expense_period:
        try:
            fallback = normalize_month(expense_period, "expense_period")
        except ValidationError:
            fallback = None
    if fallback is None:
        fallback = parse_iso_date(expense_date, "expense_date").strftime("%Y-%m")

    if rule == "custom":
        resolved = resolve_periods(
            "custom", expense_date,
            service_period=service_period,
            settlement_period=settlement_period or fallback)
    else:
        resolved = resolve_periods(rule, expense_date, service_period=service_period,
                                   settlement_period=settlement_period)
    return resolved.rule, resolved.service_period, resolved.settlement_period


def preview_allocation(db_path, *, apartment_id: str, amount_paise: int, method: str,
                       participants: Sequence[str] | None = None,
                       weights: Mapping[str, int] | None = None,
                       shares_paise: Mapping[str, int] | None = None,
                       payer_resident_id: str | None = None) -> dict[str, Any]:
    """Calculate the shares an expense *would* get, without saving anything."""
    with connect(db_path, read_only=True) as conn:
        get_apartment(conn, apartment_id)
        residents = resident_ids(conn, apartment_id)
        names = {row["resident_id"]: row["display_name"] for row in conn.execute(
            "SELECT resident_id, display_name FROM resident WHERE apartment_id=?",
            (apartment_id,)).fetchall()}
    plan = plan_allocation(amount_paise=amount_paise, method=method,
                           all_residents=residents, participants=participants,
                           weights=weights, shares_paise=shares_paise,
                           payer_resident_id=payer_resident_id)
    payload = plan.to_dict()
    payload["lines"] = [
        {"resident_id": rid, "display_name": names.get(rid, rid),
         "amount_paise": plan.shares_paise[rid], "is_participant": rid in plan.participants}
        for rid in residents
    ]
    return payload


def _create_expense(
    conn: sqlite3.Connection, *, apartment_id: str, expense_id: str, description: str,
    amount_paise: int, expense_date: str, expense_period: str | None = None,
    allocations_paise: Mapping[str, int] | None = None,
    payer_resident_id: str | None = None, provider_name: str | None = None,
    expense_definition_id: str | None = None, statement_id: str | None = None,
    allocation_method: str = "custom", allocation_basis: Mapping[str, Any] | None = None,
    created_at: str | None = None,
    # Added after the prototype:
    period_rule: str | None = None, service_period: str | None = None,
    settlement_period: str | None = None, due_date: str | None = None,
    reference: str | None = None, notes: str | None = None,
    expense_kind: str = "general", generated_from_recurrence: bool = False,
    participants: Sequence[str] | None = None, weights: Mapping[str, int] | None = None,
    actor: str = "operator",
) -> dict[str, Any]:
    """Write one expense and its allocations inside the caller's transaction."""
    ensure_paise(amount_paise, "amount_paise")
    if not isinstance(expense_id, str) or not expense_id.strip():
        raise ValidationError("expense_id, description, and expense_period are required.",
                              details={"field": "expense_id"})
    if not isinstance(description, str) or not description.strip():
        raise ValidationError("expense_id, description, and expense_period are required.",
                              details={"field": "description"})
    if allocation_method not in ALLOCATION_METHODS:
        raise ValidationError(f"Unsupported allocation method: {allocation_method}",
                              details={"field": "allocation_method",
                                       "allowed": list(ALLOCATION_METHODS)})
    if expense_kind not in EXPENSE_KINDS:
        raise ValidationError(f"Unsupported expense_kind: {expense_kind}",
                              details={"field": "expense_kind", "allowed": list(EXPENSE_KINDS)})
    parse_iso_date(expense_date, "expense_date")

    if allocations_paise is None:
        if allocation_method not in ("equal", "weighted"):
            raise ValidationError(
                "Provide allocations_paise, or an allocation_method of 'equal' or "
                "'weighted' with participants/weights.",
                details={"field": "allocations_paise"})
    else:
        if not isinstance(allocations_paise, Mapping):
            raise ValidationError("allocations_paise must map resident IDs to paise.",
                                  details={"field": "allocations_paise"})
        if any(isinstance(v, bool) or not isinstance(v, int) or v < 0
               for v in allocations_paise.values()):
            raise ValidationError(
                "Each allocation must be a non-negative integer number of paise.",
                details={"field": "allocations_paise"})
        if sum(allocations_paise.values()) != amount_paise:
            raise ValidationError(
                "Resident allocations must sum exactly to the expense amount.",
                code="allocation_total_mismatch",
                details={"expected_paise": amount_paise,
                         "provided_paise": sum(allocations_paise.values())})

    timestamp = created_at or now_timestamp()
    get_apartment(conn, apartment_id)
    residents = resident_ids(conn, apartment_id)
    if not residents:
        raise ValidationError("No residents registered for this apartment.",
                              code="no_residents")

    if allocations_paise is None:
        plan = plan_allocation(amount_paise=amount_paise, method=allocation_method,
                               all_residents=residents, participants=participants,
                               weights=weights, payer_resident_id=payer_resident_id)
        shares = plan.shares_paise
        basis_source: dict[str, Any] = dict(plan.basis)
    else:
        if set(allocations_paise) != set(residents):
            raise ValidationError(
                "Allocations must contain exactly one amount for every apartment resident.",
                code="allocation_resident_mismatch",
                details={"expected_resident_ids": residents,
                         "provided_resident_ids": sorted(allocations_paise),
                         "missing_resident_ids": sorted(set(residents) - set(allocations_paise)),
                         "unexpected_resident_ids": sorted(set(allocations_paise) - set(residents))})
        shares = dict(allocations_paise)
        basis_source = {"method": allocation_method}

    if payer_resident_id and payer_resident_id not in residents:
        raise ValidationError("Payer must belong to this apartment.",
                              details={"field": "payer_resident_id",
                                       "value": payer_resident_id})
    if expense_definition_id:
        row = conn.execute(
            "SELECT apartment_id, is_active FROM expense_definition WHERE expense_definition_id=?",
            (expense_definition_id,)).fetchone()
        if not row or row["apartment_id"] != apartment_id:
            raise ValidationError("Expense definition must belong to this apartment.",
                                  details={"field": "expense_definition_id"})
    if statement_id:
        require_draft_statement(conn, statement_id, apartment_id)

    rule, resolved_service, resolved_settlement = _resolve_expense_periods(
        conn, expense_definition_id=expense_definition_id, period_rule=period_rule,
        expense_date=expense_date, expense_period=expense_period,
        service_period=service_period, settlement_period=settlement_period)
    resolved_due = due_date or default_due_date(resolved_settlement)
    parse_iso_date(resolved_due, "due_date")

    if statement_id:
        statement_month = conn.execute(
            "SELECT settlement_month FROM monthly_statement WHERE statement_id=?",
            (statement_id,)).fetchone()[0]
        if statement_month != resolved_settlement:
            raise ValidationError(
                "This expense settles in a different month from the statement it is "
                "being attached to.",
                code="settlement_period_mismatch",
                details={"statement_settlement_month": statement_month,
                         "expense_settlement_period": resolved_settlement,
                         "hint": "Change the period rule or the periods, or attach the "
                                 "expense to the statement for its own settlement month."})

    component_id = new_id("component")
    basis = dict(allocation_basis) if allocation_basis else basis_source
    basis.setdefault("method", allocation_method)
    basis_json = json.dumps(basis, sort_keys=True, default=str)
    try:
        conn.execute(
            """INSERT INTO expense_instance
               (expense_id, expense_definition_id, statement_id, description, amount_paise,
                expense_date, expense_period, payer_resident_id, provider_name, is_settled,
                created_at, service_period, settlement_period, period_rule, due_date,
                allocation_method, expense_kind, apartment_id, reference, notes,
                generated_from_recurrence, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,0,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (expense_id.strip(), expense_definition_id, statement_id, description.strip(),
             amount_paise, expense_date, expense_period or resolved_settlement,
             payer_resident_id, optional_text(provider_name, "provider_name", max_length=200),
             timestamp, resolved_service, resolved_settlement, rule, resolved_due,
             allocation_method, expense_kind, apartment_id,
             optional_text(reference, "reference", max_length=200),
             optional_text(notes, "notes"), 1 if generated_from_recurrence else 0,
             timestamp))
        conn.execute(
            """INSERT INTO expense_component
               (component_id, expense_id, name, amount_paise, allocation_method, host_resident_id)
               VALUES (?,?,?,?,?,NULL)""",
            (component_id, expense_id.strip(), description.strip(), amount_paise,
             allocation_method))
        for resident_id in residents:
            amount = shares[resident_id]
            if amount == 0:
                # A zero share is a recorded decision, not a charge. It is
                # kept in the allocation basis rather than as a 0-paise row.
                continue
            conn.execute(
                """INSERT INTO allocation
                   (allocation_id, expense_id, component_id, resident_id, amount_paise,
                    allocation_basis_json)
                   VALUES (?,?,?,?,?,?)""",
                (new_id("allocation"), expense_id.strip(), component_id, resident_id,
                 amount, basis_json))
    except sqlite3.IntegrityError as exc:
        message = str(exc)
        if "idx_recurrence_unique" in message:
            raise ConflictError(
                "A recurring expense for this definition and settlement month already "
                "exists. Generation never creates a duplicate.",
                code="duplicate_recurring_expense",
                details={"expense_definition_id": expense_definition_id,
                         "settlement_period": resolved_settlement}) from exc
        raise ValidationError(f"Could not save expense: {exc}") from exc
    except sqlite3.Error as exc:
        raise ValidationError(f"Could not save expense: {exc}") from exc

    audit.record(conn, entity_type="expense", entity_id=expense_id.strip(),
                 action="create", actor=actor, apartment_id=apartment_id,
                 new_value={"description": description.strip(),
                            "amount_paise": amount_paise,
                            "settlement_period": resolved_settlement,
                            "service_period": resolved_service,
                            "payer_resident_id": payer_resident_id,
                            "allocation_method": allocation_method,
                            "shares_paise": {k: v for k, v in shares.items() if v}})
    return {
        "expense_id": expense_id.strip(),
        "amount_paise": amount_paise,
        "allocations_paise": dict(shares),
        "component_id": component_id,
        "period_rule": rule,
        "service_period": resolved_service,
        "settlement_period": resolved_settlement,
        "due_date": resolved_due,
        "allocation_basis": basis,
    }


def create_expense(
    db_path, *, apartment_id: str, expense_id: str, description: str, amount_paise: int,
    expense_date: str, expense_period: str | None = None,
    allocations_paise: Mapping[str, int] | None = None,
    payer_resident_id: str | None = None, provider_name: str | None = None,
    expense_definition_id: str | None = None, statement_id: str | None = None,
    allocation_method: str = "custom", allocation_basis: Mapping[str, Any] | None = None,
    created_at: str | None = None,
    # Added after the prototype:
    period_rule: str | None = None, service_period: str | None = None,
    settlement_period: str | None = None, due_date: str | None = None,
    reference: str | None = None, notes: str | None = None,
    expense_kind: str = "general", generated_from_recurrence: bool = False,
    participants: Sequence[str] | None = None, weights: Mapping[str, int] | None = None,
    actor: str = "operator",
) -> dict[str, Any]:
    """Create one expense with its resident allocations, atomically.

    ``allocations_paise`` names the exact share of every resident of the
    apartment; a resident who does not take part is given ``0``. Supply it
    directly, or let the planner build it by passing ``allocation_method`` of
    ``equal``/``weighted`` with ``participants``/``weights``.

    Zero shares are not written as allocation rows -- they carry no financial
    meaning -- but they are validated and reported, so an incomplete allocation
    is always rejected rather than quietly accepted.
    """
    with transaction(db_path) as conn:
        return _create_expense(
            conn, apartment_id=apartment_id, expense_id=expense_id, description=description,
            amount_paise=amount_paise, expense_date=expense_date, expense_period=expense_period,
            allocations_paise=allocations_paise, payer_resident_id=payer_resident_id,
            provider_name=provider_name, expense_definition_id=expense_definition_id,
            statement_id=statement_id, allocation_method=allocation_method,
            allocation_basis=allocation_basis, created_at=created_at, period_rule=period_rule,
            service_period=service_period, settlement_period=settlement_period, due_date=due_date,
            reference=reference, notes=notes, expense_kind=expense_kind,
            generated_from_recurrence=generated_from_recurrence, participants=participants,
            weights=weights, actor=actor)


def _reallocate(conn: sqlite3.Connection, expense: dict[str, Any], *,
                shares: Mapping[str, int], method: str,
                basis: Mapping[str, Any]) -> None:
    component = conn.execute(
        "SELECT component_id FROM expense_component WHERE expense_id=? ORDER BY rowid",
        (expense["expense_id"],)).fetchall()
    if len(component) != 1:
        raise ConflictError(
            "This expense has itemised components, so its allocations cannot be replaced "
            "here. Edit it through its own module (electricity bills are edited as bills).",
            code="component_expense_not_editable",
            details={"expense_id": expense["expense_id"], "component_count": len(component)})
    component_id = component[0][0]
    basis_json = json.dumps(dict(basis), sort_keys=True, default=str)
    applied = conn.execute(
        """SELECT COALESCE(SUM(pa.amount_paise), 0) FROM payment_application pa
           JOIN allocation a ON a.allocation_id = pa.allocation_id
           WHERE a.expense_id = ?""", (expense["expense_id"],)).fetchone()[0]
    if applied:
        raise ConflictError(
            "Payments have already been applied to this expense's charges, so its "
            "allocations cannot be replaced. Reverse the payment first, or record a "
            "ledger adjustment.",
            code="allocations_have_payments",
            details={"expense_id": expense["expense_id"], "applied_paise": applied})
    conn.execute("DELETE FROM allocation WHERE expense_id=?", (expense["expense_id"],))
    conn.execute(
        "UPDATE expense_component SET amount_paise=?, allocation_method=? WHERE component_id=?",
        (expense["amount_paise"], method, component_id))
    for resident_id, amount in sorted(shares.items()):
        if amount == 0:
            continue
        conn.execute(
            """INSERT INTO allocation
               (allocation_id, expense_id, component_id, resident_id, amount_paise,
                allocation_basis_json)
               VALUES (?,?,?,?,?,?)""",
            (new_id("allocation"), expense["expense_id"], component_id, resident_id,
             amount, basis_json))


def update_expense(
    db_path, expense_id: str, *, apartment_id: str | None = None, reason: str | None = None,
    actor: str = "operator", allocations_paise: Mapping[str, int] | None = None,
    allocation_method: str | None = None, participants: Sequence[str] | None = None,
    weights: Mapping[str, int] | None = None, now: Any = None, **changes: Any,
) -> dict[str, Any]:
    """Edit an expense.

    Allowed while the expense's statement is a draft, or while it is inside its
    72-hour correction window -- in which case a ``reason`` is required and the
    edit is recorded as a numbered statement revision. Once the statement is
    closed the expense is immutable and the correction must be a ledger
    adjustment.
    """
    unknown = sorted(set(changes) - set(EDITABLE_FIELDS))
    if unknown:
        raise ValidationError(f"Unsupported expense fields: {', '.join(unknown)}.",
                              details={"unsupported_fields": unknown,
                                       "editable_fields": EDITABLE_FIELDS})
    from . import statements  # local import: statements builds snapshots of expenses

    with transaction(db_path) as conn:
        before = get_expense(conn, expense_id, apartment_id)
        owning_apartment = before.get("apartment_id") or apartment_id
        mode = assert_not_locked(conn, before["statement_id"], "expense")
        if mode == "revision" and not (reason or "").strip():
            raise ValidationError(
                "This expense belongs to a finalized statement inside its correction "
                "window. A reason is required so the change is recorded as a revision.",
                code="revision_reason_required",
                details={"statement_id": before["statement_id"]})

        residents = resident_ids(conn, owning_apartment)
        after = dict(before)
        if "description" in changes:
            after["description"] = require_text(changes["description"], "description",
                                                max_length=500)
        if "amount_paise" in changes:
            after["amount_paise"] = ensure_paise(changes["amount_paise"], "amount_paise")
        if "expense_date" in changes:
            parse_iso_date(changes["expense_date"], "expense_date")
            after["expense_date"] = changes["expense_date"]
        for field in ("service_period", "settlement_period"):
            if field in changes and changes[field] is not None:
                after[field] = normalize_month(changes[field], field)
        if "due_date" in changes and changes["due_date"]:
            parse_iso_date(changes["due_date"], "due_date")
            after["due_date"] = changes["due_date"]
        if "payer_resident_id" in changes:
            value = changes["payer_resident_id"]
            if value and value not in residents:
                raise ValidationError("Payer must belong to this apartment.",
                                      details={"field": "payer_resident_id"})
            after["payer_resident_id"] = value or None
        for field, max_length in (("provider_name", 200), ("reference", 200), ("notes", 2000)):
            if field in changes:
                after[field] = optional_text(changes[field], field, max_length=max_length)
        if "expense_definition_id" in changes:
            value = changes["expense_definition_id"]
            if value:
                row = conn.execute(
                    "SELECT apartment_id FROM expense_definition WHERE expense_definition_id=?",
                    (value,)).fetchone()
                if not row or row[0] != owning_apartment:
                    raise ValidationError("Expense definition must belong to this apartment.",
                                          details={"field": "expense_definition_id"})
            after["expense_definition_id"] = value or None
        if "statement_id" in changes:
            value = changes["statement_id"]
            if value:
                statement = require_draft_statement(conn, value, owning_apartment)
                if statement["settlement_month"] != after["settlement_period"]:
                    raise ValidationError(
                        "This expense settles in a different month from that statement.",
                        code="settlement_period_mismatch",
                        details={"statement_settlement_month": statement["settlement_month"],
                                 "expense_settlement_period": after["settlement_period"]})
            after["statement_id"] = value or None

        after["updated_at"] = now_timestamp()
        conn.execute(
            """UPDATE expense_instance SET description=?, amount_paise=?, expense_date=?,
                   service_period=?, settlement_period=?, due_date=?, payer_resident_id=?,
                   provider_name=?, reference=?, notes=?, statement_id=?,
                   expense_definition_id=?, updated_at=?
               WHERE expense_id=?""",
            (after["description"], after["amount_paise"], after["expense_date"],
             after["service_period"], after["settlement_period"], after["due_date"],
             after["payer_resident_id"], after["provider_name"], after["reference"],
             after["notes"], after["statement_id"], after["expense_definition_id"],
             after["updated_at"], expense_id))

        method = allocation_method or before.get("allocation_method") or "custom"
        needs_reallocation = (allocations_paise is not None or allocation_method is not None
                              or participants is not None or weights is not None
                              or after["amount_paise"] != before["amount_paise"])
        if needs_reallocation:
            if allocations_paise is not None:
                if set(allocations_paise) != set(residents):
                    raise ValidationError(
                        "Allocations must contain exactly one amount for every apartment resident.",
                        code="allocation_resident_mismatch",
                        details={"expected_resident_ids": residents,
                                 "provided_resident_ids": sorted(allocations_paise)})
                for value in allocations_paise.values():
                    ensure_paise(value, "allocation amount")
                if sum(allocations_paise.values()) != after["amount_paise"]:
                    raise ValidationError(
                        "Resident allocations must sum exactly to the expense amount.",
                        code="allocation_total_mismatch",
                        details={"expected_paise": after["amount_paise"],
                                 "provided_paise": sum(allocations_paise.values())})
                shares = dict(allocations_paise)
                basis: dict[str, Any] = {"method": method}
                if method not in ALLOCATION_METHODS:
                    raise ValidationError(f"Unsupported allocation method: {method}")
            else:
                plan_method = method if method in ("equal", "weighted", "custom") else "equal"
                if plan_method == "custom":
                    # The amount changed but no new shares were given; refuse rather
                    # than silently re-spreading money the operator allocated by hand.
                    raise ValidationError(
                        "This expense uses custom shares. Supply allocations_paise that sum "
                        "to the new amount, or choose an allocation method.",
                        code="allocations_required",
                        details={"expense_id": expense_id,
                                 "amount_paise": after["amount_paise"]})
                existing_participants = participants
                if existing_participants is None and plan_method == "equal":
                    existing_participants = sorted(
                        row[0] for row in conn.execute(
                            "SELECT DISTINCT resident_id FROM allocation WHERE expense_id=?",
                            (expense_id,)).fetchall()) or residents
                plan = plan_allocation(amount_paise=after["amount_paise"], method=plan_method,
                                       all_residents=residents,
                                       participants=existing_participants, weights=weights,
                                       payer_resident_id=after["payer_resident_id"])
                shares, basis = plan.shares_paise, dict(plan.basis)
                method = plan_method
            _reallocate(conn, after, shares=shares, method=method, basis=basis)
            conn.execute("UPDATE expense_instance SET allocation_method=? WHERE expense_id=?",
                         (method, expense_id))
            after["allocation_method"] = method
            audit.record(conn, entity_type="expense", entity_id=expense_id,
                         action="reallocate", actor=actor, apartment_id=owning_apartment,
                         reason=reason, field_name="allocations",
                         new_value={k: v for k, v in shares.items() if v})

        audit.record_field_changes(
            conn, entity_type="expense", entity_id=expense_id, before=before, after=after,
            fields=EDITABLE_FIELDS, actor=actor, apartment_id=owning_apartment, reason=reason)

        if mode == "revision":
            statements.record_revision(
                conn, statement_id=before["statement_id"], reason=reason.strip(),
                created_by=actor, now=now,
                change_note=f"expense {expense_id} edited during the correction window")
        return get_expense(conn, expense_id)


def delete_expense(db_path, expense_id: str, *, apartment_id: str | None = None,
                   reason: str | None = None, actor: str = "operator") -> dict[str, Any]:
    """Remove an expense. Only possible while it is unattached or in a draft.

    Finalized and closed statements keep their expenses; corrections there are
    revisions or ledger adjustments, never deletions.
    """
    with transaction(db_path) as conn:
        expense = get_expense(conn, expense_id, apartment_id)
        if expense["statement_id"]:
            statement = conn.execute(
                "SELECT status FROM monthly_statement WHERE statement_id=?",
                (expense["statement_id"],)).fetchone()
            if statement[0] != "draft":
                raise ConflictError(
                    "Expenses can only be removed while their statement is a draft.",
                    code="statement_not_draft",
                    details={"statement_id": expense["statement_id"], "status": statement[0]})
        applied = conn.execute(
            """SELECT COALESCE(SUM(pa.amount_paise), 0) FROM payment_application pa
               JOIN allocation a ON a.allocation_id = pa.allocation_id
               WHERE a.expense_id = ?""", (expense_id,)).fetchone()[0]
        if applied:
            raise ConflictError(
                "Payments have been applied to this expense's charges. Reverse those "
                "payments first so the audit trail stays intact.",
                code="allocations_have_payments",
                details={"expense_id": expense_id, "applied_paise": applied})
        linked = conn.execute(
            "SELECT COUNT(*) FROM payment WHERE expense_id=?", (expense_id,)).fetchone()[0]
        if linked:
            raise ConflictError(
                "A payment is linked to this expense as its funding. Unlink or reverse "
                "that payment first.",
                code="expense_has_linked_payment",
                details={"expense_id": expense_id, "linked_payments": linked})
        rent_cycle = conn.execute(
            "SELECT cycle_id, settlement_month, disbursement_status FROM rent_cycle "
            "WHERE expense_id=?", (expense_id,)).fetchone()
        if rent_cycle is not None:
            if rent_cycle["disbursement_status"] != "pending":
                raise ConflictError(
                    f"This is the rent charge for {rent_cycle['settlement_month']}, and the "
                    "landlord payout for it has been recorded. It cannot be removed.",
                    code="rent_cycle_disbursed",
                    details={"expense_id": expense_id, "cycle_id": rent_cycle["cycle_id"]})
            # A pending rent cycle is only a wrapper around this expense, so it
            # goes with it; the month's rent can then be generated afresh.
            conn.execute("DELETE FROM rent_cycle WHERE cycle_id=?", (rent_cycle["cycle_id"],))
            audit.record(conn, entity_type="rent_cycle", entity_id=rent_cycle["cycle_id"],
                         action="delete", actor=actor, apartment_id=expense.get("apartment_id"),
                         reason=reason,
                         old_value={"settlement_month": rent_cycle["settlement_month"],
                                    "expense_id": expense_id})
        conn.execute("DELETE FROM allocation WHERE expense_id=?", (expense_id,))
        conn.execute("DELETE FROM smart_plug_reading WHERE expense_id=?", (expense_id,))
        conn.execute("DELETE FROM electricity_bill WHERE expense_id=?", (expense_id,))
        conn.execute("DELETE FROM expense_component WHERE expense_id=?", (expense_id,))
        conn.execute("DELETE FROM expense_instance WHERE expense_id=?", (expense_id,))
        audit.record(conn, entity_type="expense", entity_id=expense_id, action="delete",
                     actor=actor, apartment_id=expense.get("apartment_id"), reason=reason,
                     old_value={k: expense[k] for k in
                                ("description", "amount_paise", "expense_date",
                                 "settlement_period")})
        return {"expense_id": expense_id, "deleted": True}


def read_expense(db_path, expense_id: str) -> dict[str, Any]:
    with connect(db_path, read_only=True) as conn:
        expense = get_expense(conn, expense_id)
        expense["components"] = db.fetch_all(
            conn, "SELECT * FROM expense_component WHERE expense_id=? ORDER BY rowid",
            (expense_id,))
        expense["allocations"] = db.fetch_all(
            conn,
            """SELECT a.*, r.display_name,
                      (SELECT COALESCE(SUM(pa.amount_paise), 0) FROM payment_application pa
                        WHERE pa.allocation_id = a.allocation_id) AS settled_paise
                 FROM allocation a JOIN resident r ON r.resident_id = a.resident_id
                WHERE a.expense_id = ? ORDER BY r.display_name, a.rowid""",
            (expense_id,))
        for row in expense["allocations"]:
            row["outstanding_paise"] = row["amount_paise"] - row["settled_paise"]
            row["allocation_basis"] = json.loads(row["allocation_basis_json"]) \
                if row.get("allocation_basis_json") else None
        expense["linked_payments"] = db.fetch_all(
            conn, "SELECT * FROM payment WHERE expense_id=? ORDER BY payment_date, payment_id",
            (expense_id,))
        expense["readings"] = db.fetch_all(
            conn, "SELECT * FROM smart_plug_reading WHERE expense_id=? ORDER BY rowid",
            (expense_id,))
        expense["electricity_bill"] = db.fetch_one(
            conn, "SELECT * FROM electricity_bill WHERE expense_id=?", (expense_id,))
        expense["statement"] = db.fetch_one(
            conn, "SELECT statement_id, settlement_month, status FROM monthly_statement "
                  "WHERE statement_id=?", (expense["statement_id"],)) \
            if expense["statement_id"] else None
        return expense


def list_expenses(
    db_path, *, apartment_id: str, settlement_period: str | None = None,
    service_period: str | None = None, statement_id: str | None = None,
    resident_id: str | None = None, expense_definition_id: str | None = None,
    expense_kind: str | None = None, category: str | None = None,
    search: str | None = None, date_from: str | None = None, date_to: str | None = None,
    unattached_only: bool = False, limit: int | None = 50, offset: int | None = 0,
    sort: str = "expense_date_desc",
) -> dict[str, Any]:
    limit, offset = paginate(limit, offset)
    clauses = ["e.apartment_id = ?"]
    params: list[Any] = [apartment_id]
    if settlement_period:
        clauses.append("e.settlement_period = ?")
        params.append(normalize_month(settlement_period, "settlement_period"))
    if service_period:
        clauses.append("e.service_period = ?")
        params.append(normalize_month(service_period, "service_period"))
    if statement_id:
        clauses.append("e.statement_id = ?")
        params.append(statement_id)
    if unattached_only:
        clauses.append("e.statement_id IS NULL")
    if expense_definition_id:
        clauses.append("e.expense_definition_id = ?")
        params.append(expense_definition_id)
    if expense_kind:
        clauses.append("e.expense_kind = ?")
        params.append(expense_kind)
    if category:
        clauses.append("d.category = ?")
        params.append(category)
    if resident_id:
        clauses.append("EXISTS (SELECT 1 FROM allocation a WHERE a.expense_id = e.expense_id "
                       "AND a.resident_id = ?)")
        params.append(resident_id)
    if date_from:
        clauses.append("e.expense_date >= ?")
        params.append(parse_iso_date(date_from, "date_from").isoformat())
    if date_to:
        clauses.append("e.expense_date <= ?")
        params.append(parse_iso_date(date_to, "date_to").isoformat())
    if search:
        clauses.append("(e.description LIKE ? OR COALESCE(e.provider_name,'') LIKE ? "
                       "OR COALESCE(e.reference,'') LIKE ? OR COALESCE(e.notes,'') LIKE ?)")
        pattern = f"%{search.strip()}%"
        params.extend([pattern] * 4)

    orders = {
        "expense_date_desc": "e.expense_date DESC, e.rowid DESC",
        "expense_date_asc": "e.expense_date ASC, e.rowid ASC",
        "amount_desc": "e.amount_paise DESC, e.rowid DESC",
        "amount_asc": "e.amount_paise ASC, e.rowid ASC",
        "description_asc": "e.description ASC, e.rowid ASC",
    }
    if sort not in orders:
        raise ValidationError(f"Unsupported sort: {sort}",
                              details={"field": "sort", "allowed": sorted(orders)})
    where = " AND ".join(clauses)
    base = f"""
        FROM expense_instance e
        LEFT JOIN expense_definition d ON d.expense_definition_id = e.expense_definition_id
        LEFT JOIN monthly_statement s ON s.statement_id = e.statement_id
        LEFT JOIN resident r ON r.resident_id = e.payer_resident_id
        WHERE {where}
    """
    with connect(db_path, read_only=True) as conn:
        get_apartment(conn, apartment_id)
        total = int(conn.execute(f"SELECT COUNT(*) {base}", tuple(params)).fetchone()[0])
        total_amount = int(conn.execute(
            f"SELECT COALESCE(SUM(e.amount_paise), 0) {base}", tuple(params)).fetchone()[0])
        rows = db.fetch_all(
            conn,
            f"""SELECT e.*, d.name AS definition_name, d.category AS category,
                       s.settlement_month AS statement_month, s.status AS statement_status,
                       r.display_name AS payer_display_name,
                       (SELECT COUNT(*) FROM allocation a WHERE a.expense_id = e.expense_id)
                           AS allocation_count,
                       (SELECT COALESCE(SUM(a.amount_paise), 0) FROM allocation a
                         WHERE a.expense_id = e.expense_id) AS allocated_paise,
                       (SELECT COALESCE(SUM(p.amount_paise), 0) FROM payment p
                         WHERE p.expense_id = e.expense_id
                           AND p.payment_kind = 'provider_advance'
                           AND p.status = 'active') AS linked_advance_paise
                  {base}
                ORDER BY {orders[sort]} LIMIT ? OFFSET ?""",
            params + [limit, offset])
    return {"items": rows, "total": total, "total_amount_paise": total_amount,
            "limit": limit, "offset": offset}

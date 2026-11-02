"""The water supply: a fixed monthly contract, such as a society's pipeline connection.

The flat pays one fixed rate a month, whatever it uses. Each month is billed as
one ordinary expense under the household's water definition and split equally
between the residents taking part, any paise that do not divide going one each
to them in resident order -- so the shares always add up to the rate exactly.

**Nothing prorates**, as everywhere in the household: a resident who moves in
joins the open month's bill, and one who moves out keeps their full share of
the months they were in.

The month is paid in two steps, as every household service's is (see
:mod:`aem.services.service_bills`): flatmates pay their shares into the House
Kitty, and the supplier is paid from it, in one payout or several. A resident
who pays the supplier from their own account pays their own share through the
kitty, and the kitty pays back what they paid beyond it.

While the month is open its bill follows the contract and the flat: a new rate,
or a resident moving in, re-splits it, and what each resident paid moves to
their new share (see :mod:`aem.services.bill_shares`). It freezes for good once
the month is over or its statement is finalized.
"""
from __future__ import annotations

import calendar
import json
from typing import Any, Mapping, Sequence

from ..errors import AemError, ConflictError, NotFoundError, ValidationError
from ..ids import new_id, now_timestamp
from ..money import ensure_paise, format_inr
from ..periods import month_label, normalize_month, parse_iso_date
from ..persistence import Database, UnitOfWork
from . import audit, bill_shares, definitions, deletions, expenses, payments, service_bills
from .allocation import plan_allocation
from .common import (
    get_apartment,
    get_resident,
    list_residents,
    optional_text,
    require_text,
    resident_ids,
)

SERVICE_TYPE = "water_tanker"
DEFAULT_NAME = "Water Supply"
DEFAULT_PROVIDER = "Society Water Supply"
#: Where a payout to the supplier came from: the kitty, or a resident's own account.
PAYOUT_MODES = ("pool", "resident_advance")


def _today() -> str:
    return now_timestamp()[:10]


def _current_month() -> str:
    return _today()[:7]


def _billing_date(month: str, day: int | None) -> str | None:
    """The billing day in `month`, pulled back to the month's last day."""
    if not day:
        return None
    year, mon = (int(part) for part in month.split("-"))
    return f"{month}-{min(day, calendar.monthrange(year, mon)[1]):02d}"


# ----------------------------------------------------------- the service --

def _live(uow: UnitOfWork, apartment_id: str) -> dict[str, Any] | None:
    return uow.water.live_service(apartment_id)


def _require_live(uow: UnitOfWork, apartment_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    service = _live(uow, apartment_id)
    config = uow.water.config(service["service_id"]) if service else None
    if service is None or config is None:
        raise ConflictError("Set up the water supply first.", code="water_not_configured")
    return service, config


def _public_service(service: Mapping[str, Any]) -> dict[str, Any]:
    return {key: service[key] for key in (
        "service_id", "service_type", "display_name", "provider_name", "status",
        "billing_day_of_month", "period_rule", "created_at", "updated_at")}


def _public_config(config: Mapping[str, Any]) -> dict[str, Any]:
    return {key: config[key] for key in (
        "provider_name", "monthly_rate_paise", "consumer_id", "billing_day_of_month",
        "expense_definition_id", "pipeline_notes", "notes", "updated_at")}


# -------------------------------------------------------- the month's bill --

def _month_bill(uow: UnitOfWork, apartment_id: str, config: Mapping[str, Any], month: str,
                include_resident_ids: Sequence[str] = ()) -> dict[str, Any]:
    """What `month` would bill, and to whom. Nothing is written.

    The active residents take part, and ``include_resident_ids`` -- those
    already on the month's bill -- stay on it after they move out. The rate is
    split equally between them: the remainder paise go one each, in resident
    order, so the shares add up to the rate exactly."""
    everyone = resident_ids(uow, apartment_id)
    participants = sorted(set(resident_ids(uow, apartment_id, active_only=True))
                          | (set(include_resident_ids) & set(everyone)))
    total = int(config["monthly_rate_paise"])
    blocked: str | None = None
    shares: dict[str, int] = {}
    basis: dict[str, Any] = {}
    if total <= 0:
        blocked = "The water contract has no monthly rate yet, so there is nothing to bill."
    elif not participants:
        blocked = "Nobody lives in the flat, so there is nobody to share the water bill."
    else:
        plan = plan_allocation(amount_paise=total, method="equal", all_residents=everyone,
                               participants=participants)
        shares = {rid: paise for rid, paise in plan.shares_paise.items() if paise}
        basis = dict(plan.basis)
    count = len(participants)
    return {
        "settlement_month": month, "total_paise": total, "participants": participants,
        "participant_count": count,
        "base_share_paise": total // count if count else 0,
        "remainder_paise": total % count if count else 0,
        "shares": shares, "basis": basis, "blocked_reason": blocked,
    }


def _bill_basis(bill: Mapping[str, Any]) -> dict[str, Any]:
    """Kept with each charge: how the month is split, the rate it was billed at,
    and who is on it -- so a month gone by reads as it was billed."""
    return {**bill["basis"], "source": "water", "monthly_rate_paise": bill["total_paise"],
            "participants": list(bill["participants"])}


def _stored_basis(allocations: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    for row in allocations:
        if row.get("allocation_basis_json"):
            try:
                return json.loads(row["allocation_basis_json"])
            except ValueError:
                return {}
    return {}


def _on_the_bill(allocations: Sequence[Mapping[str, Any]],
                 basis: Mapping[str, Any]) -> list[str]:
    """Who the month's bill already charges: as kept with it, or for a bill
    recorded some other way, whoever it charges anything."""
    return list(basis.get("participants") or
                [row["resident_id"] for row in allocations if row["amount_paise"]])


def _recorded_bill(uow: UnitOfWork, apartment_id: str, definition_id: str | None,
                   month: str) -> dict[str, Any] | None:
    if not definition_id:
        return None
    recorded = uow.furniture.expenses_for_month(apartment_id, definition_id, month)
    return recorded[0] if recorded else None


def _bill_is_live(month: str, statement: Mapping[str, Any] | None) -> bool:
    """Whether a recorded bill still follows the contract and the flat."""
    return month >= _current_month() and (statement is None or statement["status"] == "draft")


def _bill_notes(service: Mapping[str, Any], config: Mapping[str, Any],
                bill: Mapping[str, Any]) -> str:
    return (f"{config['provider_name']}: fixed {format_inr(bill['total_paise'])} a month, "
            f"shared equally by {bill['participant_count']}")[:2000]


def _record_bill(uow: UnitOfWork, apartment_id: str, service: Mapping[str, Any],
                 config: Mapping[str, Any], month: str, bill: Mapping[str, Any],
                 statement: Mapping[str, Any] | None, timestamp: str, actor: str) -> str:
    """Write the month's bill as one expense; returns its ID."""
    expense_id = new_id("water")
    expenses._create_expense(
        uow, apartment_id=apartment_id, expense_id=expense_id,
        description=f"{service['display_name']} for {month_label(month)}",
        amount_paise=bill["total_paise"], expense_date=f"{month}-01",
        allocation_method="equal",
        allocations_paise={rid: bill["shares"].get(rid, 0)
                           for rid in resident_ids(uow, apartment_id)},
        allocation_basis=_bill_basis(bill), provider_name=config["provider_name"],
        expense_definition_id=config["expense_definition_id"],
        statement_id=statement["statement_id"] if statement else None,
        period_rule="current_month", service_period=month, settlement_period=month,
        due_date=_billing_date(month, config["billing_day_of_month"]),
        notes=_bill_notes(service, config, bill), created_at=timestamp, actor=actor)
    audit.record(uow, entity_type="household_service", entity_id=service["service_id"],
                 action="generate", actor=actor, apartment_id=apartment_id,
                 new_value={"settlement_month": month, "expense_id": expense_id,
                            "total_paise": bill["total_paise"],
                            "participants": bill["participants"]})
    return expense_id


def _has_payments(uow: UnitOfWork, expense_id: str,
                  allocations: Sequence[Mapping[str, Any]]) -> bool:
    return bool(sum(row["settled_paise"] for row in allocations)
                or uow.furniture.service_disbursements(expense_id)
                or uow.furniture.linked_advance_total_paise(expense_id)
                or uow.payments.kitty_payments_for_bill(expense_id))


def _sync_live_month_bill(uow: UnitOfWork, apartment_id: str, month: str, *,
                          check_reduction_guards: bool = True, timestamp: str | None = None,
                          actor: str = "operator") -> dict[str, Any] | None:
    """Record `month`'s bill if it is due, or bring the recorded one up to date
    with the contract and the flat; returns the bill as it then stands, if there
    is one.

    A frozen bill is returned as it is, and so is one this module did not record.
    A month is billed by itself once it has
    begun, from the month the supply was set up. Its charges change in place,
    and what each resident paid -- into the kitty, or to the supplier -- follows
    their new share (see :mod:`aem.services.bill_shares`). A change the kitty
    cannot follow -- one leaving it having paid a resident back more than it
    would then owe them, or one taking the bill away after it was paid towards --
    is refused with ``check_reduction_guards`` and otherwise leaves the bill as it
    was. A bill left with nothing to bill, and nothing paid, is removed.
    """
    timestamp = timestamp or now_timestamp()
    service = _live(uow, apartment_id)
    config = uow.water.config(service["service_id"]) if service else None
    if service is None or config is None:
        return None
    definition_id = config["expense_definition_id"]
    statement = uow.furniture.statement_for_month(apartment_id, month)
    expense = _recorded_bill(uow, apartment_id, definition_id, month)
    if expense is not None and not _bill_is_live(month, statement):
        return expense
    if expense is None:
        if (not definition_id or month > _current_month()
                or (statement is not None and statement["status"] != "draft")
                or month < service["created_at"][:7]):
            return None
        bill = _month_bill(uow, apartment_id, config, month)
        if bill["blocked_reason"]:
            return None
        _record_bill(uow, apartment_id, service, config, month, bill, statement, timestamp,
                     actor)
        return _recorded_bill(uow, apartment_id, definition_id, month)

    expense_id = expense["expense_id"]
    allocations = uow.furniture.allocations_for_expense(expense_id)
    basis = _stored_basis(allocations)
    if basis.get("source") != "water":
        # A water expense entered some other way -- a tanker bill, by hand -- is
        # kept as it was entered: the contract never rewrites it.
        return expense
    bill = _month_bill(uow, apartment_id, config, month, _on_the_bill(allocations, basis))
    if bill["blocked_reason"]:
        if _has_payments(uow, expense_id, allocations):
            problem: AemError = ConflictError(
                f"Payments have already been recorded for the {month_label(month)} water bill, "
                "so it cannot be taken away.", code="water_bill_has_payments",
                details={"settlement_month": month, "expense_id": expense_id})
            if check_reduction_guards:
                raise problem
            return expense
        # Nothing to bill any more, and nothing paid: no bill.
        uow.expenses.delete_allocations(expense_id)
        uow.expenses.delete_components(expense_id)
        uow.expenses.delete_expense(expense_id)
        audit.record(uow, entity_type="expense", entity_id=expense_id, action="delete",
                     actor=actor, apartment_id=apartment_id,
                     reason=bill["blocked_reason"],
                     old_value={"settlement_month": month,
                                "amount_paise": expense["amount_paise"]})
        return None

    shares = bill["shares"]
    new_basis = _bill_basis(bill)
    basis_json = json.dumps(new_basis, sort_keys=True, default=str)
    notes = _bill_notes(service, config, bill)
    current = {row["resident_id"]: row for row in allocations}
    if (expense["amount_paise"] == bill["total_paise"] and basis == new_basis
            and expense.get("notes") == notes
            and expense.get("provider_name") == config["provider_name"]
            and {rid: row["amount_paise"] for rid, row in current.items()
                 if row["amount_paise"]} == shares):
        return expense

    month_name = month_label(month)
    rebalance = bill_shares.plan(
        uow, scope="service", apartment_id=apartment_id, expense_id=expense_id,
        shares=shares, charged=list(current),
        tranches=uow.furniture.service_disbursements(expense_id),
        owed={service["service_id"]: bill["total_paise"]},
        names={row["resident_id"]: row["display_name"]
               for row in list_residents(uow, apartment_id)},
        what=f"the {month_name} water bill")
    if rebalance.problem is not None:
        if check_reduction_guards:
            raise rebalance.problem
        return expense
    uow.furniture.update_bill(expense_id, amount_paise=bill["total_paise"], notes=notes,
                              provider_name=config["provider_name"], updated_at=timestamp)
    component_id = uow.expenses.component_ids(expense_id)[0]
    uow.expenses.update_component(component_id, amount_paise=bill["total_paise"],
                                  allocation_method="equal")

    def set_charge(rid: str, amount: int) -> None:
        row = current.get(rid)
        if row is not None and amount:
            uow.furniture.update_allocation(row["allocation_id"], amount_paise=amount,
                                            allocation_basis_json=basis_json)
        elif row is not None:
            uow.furniture.delete_allocation(row["allocation_id"])
        elif amount:
            uow.expenses.insert_allocation(
                allocation_id=new_id("allocation"), expense_id=expense_id,
                component_id=component_id, resident_id=rid, amount_paise=amount,
                allocation_basis_json=basis_json)

    bill_shares.apply(
        uow, rebalance, set_charge=set_charge, payee=lambda group: config["provider_name"],
        share_note=f"{service['display_name']} for {month_name}",
        advance_note=(f"Extra paid for {service['display_name']} ({month_name}) to be "
                      "reimbursed via House Kitty"),
        timestamp=timestamp, actor=actor)
    audit.record(uow, entity_type="expense", entity_id=expense_id, action="sync",
                 actor=actor, apartment_id=apartment_id,
                 old_value={"amount_paise": expense["amount_paise"],
                            "shares": {rid: row["amount_paise"] for rid, row in current.items()}},
                 new_value={"amount_paise": bill["total_paise"], "shares": shares})
    return _recorded_bill(uow, apartment_id, definition_id, month)


# ----------------------------------------------------------------- overview --

def get_overview(db: Database, apartment_id: str, settlement_month: str) -> dict[str, Any]:
    """The water supply, its contract, and what `settlement_month` bills.

    The month's bill is recorded, or brought up to date, as it is looked at.
    ``service`` is null until the supply is set up."""
    month = normalize_month(settlement_month, "month")
    with db.transaction() as uow:
        get_apartment(uow, apartment_id)
        _sync_live_month_bill(uow, apartment_id, month, check_reduction_guards=False)
        return _overview(uow, apartment_id, month)


def _overview(uow: UnitOfWork, apartment_id: str, month: str) -> dict[str, Any]:
    service = _live(uow, apartment_id)
    config = uow.water.config(service["service_id"]) if service else None
    if service is None or config is None:
        return {"service": None, "config": None, "month": None, "next_billing_date": None}
    names = {row["resident_id"]: row["display_name"] for row in list_residents(uow, apartment_id)}
    statement = uow.furniture.statement_for_month(apartment_id, month)
    expense = _recorded_bill(uow, apartment_id, config["expense_definition_id"], month)
    if expense is not None:
        allocations = uow.furniture.allocations_for_expense(expense["expense_id"])
        bill = _month_bill(uow, apartment_id, config, month,
                           _on_the_bill(allocations, _stored_basis(allocations)))
        funding = service_bills.funding(uow, apartment_id, expense)
        # As recorded: a month gone by keeps its bill, whatever the rate since.
        total = expense["amount_paise"]
        charged = {rid: row["share_paise"] for rid, row in funding["charges"].items()
                   if row["share_paise"]}
        count = len(charged)
        bill.update(total_paise=total, participants=sorted(charged), participant_count=count,
                    base_share_paise=total // count if count else 0,
                    remainder_paise=total % count if count else 0, blocked_reason=None)
    else:
        bill = _month_bill(uow, apartment_id, config, month)
        funding = None
        charged = bill["shares"]

    shares = []
    for rid in sorted(set(charged) | set(bill["participants"]),
                      key=lambda r: (names.get(r, r), r)):
        owed = charged.get(rid, 0)
        charge = funding["charges"].get(rid) if funding else None
        paid = charge["settled_paise"] if charge else 0
        advanced = funding["advanced"].get(rid, 0) if funding else 0
        status = service_bills.share_status(owed, paid, advanced) if funding else None
        covered = min(owed, paid + advanced)
        shares.append({
            "resident_id": rid, "display_name": names.get(rid, rid),
            # What they are charged: share = paid + still owed, always.
            "total_paise": owed,
            "paid_paise": covered if funding else 0,
            "settled_paise": paid,
            "outstanding_paise": owed - covered if funding else 0,
            "status": status,
            "paid_date": (funding["advanced_on"][rid] if status == "funded_payout"
                          else charge["last_payment_date"] if paid and charge else None),
            "payments": funding["paid_in"].get(rid, []) if funding else [],
        })
    month_bill = {
        "settlement_month": month,
        "total_paise": bill["total_paise"],
        "participant_count": bill["participant_count"],
        "base_share_paise": bill["base_share_paise"],
        "remainder_paise": bill["remainder_paise"],
        "shares": shares,
        "blocked_reason": bill["blocked_reason"],
        "due_date": _billing_date(month, config["billing_day_of_month"]),
        "expense": expense,
        "statement_status": statement["status"] if statement else None,
        # 'live': billed and following the contract; 'frozen': the month's
        # snapshot; 'upcoming': not begun; 'not_billed': nothing to bill.
        "bill_state": (("live" if _bill_is_live(month, statement) else "frozen") if expense
                       else "upcoming" if month > _current_month() else "not_billed"),
        "transactions_open": deletions.month_is_open(uow, apartment_id, month),
    }
    if funding is not None:
        month_bill.update({key: funding[key] for key in (
            "collected_paise", "collected_count", "payout_status", "disbursed_paise",
            "remaining_disbursement_paise", "disbursements", "reimbursement",
            "reimbursements")})
    else:
        month_bill.update(collected_paise=0, collected_count=0, payout_status="pending",
                          disbursed_paise=0, remaining_disbursement_paise=0, disbursements=[],
                          reimbursement=None, reimbursements=[])
    today = _today()
    due = _billing_date(today[:7], config["billing_day_of_month"])
    if due and due < today:
        year, mon = (int(part) for part in today[:7].split("-"))
        due = _billing_date(f"{year + mon // 12}-{mon % 12 + 1:02d}",
                            config["billing_day_of_month"])
    return {"service": _public_service(service), "config": _public_config(config),
            "month": month_bill, "next_billing_date": due}


# -------------------------------------------------------------------- setup --

def _clean_setup(payload: Mapping[str, Any], before: Mapping[str, Any] | None) -> dict[str, Any]:
    merged = {**(before or {}), **{key: value for key, value in payload.items()
                                   if value is not None or key in ("consumer_id",
                                                                   "billing_day_of_month",
                                                                   "pipeline_notes", "notes")}}
    day = merged.get("billing_day_of_month")
    if day is not None and (isinstance(day, bool) or not isinstance(day, int)
                            or not 1 <= day <= 31):
        raise ValidationError("billing_day_of_month must be a day from 1 to 31.",
                              details={"field": "billing_day_of_month"})
    rate = merged.get("monthly_rate_paise")
    return {
        "display_name": require_text(merged.get("display_name") or DEFAULT_NAME,
                                     "display_name", max_length=100),
        "provider_name": require_text(merged.get("provider_name") or DEFAULT_PROVIDER,
                                      "provider_name", max_length=100),
        "monthly_rate_paise": ensure_paise(0 if rate is None else rate, "monthly_rate_paise"),
        "consumer_id": optional_text(merged.get("consumer_id"), "consumer_id", max_length=100),
        "billing_day_of_month": day,
        "pipeline_notes": optional_text(merged.get("pipeline_notes"), "pipeline_notes",
                                        max_length=2000),
        "notes": optional_text(merged.get("notes"), "notes", max_length=2000),
    }


def setup_water(db: Database, apartment_id: str, payload: Mapping[str, Any], *,
                settlement_month: str | None = None,
                actor: str = "operator") -> dict[str, Any]:
    """Set up the flat's water supply, or change its contract. Only the fields
    sent change.

    Each month's bill is recorded under the household's water definition; one is
    created when the household has none. A new rate moves the open month's bill
    with it -- refused if the kitty could not follow (see
    :func:`_sync_live_month_bill`). Returns the overview of `settlement_month`,
    or of this month."""
    month = normalize_month(settlement_month or _current_month(), "month")
    timestamp = now_timestamp()
    with db.read() as uow:
        get_apartment(uow, apartment_id)
        service = _live(uow, apartment_id)
        config = uow.water.config(service["service_id"]) if service else None
        before = {**(config or {}), **(service or {})} if service else None
        cleaned = _clean_setup(payload, before)
        definition_id = ((config or {}).get("expense_definition_id")
                         or uow.water.find_definition_id(apartment_id))
        id_taken = definition_id is None and uow.water.definition_id_taken("water")

    if definition_id is None:
        definition_id = definitions.create_expense_definition(
            db, apartment_id=apartment_id,
            definition_id=new_id("water") if id_taken else "water",
            name="Water", category="Utilities", period_rule="current_month",
            default_allocation_method="equal", is_recurring=False, recurrence="none",
            default_provider_name=cleaned["provider_name"], sort_order=70,
            notes="Created by the water supply. Each month's bill is generated from the "
                  "Water screen.", actor=actor)["expense_definition_id"]

    config_fields = {key: cleaned[key] for key in (
        "provider_name", "monthly_rate_paise", "consumer_id", "billing_day_of_month",
        "pipeline_notes", "notes")}
    with db.transaction() as uow:
        service = _live(uow, apartment_id)
        if service is None:
            service_id = new_id("svc")
            uow.water.insert_service(
                service_id=service_id, apartment_id=apartment_id,
                display_name=cleaned["display_name"], provider_name=cleaned["provider_name"],
                billing_day_of_month=cleaned["billing_day_of_month"], created_at=timestamp)
            uow.water.insert_config(service_id=service_id, apartment_id=apartment_id,
                                    created_at=timestamp,
                                    expense_definition_id=definition_id, **config_fields)
            audit.record(uow, entity_type="household_service", entity_id=service_id,
                         action="create", actor=actor, apartment_id=apartment_id,
                         new_value={"service_type": SERVICE_TYPE,
                                    "display_name": cleaned["display_name"], **config_fields})
        else:
            service_id = service["service_id"]
            before_config = uow.water.config(service_id) or {}
            uow.water.update_service(
                service_id, display_name=cleaned["display_name"],
                provider_name=cleaned["provider_name"],
                billing_day_of_month=cleaned["billing_day_of_month"], updated_at=timestamp)
            uow.water.update_config(service_id, updated_at=timestamp,
                                    expense_definition_id=definition_id, **config_fields)
            changed = {key: value for key, value in
                       {"display_name": cleaned["display_name"], **config_fields}.items()
                       if {**before_config, **service}.get(key) != value}
            if changed:
                audit.record(uow, entity_type="household_service", entity_id=service_id,
                             action="update", actor=actor, apartment_id=apartment_id,
                             old_value={key: {**before_config, **service}.get(key)
                                        for key in changed},
                             new_value=changed)
        # The open month follows the contract.
        _sync_live_month_bill(uow, apartment_id, _current_month(), timestamp=timestamp,
                              actor=actor)
        return _overview(uow, apartment_id, month)


# ----------------------------------------------------------- paying it -- #

def _require_recorded_bill(uow: UnitOfWork, apartment_id: str, config: Mapping[str, Any],
                           month: str) -> dict[str, Any]:
    expense = _recorded_bill(uow, apartment_id, config["expense_definition_id"], month)
    if expense is None:
        raise ConflictError(f"The {month_label(month)} water bill is not recorded yet.",
                            code="water_bill_not_recorded",
                            details={"settlement_month": month})
    return expense


def record_payment(db: Database, apartment_id: str, payload: Mapping[str, Any], *,
                   actor: str = "operator") -> dict[str, Any]:
    """Record a resident paying their share of the month's water bill into the kitty.

    ``payload``: ``settlement_month``, ``resident_id``, ``amount_paise``,
    ``payment_date``, and optionally ``payment_mode``, ``reference`` and ``note``.
    The payment settles their share of this bill first, up to what is still
    owed on it; anything beyond goes to their other charges, oldest first, and
    what is left after that stays a credit. A share already paid, or covered by
    their paying the supplier, is refused.
    """
    month = normalize_month(payload.get("settlement_month"), "settlement_month")
    resident_id = require_text(payload.get("resident_id"), "resident_id", max_length=64)
    amount = ensure_paise(payload.get("amount_paise"), "amount_paise", allow_zero=False)
    payment_date = parse_iso_date(payload.get("payment_date"), "payment_date").isoformat()
    timestamp = now_timestamp()
    with db.transaction() as uow:
        get_apartment(uow, apartment_id)
        name = get_resident(uow, resident_id, apartment_id)["display_name"]
        service, config = _require_live(uow, apartment_id)
        _sync_live_month_bill(uow, apartment_id, month, check_reduction_guards=False,
                              timestamp=timestamp, actor=actor)
        expense = _require_recorded_bill(uow, apartment_id, config, month)
        charges = uow.furniture.resident_charges_for_expense(expense["expense_id"], resident_id)
        if not charges:
            raise ValidationError(
                f"{name} has no share of the {month_label(month)} water bill.",
                code="not_a_water_participant",
                details={"field": "resident_id", "resident_id": resident_id})
        target = next((charge for charge in charges if charge["outstanding_paise"] > 0), None)
        if target is None:
            raise ConflictError(
                f"{name} has already paid their share of the {month_label(month)} water bill.",
                code="water_share_already_paid", details={"resident_id": resident_id})
        advanced = sum(tranche["amount_paise"]
                       for tranche in uow.furniture.service_disbursements(expense["expense_id"])
                       if tranche["funding_resident_id"] == resident_id)
        if advanced and advanced >= sum(charge["outstanding_paise"] for charge in charges):
            raise ConflictError(
                f"{name} paid {config['provider_name']} the {month_label(month)} bill, which "
                "already covers their share; the kitty owes them the rest.",
                code="water_share_funded_by_payout", details={"resident_id": resident_id})
        payments._record_payment(
            uow, apartment_id=apartment_id, payment_id=new_id("payment"),
            payer_resident_id=resident_id, amount_paise=amount, payment_date=payment_date,
            payment_kind="to_pool", payee_or_provider=None,
            reference=optional_text(payload.get("reference"), "reference", max_length=200),
            note=(optional_text(payload.get("note"), "note")
                  or f"{service['display_name']} for {month_label(month)}"),
            timestamp=timestamp, expense_id=expense["expense_id"], actor=actor,
            target_allocation_id=target["allocation_id"],
            payment_mode=payload.get("payment_mode"))
        return _overview(uow, apartment_id, month)


def disburse_payout(db: Database, apartment_id: str, payload: Mapping[str, Any], *,
                    actor: str = "operator") -> dict[str, Any]:
    """Record a payment to the water supplier towards the month's bill.

    ``payload``: ``settlement_month``, ``disbursement_mode``, ``reference``,
    ``disbursed_date``, and optionally ``amount_paise`` (all that is still owed
    unless given), ``funding_resident_id`` and ``notes``.

    ``pool``
        The kitty paid it, out of what residents paid in. Nothing is added to the
        ledger: the bill is already a cost to the kitty.

    ``resident_advance``
        A resident paid it from their own account. What covers their own open
        share goes through the kitty, as everyone else's does: a payment into the
        kitty settling their share, and a ``pool`` payout of it. Only what they
        paid beyond their share is an advance -- a ``provider_advance`` linked to
        the bill -- which the kitty pays back to them (``reimbursements``).

    A supplier is paid in tranches, each with its own date and bank reference,
    never adding up to more than the bill.
    """
    month = normalize_month(payload.get("settlement_month"), "settlement_month")
    mode = payload.get("disbursement_mode")
    if mode not in PAYOUT_MODES:
        raise ValidationError("Unsupported disbursement_mode.",
                              details={"field": "disbursement_mode",
                                       "allowed": list(PAYOUT_MODES)})
    reference = require_text(payload.get("reference"), "reference", max_length=200)
    disbursed_date = parse_iso_date(payload.get("disbursed_date"), "disbursed_date").isoformat()
    note = optional_text(payload.get("notes"), "notes", max_length=500)
    amount_paise = payload.get("amount_paise")
    if amount_paise is not None:
        amount_paise = ensure_paise(amount_paise, "amount_paise", allow_zero=False)
    funding_resident_id = payload.get("funding_resident_id")
    if mode == "pool" and funding_resident_id:
        raise ValidationError("A payout from the kitty has no funding resident.",
                              details={"field": "funding_resident_id"})
    if mode == "resident_advance" and not funding_resident_id:
        raise ValidationError("Choose the resident who paid the supplier.",
                              details={"field": "funding_resident_id"})
    timestamp = now_timestamp()
    with db.transaction() as uow:
        get_apartment(uow, apartment_id)
        service, config = _require_live(uow, apartment_id)
        _sync_live_month_bill(uow, apartment_id, month, check_reduction_guards=False,
                              timestamp=timestamp, actor=actor)
        expense = _require_recorded_bill(uow, apartment_id, config, month)
        expense_id, total = expense["expense_id"], expense["amount_paise"]
        provider = config["provider_name"]
        tranches = uow.furniture.service_disbursements(expense_id)
        remaining = total - sum(tranche["amount_paise"] for tranche in tranches)
        if remaining <= 0:
            raise ConflictError(
                f"{provider} has already been paid the {month_label(month)} bill in full.",
                code="water_already_disbursed", details={"expense_id": expense_id})
        amount = remaining if amount_paise is None else amount_paise
        if amount > remaining:
            raise ValidationError(
                f"That is more than the {format_inr(remaining)} still owed to {provider} for "
                f"{month_label(month)}.", code="tranche_exceeds_remaining",
                details={"field": "amount_paise", "amount_paise": amount,
                         "remaining_paise": remaining})
        advanced = sum(tranche["amount_paise"] for tranche in tranches
                       if tranche["disbursed_by"] == "resident_advance")
        external = uow.furniture.linked_advance_total_paise(expense_id) - advanced
        if external > 0 or expense["payer_resident_id"]:
            raise ConflictError(
                f"The {month_label(month)} water bill already records a resident as having "
                "paid it, so a payout cannot also be recorded here. Correct the expense, or the "
                "payment linked to it, first.", code="water_already_funded",
                details={"expense_id": expense_id,
                         "payer_resident_id": expense["payer_resident_id"],
                         "linked_advance_paise": external})

        written: list[dict[str, Any]] = []
        if mode == "pool":
            written.append(dict(disbursement_id=new_id("svcdisb"), amount_paise=amount,
                                disbursed_by="pool", funding_resident_id=None,
                                funding_payment_id=None, note=note))
        else:
            name = get_resident(uow, funding_resident_id, apartment_id)["display_name"]
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
                    note=f"{service['display_name']} for {month_label(month)}",
                    timestamp=timestamp, expense_id=expense_id, actor=actor,
                    target_allocation_id=open_charge["allocation_id"])
                share_payout_id = service_bills.share_payout_id(share_payment_id)
                written.append(dict(disbursement_id=share_payout_id,
                                    amount_paise=share_paise, disbursed_by="pool",
                                    funding_resident_id=None, funding_payment_id=None,
                                    note=f"{name}'s allotted share via House Kitty"))
            if extra_paise:
                # Only what they paid beyond their share is an advance, which the
                # kitty pays back out of what the others pay in.
                funding_payment_id = new_id("water-payout")
                payments._record_payment(
                    uow, apartment_id=apartment_id, payment_id=funding_payment_id,
                    payer_resident_id=funding_resident_id, amount_paise=extra_paise,
                    payment_date=disbursed_date, payment_kind="provider_advance",
                    payee_or_provider=provider, reference=reference,
                    note=(f"Extra paid for {service['display_name']} ({month_label(month)}) "
                          "to be reimbursed via House Kitty"),
                    timestamp=timestamp, expense_id=expense_id, actor=actor)
                written.append(dict(disbursement_id=new_id("svcdisb"), amount_paise=extra_paise,
                                    disbursed_by="resident_advance",
                                    funding_resident_id=funding_resident_id,
                                    funding_payment_id=funding_payment_id,
                                    note=note or (f"Extra above {name}'s share "
                                                  "(reimbursable via House Kitty)")))
        for tranche in written:
            uow.furniture.insert_service_disbursement(
                service_id=service["service_id"], expense_id=expense_id,
                apartment_id=apartment_id, settlement_month=month, disbursed_at=disbursed_date,
                disbursement_reference=reference, created_at=timestamp, **tranche)
        audit.record(uow, entity_type="household_service", entity_id=service["service_id"],
                     action="disburse", actor=actor, apartment_id=apartment_id,
                     new_value={"settlement_month": month, "expense_id": expense_id,
                                "amount_paise": amount, "total_paise": total,
                                "remaining_paise": remaining - amount, "disbursed_by": mode,
                                "disbursed_at": disbursed_date,
                                "disbursement_reference": reference,
                                "funding_resident_id": funding_resident_id, "note": note,
                                "tranches": [{key: tranche[key] for key in (
                                    "disbursement_id", "amount_paise", "disbursed_by",
                                    "funding_payment_id")} for tranche in written]})
        return _overview(uow, apartment_id, month)


def reimburse_from_kitty(db: Database, apartment_id: str, payload: Mapping[str, Any], *,
                         actor: str = "operator") -> dict[str, Any]:
    """Pay back, from the kitty, a resident who paid the supplier more than their share.

    ``payload``: ``settlement_month``, ``recipient_resident_id``, ``amount_paise``,
    ``payment_date``, and optionally ``reference`` and ``notes``. It is a
    ``refund`` out of what the kitty collected for the bill: never more than it
    holds for it, nor more than they are still owed. Part can wait until the
    others have paid in."""
    month = normalize_month(payload.get("settlement_month"), "settlement_month")
    recipient = require_text(payload.get("recipient_resident_id"), "recipient_resident_id",
                             max_length=64)
    amount = ensure_paise(payload.get("amount_paise"), "amount_paise", allow_zero=False)
    payment_date = parse_iso_date(payload.get("payment_date"), "payment_date").isoformat()
    timestamp = now_timestamp()
    with db.transaction() as uow:
        get_apartment(uow, apartment_id)
        service, config = _require_live(uow, apartment_id)
        _sync_live_month_bill(uow, apartment_id, month, check_reduction_guards=False,
                              timestamp=timestamp, actor=actor)
        expense = _require_recorded_bill(uow, apartment_id, config, month)
        owing = service_bills.funding(uow, apartment_id, expense)["reimbursements"]
        owed = next((entry for entry in owing
                     if entry["recipient_resident_id"] == recipient), None)
        if owed is None:
            raise NotFoundError(
                f"The kitty owes nobody by that name for the {month_label(month)} water bill.",
                code="water_no_reimbursement",
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
                f"flatmates for this bill that has not already gone to {config['provider_name']} "
                f"or back to {name}.", code="exceeds_kitty_cash",
                details={"field": "amount_paise", "amount_paise": amount,
                         "kitty_cash_available_paise": owed["kitty_cash_available_paise"]})
        payment = payments._record_payment(
            uow, apartment_id=apartment_id,
            payment_id=new_id(service_bills.reimbursement_id_base(expense["expense_id"])),
            payer_resident_id=recipient, amount_paise=amount, payment_date=payment_date,
            payment_kind="refund", payee_or_provider=None,
            reference=optional_text(payload.get("reference"), "reference", max_length=200),
            note=(optional_text(payload.get("notes"), "notes", max_length=500)
                  or f"Reimbursement for {service['display_name']} for {month_label(month)}, "
                     f"paid to {config['provider_name']} on the household's behalf"),
            timestamp=timestamp, expense_id=None, actor=actor)
        audit.record(uow, entity_type="household_service", entity_id=service["service_id"],
                     action="reimburse", actor=actor, apartment_id=apartment_id,
                     new_value={"settlement_month": month, "expense_id": expense["expense_id"],
                                "recipient_resident_id": recipient, "amount_paise": amount,
                                "payment_id": payment["payment_id"],
                                "payment_date": payment_date})
        return _overview(uow, apartment_id, month)

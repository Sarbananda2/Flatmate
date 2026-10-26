"""Furniture rental: the first household service module (Furlenco, Rentomojo).

The subscription, the deposit paid for it, and the items rented are kept
here. Each month the items in the flat are billed as one ordinary expense
under the household's "Furniture rental" definition, so the ledger, the
statements and reconciliation treat it like any other charge and never read
these tables:

* a **common** item (the sofa, the fridge) is split equally between the
  residents taking part, any paise left over going one each in resident order;
* a **resident's** item (their bed, their desk) is charged to them alone.

**Nothing prorates** -- the household's rule for every category. An item counts
in full for any month it was in the flat: delivered by the month's last day and
not returned before its first. An item still awaiting delivery is not billed.

Each month is billed once: the expense is unique for the definition and month,
so a bill already recorded, by hand or by the recurring generator, is refused
rather than duplicated.

**Paying the bill** takes the same two steps as rent. Flatmates pay their
shares into the kitty, each payment settling their share of the bill first.
The provider is then paid the bill, in one payout or several, each kept for
good as a ``service_disbursement`` tranche: from the kitty, or by a resident
from their own account. What a resident paid towards their own share goes
through the kitty like everyone else's: a payment into it and a kitty payout.
Only what they paid beyond their share is a ``provider_advance`` linked to the
month's expense; the kitty owes them that, and pays it back -- out of what the
others paid in for the bill -- as a ``refund``.
"""
from __future__ import annotations

import calendar
import json
from datetime import date
from typing import Any, Mapping, Sequence

from ..errors import AemError, ConflictError, NotFoundError, ValidationError
from ..ids import new_id, now_timestamp
from ..money import ensure_paise, format_inr
from ..periods import month_end, normalize_month, parse_iso_date
from ..persistence import Database, UnitOfWork
from . import audit, definitions, deletions, expenses, payments
from .common import get_apartment, get_resident, list_residents, optional_text, require_text
from .rent import month_label

SERVICE_TYPE = "furniture_rental"
DEFAULT_NAME = "Furniture rental"
CATEGORIES = ("living_room", "bedroom", "appliance", "dining", "workstation", "other")
SCOPES = ("common", "resident")
ITEM_STATUSES = ("active", "pending_delivery", "returned")
#: Statuses of an item still in the flat or on its way: a subscription with
#: any of these cannot be ended.
OPEN_STATUSES = ("active", "pending_delivery")


# ------------------------------------------------------------------ helpers --

def _today() -> str:
    return now_timestamp()[:10]


def _deposit_status(paid: int, refunded: int) -> str:
    if paid > 0 and refunded >= paid:
        return "refunded"
    return "partially_refunded" if refunded > 0 else "held"


def _billing_date(month: str, day: int) -> str:
    """The billing day in `month`, pulled back to the month's last day."""
    year, mon = (int(part) for part in month.split("-"))
    last = calendar.monthrange(year, mon)[1]
    return date(year, mon, min(day, last)).isoformat()


def next_billing_date(day: int | None, today: str) -> str | None:
    """The next billing date on or after `today`, or None with no billing day set."""
    if day is None:
        return None
    this_month = today[:7]
    candidate = _billing_date(this_month, day)
    if candidate >= today:
        return candidate
    year, mon = (int(part) for part in this_month.split("-"))
    following = f"{year + mon // 12}-{mon % 12 + 1:02d}"
    return _billing_date(following, day)


def is_billable(item: Mapping[str, Any], month: str) -> bool:
    """Whether `item` was in the flat at any point in `month`."""
    if item["status"] == "pending_delivery":
        return False
    first, last = f"{month}-01", month_end(month).isoformat()
    if item.get("delivery_date") and item["delivery_date"] > last:
        return False
    if item.get("return_date") and item["return_date"] < first:
        return False
    return True


def _live(uow: UnitOfWork, apartment_id: str) -> dict[str, Any] | None:
    """The flat's first live furniture subscription, if it has one."""
    return uow.furniture.live_service(apartment_id, SERVICE_TYPE)


def _live_services(uow: UnitOfWork, apartment_id: str) -> list[dict[str, Any]]:
    """Every live furniture subscription: a flat may rent from several providers."""
    return uow.furniture.live_services(apartment_id, SERVICE_TYPE)


def _require_live(uow: UnitOfWork, apartment_id: str) -> dict[str, Any]:
    service = _live(uow, apartment_id)
    if service is None:
        raise ConflictError("Set up furniture rental first.", code="furniture_not_configured")
    return service


def _service_in(uow: UnitOfWork, apartment_id: str, service_id: str, *,
                live: bool = True) -> dict[str, Any]:
    """One of the flat's furniture subscriptions, by its ID; a live one unless told otherwise."""
    service = uow.furniture.service_in_apartment(service_id, apartment_id)
    if (service is None or service["service_type"] != SERVICE_TYPE
            or (live and service["status"] == "retired")):
        raise NotFoundError("No such furniture rental service.",
                            code="furniture_service_not_found",
                            details={"service_id": service_id})
    return service


def _provider_name(service: Mapping[str, Any], config: Mapping[str, Any] | None) -> str:
    return (config or {}).get("provider_name") or service["provider_name"] \
        or service["display_name"]


def _public_service(service: Mapping[str, Any]) -> dict[str, Any]:
    return {key: service[key] for key in (
        "service_id", "service_type", "display_name", "provider_name", "status",
        "billing_day_of_month", "period_rule", "created_at", "updated_at")}


def _public_config(config: Mapping[str, Any] | None) -> dict[str, Any] | None:
    return {key: config[key] for key in (
        "provider_name", "customer_id", "subscription_reference", "deposit_paid_paise",
        "deposit_refunded_paise", "deposit_status", "expense_definition_id", "notes",
        "updated_at")} if config else None


def _definition_id(uow: UnitOfWork, apartment_id: str) -> str | None:
    """The definition every provider's share of a month's bill is recorded under."""
    return next((config["expense_definition_id"]
                 for config in uow.furniture.configs_for_apartment(apartment_id)
                 if config["expense_definition_id"]), None)


def _item_in(uow: UnitOfWork, apartment_id: str, item_id: str) -> dict[str, Any]:
    item = uow.furniture.item(item_id)
    if item is None or item["apartment_id"] != apartment_id:
        raise NotFoundError("No such rented item.", code="furniture_item_not_found",
                            details={"item_id": item_id})
    return item


def _public_item(item: Mapping[str, Any]) -> dict[str, Any]:
    return {key: item.get(key) for key in (
        "item_id", "service_id", "provider_name", "service_display_name", "name", "category",
        "monthly_rent_paise", "allocation_scope", "assigned_resident_id",
        "assigned_resident_name", "status", "delivery_date", "return_date", "created_at",
        "updated_at")}


# -------------------------------------------------------- the month's bill --

def _month_bill(uow: UnitOfWork, apartment_id: str, items: list[dict[str, Any]], month: str,
                excluded_resident_ids: Sequence[str] | None = None, *,
                include_resident_ids: Sequence[str] | None = None) -> dict[str, Any]:
    """What `month` would bill, and to whom. Nothing is written.

    The active residents take part, less those excluded. ``include_resident_ids``
    keeps residents already on the month's bill taking part after they move out:
    nothing prorates, so a month they were in is theirs in full.
    """
    residents = list_residents(uow, apartment_id)
    names = {row["resident_id"]: row["display_name"] for row in residents}
    active = sorted(row["resident_id"] for row in residents if row["is_active"])
    included = sorted({str(rid) for rid in (include_resident_ids or [])} & set(names))
    excluded = sorted({str(rid) for rid in (excluded_resident_ids or [])})
    unknown = sorted(set(excluded) - set(names))
    if unknown:
        raise ValidationError("excluded_resident_ids must all be residents of this apartment.",
                              details={"field": "excluded_resident_ids", "unknown": unknown})
    participants = [rid for rid in sorted(set(active) | set(included)) if rid not in excluded]

    billable = [item for item in items if is_billable(item, month)]
    common = [item for item in billable if item["allocation_scope"] == "common"]
    private = [item for item in billable if item["allocation_scope"] == "resident"]
    common_total = sum(item["monthly_rent_paise"] for item in common)
    count = len(participants)
    base, remainder = divmod(common_total, count) if count else (0, 0)
    # The paise that do not divide go one each, in resident order.
    common_share = {rid: base + (1 if index < remainder else 0)
                    for index, rid in enumerate(participants)}

    private_by: dict[str, list[dict[str, Any]]] = {}
    for item in private:
        private_by.setdefault(item["assigned_resident_id"], []).append(item)

    blocked: str | None = None
    if not billable:
        blocked = f"No rented items were in the flat in {month_label(month)}, so there is nothing to bill."
    elif common_total and not count:
        blocked = "Nobody is taking part, so the common items cannot be shared."
    else:
        outside = sorted(rid for rid in private_by if rid not in participants)
        if outside:
            blocked = (f"{names.get(outside[0], outside[0])} has a rented item of their own but "
                       "is not taking part this month. Include them, or reassign or return the item.")

    shares = []
    for rid in sorted(set(participants) | set(private_by), key=lambda r: (names.get(r, r), r)):
        own = private_by.get(rid, [])
        own_total = sum(item["monthly_rent_paise"] for item in own)
        shares.append({
            "resident_id": rid, "display_name": names.get(rid, rid),
            "taking_part": rid in participants,
            "common_share_paise": common_share.get(rid, 0),
            "private_paise": own_total,
            "private_items": [item["name"] for item in own],
            "total_paise": common_share.get(rid, 0) + own_total,
        })
    private_total = sum(item["monthly_rent_paise"] for item in private)
    return {
        "settlement_month": month,
        "billable_items": [_public_item(item) for item in billable],
        "common_total_paise": common_total,
        "private_total_paise": private_total,
        "total_paise": common_total + private_total,
        "participant_count": count,
        "participants": participants,
        "base_share_paise": base,
        "remainder_paise": remainder,
        "shares": shares,
        "excluded_residents": [{"resident_id": rid, "display_name": names[rid]}
                               for rid in excluded],
        "blocked_reason": blocked,
    }


def _bill_notes(bill: Mapping[str, Any]) -> str:
    """The items behind a month's bill, in words, kept on the expense for the record."""
    common = [f"{item['name']} {format_inr(item['monthly_rent_paise'])}"
              for item in bill["billable_items"] if item["allocation_scope"] == "common"]
    parts = [f"Common: {', '.join(common)}"] if common else []
    for row in bill["shares"]:
        own = [item for item in bill["billable_items"]
               if item["assigned_resident_id"] == row["resident_id"]]
        if own:
            parts.append(f"{row['display_name']}: " + ", ".join(
                f"{item['name']} {format_inr(item['monthly_rent_paise'])}" for item in own))
    return " · ".join(parts)[:2000]


# --------------------------------------------------- paying the month's bill --

def _reimbursement_id_base(expense_id: str) -> str:
    """What a kitty reimbursement's payment ID starts with, ahead of its own suffix.

    Only a provider advance may name the expense it is for, so a refund carries
    its link to the month's bill in its ID instead, as a reversal or a direct
    settlement carries its own.
    """
    return f"{expense_id}-reimbursement"


def _share_payout_id(payment_id: str) -> str:
    """The kitty's payout of a share a resident paid the provider directly.

    It is kept with the payment into the kitty that settled the share, so a
    payout towards the bill can be told apart from the kitty paying out what
    others paid in. Migration 0023 links its tranches to their payments alike.
    """
    return f"svcdisb_{payment_id}"


def _share_status(owed: int, paid: int, advanced: int) -> str | None:
    """Where a resident's charge on the bill stands. ``funded_payout``: they paid
    the provider, which covers their share without anything into the kitty."""
    if owed <= 0:
        return None
    if paid >= owed:
        return "paid"
    if advanced and paid + advanced >= owed:
        return "funded_payout"
    return "partial" if paid + advanced else "unpaid"


def _public_tranche(tranche: Mapping[str, Any]) -> dict[str, Any]:
    return {key: tranche.get(key) for key in (
        "disbursement_id", "service_id", "provider_name", "amount_paise", "disbursed_at",
        "disbursement_reference", "disbursed_by", "funding_resident_id",
        "funding_resident_name", "funding_payment_id", "note", "created_at")}


def _funding(uow: UnitOfWork, expense: Mapping[str, Any]) -> dict[str, Any]:
    """The recorded bill's two steps: what the kitty has collected from residents,
    and what has been paid to the provider -- and what the kitty owes whoever
    paid the provider from their own account.

    ``collected_paise`` counts what residents have paid into the kitty against
    their charge, plus what a resident paid the provider directly towards their
    own share; ``collected_count`` is how many charges are covered in full. The
    provider may be paid in tranches: ``disbursements`` lists them, and
    ``payout_status`` is 'pending' until the first, 'partially_disbursed' while
    some is still owed, and 'disbursed' once all of it has been paid.

    ``reimbursements`` is what is owed back to each resident who paid a provider
    more than their own share -- one for each, however many providers they paid
    -- and ``reimbursement`` the first of them. Their share went through the
    kitty, so their advance is the extra alone; ``total_fronted_paise`` is
    everything they paid, their share included. They are owed the advance, out
    of what the kitty collected for the bill less what it has paid the
    providers itself and what it has already paid back.
    """
    expense_id, total = expense["expense_id"], expense["amount_paise"]
    charges = {row["resident_id"]: row for row in uow.furniture.charges_for_expense(expense_id)}
    paid_in: dict[str, list[dict[str, Any]]] = {}
    for payment in uow.furniture.payments_for_expense(expense_id):
        paid_in.setdefault(payment.pop("resident_id"), []).append(payment)
    tranches = uow.furniture.service_disbursements(expense_id)
    advanced: dict[str, int] = {}
    advanced_on: dict[str, str] = {}
    for tranche in tranches:
        if tranche["disbursed_by"] == "resident_advance":
            rid = tranche["funding_resident_id"]
            advanced[rid] = advanced.get(rid, 0) + tranche["amount_paise"]
            advanced_on[rid] = tranche["disbursed_at"]
    covered = {rid: min(row["share_paise"], row["settled_paise"] + advanced.get(rid, 0))
               for rid, row in charges.items()}
    disbursed = sum(tranche["amount_paise"] for tranche in tranches)
    result: dict[str, Any] = {
        "charges": charges, "paid_in": paid_in, "advanced": advanced,
        "advanced_on": advanced_on,
        "collected_paise": sum(covered.values()),
        "collected_count": sum(1 for rid, row in charges.items()
                               if row["share_paise"] > 0 and covered[rid] >= row["share_paise"]),
        "payout_status": ("disbursed" if disbursed >= total
                          else "partially_disbursed" if disbursed else "pending"),
        "disbursed_paise": disbursed,
        "remaining_disbursement_paise": max(0, total - disbursed),
        "disbursements": [_public_tranche(tranche) for tranche in tranches],
        "tranches": tranches,
        "reimbursement": None,
        "reimbursements": [],
    }
    advancers = list(dict.fromkeys(tranche["funding_resident_id"] for tranche in tranches
                                   if tranche["disbursed_by"] == "resident_advance"))
    if not advancers:
        return result

    # What the kitty holds for the bill is shared by everyone it owes.
    refunds_all = uow.furniture.reimbursements(f"{_reimbursement_id_base(expense_id)}_")
    from_kitty = sum(tranche["amount_paise"] for tranche in tranches
                     if tranche["disbursed_by"] == "pool")
    kitty = max(0, sum(row["settled_paise"] for row in charges.values()) - from_kitty
                - sum(row["amount_paise"] for row in refunds_all))
    for payer in advancers:
        own = charges.get(payer)
        share = own["share_paise"] if own else 0
        fronted = advanced[payer]
        owed_back = max(0, fronted - max(0, share - (own["settled_paise"] if own else 0)))
        if owed_back <= 0:
            continue
        # Their own share, paid to a provider through the kitty.
        via_kitty_ids = {_share_payout_id(payment["payment_id"])
                         for payment in paid_in.get(payer, [])}
        via_kitty = sum(tranche["amount_paise"] for tranche in tranches
                        if tranche["disbursed_by"] == "pool"
                        and tranche["disbursement_id"] in via_kitty_ids)
        refunds = [row for row in refunds_all if row["payer_resident_id"] == payer]
        reimbursed = sum(row["amount_paise"] for row in refunds)
        pending = max(0, owed_back - reimbursed)
        reimbursable = min(kitty, pending)
        result["reimbursements"].append({
            "recipient_resident_id": payer,
            "recipient_name": next(tranche["funding_resident_name"] for tranche in tranches
                                   if tranche["funding_resident_id"] == payer),
            "total_fronted_paise": fronted + via_kitty,
            "payer_share_paise": share,
            "net_reimbursement_owed_paise": owed_back,
            "reimbursed_so_far_paise": reimbursed,
            "pending_reimbursement_paise": pending,
            "kitty_cash_available_paise": kitty,
            "reimbursable_paise": reimbursable,
            "can_reimburse": reimbursable > 0,
            "reimbursements": [{key: row[key] for key in (
                "payment_id", "amount_paise", "payment_date", "reference")} for row in refunds],
        })
    result["reimbursement"] = result["reimbursements"][0] if result["reimbursements"] else None
    return result


def _provider_totals(billable_items: Sequence[Mapping[str, Any]],
                     expense: Mapping[str, Any] | None,
                     service_ids: Sequence[str]) -> dict[str, int]:
    """What each provider bills for the month: its items in the flat. With one
    provider billing, it is owed the whole recorded bill."""
    totals: dict[str, int] = {}
    for item in billable_items:
        totals[item["service_id"]] = totals.get(item["service_id"], 0) + item["monthly_rent_paise"]
    if expense is not None and len(totals) <= 1:
        billing = next(iter(totals), None) or next(iter(service_ids), None)
        if billing:
            totals = {billing: expense["amount_paise"]}
    return totals


def _provider_payouts(uow: UnitOfWork, apartment_id: str, bill: Mapping[str, Any],
                      expense: Mapping[str, Any] | None,
                      services: Sequence[Mapping[str, Any]],
                      tranches: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """What each provider is owed for the month, and what has been paid them.

    One row for each live subscription, and for a retired one with items
    billed this month or paid towards this bill. A provider is 'pending' until paid anything, 'partially_disbursed'
    while some is still owed, and 'disbursed' once paid in full.
    """
    providers = [(service, uow.furniture.config(service["service_id"])) for service in services]
    known = {service["service_id"] for service in services}
    for service_id in [*(item["service_id"] for item in bill["billable_items"]),
                       *(tranche["service_id"] for tranche in tranches)]:
        if service_id not in known:
            retired = uow.furniture.service_in_apartment(service_id, apartment_id)
            if retired is not None:
                providers.append((retired, uow.furniture.config(service_id)))
                known.add(service_id)
    totals = _provider_totals(bill["billable_items"], expense,
                              [service["service_id"] for service, _ in providers])
    rows = []
    for service, config in providers:
        service_id = service["service_id"]
        own = [tranche for tranche in tranches if tranche["service_id"] == service_id]
        total = totals.get(service_id, 0)
        disbursed = sum(tranche["amount_paise"] for tranche in own)
        rows.append({
            "service_id": service_id,
            "display_name": service["display_name"],
            "provider_name": _provider_name(service, config),
            "customer_id": config["customer_id"] if config else None,
            "subscription_reference": config["subscription_reference"] if config else None,
            "billing_day_of_month": service["billing_day_of_month"],
            "item_count": sum(1 for item in bill["billable_items"]
                              if item["service_id"] == service_id),
            "total_paise": total,
            "disbursed_paise": disbursed,
            "remaining_disbursement_paise": max(0, total - disbursed),
            "payout_status": ("disbursed" if total > 0 and disbursed >= total
                              else "partially_disbursed" if disbursed else "pending"),
            "disbursements": [_public_tranche(tranche) for tranche in own],
        })
    return rows


#: What the month's bill reports of its two steps, from :func:`_funding`.
FUNDING_FIELDS = ("collected_paise", "collected_count", "payout_status", "disbursed_paise",
                  "remaining_disbursement_paise", "disbursements", "reimbursement",
                  "reimbursements")


def _attach_funding(uow: UnitOfWork, apartment_id: str, bill: dict[str, Any],
                    expense: Mapping[str, Any] | None,
                    services: Sequence[Mapping[str, Any]]) -> None:
    """Add to the month's bill who has paid their share, the payouts to each
    provider, and the reimbursements. Before the bill is recorded nobody owes
    anything yet.

    Each share's ``settled_paise`` is what they paid into the kitty against it,
    and ``paid_paise`` that plus what paying the provider covered of it. Its
    ``payments`` are the payments into the kitty that settled its charge,
    oldest first, each with what it put towards the charge.
    """
    if expense is None:
        for share in bill["shares"]:
            share.update(paid_paise=0, settled_paise=0, outstanding_paise=0, status=None,
                         paid_date=None, payments=[])
        bill.update(collected_paise=0, collected_count=0, payout_status="pending",
                    disbursed_paise=0, remaining_disbursement_paise=0, disbursements=[],
                    reimbursement=None, reimbursements=[],
                    provider_payouts=_provider_payouts(uow, apartment_id, bill, None,
                                                       services, []))
        return
    funding = _funding(uow, expense)
    for share in bill["shares"]:
        rid = share["resident_id"]
        charge = funding["charges"].get(rid)
        owed = charge["share_paise"] if charge else 0
        paid = charge["settled_paise"] if charge else 0
        advanced = funding["advanced"].get(rid, 0)
        status = _share_status(owed, paid, advanced)
        covered = min(owed, paid + advanced)
        share.update(
            # What they are charged, as recorded: share = paid + still owed, always.
            total_paise=owed,
            paid_paise=covered, settled_paise=paid, outstanding_paise=owed - covered,
            status=status,
            paid_date=(funding["advanced_on"][rid] if status == "funded_payout"
                       else charge["last_payment_date"] if paid else None),
            payments=funding["paid_in"].get(rid, []))
    bill.update({key: funding[key] for key in FUNDING_FIELDS})
    bill["provider_payouts"] = _provider_payouts(uow, apartment_id, bill, expense, services,
                                                 funding["tranches"])


# ------------------------------------------------------------ the live bill --
#
# A month's bill is recorded as soon as the month has anything in the flat to
# bill, and follows its items -- added, changed, returned or removed, from any
# provider -- for as long as the month is open. It freezes for good when the
# month is over, or sooner if the month's statement is finalized: that is the
# month's snapshot. A future month is only a preview until it begins, and a
# month before furniture rental was set up is not billed by itself.

def _current_month() -> str:
    return _today()[:7]


def _bill_is_live(month: str, statement: Mapping[str, Any] | None) -> bool:
    """Whether a recorded bill still follows its items."""
    return month >= _current_month() and (statement is None or statement["status"] == "draft")


def _bill_basis(bill: Mapping[str, Any], excluded: Sequence[str]) -> dict[str, Any]:
    """Kept with each charge: what the bill is made of, and who is on it."""
    return {"method": "custom", "source": SERVICE_TYPE,
            "common_total_paise": bill["common_total_paise"],
            "private_total_paise": bill["private_total_paise"],
            "participants": list(bill["participants"]),
            "excluded_resident_ids": sorted(excluded)}


def _stored_basis(allocations: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    for row in allocations:
        if row.get("allocation_basis_json"):
            try:
                return json.loads(row["allocation_basis_json"])
            except ValueError:
                return {}
    return {}


def _billing_providers(services: Sequence[Mapping[str, Any]],
                       bill: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Every provider with something in the flat this month, or the first one."""
    return [svc for svc in services
            if any(item["service_id"] == svc["service_id"] for item in bill["billable_items"])] \
        or list(services[:1])


def _record_bill(uow: UnitOfWork, apartment_id: str, month: str, bill: Mapping[str, Any],
                 excluded: Sequence[str], services: Sequence[Mapping[str, Any]],
                 definition_id: str, statement: Mapping[str, Any] | None, timestamp: str,
                 actor: str) -> str:
    """Write the month's bill as one expense; returns its ID."""
    billing = _billing_providers(services, bill)
    due_dates = sorted(_billing_date(month, svc["billing_day_of_month"])
                       for svc in billing if svc["billing_day_of_month"])
    allocations = {row["resident_id"]: 0 for row in list_residents(uow, apartment_id)}
    allocations.update({row["resident_id"]: row["total_paise"] for row in bill["shares"]})
    expense_id = new_id("furniture")
    expenses._create_expense(
        uow, apartment_id=apartment_id, expense_id=expense_id,
        description=f"{services[0]['display_name']} for {month_label(month)}",
        amount_paise=bill["total_paise"], expense_date=f"{month}-01",
        allocation_method="custom", allocations_paise=allocations,
        allocation_basis=_bill_basis(bill, excluded),
        provider_name=", ".join(dict.fromkeys(
            _provider_name(svc, uow.furniture.config(svc["service_id"])) for svc in billing)),
        expense_definition_id=definition_id,
        statement_id=statement["statement_id"] if statement else None,
        period_rule="current_month", service_period=month, settlement_period=month,
        due_date=due_dates[0] if due_dates else None,
        notes=_bill_notes(bill), created_at=timestamp, actor=actor)
    audit.record(uow, entity_type="household_service", entity_id=services[0]["service_id"],
                 action="generate", actor=actor, apartment_id=apartment_id,
                 new_value={"settlement_month": month, "expense_id": expense_id,
                            "total_paise": bill["total_paise"],
                            "common_total_paise": bill["common_total_paise"],
                            "private_total_paise": bill["private_total_paise"]})
    return expense_id


def _sync_problem(uow: UnitOfWork, expense: Mapping[str, Any], bill: Mapping[str, Any],
                  allocations: Sequence[Mapping[str, Any]]) -> AemError | None:
    """Why the recorded bill cannot follow its items as they now stand, if it cannot:
    nobody is paid back, and no provider handed back, by a bill going down."""
    month = bill["settlement_month"]
    paid = sum(row["settled_paise"] for row in allocations)
    tranches = uow.furniture.service_disbursements(expense["expense_id"])
    advances = uow.furniture.linked_advance_total_paise(expense["expense_id"])
    if not bill["billable_items"]:
        if paid or tranches or advances:
            return ConflictError(
                "Payments or provider payouts have already been recorded for this month's "
                "furniture bill, so its last billable item cannot be removed.",
                code="furniture_bill_has_payments",
                details={"settlement_month": month, "expense_id": expense["expense_id"]})
        return None
    if bill["blocked_reason"]:
        return ValidationError(bill["blocked_reason"], code="furniture_bill_blocked",
                               details={"settlement_month": month})
    shares = {row["resident_id"]: row["total_paise"] for row in bill["shares"]}
    settled: dict[str, int] = {}
    for row in allocations:
        settled[row["resident_id"]] = settled.get(row["resident_id"], 0) + row["settled_paise"]
    names = {row["resident_id"]: row["display_name"] for row in bill["shares"]}
    for rid, amount in sorted(settled.items()):
        if amount > shares.get(rid, 0):
            name = names.get(rid, rid)
            return ConflictError(
                f"{name} has already paid {format_inr(amount)} towards the "
                f"{month_label(month)} furniture bill, more than this would leave their share "
                f"at ({format_inr(shares.get(rid, 0))}).", code="furniture_share_below_paid",
                details={"resident_id": rid, "settled_paise": amount,
                         "share_paise": shares.get(rid, 0)})
    totals: dict[str, int] = {}
    for item in bill["billable_items"]:
        totals[item["service_id"]] = totals.get(item["service_id"], 0) + item["monthly_rent_paise"]
    paid_out: dict[str, int] = {}
    for tranche in tranches:
        paid_out[tranche["service_id"]] = (paid_out.get(tranche["service_id"], 0)
                                           + tranche["amount_paise"])
    for service_id, amount in sorted(paid_out.items()):
        if amount > totals.get(service_id, 0):
            provider = next((t["provider_name"] for t in tranches
                             if t["service_id"] == service_id), "The provider")
            return ConflictError(
                f"{provider} has already been paid {format_inr(amount)} for "
                f"{month_label(month)}, more than this would leave it owed "
                f"({format_inr(totals.get(service_id, 0))}).",
                code="furniture_provider_below_disbursed",
                details={"service_id": service_id, "disbursed_paise": amount,
                         "total_paise": totals.get(service_id, 0)})
    if advances > bill["total_paise"]:
        return ConflictError(
            f"Residents have already paid the providers {format_inr(advances)} for "
            f"{month_label(month)}, more than this would leave the bill at "
            f"({format_inr(bill['total_paise'])}).", code="furniture_provider_below_disbursed",
            details={"advanced_paise": advances, "total_paise": bill["total_paise"]})
    return None


def _sync_live_month_bill(uow: UnitOfWork, apartment_id: str, month: str, *,
                          excluded_resident_ids: Sequence[str] | None = None,
                          check_reduction_guards: bool = True, timestamp: str | None = None,
                          actor: str = "operator") -> dict[str, Any] | None:
    """Record `month`'s bill if it is due, or bring the recorded one up to date
    with the items; returns the bill as it then stands, if there is one.

    A frozen bill (see :func:`_bill_is_live`) is returned as it is. A bill that
    would go below what has been paid -- a resident's share below what they paid
    into the kitty, a provider's part below what was paid them -- is refused
    with ``check_reduction_guards``, and otherwise left as it was. A bill left
    with nothing to bill, and nothing paid towards it, is removed.

    Charges are changed in place, so the payments applied to them stay with them.
    """
    timestamp = timestamp or now_timestamp()
    services = _live_services(uow, apartment_id)
    definition_id = _definition_id(uow, apartment_id)
    statement = uow.furniture.statement_for_month(apartment_id, month)
    expense = _recorded_bill(uow, apartment_id, {"expense_definition_id": definition_id}, month)
    if expense is not None and not _bill_is_live(month, statement):
        return expense
    if expense is None:
        if (not services or not definition_id or month > _current_month()
                or (statement is not None and statement["status"] != "draft")
                or month < min(service["created_at"][:7] for service in services)):
            return None
    allocations = uow.furniture.allocations_for_expense(expense["expense_id"]) if expense else []
    basis = _stored_basis(allocations)
    excluded = (list(excluded_resident_ids) if excluded_resident_ids is not None
                else basis.get("excluded_resident_ids", []))
    bill = _month_bill(uow, apartment_id,
                       uow.furniture.items_for_apartment(apartment_id, include_retired=True),
                       month, excluded, include_resident_ids=basis.get("participants", []))
    if expense is None:
        if bill["blocked_reason"] or not bill["billable_items"]:
            return None
        _record_bill(uow, apartment_id, month, bill, excluded, services, definition_id,
                     statement, timestamp, actor)
        return _recorded_bill(uow, apartment_id, {"expense_definition_id": definition_id},
                              month)

    problem = _sync_problem(uow, expense, bill, allocations)
    if problem is not None:
        if check_reduction_guards:
            raise problem
        return expense
    if not bill["billable_items"]:
        # Nothing in the flat this month any more, and nothing paid: no bill.
        uow.expenses.delete_allocations(expense["expense_id"])
        uow.expenses.delete_components(expense["expense_id"])
        uow.expenses.delete_expense(expense["expense_id"])
        audit.record(uow, entity_type="expense", entity_id=expense["expense_id"],
                     action="delete", actor=actor, apartment_id=apartment_id,
                     reason="Nothing rented was in the flat this month any more.",
                     old_value={"settlement_month": month, "amount_paise": expense["amount_paise"]})
        return None

    shares = {row["resident_id"]: row["total_paise"] for row in bill["shares"]
              if row["total_paise"] > 0}
    new_basis = _bill_basis(bill, excluded)
    basis_json = json.dumps(new_basis, sort_keys=True, default=str)
    providers = ", ".join(dict.fromkeys(
        _provider_name(svc, uow.furniture.config(svc["service_id"]))
        for svc in _billing_providers(services, bill)))
    notes = _bill_notes(bill)
    current = {row["resident_id"]: row for row in allocations}
    if (expense["amount_paise"] == bill["total_paise"] and expense.get("notes") == notes
            and expense.get("provider_name") == providers and basis == new_basis
            and {rid: row["amount_paise"] for rid, row in current.items()} == shares):
        return expense
    uow.furniture.update_bill(expense["expense_id"], amount_paise=bill["total_paise"],
                              notes=notes, provider_name=providers, updated_at=timestamp)
    component_id = uow.expenses.component_ids(expense["expense_id"])[0]
    uow.expenses.update_component(component_id, amount_paise=bill["total_paise"],
                                  allocation_method="custom")
    for rid in sorted(set(current) | set(shares)):
        amount, row = shares.get(rid, 0), current.get(rid)
        if row is not None and amount:
            uow.furniture.update_allocation(row["allocation_id"], amount_paise=amount,
                                            allocation_basis_json=basis_json)
        elif row is not None:
            uow.furniture.delete_allocation(row["allocation_id"])
        else:
            uow.expenses.insert_allocation(
                allocation_id=new_id("allocation"), expense_id=expense["expense_id"],
                component_id=component_id, resident_id=rid, amount_paise=amount,
                allocation_basis_json=basis_json)
    audit.record(uow, entity_type="expense", entity_id=expense["expense_id"], action="sync",
                 actor=actor, apartment_id=apartment_id,
                 old_value={"amount_paise": expense["amount_paise"],
                            "shares": {rid: row["amount_paise"] for rid, row in current.items()}},
                 new_value={"amount_paise": bill["total_paise"], "shares": shares})
    return _recorded_bill(uow, apartment_id, {"expense_definition_id": definition_id}, month)


def _sync_open_months(uow: UnitOfWork, apartment_id: str, *, timestamp: str,
                      actor: str = "operator") -> None:
    """After an item changes, bring every live month's bill up to date with it --
    refusing the change if it would leave a bill below what has been paid."""
    definition_id = _definition_id(uow, apartment_id)
    months = {_current_month()}
    if definition_id:
        months |= {month for month in uow.furniture.bill_months(apartment_id, definition_id)
                   if month >= _current_month()}
    for month in sorted(months):
        _sync_live_month_bill(uow, apartment_id, month, timestamp=timestamp, actor=actor)


# ----------------------------------------------------------------- overview --

def get_overview(db: Database, apartment_id: str, settlement_month: str) -> dict[str, Any]:
    """The subscriptions, their items, and what `settlement_month` bills.

    The month's bill is recorded, or brought up to date, as it is looked at, so
    an open month is always billed as its items stand. ``service`` is null until
    furniture rental is set up, and after it is retired until it is set up again.
    """
    month = normalize_month(settlement_month, "month")
    with db.transaction() as uow:
        get_apartment(uow, apartment_id)
        _sync_live_month_bill(uow, apartment_id, month, check_reduction_guards=False)
        return _overview(uow, apartment_id, month)


def _recorded_bill(uow: UnitOfWork, apartment_id: str, config: Mapping[str, Any] | None,
                   month: str) -> dict[str, Any] | None:
    """The month's bill as recorded, if it has been."""
    if not config or not config["expense_definition_id"]:
        return None
    recorded = uow.furniture.expenses_for_month(apartment_id, config["expense_definition_id"],
                                                month)
    return recorded[0] if recorded else None


def _overview(uow: UnitOfWork, apartment_id: str, month: str) -> dict[str, Any]:
    """Every live provider's subscription, all their items, and the month's bill.

    ``services`` lists each provider with its own totals and deposit; the
    top-level ``service`` and ``config`` are the first of them, and the totals
    are across all of them. The month is billed as one expense, and each
    provider is paid its own part of it (``month.provider_payouts``).
    """
    services = _live_services(uow, apartment_id)
    if not services:
        return {"service": None, "config": None, "services": [], "items": [], "month": None,
                "monthly_total_paise": 0, "active_item_count": 0, "next_billing_date": None,
                "deposit_held_paise": 0}
    items = uow.furniture.items_for_apartment(apartment_id)
    # A subscription ended this month still bills what was in the flat in it.
    billed_items = uow.furniture.items_for_apartment(apartment_id, include_retired=True)
    entries = []
    for service in services:
        config = uow.furniture.config(service["service_id"])
        active = [item for item in items
                  if item["service_id"] == service["service_id"] and item["status"] == "active"]
        entries.append({
            "service": _public_service(service),
            "config": _public_config(config),
            "monthly_total_paise": sum(item["monthly_rent_paise"] for item in active),
            "active_item_count": len(active),
            "next_billing_date": next_billing_date(service["billing_day_of_month"], _today()),
            "deposit_held_paise": (config["deposit_paid_paise"] - config["deposit_refunded_paise"])
            if config else 0,
        })
    statement = uow.furniture.statement_for_month(apartment_id, month)
    definition_id = _definition_id(uow, apartment_id)
    expense = _recorded_bill(uow, apartment_id, {"expense_definition_id": definition_id}, month)
    # A recorded bill is shown with the residents it was recorded for, and its totals
    # as recorded: a frozen month keeps its snapshot whatever the items say since.
    basis = (_stored_basis(uow.furniture.allocations_for_expense(expense["expense_id"]))
             if expense else {})
    bill = _month_bill(uow, apartment_id, billed_items, month,
                       basis.get("excluded_resident_ids"),
                       include_resident_ids=basis.get("participants"))
    if expense is not None:
        bill.update(total_paise=expense["amount_paise"],
                    common_total_paise=basis.get("common_total_paise",
                                                 bill["common_total_paise"]),
                    private_total_paise=basis.get("private_total_paise",
                                                  bill["private_total_paise"]))
    bill["expense"] = expense
    bill["statement_status"] = statement["status"] if statement else None
    bill["can_generate"] = (expense is None and bill["blocked_reason"] is None
                            and (statement is None or statement["status"] == "draft"))
    # 'live': billed and following its items; 'frozen': the month's snapshot;
    # 'upcoming': not begun; 'not_billed': nothing to bill, or before the rental.
    bill["bill_state"] = (("live" if _bill_is_live(month, statement) else "frozen") if expense
                          else "upcoming" if month > _current_month() else "not_billed")
    # Whether a payment or payout recorded by mistake can still be deleted.
    bill["transactions_open"] = deletions.month_is_open(uow, apartment_id, month)
    _attach_funding(uow, apartment_id, bill, expense, services)
    billing_dates = [entry["next_billing_date"] for entry in entries if entry["next_billing_date"]]
    return {
        "service": entries[0]["service"],
        "config": entries[0]["config"],
        "services": entries,
        "items": [_public_item(item) for item in items],
        "month": bill,
        "monthly_total_paise": sum(entry["monthly_total_paise"] for entry in entries),
        "active_item_count": sum(entry["active_item_count"] for entry in entries),
        "next_billing_date": min(billing_dates) if billing_dates else None,
        "deposit_held_paise": sum(entry["deposit_held_paise"] for entry in entries),
    }


# -------------------------------------------------------------------- setup --

def _clean_setup(payload: Mapping[str, Any], before: Mapping[str, Any] | None) -> dict[str, Any]:
    merged = {**(before or {}), **payload}
    display_name = require_text(merged.get("display_name") or DEFAULT_NAME, "display_name",
                                max_length=100)
    provider = require_text(merged.get("provider_name") or "Furlenco", "provider_name",
                            max_length=100)
    paid = ensure_paise(merged.get("deposit_paid_paise") or 0, "deposit_paid_paise")
    refunded = ensure_paise(merged.get("deposit_refunded_paise") or 0, "deposit_refunded_paise")
    if refunded > paid:
        raise ValidationError("The deposit refunded cannot be more than the deposit paid.",
                              details={"field": "deposit_refunded_paise"})
    day = merged.get("billing_day_of_month")
    if day is not None and (isinstance(day, bool) or not isinstance(day, int)
                            or not 1 <= day <= 31):
        raise ValidationError("billing_day_of_month must be a day from 1 to 31.",
                              details={"field": "billing_day_of_month"})
    return {
        "display_name": display_name, "provider_name": provider, "billing_day_of_month": day,
        "customer_id": optional_text(merged.get("customer_id"), "customer_id", max_length=100),
        "subscription_reference": optional_text(merged.get("subscription_reference"),
                                                "subscription_reference", max_length=100),
        "deposit_paid_paise": paid, "deposit_refunded_paise": refunded,
        "deposit_status": _deposit_status(paid, refunded),
        "notes": optional_text(merged.get("notes"), "notes", max_length=2000),
    }


def setup_furniture_rental(db: Database, apartment_id: str, payload: Mapping[str, Any], *,
                           settlement_month: str | None = None,
                           actor: str = "operator") -> dict[str, Any]:
    """Set up a furniture rental subscription, or update one the flat has.

    A flat may rent from several providers at once. ``service_id`` names the
    subscription to update; ``create_new`` adds another provider; with neither,
    the flat's first subscription is updated, or one is set up if it has none.
    Only the fields sent are changed.

    Every provider's items are billed together, each month as one expense under
    the household's "Furniture rental" definition when it has one; otherwise a
    non-recurring one is created, since the bill is generated from this module
    rather than by the recurring generator.
    """
    payload = dict(payload)
    service_id = optional_text(payload.pop("service_id", None), "service_id", max_length=64)
    create_new = bool(payload.pop("create_new", False))
    month = normalize_month(settlement_month or _today()[:7], "month")
    timestamp = now_timestamp()
    with db.read() as uow:
        get_apartment(uow, apartment_id)
        if service_id:
            service = _service_in(uow, apartment_id, service_id)
        elif create_new:
            service = None
        else:
            service = _live(uow, apartment_id)
        config = uow.furniture.config(service["service_id"]) if service else None
        before = {**(config or {}), **(service or {})}
        cleaned = _clean_setup(payload, before if service else None)
        definition_id = (config or {}).get("expense_definition_id") \
            or _definition_id(uow, apartment_id) or uow.furniture.find_definition_id(apartment_id)
        id_taken = definition_id is None and uow.furniture.definition_id_taken("furniture-rental")

    if definition_id is None:
        definition_id = definitions.create_expense_definition(
            db, apartment_id=apartment_id,
            definition_id=new_id("furniture") if id_taken else "furniture-rental",
            name=DEFAULT_NAME, category="Housing", period_rule="current_month",
            default_allocation_method="equal", is_recurring=False, recurrence="none",
            default_provider_name=cleaned["provider_name"], sort_order=100,
            notes="Created by furniture rental. Each month's bill is generated from the "
                  "Furniture Rental screen.", actor=actor)["expense_definition_id"]

    service_fields = {key: cleaned[key] for key in
                      ("display_name", "provider_name", "billing_day_of_month")}
    config_fields = {key: cleaned[key] for key in
                     ("provider_name", "customer_id", "subscription_reference",
                      "deposit_paid_paise", "deposit_refunded_paise", "deposit_status", "notes")}
    with db.transaction() as uow:
        service = _service_in(uow, apartment_id, service["service_id"]) if service else None
        if service is None:
            service_id = new_id("svc")
            uow.furniture.insert_service(service_id=service_id, apartment_id=apartment_id,
                                         service_type=SERVICE_TYPE, created_at=timestamp,
                                         **service_fields)
            uow.furniture.insert_config(service_id=service_id, apartment_id=apartment_id,
                                        updated_at=timestamp,
                                        expense_definition_id=definition_id, **config_fields)
            audit.record(uow, entity_type="household_service", entity_id=service_id,
                         action="create", actor=actor, apartment_id=apartment_id,
                         new_value={"service_type": SERVICE_TYPE, **service_fields,
                                    **config_fields})
        else:
            service_id = service["service_id"]
            before_config = uow.furniture.config(service_id) or {}
            uow.furniture.update_service(service_id, updated_at=timestamp, **service_fields)
            uow.furniture.update_config(service_id, updated_at=timestamp,
                                        expense_definition_id=definition_id, **config_fields)
            changed = {key: value for key, value in {**service_fields, **config_fields}.items()
                       if {**before_config, **service}.get(key) != value}
            if changed:
                audit.record(uow, entity_type="household_service", entity_id=service_id,
                             action="update", actor=actor, apartment_id=apartment_id,
                             new_value=changed)
        return _overview(uow, apartment_id, month)


def retire_furniture_rental(db: Database, apartment_id: str, *, service_id: str | None = None,
                            settlement_month: str | None = None,
                            actor: str = "operator") -> dict[str, Any]:
    """End a subscription: ``service_id``'s, or the flat's first. Every item on it
    must have been returned first; the service, its items and every bill already
    recorded stay for the record."""
    month = normalize_month(settlement_month or _today()[:7], "month")
    timestamp = now_timestamp()
    with db.transaction() as uow:
        service = (_service_in(uow, apartment_id, service_id) if service_id
                   else _require_live(uow, apartment_id))
        still_here = [item["name"] for item in uow.furniture.items(service["service_id"])
                      if item["status"] in OPEN_STATUSES]
        if still_here:
            raise ConflictError(
                "Return every item before ending the subscription: "
                f"{', '.join(still_here)} still {'is' if len(still_here) == 1 else 'are'} "
                "with the flat.", code="furniture_items_outstanding",
                details={"items": still_here})
        uow.furniture.retire_service(service["service_id"], retired_at=timestamp)
        audit.record(uow, entity_type="household_service", entity_id=service["service_id"],
                     action="retire", actor=actor, apartment_id=apartment_id,
                     new_value={"status": "retired", "retired_at": timestamp})
        return _overview(uow, apartment_id, month)


# -------------------------------------------------------------------- items --

def _clean_item(uow: UnitOfWork, apartment_id: str, values: Mapping[str, Any],
                before: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """An item's fields, checked together as they will be stored.

    An item is rented from one provider: ``service_id`` names its subscription.
    Left out, it stays with the provider it has, or goes to the flat's only
    provider; with several, it must be chosen.
    """
    service_id = optional_text(values.get("service_id"), "service_id", max_length=64)
    if service_id:
        service = uow.furniture.service_in_apartment(service_id, apartment_id)
        if (service is None or service["service_type"] != SERVICE_TYPE
                or service["status"] == "retired"):
            raise ValidationError("Choose an active rental service provider for this apartment.",
                                  code="invalid_furniture_service",
                                  details={"field": "service_id"})
    elif before is not None and before.get("service_id"):
        service_id = before["service_id"]
    else:
        live = _live_services(uow, apartment_id)
        if not live:
            raise ConflictError("Set up furniture rental first.",
                                code="furniture_not_configured")
        if len(live) > 1:
            raise ValidationError("Choose the rental service provider for this item.",
                                  code="furniture_service_required",
                                  details={"field": "service_id"})
        service_id = live[0]["service_id"]
    name = require_text(values.get("name"), "name", max_length=200)
    # Not asked for any more: an item sent without one is "other".
    category = values.get("category") or "other"
    if category not in CATEGORIES:
        raise ValidationError(f"category must be one of {', '.join(CATEGORIES)}.",
                              details={"field": "category", "allowed": list(CATEGORIES)})
    rent = ensure_paise(values.get("monthly_rent_paise"), "monthly_rent_paise", allow_zero=False)
    scope = values.get("allocation_scope")
    if scope not in SCOPES:
        raise ValidationError("allocation_scope must be common or resident.",
                              details={"field": "allocation_scope", "allowed": list(SCOPES)})
    resident_id = optional_text(values.get("assigned_resident_id"), "assigned_resident_id",
                                max_length=64)
    if scope == "common" and resident_id:
        raise ValidationError("A common item is shared by everyone, so it has no resident.",
                              details={"field": "assigned_resident_id"})
    if scope == "resident":
        if not resident_id:
            raise ValidationError("Choose the resident this item is for.",
                                  details={"field": "assigned_resident_id"})
        resident = uow.residents.get(resident_id)
        if resident is None or resident["apartment_id"] != apartment_id:
            raise ValidationError("The item must be for a resident of this apartment.",
                                  details={"field": "assigned_resident_id"})
        newly_assigned = before is None or before.get("assigned_resident_id") != resident_id
        if newly_assigned and not resident["is_active"]:
            raise ValidationError(f"{resident['display_name']} has moved out.",
                                  details={"field": "assigned_resident_id"})
    status = values.get("status") or "active"
    if status not in ITEM_STATUSES:
        raise ValidationError(f"status must be one of {', '.join(ITEM_STATUSES)}.",
                              details={"field": "status", "allowed": list(ITEM_STATUSES)})
    delivery = optional_text(values.get("delivery_date"), "delivery_date", max_length=10)
    returned = optional_text(values.get("return_date"), "return_date", max_length=10)
    for field, value in (("delivery_date", delivery), ("return_date", returned)):
        if value:
            parse_iso_date(value, field)
    if status == "returned" and not returned:
        raise ValidationError("A returned item needs the date it went back.",
                              details={"field": "return_date"})
    if status != "returned" and returned:
        raise ValidationError("Only a returned item has a return date.",
                              details={"field": "return_date"})
    if delivery and returned and returned < delivery:
        raise ValidationError("The return date cannot be before the delivery date.",
                              details={"field": "return_date"})
    return {"service_id": service_id, "name": name, "category": category,
            "monthly_rent_paise": rent,
            "allocation_scope": scope, "assigned_resident_id": resident_id if scope == "resident"
            else None, "status": status, "delivery_date": delivery, "return_date": returned}


def add_item(db: Database, apartment_id: str, payload: Mapping[str, Any], *,
             actor: str = "operator") -> dict[str, Any]:
    timestamp = now_timestamp()
    with db.transaction() as uow:
        values = _clean_item(uow, apartment_id, payload)
        item_id = new_id("furn")
        uow.furniture.insert_item(item_id=item_id, apartment_id=apartment_id,
                                  created_at=timestamp, **values)
        audit.record(uow, entity_type="furniture_rental_item", entity_id=item_id,
                     action="create", actor=actor, apartment_id=apartment_id, new_value=values)
        _sync_open_months(uow, apartment_id, timestamp=timestamp, actor=actor)
        return _public_item(uow.furniture.item(item_id))


def update_item(db: Database, apartment_id: str, item_id: str, changes: Mapping[str, Any], *,
                actor: str = "operator") -> dict[str, Any]:
    """Change an item. Only the fields sent change; making an item common clears
    its resident, and undoing a return clears its return date."""
    timestamp = now_timestamp()
    with db.transaction() as uow:
        before = _item_in(uow, apartment_id, item_id)
        merged = {**before, **changes}
        if changes.get("allocation_scope") == "common" and "assigned_resident_id" not in changes:
            merged["assigned_resident_id"] = None
        if (changes.get("status") in ("active", "pending_delivery")
                and "return_date" not in changes):
            merged["return_date"] = None
        values = _clean_item(uow, apartment_id, merged, before)
        changed = {key: value for key, value in values.items() if before.get(key) != value}
        if changed:
            uow.furniture.update_item(item_id, updated_at=timestamp, **changed)
            audit.record(uow, entity_type="furniture_rental_item", entity_id=item_id,
                         action="update", actor=actor, apartment_id=apartment_id,
                         old_value={key: before.get(key) for key in changed}, new_value=changed)
            _sync_open_months(uow, apartment_id, timestamp=timestamp, actor=actor)
        return _public_item(uow.furniture.item(item_id))


def delete_item(db: Database, apartment_id: str, item_id: str, *,
                actor: str = "operator") -> None:
    """Remove an item entered by mistake. An open month's bill drops it; a
    frozen one keeps the items behind it in its notes. To stop paying for an
    item, return it."""
    with db.transaction() as uow:
        before = _item_in(uow, apartment_id, item_id)
        uow.furniture.delete_item(item_id)
        audit.record(uow, entity_type="furniture_rental_item", entity_id=item_id,
                     action="delete", actor=actor, apartment_id=apartment_id,
                     old_value=_public_item(before))
        _sync_open_months(uow, apartment_id, timestamp=now_timestamp(), actor=actor)


# ------------------------------------------------------------ generating -- #

def generate_month(db: Database, apartment_id: str, settlement_month: str,
                   excluded_resident_ids: Sequence[str] | None = None, *,
                   actor: str = "operator") -> dict[str, Any]:
    """Record `settlement_month`'s furniture bill as one expense, in one transaction.

    An open month's bill is recorded by itself (see the live bill above); this
    records one by hand -- a month before the rental was set up, or one with
    some residents left out. The expense is due on the earliest billing day of
    the providers billing, and is attached to the month's statement, which must
    still be a draft. A month already recorded is refused, as is a month whose
    bill cannot be shared (see ``blocked_reason``).
    """
    month = normalize_month(settlement_month, "settlement_month")
    timestamp = now_timestamp()
    with db.transaction() as uow:
        get_apartment(uow, apartment_id)
        service = _require_live(uow, apartment_id)
        services = _live_services(uow, apartment_id)
        definition_id = _definition_id(uow, apartment_id)
        if not definition_id:
            raise ConflictError("Set up furniture rental again before generating a bill.",
                                code="furniture_not_configured")
        statement = uow.furniture.statement_for_month(apartment_id, month)
        if statement and statement["status"] != "draft":
            raise ConflictError(
                f"The {month} statement has been finalized, so the furniture bill can no "
                "longer be added to it.", code="statement_not_draft",
                details={"statement_id": statement["statement_id"], "status": statement["status"]})
        recorded = uow.furniture.expenses_for_month(apartment_id, definition_id, month)
        if recorded:
            raise ConflictError(
                f"Furniture rental for {month_label(month)} is already recorded as an expense.",
                code="furniture_bill_exists", details={"expense_id": recorded[0]["expense_id"]})
        excluded = sorted({str(rid) for rid in (excluded_resident_ids or [])})
        bill = _month_bill(uow, apartment_id,
                           uow.furniture.items_for_apartment(apartment_id, include_retired=True),
                           month, excluded)
        if bill["blocked_reason"]:
            raise ValidationError(bill["blocked_reason"], code="furniture_bill_blocked",
                                  details={"settlement_month": month})
        _record_bill(uow, apartment_id, month, bill, excluded, services, definition_id,
                     statement, timestamp, actor)
        return _overview(uow, apartment_id, month)


# ----------------------------------------------------------- paying it -- #

def _require_recorded_bill(uow: UnitOfWork, apartment_id: str, service: Mapping[str, Any],
                           month: str) -> dict[str, Any]:
    expense = _recorded_bill(uow, apartment_id, uow.furniture.config(service["service_id"]),
                             month)
    if expense is None:
        raise ConflictError(f"Record the {month_label(month)} furniture bill first.",
                            code="furniture_bill_not_recorded",
                            details={"settlement_month": month})
    return expense


def record_furniture_payment(db: Database, apartment_id: str, settlement_month: str,
                             resident_id: str, *, amount_paise: int, payment_date: str,
                             payment_mode: str | None = None, reference: str | None = None,
                             note: str | None = None, actor: str = "operator") -> dict[str, Any]:
    """Record a resident paying their share of the month's bill into the kitty.

    The payment is targeted at their charge on the bill, so it settles this bill
    first -- up to what is still owed on it -- whatever else they owe. Anything
    paid beyond it is applied to their other charges oldest-first, and what is
    left after that stays a credit. A share already paid, or covered by the
    resident having paid the provider, is refused.
    """
    month = normalize_month(settlement_month, "settlement_month")
    ensure_paise(amount_paise, "amount_paise", allow_zero=False)
    payment_date = parse_iso_date(payment_date, "payment_date").isoformat()
    timestamp = now_timestamp()
    with db.transaction() as uow:
        get_apartment(uow, apartment_id)
        resident = get_resident(uow, resident_id, apartment_id)
        name = resident["display_name"]
        service = _require_live(uow, apartment_id)
        _sync_live_month_bill(uow, apartment_id, month, check_reduction_guards=False,
                              timestamp=timestamp, actor=actor)
        expense = _require_recorded_bill(uow, apartment_id, service, month)
        charges = uow.furniture.resident_charges_for_expense(expense["expense_id"], resident_id)
        if not charges:
            raise ValidationError(
                f"{name} has no share of the {month_label(month)} furniture bill.",
                code="not_a_furniture_participant",
                details={"field": "resident_id", "resident_id": resident_id})
        target = next((charge for charge in charges if charge["outstanding_paise"] > 0), None)
        if target is None:
            raise ConflictError(
                f"{name} has already paid their share of the {month_label(month)} furniture bill.",
                code="furniture_share_already_paid", details={"resident_id": resident_id})
        # A payout recorded before a resident's own share went through the kitty
        # left the share open but covered by their advance.
        advanced = sum(tranche["amount_paise"]
                       for tranche in uow.furniture.service_disbursements(expense["expense_id"])
                       if tranche["funding_resident_id"] == resident_id)
        if advanced and advanced >= sum(charge["outstanding_paise"] for charge in charges):
            raise ConflictError(
                f"{name} paid {service['provider_name'] or service['display_name']} the "
                f"{month_label(month)} bill, which already covers their share; the kitty owes "
                "them the rest.", code="furniture_share_funded_by_payout",
                details={"resident_id": resident_id})
        payments._record_payment(
            uow, apartment_id=apartment_id, payment_id=new_id("payment"),
            payer_resident_id=resident_id, amount_paise=amount_paise,
            payment_date=payment_date, payment_kind="to_pool", payee_or_provider=None,
            reference=reference,
            note=(optional_text(note, "note")
                  or f"{service['display_name']} for {month_label(month)}"),
            timestamp=timestamp, expense_id=None, actor=actor,
            target_allocation_id=target["allocation_id"], payment_mode=payment_mode)
        return _overview(uow, apartment_id, month)


#: Where a payout to the provider came from: the kitty, or a resident's own account.
PAYOUT_MODES = ("pool", "resident_advance")


def record_provider_payout(db: Database, apartment_id: str, settlement_month: str, *,
                           disbursement_mode: str, reference: str, disbursed_date: str,
                           amount_paise: int | None = None,
                           funding_resident_id: str | None = None,
                           service_id: str | None = None, notes: str | None = None,
                           actor: str = "operator") -> dict[str, Any]:
    """Record a payment to a provider towards the month's recorded bill.

    ``service_id`` names the provider being paid; left out, it is the one
    provider with items billed this month, and with several it must be given.
    Each provider is owed its own items' part of the bill -- with one provider,
    all of it.

    A provider can be paid in tranches -- what the kitty has collected by the
    billing day, and the rest when it comes in. ``amount_paise`` is this
    tranche; left out, it is everything still owed to that provider. Each
    tranche is kept, for good, with its own date and bank reference, and they
    can never add up to more than the provider is owed, nor the bill.

    ``pool``
        The tranche left the kitty. Nothing is added to the ledger: the bill was
        already a cost to the kitty, and the residents' payments into it are
        what cover it.

    ``resident_advance``
        A resident paid it from their own account. Whatever of it covers their
        own open share of the bill goes through the kitty, as everyone else's
        share does: a payment into the kitty settling their share, and a
        ``pool`` tranche paying it out. Only what they paid beyond their share is
        an advance: a ``provider_advance`` linked to the month's expense and a
        ``resident_advance`` tranche, which the kitty pays back to them
        (``reimbursement`` in the overview).

    A bill the ledger already shows a resident paying -- an advance linked to it
    from elsewhere, or a payer named on the expense -- is refused, so the same
    payment is never counted twice.
    """
    month = normalize_month(settlement_month, "settlement_month")
    if disbursement_mode not in PAYOUT_MODES:
        raise ValidationError("Unsupported disbursement_mode.",
                              details={"field": "disbursement_mode",
                                       "allowed": list(PAYOUT_MODES)})
    reference = require_text(reference, "reference", max_length=200)
    disbursed_date = parse_iso_date(disbursed_date, "disbursed_date").isoformat()
    note = optional_text(notes, "notes", max_length=500)
    if amount_paise is not None:
        amount_paise = ensure_paise(amount_paise, "amount_paise", allow_zero=False)
    if disbursement_mode == "pool" and funding_resident_id:
        raise ValidationError("A payout from the kitty has no funding resident.",
                              details={"field": "funding_resident_id"})
    if disbursement_mode == "resident_advance" and not funding_resident_id:
        raise ValidationError("Choose the resident who paid the provider.",
                              details={"field": "funding_resident_id"})
    timestamp = now_timestamp()
    with db.transaction() as uow:
        get_apartment(uow, apartment_id)
        _sync_live_month_bill(uow, apartment_id, month, check_reduction_guards=False,
                              timestamp=timestamp, actor=actor)
        expense = _require_recorded_bill(uow, apartment_id, _require_live(uow, apartment_id),
                                         month)
        services = _live_services(uow, apartment_id)
        billable = [item for item in uow.furniture.items_for_apartment(
                        apartment_id, include_retired=True) if is_billable(item, month)]
        totals = _provider_totals(billable, expense, [svc["service_id"] for svc in services])
        if service_id:
            service = _service_in(uow, apartment_id, service_id, live=False)
        else:
            billing = [svc for svc in services if totals.get(svc["service_id"], 0) > 0]
            candidates = billing or services
            if len(candidates) > 1:
                raise ValidationError("Choose which provider is being paid.",
                                      code="furniture_service_required",
                                      details={"field": "service_id"})
            service = candidates[0]
        provider = _provider_name(service, uow.furniture.config(service["service_id"]))
        total = expense["amount_paise"]
        tranches = uow.furniture.service_disbursements(expense["expense_id"])
        owed = totals.get(service["service_id"], 0)
        remaining = owed - sum(tranche["amount_paise"] for tranche in tranches
                               if tranche["service_id"] == service["service_id"])
        if remaining <= 0:
            raise ConflictError(
                f"{provider} has already been paid the {month_label(month)} bill in full.",
                code="furniture_already_disbursed",
                details={"expense_id": expense["expense_id"],
                         "service_id": service["service_id"]})
        # Never more than the bill, whatever the providers' parts add up to.
        remaining = min(remaining, total - sum(tranche["amount_paise"] for tranche in tranches))
        amount = remaining if amount_paise is None else amount_paise
        if amount > remaining:
            raise ValidationError(
                f"That is more than the {format_inr(remaining)} still owed to {provider} for "
                f"{month_label(month)}.", code="tranche_exceeds_remaining",
                details={"field": "amount_paise", "amount_paise": amount,
                         "remaining_paise": remaining})
        advanced = sum(tranche["amount_paise"] for tranche in tranches
                       if tranche["disbursed_by"] == "resident_advance")
        external = uow.furniture.linked_advance_total_paise(expense["expense_id"]) - advanced
        if external > 0 or expense["payer_resident_id"]:
            raise ConflictError(
                f"The {month_label(month)} furniture bill already records a resident as having "
                "paid it, so a payout cannot also be recorded here. Correct the expense, or the "
                "payment linked to it, first.", code="furniture_already_funded",
                details={"expense_id": expense["expense_id"],
                         "payer_resident_id": expense["payer_resident_id"],
                         "linked_advance_paise": external})

        written: list[dict[str, Any]] = []
        if disbursement_mode == "pool":
            written.append(dict(disbursement_id=new_id("svcdisb"), amount_paise=amount,
                                disbursed_by="pool", funding_resident_id=None,
                                funding_payment_id=None, note=note))
        else:
            payer = get_resident(uow, funding_resident_id, apartment_id)
            name = payer["display_name"]
            open_charge = next((charge for charge in uow.furniture.resident_charges_for_expense(
                expense["expense_id"], funding_resident_id) if charge["outstanding_paise"] > 0),
                None)
            share_paise = min(amount, open_charge["outstanding_paise"]) if open_charge else 0
            extra_paise = amount - share_paise
            if share_paise:
                # Their own share goes through the kitty, as everyone else's does:
                # a payment into it, and the kitty's payout of it to the provider.
                share_payment_id = new_id("payment")
                payments._record_payment(
                    uow, apartment_id=apartment_id, payment_id=share_payment_id,
                    payer_resident_id=funding_resident_id, amount_paise=share_paise,
                    payment_date=disbursed_date, payment_kind="to_pool", payee_or_provider=None,
                    reference=reference, note=f"{service['display_name']} for {month_label(month)}",
                    timestamp=timestamp, expense_id=None, actor=actor,
                    target_allocation_id=open_charge["allocation_id"])
                written.append(dict(disbursement_id=_share_payout_id(share_payment_id),
                                    amount_paise=share_paise, disbursed_by="pool",
                                    funding_resident_id=None, funding_payment_id=None,
                                    note=f"{name}'s allotted share via House Kitty"))
            if extra_paise:
                # Only what they paid beyond their share is an advance, which the
                # kitty pays back out of what the others pay in.
                funding_payment_id = new_id("furniture-payout")
                payments._record_payment(
                    uow, apartment_id=apartment_id, payment_id=funding_payment_id,
                    payer_resident_id=funding_resident_id, amount_paise=extra_paise,
                    payment_date=disbursed_date, payment_kind="provider_advance",
                    payee_or_provider=provider, reference=reference,
                    note=(f"Extra paid for {service['display_name']} ({month_label(month)}) "
                          "to be reimbursed via House Kitty"),
                    timestamp=timestamp, expense_id=expense["expense_id"], actor=actor)
                written.append(dict(disbursement_id=new_id("svcdisb"), amount_paise=extra_paise,
                                    disbursed_by="resident_advance",
                                    funding_resident_id=funding_resident_id,
                                    funding_payment_id=funding_payment_id,
                                    note=note or (f"Extra above {name}'s share "
                                                  "(reimbursable via House Kitty)")))
        for tranche in written:
            uow.furniture.insert_service_disbursement(
                service_id=service["service_id"], expense_id=expense["expense_id"],
                apartment_id=apartment_id, settlement_month=month, disbursed_at=disbursed_date,
                disbursement_reference=reference, created_at=timestamp, **tranche)
        audit.record(uow, entity_type="household_service", entity_id=service["service_id"],
                     action="disburse", actor=actor, apartment_id=apartment_id,
                     new_value={"settlement_month": month, "expense_id": expense["expense_id"],
                                "amount_paise": amount, "total_paise": total,
                                "remaining_paise": remaining - amount,
                                "disbursed_by": disbursement_mode, "disbursed_at": disbursed_date,
                                "disbursement_reference": reference,
                                "funding_resident_id": funding_resident_id, "note": note,
                                "tranches": [{key: tranche[key] for key in (
                                    "disbursement_id", "amount_paise", "disbursed_by",
                                    "funding_payment_id")} for tranche in written]})
        return _overview(uow, apartment_id, month)


def mark_paid_by_resident(db: Database, apartment_id: str, settlement_month: str,
                          payer_resident_id: str, *, payment_date: str,
                          amount_paise: int | None = None, reference: str | None = None,
                          service_id: str | None = None, notes: str | None = None,
                          actor: str = "operator") -> dict[str, Any]:
    """A resident paying a provider from their own account: a ``resident_advance``
    payout (see :func:`record_provider_payout`), kept for its original endpoint."""
    return record_provider_payout(
        db, apartment_id, settlement_month, disbursement_mode="resident_advance",
        reference=reference or "", disbursed_date=payment_date, amount_paise=amount_paise,
        funding_resident_id=payer_resident_id, service_id=service_id, notes=notes,
        actor=actor)


def delete_payment(db: Database, apartment_id: str, payment_id: str, *,
                   actor: str = "operator") -> dict[str, Any]:
    """Delete, while its month is open, a resident's payment into the kitty towards
    a furniture bill or the kitty's reimbursement for one -- recorded by mistake.
    Returns the overview of that month. See :mod:`aem.services.deletions`."""
    with db.transaction() as uow:
        get_apartment(uow, apartment_id)
        deleted = deletions.delete_payment_in(uow, apartment_id=apartment_id, scope="service",
                                              payment_id=payment_id, actor=actor)
        return {**_overview(uow, apartment_id, deleted["settlement_month"]),
                "deleted": deleted}


def delete_provider_payout(db: Database, apartment_id: str, disbursement_id: str, *,
                           actor: str = "operator") -> dict[str, Any]:
    """Delete, while its month is open, a payout to a provider recorded by mistake,
    with the payments recorded alongside it. Returns the overview of that month."""
    with db.transaction() as uow:
        get_apartment(uow, apartment_id)
        deleted = deletions.delete_payout_in(uow, apartment_id=apartment_id, scope="service",
                                             disbursement_id=disbursement_id, actor=actor)
        return {**_overview(uow, apartment_id, deleted["settlement_month"]),
                "deleted": deleted}


def reimburse_from_kitty(db: Database, apartment_id: str, settlement_month: str,
                         recipient_resident_id: str, *, amount_paise: int, payment_date: str,
                         reference: str | None = None, notes: str | None = None,
                         actor: str = "operator") -> dict[str, Any]:
    """Pay back, from the kitty, a resident who paid a provider more than their share.

    It is a ``refund`` to them out of what the kitty collected for the bill. It
    can be no more than the kitty has collected for it and not yet paid back,
    nor more than they are still owed for it; part of it can wait until the
    rest of the flatmates have paid in.
    """
    month = normalize_month(settlement_month, "settlement_month")
    amount = ensure_paise(amount_paise, "amount_paise", allow_zero=False)
    payment_date = parse_iso_date(payment_date, "payment_date").isoformat()
    timestamp = now_timestamp()
    with db.transaction() as uow:
        get_apartment(uow, apartment_id)
        service = _require_live(uow, apartment_id)
        _sync_live_month_bill(uow, apartment_id, month, check_reduction_guards=False,
                              timestamp=timestamp, actor=actor)
        expense = _require_recorded_bill(uow, apartment_id, service, month)
        provider = "the provider"
        owing = _funding(uow, expense)["reimbursements"]
        if not owing:
            raise ConflictError(
                f"Nobody is recorded as having paid a provider more than their share for "
                f"{month_label(month)}, so the kitty has nobody to pay back.",
                code="furniture_no_payout", details={"settlement_month": month})
        owed = next((entry for entry in owing
                     if entry["recipient_resident_id"] == recipient_resident_id), None)
        if owed is None:
            names = ", ".join(entry["recipient_name"] for entry in owing)
            raise ValidationError(
                f"The kitty owes {names} for {month_label(month)}, not this resident.",
                code="not_the_payout_resident",
                details={"field": "recipient_resident_id",
                         "payer_resident_ids": [entry["recipient_resident_id"]
                                                for entry in owing]})
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
                f"flatmates for this bill that has not already gone to {provider} or back to "
                f"{name}.",
                code="exceeds_kitty_cash",
                details={"field": "amount_paise", "amount_paise": amount,
                         "kitty_cash_available_paise": owed["kitty_cash_available_paise"]})
        payment = payments._record_payment(
            uow, apartment_id=apartment_id,
            payment_id=new_id(_reimbursement_id_base(expense["expense_id"])),
            payer_resident_id=recipient_resident_id, amount_paise=amount,
            payment_date=payment_date, payment_kind="refund", payee_or_provider=None,
            reference=reference,
            note=(optional_text(notes, "notes", max_length=500)
                  or f"Reimbursement for {service['display_name']} for {month_label(month)}, "
                     f"paid to {provider} on the household's behalf"),
            timestamp=timestamp, expense_id=None, actor=actor)
        audit.record(uow, entity_type="household_service", entity_id=service["service_id"],
                     action="reimburse", actor=actor, apartment_id=apartment_id,
                     new_value={"settlement_month": month, "expense_id": expense["expense_id"],
                                "recipient_resident_id": recipient_resident_id,
                                "amount_paise": amount, "payment_id": payment["payment_id"],
                                "payment_date": payment_date, "reference": reference})
        return _overview(uow, apartment_id, month)

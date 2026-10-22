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
"""
from __future__ import annotations

import calendar
from datetime import date
from typing import Any, Mapping, Sequence

from ..errors import ConflictError, NotFoundError, ValidationError
from ..ids import new_id, now_timestamp
from ..money import ensure_paise, format_inr
from ..periods import month_end, normalize_month, parse_iso_date
from ..persistence import Database, UnitOfWork
from . import audit, definitions, expenses
from .common import get_apartment, list_residents, optional_text, require_text
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
    return uow.furniture.live_service(apartment_id, SERVICE_TYPE)


def _require_live(uow: UnitOfWork, apartment_id: str) -> dict[str, Any]:
    service = _live(uow, apartment_id)
    if service is None:
        raise ConflictError("Set up furniture rental first.", code="furniture_not_configured")
    return service


def _item_in(uow: UnitOfWork, apartment_id: str, item_id: str) -> dict[str, Any]:
    item = uow.furniture.item(item_id)
    if item is None or item["apartment_id"] != apartment_id:
        raise NotFoundError("No such rented item.", code="furniture_item_not_found",
                            details={"item_id": item_id})
    return item


def _public_item(item: Mapping[str, Any]) -> dict[str, Any]:
    return {key: item.get(key) for key in (
        "item_id", "service_id", "name", "category", "monthly_rent_paise", "allocation_scope",
        "assigned_resident_id", "assigned_resident_name", "status", "delivery_date",
        "return_date", "created_at", "updated_at")}


# -------------------------------------------------------- the month's bill --

def _month_bill(uow: UnitOfWork, apartment_id: str, items: list[dict[str, Any]], month: str,
                excluded_resident_ids: Sequence[str] | None = None) -> dict[str, Any]:
    """What `month` would bill, and to whom. Nothing is written."""
    residents = list_residents(uow, apartment_id)
    names = {row["resident_id"]: row["display_name"] for row in residents}
    active = sorted(row["resident_id"] for row in residents if row["is_active"])
    excluded = sorted({str(rid) for rid in (excluded_resident_ids or [])})
    unknown = sorted(set(excluded) - set(names))
    if unknown:
        raise ValidationError("excluded_resident_ids must all be residents of this apartment.",
                              details={"field": "excluded_resident_ids", "unknown": unknown})
    participants = [rid for rid in active if rid not in excluded]

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


# ----------------------------------------------------------------- overview --

def get_overview(db: Database, apartment_id: str, settlement_month: str) -> dict[str, Any]:
    """The subscription, its items, and what `settlement_month` bills.

    ``service`` is null until furniture rental is set up, and after it is
    retired until it is set up again.
    """
    month = normalize_month(settlement_month, "month")
    with db.read() as uow:
        get_apartment(uow, apartment_id)
        return _overview(uow, apartment_id, month)


def _overview(uow: UnitOfWork, apartment_id: str, month: str) -> dict[str, Any]:
    service = _live(uow, apartment_id)
    if service is None:
        return {"service": None, "config": None, "items": [], "month": None,
                "monthly_total_paise": 0, "active_item_count": 0, "next_billing_date": None,
                "deposit_held_paise": 0}
    config = uow.furniture.config(service["service_id"])
    items = uow.furniture.items(service["service_id"])
    bill = _month_bill(uow, apartment_id, items, month)
    recorded = (uow.furniture.expenses_for_month(apartment_id, config["expense_definition_id"],
                                                 month)
                if config and config["expense_definition_id"] else [])
    statement = uow.furniture.statement_for_month(apartment_id, month)
    expense = recorded[0] if recorded else None
    bill["expense"] = expense
    bill["statement_status"] = statement["status"] if statement else None
    bill["can_generate"] = (expense is None and bill["blocked_reason"] is None
                            and (statement is None or statement["status"] == "draft"))
    active = [item for item in items if item["status"] == "active"]
    return {
        "service": {key: service[key] for key in (
            "service_id", "service_type", "display_name", "provider_name", "status",
            "billing_day_of_month", "period_rule", "created_at", "updated_at")},
        "config": {key: config[key] for key in (
            "provider_name", "customer_id", "subscription_reference", "deposit_paid_paise",
            "deposit_refunded_paise", "deposit_status", "expense_definition_id", "notes",
            "updated_at")} if config else None,
        "items": [_public_item(item) for item in items],
        "month": bill,
        "monthly_total_paise": sum(item["monthly_rent_paise"] for item in active),
        "active_item_count": len(active),
        "next_billing_date": next_billing_date(service["billing_day_of_month"], _today()),
        "deposit_held_paise": (config["deposit_paid_paise"] - config["deposit_refunded_paise"])
        if config else 0,
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
    """Set up the furniture rental subscription, or update the one the flat has.

    Only the fields sent are changed. Each month's bill is recorded under the
    household's "Furniture rental" definition when it has one; otherwise a
    non-recurring one is created, since the bill is generated from this module
    rather than by the recurring generator.
    """
    month = normalize_month(settlement_month or _today()[:7], "month")
    timestamp = now_timestamp()
    with db.read() as uow:
        get_apartment(uow, apartment_id)
        service = _live(uow, apartment_id)
        config = uow.furniture.config(service["service_id"]) if service else None
        before = {**(config or {}), **(service or {})}
        cleaned = _clean_setup(payload, before if service else None)
        definition_id = (config or {}).get("expense_definition_id") \
            or uow.furniture.find_definition_id(apartment_id)
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
        service = _live(uow, apartment_id)
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


def retire_furniture_rental(db: Database, apartment_id: str, *,
                            settlement_month: str | None = None,
                            actor: str = "operator") -> dict[str, Any]:
    """End the subscription. Every item must have been returned first; the
    service, its items and every bill already recorded stay for the record."""
    month = normalize_month(settlement_month or _today()[:7], "month")
    timestamp = now_timestamp()
    with db.transaction() as uow:
        service = _require_live(uow, apartment_id)
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
    """An item's fields, checked together as they will be stored."""
    name = require_text(values.get("name"), "name", max_length=200)
    category = values.get("category")
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
    return {"name": name, "category": category, "monthly_rent_paise": rent,
            "allocation_scope": scope, "assigned_resident_id": resident_id if scope == "resident"
            else None, "status": status, "delivery_date": delivery, "return_date": returned}


def add_item(db: Database, apartment_id: str, payload: Mapping[str, Any], *,
             actor: str = "operator") -> dict[str, Any]:
    timestamp = now_timestamp()
    with db.transaction() as uow:
        service = _require_live(uow, apartment_id)
        values = _clean_item(uow, apartment_id, payload)
        item_id = new_id("furn")
        uow.furniture.insert_item(item_id=item_id, service_id=service["service_id"],
                                  apartment_id=apartment_id, created_at=timestamp, **values)
        audit.record(uow, entity_type="furniture_rental_item", entity_id=item_id,
                     action="create", actor=actor, apartment_id=apartment_id, new_value=values)
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
        return _public_item(uow.furniture.item(item_id))


def delete_item(db: Database, apartment_id: str, item_id: str, *,
                actor: str = "operator") -> None:
    """Remove an item entered by mistake. A bill already recorded keeps the
    items behind it in its notes; to stop paying for an item, return it."""
    with db.transaction() as uow:
        before = _item_in(uow, apartment_id, item_id)
        uow.furniture.delete_item(item_id)
        audit.record(uow, entity_type="furniture_rental_item", entity_id=item_id,
                     action="delete", actor=actor, apartment_id=apartment_id,
                     old_value=_public_item(before))


# ------------------------------------------------------------ generating -- #

def generate_month(db: Database, apartment_id: str, settlement_month: str,
                   excluded_resident_ids: Sequence[str] | None = None, *,
                   actor: str = "operator") -> dict[str, Any]:
    """Record `settlement_month`'s furniture bill as one expense, in one transaction.

    The expense is due on the billing day, if one is set, and is attached to the
    month's statement, which must still be a draft. A month already recorded is
    refused, as is a month whose bill cannot be shared (see ``blocked_reason``).
    """
    month = normalize_month(settlement_month, "settlement_month")
    timestamp = now_timestamp()
    with db.transaction() as uow:
        get_apartment(uow, apartment_id)
        service = _require_live(uow, apartment_id)
        config = uow.furniture.config(service["service_id"])
        definition_id = config["expense_definition_id"] if config else None
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
        bill = _month_bill(uow, apartment_id, uow.furniture.items(service["service_id"]), month,
                           excluded_resident_ids)
        if bill["blocked_reason"]:
            raise ValidationError(bill["blocked_reason"], code="furniture_bill_blocked",
                                  details={"settlement_month": month})

        allocations = {row["resident_id"]: 0 for row in list_residents(uow, apartment_id)}
        allocations.update({row["resident_id"]: row["total_paise"] for row in bill["shares"]})
        expense_id = new_id("furniture")
        expenses._create_expense(
            uow, apartment_id=apartment_id, expense_id=expense_id,
            description=f"{service['display_name']} for {month_label(month)}",
            amount_paise=bill["total_paise"], expense_date=f"{month}-01",
            allocation_method="custom", allocations_paise=allocations,
            allocation_basis={"method": "custom", "source": SERVICE_TYPE,
                              "common_total_paise": bill["common_total_paise"],
                              "private_total_paise": bill["private_total_paise"]},
            provider_name=service["provider_name"], expense_definition_id=definition_id,
            statement_id=statement["statement_id"] if statement else None,
            period_rule="current_month", service_period=month, settlement_period=month,
            due_date=(_billing_date(month, service["billing_day_of_month"])
                      if service["billing_day_of_month"] else None),
            notes=_bill_notes(bill), created_at=timestamp, actor=actor)
        audit.record(uow, entity_type="household_service", entity_id=service["service_id"],
                     action="generate", actor=actor, apartment_id=apartment_id,
                     new_value={"settlement_month": month, "expense_id": expense_id,
                                "total_paise": bill["total_paise"],
                                "common_total_paise": bill["common_total_paise"],
                                "private_total_paise": bill["private_total_paise"]})
        return _overview(uow, apartment_id, month)

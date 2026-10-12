"""Expense definitions and recurring generation.

No category is hardcoded anywhere in the application. Rent, electricity,
groceries, maintenance, maid, broadband, and furniture rental are all just rows
in ``expense_definition`` that the operator creates, edits, and deactivates.
The seed helper offers a starting set only when the operator asks for it.

A definition carries:

* the period rule (see :mod:`aem.periods`), which decides which statement month
  charges an expense entered against it;
* the default allocation method, participants, and weights;
* an optional default amount, used as the suggestion when generating a
  recurring expense. The actual amount is free to differ every month.
"""
from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from ..errors import ConflictError, NotFoundError, ValidationError
from ..ids import new_id, now_timestamp
from ..money import ensure_paise
from ..periods import PERIOD_RULES, normalize_month, resolve_periods
from ..persistence import Database, IntegrityViolation
from . import audit
from .common import (
    ALLOCATION_METHODS,
    RECURRENCES,
    get_apartment,
    optional_text,
    require_text,
    resident_ids,
)

EDITABLE_FIELDS = ["name", "category", "period_rule", "default_allocation_method",
                   "is_recurring", "recurrence", "default_amount_paise",
                   "default_provider_name", "default_participants_json",
                   "default_weights_json", "day_of_month", "notes", "sort_order",
                   "is_active"]

# The essentials catalogue.
#
# A suggestion list, nothing more. Every entry creates an ordinary
# `expense_definition` row the operator can rename, re-rule, or retire -- the
# application has no special knowledge of "rent" or "electricity" anywhere.
# It exists so the guided setup can offer sensible period rules rather than
# making a new operator work them out from scratch.
#
# `suggested` entries are pre-selected in the guided setup, because nearly
# every shared flat pays them. The rest are one tick away.
ESSENTIALS_CATALOGUE = (
    {"definition_id": "rent", "name": "Rent", "category": "Housing",
     "period_rule": "current_month", "default_allocation_method": "equal",
     "recurrence": "monthly", "sort_order": 10, "suggested": True,
     "hint": "October's rent is charged in the October statement."},
    {"definition_id": "electricity", "name": "Electricity", "category": "Utilities",
     "period_rule": "previous_month", "default_allocation_method": "measured",
     "recurrence": "monthly", "sort_order": 20, "suggested": True,
     "hint": "The bill that arrives in October is for September's usage, and is "
             "charged in the October statement. Entered through the Electricity screen."},
    # The identifier stays "maid" although the display name is now "House help":
    # changing it would give anyone who already seeded the old starter set a
    # second, duplicate category rather than a renamed one. The identifier is an
    # internal key; the name is what the operator sees, and they can rename it.
    {"definition_id": "maid", "name": "House help", "category": "Household help",
     "period_rule": "current_month", "default_allocation_method": "equal",
     "recurrence": "monthly", "sort_order": 30, "suggested": True,
     "hint": "Cleaning, washing, and similar. Charged in the month it covers."},
    {"definition_id": "cook", "name": "Cook", "category": "Household help",
     "period_rule": "current_month", "default_allocation_method": "equal",
     "recurrence": "monthly", "sort_order": 40, "suggested": False,
     "hint": "Separate from house help if you pay them separately."},
    {"definition_id": "broadband", "name": "Broadband", "category": "Utilities",
     "period_rule": "current_month", "default_allocation_method": "equal",
     "recurrence": "monthly", "sort_order": 50, "suggested": True,
     "hint": "Charged in the month it covers."},
    {"definition_id": "maintenance", "name": "Society maintenance", "category": "Housing",
     "period_rule": "current_month", "default_allocation_method": "equal",
     "recurrence": "monthly", "sort_order": 60, "suggested": True,
     "hint": "The building or society charge."},
    {"definition_id": "water", "name": "Water", "category": "Utilities",
     "period_rule": "current_month", "default_allocation_method": "equal",
     "recurrence": "monthly", "sort_order": 70, "suggested": True,
     "hint": "Tanker or metered water, where it is billed separately."},
    {"definition_id": "cooking_gas", "name": "Cooking gas", "category": "Utilities",
     "period_rule": "transaction_date", "default_allocation_method": "equal",
     "recurrence": "none", "sort_order": 80, "suggested": True,
     "hint": "A cylinder is bought when it runs out, so it is charged in the "
             "month you bought it rather than on a schedule."},
    {"definition_id": "groceries", "name": "Groceries", "category": "Food",
     "period_rule": "previous_month", "default_allocation_method": "equal",
     "recurrence": "none", "sort_order": 90, "suggested": True,
     "hint": "Bought through the month and settled the month after, so it is "
             "charged in the following statement."},
    {"definition_id": "furniture_rental", "name": "Furniture rental", "category": "Housing",
     "period_rule": "current_month", "default_allocation_method": "equal",
     "recurrence": "monthly", "sort_order": 100, "suggested": False,
     "hint": "Rented beds, wardrobes, appliances."},
    {"definition_id": "dth", "name": "DTH or cable", "category": "Utilities",
     "period_rule": "current_month", "default_allocation_method": "equal",
     "recurrence": "monthly", "sort_order": 110, "suggested": False,
     "hint": "A television subscription, where the flat shares one."},
    {"definition_id": "repairs", "name": "Repairs and replacements", "category": "Household",
     "period_rule": "transaction_date", "default_allocation_method": "equal",
     "recurrence": "none", "sort_order": 120, "suggested": False,
     "hint": "One-off things: a plumber, a new geyser, a broken chair."},
)

#: Backwards-compatible alias. `seed_starter_definitions` creates the suggested
#: subset, which is what the Setup screen's one-press starter button offers.
STARTER_DEFINITIONS = tuple(
    {key: value for key, value in entry.items() if key not in ("suggested", "hint")}
    for entry in ESSENTIALS_CATALOGUE if entry["suggested"]
)


def _json_list(value: Any, label: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value or None
    if not isinstance(value, Sequence):
        raise ValidationError(f"{label} must be a list of resident IDs.",
                              details={"field": label})
    items = [str(item) for item in value]
    if len(set(items)) != len(items):
        raise ValidationError(f"{label} must not repeat a resident.", details={"field": label})
    return json.dumps(items, sort_keys=False)


def _json_weights(value: Any, label: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value or None
    if not isinstance(value, Mapping):
        raise ValidationError(f"{label} must map resident IDs to whole-number weights.",
                              details={"field": label})
    cleaned: dict[str, int] = {}
    for rid, weight in value.items():
        if isinstance(weight, bool) or not isinstance(weight, int) or weight < 0:
            raise ValidationError(
                f"{label} for {rid} must be a non-negative whole number.",
                details={"field": label, "resident_id": str(rid)})
        cleaned[str(rid)] = weight
    return json.dumps(cleaned, sort_keys=True)


def _decode(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["default_participants"] = json.loads(row["default_participants_json"]) if row.get(
        "default_participants_json") else None
    out["default_weights"] = json.loads(row["default_weights_json"]) if row.get(
        "default_weights_json") else None
    out["is_recurring"] = bool(row.get("is_recurring"))
    out["is_active"] = bool(row.get("is_active"))
    return out


def create_expense_definition(
    db: Database, *, apartment_id: str, definition_id: str, name: str, category: str,
    period_rule: str, default_allocation_method: str = "equal",
    is_recurring: bool = True, recurrence: str | None = None,
    default_amount_paise: int | None = None, default_provider_name: str | None = None,
    default_participants: Sequence[str] | None = None,
    default_weights: Mapping[str, int] | None = None,
    day_of_month: int | None = None, notes: str | None = None,
    sort_order: int = 100, actor: str = "operator",
) -> dict[str, Any]:
    if period_rule not in PERIOD_RULES:
        raise ValidationError(f"Unsupported period_rule: {period_rule}",
                              details={"field": "period_rule",
                                       "allowed": list(PERIOD_RULES)})
    if default_allocation_method not in ALLOCATION_METHODS:
        raise ValidationError(f"Unsupported allocation method: {default_allocation_method}",
                              details={"field": "default_allocation_method",
                                       "allowed": list(ALLOCATION_METHODS)})
    if not isinstance(name, str) or not name.strip() \
            or not isinstance(category, str) or not category.strip() \
            or not isinstance(definition_id, str) or not definition_id.strip():
        raise ValidationError("Definition ID, name, and category are required.")
    resolved_recurrence = recurrence or ("monthly" if is_recurring else "none")
    if resolved_recurrence not in RECURRENCES:
        raise ValidationError(f"Unsupported recurrence: {resolved_recurrence}",
                              details={"field": "recurrence", "allowed": list(RECURRENCES)})
    if default_amount_paise is not None:
        ensure_paise(default_amount_paise, "default_amount_paise")
    if day_of_month is not None and not (1 <= int(day_of_month) <= 28):
        raise ValidationError(
            "day_of_month must be between 1 and 28 so every month has that day.",
            details={"field": "day_of_month"})
    timestamp = now_timestamp()
    with db.transaction() as uow:
        get_apartment(uow, apartment_id)
        if uow.definitions.exists(definition_id.strip()):
            raise ConflictError("Could not create expense definition: ID already exists.",
                                code="definition_exists",
                                details={"expense_definition_id": definition_id.strip()})
        known = set(resident_ids(uow, apartment_id))
        participants_json = _json_list(default_participants, "default_participants")
        weights_json = _json_weights(default_weights, "default_weights")
        for label, raw in (("default_participants", participants_json),
                           ("default_weights", weights_json)):
            if not raw:
                continue
            decoded = json.loads(raw)
            ids = decoded if isinstance(decoded, list) else list(decoded.keys())
            unknown = sorted(set(ids) - known)
            if unknown:
                raise ValidationError(
                    f"{label} names residents who do not belong to this apartment.",
                    details={"field": label, "unknown_resident_ids": unknown})
        try:
            uow.definitions.insert_definition(
                expense_definition_id=definition_id.strip(), apartment_id=apartment_id,
                name=name.strip(), category=category.strip(), period_rule=period_rule,
                default_allocation_method=default_allocation_method,
                is_recurring=int(bool(is_recurring)), recurrence=resolved_recurrence,
                default_amount_paise=default_amount_paise,
                default_provider_name=optional_text(
                    default_provider_name, "default_provider_name", max_length=200),
                default_participants_json=participants_json,
                default_weights_json=weights_json,
                day_of_month=int(day_of_month) if day_of_month is not None else None,
                notes=optional_text(notes, "notes"), sort_order=int(sort_order),
                created_at=timestamp, updated_at=timestamp)
        except IntegrityViolation as exc:
            raise ValidationError(f"Could not create expense definition: {exc}") from exc
        audit.record(uow, entity_type="expense_definition", entity_id=definition_id.strip(),
                     action="create", actor=actor, apartment_id=apartment_id,
                     new_value={"name": name.strip(), "category": category.strip(),
                                "period_rule": period_rule,
                                "recurrence": resolved_recurrence})
        return _decode(uow.definitions.get(definition_id.strip()))


def update_expense_definition(db: Database, definition_id: str, *,
                              apartment_id: str | None = None,
                              actor: str = "operator", reason: str | None = None,
                              **changes: Any) -> dict[str, Any]:
    raw_changes = dict(changes)
    if "default_participants" in raw_changes:
        raw_changes["default_participants_json"] = _json_list(
            raw_changes.pop("default_participants"), "default_participants")
    if "default_weights" in raw_changes:
        raw_changes["default_weights_json"] = _json_weights(
            raw_changes.pop("default_weights"), "default_weights")
    unknown = sorted(set(raw_changes) - set(EDITABLE_FIELDS))
    if unknown:
        raise ValidationError(f"Unsupported definition fields: {', '.join(unknown)}.",
                              details={"unsupported_fields": unknown,
                                       "editable_fields": EDITABLE_FIELDS})
    with db.transaction() as uow:
        before = uow.definitions.get(definition_id)
        if before is None:
            raise NotFoundError("Expense definition not found.",
                                details={"expense_definition_id": definition_id})
        if apartment_id is not None and before["apartment_id"] != apartment_id:
            raise ValidationError("Expense definition must belong to this apartment.",
                                  details={"expense_definition_id": definition_id})
        after = dict(before)
        if "name" in raw_changes:
            after["name"] = require_text(raw_changes["name"], "name", max_length=200)
        if "category" in raw_changes:
            after["category"] = require_text(raw_changes["category"], "category", max_length=120)
        if "period_rule" in raw_changes:
            if raw_changes["period_rule"] not in PERIOD_RULES:
                raise ValidationError(f"Unsupported period_rule: {raw_changes['period_rule']}",
                                      details={"field": "period_rule"})
            after["period_rule"] = raw_changes["period_rule"]
        if "default_allocation_method" in raw_changes:
            if raw_changes["default_allocation_method"] not in ALLOCATION_METHODS:
                raise ValidationError(
                    f"Unsupported allocation method: {raw_changes['default_allocation_method']}",
                    details={"field": "default_allocation_method"})
            after["default_allocation_method"] = raw_changes["default_allocation_method"]
        if "recurrence" in raw_changes:
            if raw_changes["recurrence"] not in RECURRENCES:
                raise ValidationError(f"Unsupported recurrence: {raw_changes['recurrence']}",
                                      details={"field": "recurrence"})
            after["recurrence"] = raw_changes["recurrence"]
            after["is_recurring"] = 0 if raw_changes["recurrence"] == "none" else 1
        if "is_recurring" in raw_changes:
            after["is_recurring"] = 1 if raw_changes["is_recurring"] else 0
            if "recurrence" not in raw_changes:
                after["recurrence"] = "monthly" if after["is_recurring"] else "none"
        if "is_active" in raw_changes:
            after["is_active"] = 1 if raw_changes["is_active"] else 0
        if "default_amount_paise" in raw_changes:
            value = raw_changes["default_amount_paise"]
            after["default_amount_paise"] = None if value is None else ensure_paise(
                value, "default_amount_paise")
        if "default_provider_name" in raw_changes:
            after["default_provider_name"] = optional_text(
                raw_changes["default_provider_name"], "default_provider_name", max_length=200)
        if "notes" in raw_changes:
            after["notes"] = optional_text(raw_changes["notes"], "notes")
        if "day_of_month" in raw_changes:
            value = raw_changes["day_of_month"]
            if value is not None and not (1 <= int(value) <= 28):
                raise ValidationError("day_of_month must be between 1 and 28.",
                                      details={"field": "day_of_month"})
            after["day_of_month"] = None if value is None else int(value)
        if "sort_order" in raw_changes:
            after["sort_order"] = int(raw_changes["sort_order"])
        for key in ("default_participants_json", "default_weights_json"):
            if key in raw_changes:
                after[key] = raw_changes[key]
        after["updated_at"] = now_timestamp()
        uow.definitions.update_definition(
            definition_id, name=after["name"], category=after["category"],
            period_rule=after["period_rule"],
            default_allocation_method=after["default_allocation_method"],
            is_recurring=after["is_recurring"], is_active=after["is_active"],
            recurrence=after["recurrence"],
            default_amount_paise=after["default_amount_paise"],
            default_provider_name=after["default_provider_name"],
            default_participants_json=after["default_participants_json"],
            default_weights_json=after["default_weights_json"],
            day_of_month=after["day_of_month"], notes=after["notes"],
            sort_order=after["sort_order"], updated_at=after["updated_at"])
        audit.record_field_changes(
            uow, entity_type="expense_definition", entity_id=definition_id, before=before,
            after=after, fields=EDITABLE_FIELDS, actor=actor,
            apartment_id=before["apartment_id"], reason=reason)
        return _decode(uow.definitions.get(definition_id))


def deactivate_expense_definition(db: Database, definition_id: str, *,
                                  apartment_id: str | None = None,
                                  actor: str = "operator") -> dict[str, Any]:
    """Deactivate rather than delete: expenses already recorded against the
    definition keep their link and stay auditable."""
    return update_expense_definition(db, definition_id, apartment_id=apartment_id,
                                     actor=actor, reason="definition deactivated",
                                     is_active=False)


def list_expense_definitions(db: Database, apartment_id: str, *,
                             include_inactive: bool = False) -> list[dict[str, Any]]:
    """List definitions. Only active ones by default, matching the prototype."""
    with db.read() as uow:
        return [_decode(row) for row in uow.definitions.list_for_apartment(
            apartment_id, include_inactive=include_inactive)]


def read_expense_definition(db: Database, definition_id: str) -> dict[str, Any]:
    with db.read() as uow:
        row = uow.definitions.get(definition_id)
        if row is None:
            raise NotFoundError("Expense definition not found.",
                                details={"expense_definition_id": definition_id})
        return _decode(row)


def seed_starter_definitions(db: Database, apartment_id: str, *,
                             actor: str = "operator") -> list[dict[str, Any]]:
    """Create a conventional starting set of definitions, on request only.

    Each one is an ordinary editable row; none of these names is special to the
    application. Definitions that already exist are left alone.
    """
    created: list[dict[str, Any]] = []
    existing = {row["expense_definition_id"]
                for row in list_expense_definitions(db, apartment_id,
                                                    include_inactive=True)}
    for starter in STARTER_DEFINITIONS:
        if starter["definition_id"] in existing:
            continue
        created.append(create_expense_definition(
            db, apartment_id=apartment_id, actor=actor,
            is_recurring=starter["recurrence"] != "none", **starter))
    return created

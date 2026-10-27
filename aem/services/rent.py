"""Rent under a single master lease, split equally.

The household has one lease, one landlord, and one monthly rent. Every active
resident who takes part in a month owes an equal share of it.

A **rent cycle** is one month's rent. It does not hold the accounting itself:
it wraps an ordinary ``expense_instance`` with ``allocation`` rows, so rent is
charged, paid off, reconciled, and put on a statement exactly like any other
expense. What the cycle adds is the part an expense cannot say -- whether the
landlord has actually been paid, from where, and with which bank reference.

Two flows of money, kept apart:

* **Internal collection.** Residents pay their share into the pool. A payment
  recorded here as rent is a ``to_pool`` payment *targeted* at that resident's
  rent charge for the month, so it settles the rent it was paid for even when
  they have older charges open. A pool payment recorded elsewhere, with no
  target, still goes to their oldest charge first.
* **Landlord disbursement.** The rent goes to the landlord, in one payout or
  several, from the pool or from one resident's own account. A resident who
  pays the landlord pays their own open share through the kitty, as everyone
  else does: a payment into it settling their share, and the kitty's payout of
  it. Only what they paid beyond their share is a ``provider_advance`` linked
  to the rent expense, which the kitty pays back to them -- out of what the
  others pay in -- as a ``refund`` (a **reimbursement**). Nothing is counted
  twice, because a linked advance replaces the expense's payer field as its
  funding record.

Rules this module holds to (see ``docs/ACCOUNTING_RULES.md``):

* **Nothing prorates.** A resident who takes part is charged a full share,
  whenever they moved in or out. Who takes part is settled when the month is
  charged; leaving somebody out is an explicit decision made then.
* **An open month follows its lease** -- see "the live cycle" below -- and
  freezes as its snapshot once it is over, finalized, or paid to the landlord
  in full.
* **Remainder paise** that cannot be divided go one each to non-payers first,
  in resident-ID order, and to the resident who paid the landlord last.
* **The shares always sum to the rent exactly.**

If the month's rent already exists as an expense -- because the recurring
generator created it -- the cycle adopts that expense rather than creating a
second one.
"""
from __future__ import annotations

import base64
import binascii
import calendar
import hashlib
import json
import re
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Mapping, Sequence

from ..errors import ConflictError, NotFoundError, ValidationError
from ..ids import new_id, now_timestamp
from ..money import ensure_paise, format_inr, paise_in_words
from ..periods import month_label, normalize_month, parse_iso_date
from ..persistence import Database, UnitOfWork
from . import audit, bill_shares, definitions, deletions, expenses, names, payments
from . import property as household
from .allocation import plan_allocation
from .apartments import premises_address
from .common import (
    get_apartment,
    get_expense,
    get_resident,
    optional_text,
    require_text,
    resident_ids,
    statement_edit_mode,
)

RENT_CATEGORY = "Housing"
DISBURSEMENT_MODES = ("pool", "resident_advance")
#: How a month's rent is divided: equally, or by the base rent of each
#: participant's room.
SPLIT_MODES = ("equal", "room_tiered")

#: What a landlord's receipt can be: a PDF or a photo of a paper slip.
LANDLORD_RECEIPT_TYPES = ("application/pdf", "image/jpeg", "image/png", "image/webp")
MAX_LANDLORD_RECEIPT_BYTES = 5 * 1024 * 1024

#: An employer asks for the landlord's PAN once rent passes ₹1,00,000 a year.
PAN_REQUIRED_ABOVE_ANNUAL_PAISE = 10_000_000


#: The landlord's name, in parts; ``landlord_name`` is composed from them.
LANDLORD_NAME_PARTS = ("landlord_first_name", "landlord_middle_name", "landlord_last_name")

_CONFIG_FIELDS = ("landlord_name", *LANDLORD_NAME_PARTS, "landlord_phone", "landlord_email",
                  "landlord_pan",
                  "landlord_bank_account", "landlord_bank_ifsc", "landlord_upi_id",
                  "payout_beneficiary_name", "monthly_rent_paise", "due_day_of_month",
                  "requires_landlord_receipt", "expense_definition_id", "rent_split_mode")

#: The contract term. A save that leaves one of these out keeps what the lease
#: already has, so a client that only knows the landlord fields cannot wipe the
#: term; a new lease takes the default.
_TERM_DEFAULTS: dict[str, Any] = {
    "lease_start_date": None, "lease_end_date": None, "duration_months": 11,
    "notice_period_days": 30, "lock_in_months": 6, "security_deposit_paise": None,
    "escalation_type": "percent", "escalation_percent": 5.0, "renewal_rent_paise": None,
}
ESCALATION_TYPES = ("percent", "fixed", "none")
MAX_TERM_MONTHS = 60


# ---------------------------------------------------------------- the lease --

def _reject_unknown(payload: Mapping[str, Any], allowed: Sequence[str], what: str) -> None:
    unknown = sorted(set(payload) - set(allowed))
    if unknown:
        raise ValidationError(f"Unsupported {what} fields: {', '.join(unknown)}.",
                              details={"unsupported_fields": unknown})


def _add_months(value: date, months: int) -> date:
    """The same day `months` later, pulled back to the month's last day if needed."""
    index = value.year * 12 + (value.month - 1) + months
    year, month = divmod(index, 12)
    return date(year, month + 1, min(value.day, calendar.monthrange(year, month + 1)[1]))


def term_end_date(start: date, duration_months: int) -> date:
    """The last day of a term: a term from 1 March for 11 months ends 31 January."""
    return _add_months(start, duration_months) - timedelta(days=1)


def _whole_number(value: Any, label: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValidationError(f"{label} must be a whole number from {low} to {high}.",
                              details={"field": label})
    return value


def _escalation(escalation_type: Any, percent: Any, renewal_rent: Any) -> dict[str, Any]:
    if escalation_type not in ESCALATION_TYPES:
        raise ValidationError("escalation_type must be 'percent', 'fixed', or 'none'.",
                              details={"field": "escalation_type"})
    if isinstance(percent, bool) or not isinstance(percent, (int, float)) \
            or not 0 <= percent <= 100:
        raise ValidationError("escalation_percent must be a number from 0 to 100.",
                              details={"field": "escalation_percent"})
    if escalation_type == "fixed":
        if renewal_rent is None:
            raise ValidationError(
                "renewal_rent_paise is needed when the renewal rent is a fixed amount.",
                details={"field": "renewal_rent_paise"})
        renewal_rent = ensure_paise(renewal_rent, "renewal_rent_paise", allow_zero=False)
    else:
        renewal_rent = None
    return {"escalation_type": escalation_type, "escalation_percent": float(percent),
            "renewal_rent_paise": renewal_rent}


def _clean_term(payload: Mapping[str, Any],
                existing: Mapping[str, Any] | None) -> dict[str, Any]:
    def given(field: str) -> Any:
        if field in payload:
            return payload[field]
        return existing[field] if existing else _TERM_DEFAULTS[field]

    duration = _whole_number(given("duration_months"), "duration_months", 1, MAX_TERM_MONTHS)
    start_text = given("lease_start_date")
    end_text = payload.get("lease_end_date")
    start = parse_iso_date(start_text, "lease_start_date") if start_text else None
    if start is None:
        if end_text:
            raise ValidationError("lease_end_date needs a lease_start_date.",
                                  details={"field": "lease_end_date"})
        end = None
    elif end_text:
        end = parse_iso_date(end_text, "lease_end_date")
        if end <= start:
            raise ValidationError("lease_end_date must be after lease_start_date.",
                                  details={"field": "lease_end_date"})
    else:
        end = term_end_date(start, duration)
    deposit = given("security_deposit_paise")
    if deposit is not None:
        deposit = ensure_paise(deposit, "security_deposit_paise")
    return {
        "lease_start_date": start.isoformat() if start else None,
        "lease_end_date": end.isoformat() if end else None,
        "duration_months": duration,
        "notice_period_days": _whole_number(given("notice_period_days"),
                                            "notice_period_days", 0, 365),
        "lock_in_months": _whole_number(given("lock_in_months"), "lock_in_months", 0,
                                        MAX_TERM_MONTHS),
        "security_deposit_paise": deposit,
        **_escalation(given("escalation_type"), given("escalation_percent"),
                      given("renewal_rent_paise")),
    }


def _clean_config(payload: Mapping[str, Any],
                  existing: Mapping[str, Any] | None = None) -> dict[str, Any]:
    _reject_unknown(payload, (*_CONFIG_FIELDS, *_TERM_DEFAULTS), "lease")

    # A field left out keeps what the lease already says, so a screen that
    # edits part of the lease (the tenancy terms, the receipt preference, the
    # deposit) never clears the rest. Sending null still clears a field.
    def kept(field: str, default: Any = None) -> Any:
        if field in payload:
            return payload[field]
        return existing[field] if existing else default

    rent = ensure_paise(kept("monthly_rent_paise"), "monthly_rent_paise", allow_zero=False)
    due_day = kept("due_day_of_month", 5)
    if isinstance(due_day, bool) or not isinstance(due_day, int) or not 1 <= due_day <= 28:
        raise ValidationError(
            "due_day_of_month must be between 1 and 28 so every month has that day.",
            details={"field": "due_day_of_month"})
    requires = kept("requires_landlord_receipt", False)
    if existing and "requires_landlord_receipt" not in payload:
        requires = bool(requires)
    if not isinstance(requires, bool):
        raise ValidationError("requires_landlord_receipt must be true or false.",
                              details={"field": "requires_landlord_receipt"})
    # The landlord's details are the owner contact's too, so both are checked
    # by the same rules. Their name is kept in parts, the whole composed from them.
    landlord = names.resolve_name(payload, existing, parts=LANDLORD_NAME_PARTS,
                                  whole="landlord_name")
    landlord_name = landlord["landlord_name"]
    return {
        **landlord,
        "landlord_phone": household.phone_number(kept("landlord_phone"),
                                                 "landlord_phone"),
        "landlord_email": household.email_address(kept("landlord_email"),
                                                  "landlord_email"),
        "landlord_pan": household.pan_number(kept("landlord_pan"), "landlord_pan"),
        "landlord_bank_account": household.bank_account(kept("landlord_bank_account"),
                                                        "landlord_bank_account"),
        "landlord_bank_ifsc": household.ifsc_code(kept("landlord_bank_ifsc"),
                                                  "landlord_bank_ifsc"),
        "landlord_upi_id": household.upi_id(kept("landlord_upi_id"), "landlord_upi_id"),
        # The landlord stays the legal owner; this is only whose name the
        # payout account is in, when it is someone else's.
        "payout_beneficiary_name": household.beneficiary_name(
            kept("payout_beneficiary_name"), landlord_name, "payout_beneficiary_name"),
        "monthly_rent_paise": rent,
        "due_day_of_month": due_day,
        "requires_landlord_receipt": requires,
        # The household's agreement model: how each month's rent is divided.
        "rent_split_mode": _split_mode(kept("rent_split_mode", "equal")),
        "expense_definition_id": optional_text(payload.get("expense_definition_id"),
                                               "expense_definition_id", max_length=120),
        **_clean_term(payload, existing),
    }


def _config_row(uow: UnitOfWork, apartment_id: str) -> dict[str, Any] | None:
    return uow.rent.active_lease(apartment_id)


def _decode_config(uow: UnitOfWork, row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["requires_landlord_receipt"] = bool(row["requires_landlord_receipt"])
    out["is_active"] = bool(row["is_active"])
    # The name to pay rent and deposits to.
    out["effective_payout_name"] = row["payout_beneficiary_name"] or row["landlord_name"]
    out["expense_definition_name"] = uow.rent.definition_name(row["expense_definition_id"])
    out["term_number"] = 1 + uow.rent.completed_term_count(row["apartment_id"])
    out["lifecycle"] = get_lease_lifecycle_status(out)
    return out


def projected_renewal_rent_paise(config: Mapping[str, Any]) -> int:
    """What the next term's rent would be under the lease's escalation clause."""
    rent = int(config["monthly_rent_paise"])
    kind = config.get("escalation_type") or "percent"
    if kind == "fixed":
        return int(config.get("renewal_rent_paise") or rent)
    if kind == "none":
        return rent
    percent = Decimal(str(config.get("escalation_percent") or 0))
    return int((Decimal(rent) * (100 + percent) / 100).quantize(Decimal(1), ROUND_HALF_UP))


def get_lease_lifecycle_status(config: Mapping[str, Any],
                               as_of_date: date | None = None) -> dict[str, Any] | None:
    """Where the lease stands in its term on `as_of_date` (today by default).

    ``None`` until the lease has a start date. The notice window opens
    ``notice_period_days`` before the last day of the term and stays open
    through that day; after it the term is expired. Neither state blocks rent.
    """
    if not config.get("lease_start_date") or not config.get("lease_end_date"):
        return None
    today = as_of_date or date.today()
    start = date.fromisoformat(config["lease_start_date"])
    end = date.fromisoformat(config["lease_end_date"])
    duration = int(config.get("duration_months") or _TERM_DEFAULTS["duration_months"])
    notice_days = int(config.get("notice_period_days") or 0)
    lock_in_months = int(config.get("lock_in_months") or 0)

    if today < start:
        month_index = 0
    else:
        month_index = 1
        while month_index < duration and _add_months(start, month_index) <= today:
            month_index += 1
    total_days = (end - start).days + 1
    elapsed_days = min(max((today - start).days, 0), total_days)
    notice_deadline = end - timedelta(days=notice_days)
    lock_in_end = term_end_date(start, lock_in_months) if lock_in_months else None
    return {
        "as_of_date": today.isoformat(),
        "term_number": int(config.get("term_number") or 1),
        "current_month_index": month_index,
        "duration_months": duration,
        "elapsed_percent": elapsed_days * 100 // total_days,
        "days_until_expiry": (end - today).days,
        "notice_deadline_date": notice_deadline.isoformat(),
        "is_notice_window_active": notice_deadline <= today <= end,
        "is_expired": today > end,
        "lock_in_end_date": lock_in_end.isoformat() if lock_in_end else None,
        "is_within_lock_in": bool(lock_in_end and start <= today <= lock_in_end),
        "projected_renewal_rent_paise": projected_renewal_rent_paise(config),
    }


def get_lease_config(db: Database, apartment_id: str) -> dict[str, Any] | None:
    """The apartment's lease, or ``None`` when rent has not been set up yet."""
    with db.read() as uow:
        get_apartment(uow, apartment_id)
        row = _config_row(uow, apartment_id)
        return _decode_config(uow, row) if row else None


def _find_rent_definition(uow: UnitOfWork, apartment_id: str) -> str | None:
    """The definition rent is already recorded under, if the household has one."""
    return uow.rent.find_rent_definition_id(apartment_id, RENT_CATEGORY)


def save_lease_config(db: Database, apartment_id: str, payload: Mapping[str, Any], *,
                      actor: str = "operator") -> dict[str, Any]:
    """Create the apartment's lease, or update the one it has.

    Rent is recorded under an expense definition in the ``Housing`` category.
    The household's existing "Rent" definition is used when there is one, so
    rent generated here and rent generated by the recurring generator are the
    same expense rather than two. If there is none, a non-recurring one is
    created.

    The open months follow the change at once -- the rent and the agreement
    model -- in the same transaction; one that would take a resident's share below
    what they have paid in, or the rent below what the landlord has been paid, is
    refused. A month over, finalized or paid to the landlord in full keeps the
    amount it was charged at.

    The landlord's details are copied to the owner contact in the same
    transaction, creating that contact if the household has none.
    """
    timestamp = now_timestamp()

    with db.read() as uow:
        get_apartment(uow, apartment_id)
        existing = _config_row(uow, apartment_id)
        cleaned = _clean_config(payload, existing)
        definition_id = cleaned.pop("expense_definition_id")
        if definition_id:
            owner = uow.rent.definition_owner(definition_id)
            if not owner or owner["apartment_id"] != apartment_id or not owner["is_active"]:
                raise ValidationError(
                    "The rent category must be an active expense definition of this apartment.",
                    details={"field": "expense_definition_id"})
        else:
            definition_id = (existing or {}).get("expense_definition_id") \
                or _find_rent_definition(uow, apartment_id)
        id_taken = definition_id is None and uow.rent.definition_id_taken("rent")

    if definition_id is None:
        definition_id = definitions.create_expense_definition(
            db, apartment_id=apartment_id,
            definition_id=new_id("rent") if id_taken else "rent", name="Rent",
            category=RENT_CATEGORY, period_rule="current_month",
            default_allocation_method="equal", is_recurring=False, recurrence="none",
            default_amount_paise=cleaned["monthly_rent_paise"],
            default_provider_name=cleaned["landlord_name"], day_of_month=1, sort_order=10,
            notes="Created by rent management. Rent is generated from the Rent screen.",
            actor=actor)["expense_definition_id"]

    values = {**cleaned, "expense_definition_id": definition_id,
              "requires_landlord_receipt": int(cleaned["requires_landlord_receipt"])}
    with db.transaction() as uow:
        before = _config_row(uow, apartment_id)
        if before is None:
            config_id = new_id("lease")
            uow.rent.insert_lease(config_id=config_id, apartment_id=apartment_id,
                                  created_at=timestamp, updated_at=timestamp, **values)
            audit.record(uow, entity_type="rent_lease_config", entity_id=config_id,
                         action="create", actor=actor, apartment_id=apartment_id,
                         new_value=values)
        else:
            config_id = before["config_id"]
            uow.rent.update_lease(config_id, updated_at=timestamp, **values)
            audit.record_field_changes(
                uow, entity_type="rent_lease_config", entity_id=config_id, before=before,
                after={**before, **values}, fields=list(values), actor=actor,
                apartment_id=apartment_id)
        # The landlord is the household's owner contact as well.
        household.sync_owner_from_lease_in(uow, apartment_id, values, actor=actor)
        sync_open_rent_cycles(uow, apartment_id, timestamp=timestamp, actor=actor)
        return _decode_config(uow, _config_row(uow, apartment_id))


# ------------------------------------------------------ renewing and leaving --

def renew_lease_contract(db: Database, apartment_id: str, payload: Mapping[str, Any], *,
                         actor: str = "operator") -> dict[str, Any]:
    """Roll the lease into its next term and archive the one that is ending.

    Anything left out follows the lease: the new term starts the day after the
    current one ends, runs as long, and charges the rent the escalation clause
    projects. Each month is charged its own term's rent, so an open month of the
    term ending keeps it.
    """
    _reject_unknown(payload, ("new_start_date", "duration_months", "new_monthly_rent_paise",
                              "escalation_type", "escalation_percent"), "renewal")
    timestamp = now_timestamp()
    with db.transaction() as uow:
        get_apartment(uow, apartment_id)
        before = _config_row(uow, apartment_id)
        if before is None:
            raise ConflictError("Set up the lease before renewing it.",
                                code="rent_not_configured")
        if not before["lease_start_date"] or not before["lease_end_date"]:
            raise ConflictError(
                "The lease has no contract term yet. Enter its start date before renewing.",
                code="lease_term_not_set")
        old_end = date.fromisoformat(before["lease_end_date"])
        start = (parse_iso_date(payload["new_start_date"], "new_start_date")
                 if payload.get("new_start_date") else old_end + timedelta(days=1))
        if start <= old_end:
            raise ValidationError(
                f"The new term must start after the current one ends on {old_end.isoformat()}.",
                details={"field": "new_start_date"})
        duration = _whole_number(payload.get("duration_months") or before["duration_months"],
                                 "duration_months", 1, MAX_TERM_MONTHS)
        old_rent = int(before["monthly_rent_paise"])
        new_rent = payload.get("new_monthly_rent_paise")
        new_rent = (projected_renewal_rent_paise(before) if new_rent is None
                    else ensure_paise(new_rent, "new_monthly_rent_paise", allow_zero=False))
        kind = payload.get("escalation_type") or before["escalation_type"]
        escalation = _escalation(
            kind,
            before["escalation_percent"] if payload.get("escalation_percent") is None
            else payload["escalation_percent"],
            # A fixed renewal rent was for the term now starting; the one after
            # that has not been agreed, so it defaults to the same rent.
            new_rent if kind == "fixed" else None)

        term_number = 1 + uow.rent.completed_term_count(apartment_id)
        applied = float(round(Decimal(new_rent - old_rent) * 100 / Decimal(old_rent), 2))
        term_id = new_id("leaseterm")
        uow.rent.insert_lease_term(
            term_id=term_id, apartment_id=apartment_id, term_number=term_number,
            start_date=before["lease_start_date"], end_date=before["lease_end_date"],
            monthly_rent_paise=old_rent, escalation_applied_percent=applied,
            archived_at=timestamp, created_at=before["created_at"])

        values = {
            "lease_start_date": start.isoformat(),
            "lease_end_date": term_end_date(start, duration).isoformat(),
            "duration_months": duration,
            "monthly_rent_paise": new_rent,
            **escalation,
            "lease_status": "active",
            "notice_served_date": None,
            "target_handover_date": None,
        }
        uow.rent.update_lease(before["config_id"], updated_at=timestamp, **values)
        audit.record(uow, entity_type="rent_lease_config", entity_id=before["config_id"],
                     action="renew", actor=actor, apartment_id=apartment_id,
                     old_value={field: before[field] for field in values},
                     new_value={**values, "archived_term_id": term_id,
                                "archived_term_number": term_number})
        # An open month of the term just archived keeps that term's rent.
        sync_open_rent_cycles(uow, apartment_id, timestamp=timestamp, actor=actor)
        return _decode_config(uow, _config_row(uow, apartment_id))


def serve_vacate_notice(db: Database, apartment_id: str, payload: Mapping[str, Any], *,
                        actor: str = "operator") -> dict[str, Any]:
    """Record that notice to vacate has been given, and the planned handover date.

    Serving notice changes nothing in the ledger and does not stop rent being
    generated for the months that remain.
    """
    _reject_unknown(payload, ("notice_served_date", "target_handover_date", "notes"),
                    "notice")
    served = parse_iso_date(payload.get("notice_served_date"), "notice_served_date")
    handover = parse_iso_date(payload.get("target_handover_date"), "target_handover_date")
    if handover < served:
        raise ValidationError("target_handover_date cannot be before notice_served_date.",
                              details={"field": "target_handover_date"})
    notes = optional_text(payload.get("notes"), "notes", max_length=1000)
    with db.transaction() as uow:
        get_apartment(uow, apartment_id)
        before = _config_row(uow, apartment_id)
        if before is None:
            raise ConflictError("Set up the lease before serving notice on it.",
                                code="rent_not_configured")
        values = {"lease_status": "notice_served", "notice_served_date": served.isoformat(),
                  "target_handover_date": handover.isoformat()}
        uow.rent.update_lease(before["config_id"], updated_at=now_timestamp(), **values)
        audit.record(uow, entity_type="rent_lease_config", entity_id=before["config_id"],
                     action="notice_served", actor=actor, apartment_id=apartment_id,
                     old_value={field: before[field] for field in values},
                     new_value=values, reason=notes)
        return _decode_config(uow, _config_row(uow, apartment_id))


def list_lease_terms(db: Database, apartment_id: str) -> list[dict[str, Any]]:
    """Completed terms, oldest first."""
    with db.read() as uow:
        get_apartment(uow, apartment_id)
        return uow.rent.lease_terms(apartment_id)


# ---------------------------------------------------------------- the cycle --

def _cycle_row(uow: UnitOfWork, apartment_id: str,
               settlement_month: str) -> dict[str, Any] | None:
    return uow.rent.cycle_for_month(apartment_id, settlement_month)


def _statement_row(uow: UnitOfWork, apartment_id: str,
                   settlement_month: str) -> dict[str, Any] | None:
    return uow.rent.statement_for_month(apartment_id, settlement_month)


def _names(uow: UnitOfWork, apartment_id: str) -> dict[str, str]:
    return {row["resident_id"]: row["display_name"]
            for row in uow.rent.resident_names(apartment_id)}


def _charges(uow: UnitOfWork, expense_id: str) -> list[dict[str, Any]]:
    """Each resident's rent charge for one expense, with what has been paid off."""
    return uow.rent.charges_for_expense(expense_id)


def _existing_rent_expenses(uow: UnitOfWork, apartment_id: str, definition_id: str,
                            settlement_month: str) -> list[dict[str, Any]]:
    return uow.rent.expenses_for_definition_month(apartment_id, definition_id,
                                                  settlement_month)


def _adoption_problem(expense: dict[str, Any], charges: list[dict[str, Any]]) -> str | None:
    """Why an existing rent expense cannot be taken over as the month's rent."""
    if expense["amount_paise"] <= 0 or not charges:
        return "The existing rent expense has no amount or no shares."
    if (expense.get("allocation_method") or "") != "equal":
        return ("The existing rent expense is not split equally. This screen manages an "
                "equal-split lease; change the expense to an equal split, or remove it.")
    return None


def _advanced_by_resident(tranches: list[dict[str, Any]]) -> dict[str, int]:
    """How much each resident has paid the landlord directly this month."""
    advanced: dict[str, int] = {}
    for tranche in tranches:
        if tranche["disbursed_by"] == "resident_advance":
            rid = tranche["funding_resident_id"]
            advanced[rid] = advanced.get(rid, 0) + tranche["amount_paise"]
    return advanced


def _reimbursement_id_base(expense_id: str) -> str:
    """The kitty's refunds to whoever paid the landlord more than their share
    are named after the rent expense, so they can be found again."""
    return f"{expense_id}-reimbursement"


def _share_payout_id(payment_id: str) -> str:
    """The kitty's payout to the landlord of a resident's own share, when they
    paid the landlord themselves: named after their payment into the kitty."""
    return f"rentdisb_{payment_id}"


def _kitty_reimbursements(uow: UnitOfWork, apartment_id: str, expense_id: str,
                          charges: list[dict[str, Any]],
                          tranches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What the kitty owes back to each resident who paid more than their own
    share of the month's rent -- to the landlord, or into the kitty -- and what it
    can pay now. See :func:`aem.services.bill_shares.reimbursements`."""
    return bill_shares.reimbursements(uow, expense_id=expense_id, charges=charges,
                                      tranches=tranches, names=_names(uow, apartment_id))


def _public_tranche(tranche: dict[str, Any]) -> dict[str, Any]:
    return {key: tranche.get(key) for key in (
        "disbursement_id", "cycle_id", "amount_paise", "disbursed_at", "disbursement_reference",
        "disbursed_by", "funding_resident_id", "funding_resident_name", "funding_payment_id",
        "note", "created_at")}


def _summarize(uow: UnitOfWork, cycle: dict[str, Any],
               config: dict[str, Any] | None) -> dict[str, Any]:
    """A rent cycle as the screen shows it.

    Amounts are read from the expense and its allocations, which are the
    accounting record, so an edit made to the rent expense elsewhere is
    reflected here. ``collected_paise`` counts what residents have paid into
    the pool against their rent charge, plus what a resident paid the
    landlord directly towards their own share -- that part was never going to
    come through the pool.

    The landlord may be paid in tranches: ``disbursements`` lists them,
    ``disbursed_paise`` is their total and ``remaining_disbursement_paise``
    what the landlord is still owed.

    Each participant's ``payments`` are the payments that settled their
    charge, oldest first, each with what it put towards the charge.
    """
    expense = get_expense(uow, cycle["expense_id"])
    charges = _charges(uow, cycle["expense_id"])
    payments_by_resident: dict[str, list[dict[str, Any]]] = {}
    for payment in uow.rent.payments_for_rent_expense(cycle["expense_id"]):
        resident_id = payment.pop("resident_id")
        payments_by_resident.setdefault(resident_id, []).append(payment)
    tranches = uow.rent.disbursements_for_cycle(cycle["cycle_id"])
    advanced = _advanced_by_resident(tranches)
    paid_out = cycle["disbursement_status"] != "pending"
    participants: list[dict[str, Any]] = []
    collected = 0
    for charge in charges:
        share, settled = charge["share_paise"], charge["settled_paise"]
        own_advance = advanced.get(charge["resident_id"], 0)
        covered = min(share, settled + own_advance)
        if settled >= share:
            status = "paid"
        elif own_advance and covered >= share:
            status = "funded_payout"
        else:
            status = "partial" if covered else "unpaid"
        collected += covered
        participants.append({
            **charge, "outstanding_paise": share - settled, "collection_status": status,
            "advanced_paise": own_advance,
            "payments": payments_by_resident.get(charge["resident_id"], []),
            "receipt_available": False})  # HRA receipt feature disabled (see DEBT-001)
    total = expense["amount_paise"]
    disbursed_paise = sum(t["amount_paise"] for t in tranches)
    remaining = (0 if cycle["disbursement_status"] == "disbursed"
                 else max(0, total - disbursed_paise))
    modes = {t["disbursed_by"] for t in tranches}
    funders = sorted(advanced)
    advances = [t for t in tranches if t["disbursed_by"] == "resident_advance"]
    funder = funders[0] if len(funders) == 1 else None
    count = len(participants)
    names = _names(uow, cycle["apartment_id"])
    taking_part = {p["resident_id"] for p in participants}
    statement = uow.rent.statement_brief(expense["statement_id"]) \
        if expense["statement_id"] else None
    landlord_receipt = uow.rent.landlord_receipt_summary(cycle["cycle_id"])
    lease_rent = (_rent_for_month(uow, config, cycle["settlement_month"]) if config
                  else None)
    reimbursements = _kitty_reimbursements(uow, cycle["apartment_id"], cycle["expense_id"],
                                           charges, tranches)
    return {
        "cycle_id": cycle["cycle_id"],
        "apartment_id": cycle["apartment_id"],
        "settlement_month": cycle["settlement_month"],
        "service_period": expense["service_period"],
        "expense_id": cycle["expense_id"],
        "description": expense["description"],
        # When the landlord is due, from the lease. The expense's own due date
        # orders the residents' charges and can differ for an adopted expense.
        "due_date": (f"{cycle['settlement_month']}-{config['due_day_of_month']:02d}"
                     if config else expense["due_date"]),
        "charge_due_date": expense["due_date"],
        "total_rent_paise": total,
        "participant_count": count,
        # Rent is only ever split equally or by room here: a rent expense
        # with its own amounts per resident came from a room-tiered month.
        "split_mode": "equal" if expense.get("allocation_method") == "equal"
        else "room_tiered",
        "base_share_paise": total // count if count else 0,
        "remainder_paise": total % count if count else 0,
        "allocated_paise": sum(p["share_paise"] for p in participants),
        "collected_paise": collected,
        "collected_count": sum(1 for p in participants
                               if p["collection_status"] in ("paid", "funded_payout")),
        "participants": participants,
        "excluded_residents": [
            {"resident_id": rid, "display_name": name}
            for rid, name in sorted(names.items(), key=lambda item: (item[1], item[0]))
            if rid not in taking_part],
        "disbursement_status": cycle["disbursement_status"],
        "disbursed_paise": disbursed_paise,
        "remaining_disbursement_paise": remaining,
        "disbursements": [_public_tranche(t) for t in tranches],
        # The latest tranche's date and reference.
        "disbursed_at": cycle["disbursed_at"],
        "disbursement_reference": cycle["disbursement_reference"],
        # How the landlord was paid: one mode, or 'mixed' across tranches; and
        # who advanced it, when that was one resident.
        "disbursed_by": (next(iter(modes)) if len(modes) == 1 else "mixed" if modes else None),
        "funding_resident_id": funder,
        "funding_resident_name": names.get(funder) if funder else None,
        "funding_payment_id": (advances[0]["funding_payment_id"] if len(advances) == 1
                               else None),
        "lease_rent_paise": lease_rent,
        "matches_lease_rent": lease_rent is None or lease_rent == total,
        # 'live' while the month follows the lease; 'frozen' once it is its snapshot.
        "cycle_state": _cycle_state(cycle, cycle["settlement_month"],
                                    _statement_row(uow, cycle["apartment_id"],
                                                   cycle["settlement_month"])),
        "landlord_receipt": landlord_receipt,
        # What the kitty owes back to whoever paid more than their share, to the
        # landlord or into the kitty: one for each such resident, and the first.
        "reimbursements": reimbursements,
        # Whether a payment or payout recorded by mistake can still be deleted:
        # until the month's statement is finalized.
        "transactions_open": deletions.month_is_open(uow, cycle["apartment_id"],
                                                     cycle["settlement_month"]),
        "reimbursement": reimbursements[0] if reimbursements else None,
        # 'pending' only once the landlord has been paid something: there is
        # nothing to chase before that.
        "landlord_receipt_status": (
            "attached" if landlord_receipt
            else "pending" if paid_out and config and config["requires_landlord_receipt"]
            else "not_required"),
        "statement": statement,
        "created_at": cycle["created_at"],
    }


def get_rent_cycle(db: Database, apartment_id: str, settlement_month: str) -> dict[str, Any] | None:
    """The month's rent cycle, or ``None`` if the month is not charged. An open
    month is charged, or brought up to date with the lease, as it is read."""
    settlement_month = normalize_month(settlement_month, "settlement_month")
    with db.transaction() as uow:
        get_apartment(uow, apartment_id)
        cycle = _sync_live_rent_cycle(uow, apartment_id, settlement_month,
                                      check_reduction_guards=False)
        return _summarize(uow, cycle, _config_row(uow, apartment_id)) if cycle else None


def _participants(uow: UnitOfWork, apartment_id: str,
                  excluded_resident_ids: Sequence[str] | None) -> tuple[list[str], list[str]]:
    """``(taking part, left out)``.

    Every active resident takes part unless the operator leaves them out.
    ``active_from`` and ``active_until`` are deliberately not consulted: a
    partial month never changes who is charged or how much.
    """
    if excluded_resident_ids is None:
        excluded: list[str] = []
    elif isinstance(excluded_resident_ids, (str, bytes)) \
            or not isinstance(excluded_resident_ids, Sequence):
        raise ValidationError("excluded_resident_ids must be a list of resident IDs.",
                              details={"field": "excluded_resident_ids"})
    else:
        excluded = sorted({str(rid) for rid in excluded_resident_ids})
    unknown = sorted(set(excluded) - set(resident_ids(uow, apartment_id)))
    if unknown:
        raise ValidationError(
            "excluded_resident_ids must all be residents of this apartment.",
            details={"field": "excluded_resident_ids", "unknown_resident_ids": unknown})
    chosen = [rid for rid in resident_ids(uow, apartment_id, active_only=True)
              if rid not in excluded]
    return chosen, excluded


def _split_mode(value: Any) -> str:
    mode = value or "equal"
    if mode not in SPLIT_MODES:
        raise ValidationError("split_mode must be equal or room_tiered.",
                              details={"field": "split_mode", "allowed": list(SPLIT_MODES)})
    return mode


def _room_tiered(uow: UnitOfWork, apartment_id: str, chosen: Sequence[str], total: int,
                 names: Mapping[str, str]) -> dict[str, Any]:
    """Each participant's share from the base rent of the room they occupy.

    It can be used only when every participant occupies a room with a base rent
    above zero and those rents add up to the month's rent exactly: nothing is
    scaled, rounded or made up.
    """
    rooms = {room["assigned_resident_id"]: room
             for room in uow.rooms.list_for_apartment(apartment_id)
             if room["assigned_resident_id"]}
    shares, missing = [], []
    for rid in sorted(chosen, key=lambda r: (names.get(r, r), r)):
        room = rooms.get(rid)
        rent_paise = room["base_rent_paise"] if room else None
        if not rent_paise:
            missing.append(names.get(rid, rid))
        shares.append({"resident_id": rid, "display_name": names.get(rid, rid),
                       "room_id": room["room_id"] if room else None,
                       "room_number": room["room_number"] if room else None,
                       "base_rent_paise": rent_paise})
    room_total = sum(row["base_rent_paise"] or 0 for row in shares)
    problem = None
    if not chosen:
        problem = "Nobody is taking part. At least one active resident must share the rent."
    elif missing:
        problem = ("All participating residents must occupy a room with a configured base "
                   f"rent. Not set for: {', '.join(missing)}.")
    elif room_total != total:
        problem = (f"The sum of room rents ({format_inr(room_total)}) does not equal the "
                   f"monthly lease rent ({format_inr(total)}).")
    return {"available": problem is None, "blocked_reason": problem,
            "room_total_paise": room_total, "lease_rent_paise": total, "shares": shares}


def _preview(uow: UnitOfWork, apartment_id: str, settlement_month: str,
             config: dict[str, Any], excluded_resident_ids: Sequence[str] | None,
             split_mode: str | None = None) -> dict[str, Any]:
    """What generating the month's rent would do, without doing it. The split
    is the lease's agreement model unless ``split_mode`` says otherwise."""
    split_mode = _split_mode(split_mode or config.get("rent_split_mode"))
    names = _names(uow, apartment_id)
    statement = _statement_row(uow, apartment_id, settlement_month)
    found = _existing_rent_expenses(uow, apartment_id, config["expense_definition_id"],
                                    settlement_month)
    base = {"settlement_month": settlement_month, "statement": statement,
            "due_date": f"{settlement_month}-{config['due_day_of_month']:02d}"}
    if found:
        expense = found[0]
        charges = _charges(uow, expense["expense_id"])
        problem = ("More than one rent expense exists for this month. Remove the duplicate "
                   "before generating." if len(found) > 1
                   else _adoption_problem(expense, charges))
        count = len(charges)
        total = expense["amount_paise"]
        return {
            **base, "mode": "adopt", "existing_expense_id": expense["expense_id"],
            "existing_expense_description": expense["description"],
            "total_rent_paise": total, "participant_count": count,
            "base_share_paise": total // count if count else 0,
            "remainder_paise": total % count if count else 0,
            "participants": [{"resident_id": c["resident_id"], "display_name": c["display_name"],
                              "share_paise": c["share_paise"]} for c in charges],
            "excluded_residents": [
                {"resident_id": rid, "display_name": name}
                for rid, name in sorted(names.items(), key=lambda item: (item[1], item[0]))
                if rid not in {c["resident_id"] for c in charges}],
            "can_generate": problem is None, "blocked_reason": problem,
            "matches_lease_rent": total == _rent_for_month(uow, config, settlement_month),
            # The rent already exists as an expense, so its split is already decided.
            "split_mode": "equal",
            "room_tiered": {"available": False, "blocked_reason":
                            "Rent for this month already exists as an expense, so it keeps "
                            "the split recorded on it.", "room_total_paise": 0,
                            "lease_rent_paise": total, "shares": []},
        }

    chosen, excluded = _participants(uow, apartment_id, excluded_resident_ids)
    total = _rent_for_month(uow, config, settlement_month)
    tiered = _room_tiered(uow, apartment_id, chosen, total, names)
    problem = None
    shares: dict[str, int] = {}
    if statement and statement["status"] != "draft":
        problem = (f"The {settlement_month} statement has been finalized, so rent can no "
                   "longer be added to it.")
    elif not chosen:
        problem = "Nobody is taking part. At least one active resident must share the rent."
    elif split_mode == "room_tiered":
        problem = tiered["blocked_reason"]
        if problem is None:
            shares = {row["resident_id"]: row["base_rent_paise"] for row in tiered["shares"]}
    else:
        shares = plan_allocation(amount_paise=total, method="equal",
                                 all_residents=resident_ids(uow, apartment_id),
                                 participants=chosen).shares_paise
    count = len(chosen)
    return {
        **base, "mode": "create", "existing_expense_id": None,
        "existing_expense_description": None,
        "total_rent_paise": total, "participant_count": count,
        "base_share_paise": total // count if count else 0,
        "remainder_paise": total % count if count else 0,
        "participants": [{"resident_id": rid, "display_name": names.get(rid, rid),
                          "share_paise": shares.get(rid, 0)}
                         for rid in sorted(chosen, key=lambda r: (names.get(r, r), r))],
        "excluded_residents": [{"resident_id": rid, "display_name": names.get(rid, rid)}
                               for rid in sorted(excluded, key=lambda r: (names.get(r, r), r))],
        "can_generate": problem is None, "blocked_reason": problem,
        "matches_lease_rent": True,
        "split_mode": split_mode,
        "room_tiered": tiered,
    }


# ------------------------------------------------------------ the live cycle --
#
# A month's rent is charged as soon as the month begins, and follows the lease --
# its rent, its agreement model, and under a room-tiered model the rooms' base
# rents -- for as long as the month is open, however much has been paid. It
# freezes for good when the month is over or its statement is finalized: that is
# the month's snapshot. A future month is only a preview until it begins, and a
# month before the lease was set up is charged only by hand.
#
# Who a month charges is settled when it is charged: a resident who moves in
# later starts with the next month, and one who moves out keeps their share of
# the months they were in. Changes follow the charges in place, so payments
# applied to them stay with them. A month going down -- even below what the
# landlord was paid -- leaves what each resident paid beyond their new share
# owed back to them (see :mod:`aem.services.bill_shares`).

def _today() -> str:
    return now_timestamp()[:10]


def _current_month() -> str:
    return _today()[:7]


def _cycle_is_live(month: str, statement: Mapping[str, Any] | None) -> bool:
    """Whether a month's rent cycle still follows the lease and room rents."""
    return month >= _current_month() and (statement is None or statement["status"] == "draft")


def _rent_for_month(uow: UnitOfWork, config: Mapping[str, Any], month: str) -> int:
    """What the lease charges for `month`: the current term's rent, or for a month
    of a term since renewed, the rent that term was let at."""
    start = config.get("lease_start_date")
    if start and month < start[:7]:
        for term in reversed(uow.rent.lease_terms(config["apartment_id"])):
            if term["start_date"][:7] <= month <= term["end_date"][:7]:
                return int(term["monthly_rent_paise"])
    return int(config["monthly_rent_paise"])


def _cycle_state(cycle: Mapping[str, Any] | None, month: str,
                 statement: Mapping[str, Any] | None) -> str:
    """'live': charged, and following the lease -- for as long as the month is
    open, whoever has been paid; 'frozen': the month's snapshot; 'upcoming': not
    begun; 'not_generated': not charged."""
    if cycle is not None:
        return "live" if _cycle_is_live(month, statement) else "frozen"
    return "upcoming" if month > _current_month() else "not_generated"


def _follow_lease(uow: UnitOfWork, apartment_id: str, settlement_month: str, *,
                  excluded_resident_ids: Sequence[str] | None = None,
                  split_mode: str | None = None, check_reduction_guards: bool = True,
                  timestamp: str | None = None,
                  actor: str = "operator") -> tuple[dict[str, Any] | None, str | None]:
    """:func:`_sync_live_rent_cycle`, also saying why the month could not follow
    the lease as it stands, when it could not."""
    timestamp = timestamp or now_timestamp()
    config = _config_row(uow, apartment_id)
    statement = _statement_row(uow, apartment_id, settlement_month)
    cycle = _cycle_row(uow, apartment_id, settlement_month)
    if cycle is not None and (_cycle_state(cycle, settlement_month, statement) == "frozen"
                              or not cycle["expense_id"]):
        return cycle, None
    if config is None:
        return cycle, None
    if cycle is None:
        if (settlement_month > _current_month()
                or (statement is not None and statement["status"] != "draft")
                or settlement_month < config["created_at"][:7]):
            return None, None
        preview = _preview(uow, apartment_id, settlement_month, config, excluded_resident_ids,
                           split_mode)
        if preview["blocked_reason"]:
            return None, preview["blocked_reason"]
        _record_cycle(uow, apartment_id, settlement_month, config,
                      excluded_resident_ids=excluded_resident_ids, split_mode=split_mode,
                      timestamp=timestamp, actor=actor)
        return _cycle_row(uow, apartment_id, settlement_month), None

    expense = get_expense(uow, cycle["expense_id"])
    allocations = uow.rent.allocations_for_expense(cycle["expense_id"])
    basis = _stored_basis(allocations)
    component_ids = uow.expenses.component_ids(cycle["expense_id"])
    managed = (basis.get("source") == "rent" or expense.get("allocation_method") == "equal"
               or basis.get("split_mode") == "room_tiered")
    if not managed or len(component_ids) != 1:
        # A split set by hand on the expense itself is kept as it is.
        return cycle, None
    if basis.get("source") == "rent":
        participants = list(basis.get("participants") or [])
    else:
        # Charged before the month followed its lease: whoever it charges.
        participants = sorted({row["resident_id"] for row in allocations})
    if excluded_resident_ids is not None:
        participants = [rid for rid in participants if rid not in set(excluded_resident_ids)]
    excluded = sorted(set(excluded_resident_ids or basis.get("excluded_resident_ids") or []))
    override = split_mode or basis.get("split_mode_override")
    mode = _split_mode(override or config["rent_split_mode"])
    total = _rent_for_month(uow, config, settlement_month)
    split = _split(uow, apartment_id, participants, total, mode,
                   payer_resident_id=expense["payer_resident_id"])
    if split["problem"]:
        # Kept as it was, never refused: a room-tiered change takes several saves
        # (the lease rent, then each room), and the first ones cannot add up yet.
        return cycle, split["problem"]
    shares = split["shares"]

    month_name = month_label(settlement_month)
    tranches = uow.rent.disbursements_for_cycle(cycle["cycle_id"])
    method = "equal" if mode == "equal" else "custom"
    due = f"{settlement_month}-{config['due_day_of_month']:02d}"
    new_basis = _rent_basis(split, mode, participants, excluded,
                            override if override != config["rent_split_mode"] else None)
    current = {row["resident_id"]: row for row in allocations}
    if (expense["amount_paise"] == total and expense.get("allocation_method") == method
            and expense.get("provider_name") == config["landlord_name"]
            and expense.get("due_date") == due and basis == new_basis
            and {rid: row["amount_paise"] for rid, row in current.items()} == shares
            and cycle["total_rent_paise"] == total
            and cycle["participant_count"] == len(shares)):
        return cycle, None

    # What each resident paid, into the kitty or to the landlord, follows their
    # new share, and the kitty's own payouts come down to a rent gone below what
    # it paid the landlord; refused if the kitty has paid someone back more than
    # it would owe.
    rebalance = bill_shares.plan(
        uow, scope="rent", apartment_id=apartment_id, expense_id=cycle["expense_id"],
        shares=shares, charged=list(current), tranches=tranches,
        owed={cycle["cycle_id"]: total}, names=_names(uow, apartment_id),
        what=f"{month_name} rent")
    if rebalance.problem is not None:
        if check_reduction_guards:
            raise rebalance.problem
        return cycle, str(rebalance.problem)

    basis_json = json.dumps(new_basis, sort_keys=True, default=str)
    uow.rent.update_rent_expense(cycle["expense_id"], amount_paise=total,
                                 allocation_method=method, provider_name=config["landlord_name"],
                                 due_date=due, updated_at=timestamp)
    uow.expenses.update_component(component_ids[0], amount_paise=total,
                                  allocation_method=method)

    def set_charge(rid: str, amount: int) -> None:
        row = current.get(rid)
        if row is not None and amount:
            uow.rent.update_allocation(row["allocation_id"], amount_paise=amount,
                                       allocation_basis_json=basis_json)
        elif row is not None:
            uow.rent.delete_allocation(row["allocation_id"])
        elif amount:
            uow.expenses.insert_allocation(
                allocation_id=new_id("allocation"), expense_id=cycle["expense_id"],
                component_id=component_ids[0], resident_id=rid, amount_paise=amount,
                allocation_basis_json=basis_json)

    bill_shares.apply(
        uow, rebalance, set_charge=set_charge, payee=lambda group: config["landlord_name"],
        share_note=f"Rent for {month_name}",
        advance_note=f"Extra paid for rent for {month_name} to be reimbursed via House Kitty",
        timestamp=timestamp, actor=actor)
    disbursed = sum(t["amount_paise"] for t in uow.rent.disbursements_for_cycle(cycle["cycle_id"]))
    status = ("disbursed" if disbursed >= total
              else "partially_disbursed" if disbursed else "pending")
    uow.rent.update_cycle_totals(cycle["cycle_id"], total_rent_paise=total,
                                 participant_count=len(shares), disbursement_status=status)
    audit.record(uow, entity_type="rent_cycle", entity_id=cycle["cycle_id"], action="sync",
                 actor=actor, apartment_id=apartment_id,
                 old_value={"total_rent_paise": expense["amount_paise"],
                            "shares": {rid: row["amount_paise"] for rid, row in current.items()}},
                 new_value={"total_rent_paise": total, "split_mode": mode, "shares": shares,
                            "disbursement_status": status})
    return _cycle_row(uow, apartment_id, settlement_month), None


def _sync_live_rent_cycle(
    uow: UnitOfWork,
    apartment_id: str,
    settlement_month: str,
    *,
    excluded_resident_ids: Sequence[str] | None = None,
    split_mode: str | None = None,
    check_reduction_guards: bool = True,
    timestamp: str | None = None,
    actor: str = "operator",
) -> dict[str, Any] | None:
    """Charge `settlement_month`'s rent if it is due, or bring the month's cycle
    up to date with the lease; returns the cycle as it then stands, if there is one.

    A frozen month (see :func:`_cycle_state`) is returned as it is. What each
    resident paid -- into the kitty, or to the landlord -- follows their new share,
    and a rent gone below what the kitty paid the landlord takes the kitty's own
    payouts down with it (see :mod:`aem.services.bill_shares`). A change that would
    leave the kitty having paid a resident back more than it then owes them
    (``reimbursement_exceeds_new_extra``) is refused with
    ``check_reduction_guards`` and otherwise leaves the month as it was. So does a
    split that cannot be made as things stand, such as room rents that do not add
    up to the lease rent.
    """
    return _follow_lease(uow, apartment_id, settlement_month,
                         excluded_resident_ids=excluded_resident_ids, split_mode=split_mode,
                         check_reduction_guards=check_reduction_guards, timestamp=timestamp,
                         actor=actor)[0]


def _splits_by_room(uow: UnitOfWork, apartment_id: str, month: str,
                    config: Mapping[str, Any]) -> bool:
    """Whether `month` is, or would be, charged by room, so a room change moves it."""
    cycle = _cycle_row(uow, apartment_id, month)
    override = (_stored_basis(uow.rent.allocations_for_expense(cycle["expense_id"]))
                .get("split_mode_override") if cycle else None)
    return (override or config["rent_split_mode"]) == "room_tiered"


def sync_open_rent_cycles(uow: UnitOfWork, apartment_id: str, *, timestamp: str | None = None,
                          actor: str = "operator", rooms_changed: bool = False) -> None:
    """After the lease or the rooms change, bring every open month up to date:
    this month and any later one charged already, while its statement is a
    draft. A month gone by keeps its rent as it stood. The change is refused if
    the kitty has already paid a resident back more than a month would then owe
    them. ``rooms_changed`` limits it to the months charged by room."""
    config = _config_row(uow, apartment_id)
    if config is None:
        return
    current = _current_month()
    months = {current} | {month for month in uow.rent.open_cycle_months(apartment_id)
                          if month >= current}
    for month in sorted(months):
        if rooms_changed and not _splits_by_room(uow, apartment_id, month, config):
            continue
        _sync_live_rent_cycle(uow, apartment_id, month, check_reduction_guards=True,
                              timestamp=timestamp, actor=actor)


def _room_tiered_for(uow: UnitOfWork, apartment_id: str, settlement_month: str,
                     config: Mapping[str, Any], cycle: Mapping[str, Any] | None) -> dict[str, Any]:
    if cycle is None:
        participants, _ = _participants(uow, apartment_id, None)
    else:
        participants = sorted({row["resident_id"] for row
                               in uow.rent.allocations_for_expense(cycle["expense_id"])})
    return _room_tiered(uow, apartment_id, participants,
                        _rent_for_month(uow, config, settlement_month), _names(uow, apartment_id))


def rent_month_overview(db: Database, apartment_id: str, settlement_month: str, *,
                        excluded_resident_ids: Sequence[str] | None = None,
                        split_mode: str | None = None) -> dict[str, Any]:
    """Everything the Rent screen needs for one month.

    An open month is charged, or brought up to date with the lease, as it is
    looked at. ``cycle`` is the month's rent, when it is charged. Otherwise
    ``preview`` describes what charging it would do -- including, for an equal
    split with somebody left out, the exact share each remaining resident would be
    charged. ``month_state`` says where the month stands (see :func:`_cycle_state`),
    and ``live_blocked_reason`` why an open month cannot follow the lease as it
    stands, if it cannot -- it then stays as it was.
    """
    settlement_month = normalize_month(settlement_month, "settlement_month")
    if split_mode is not None:
        split_mode = _split_mode(split_mode)
    with db.transaction() as uow:
        get_apartment(uow, apartment_id)
        cycle, blocked = _follow_lease(uow, apartment_id, settlement_month,
                                       check_reduction_guards=False)
        config = _config_row(uow, apartment_id)
        return {
            "settlement_month": settlement_month,
            "month_label": month_label(settlement_month),
            "config": _decode_config(uow, config) if config else None,
            "cycle": _summarize(uow, cycle, config) if cycle else None,
            "preview": _preview(uow, apartment_id, settlement_month, config,
                                excluded_resident_ids, split_mode)
            if config and not cycle else None,
            "month_state": _cycle_state(cycle, settlement_month,
                                        _statement_row(uow, apartment_id, settlement_month)),
            "live_blocked_reason": blocked if cycle else None,
            # Whether the month could be split by room: for its own participants
            # once charged, as the preview says before then.
            "room_tiered": _room_tiered_for(uow, apartment_id, settlement_month, config, cycle)
            if config else None,
        }


def _split(uow: UnitOfWork, apartment_id: str, participants: Sequence[str], total: int,
           split_mode: str, *, payer_resident_id: str | None = None) -> dict[str, Any]:
    """How `total` is divided between `participants` under `split_mode`: the
    allocation method, each participant's share (above zero), the basis kept with
    the charges, and why it cannot be divided that way, if it cannot."""
    if not participants:
        return {"method": "equal", "shares": {}, "basis": {}, "problem":
                "Nobody is taking part. At least one active resident must share the rent."}
    if split_mode == "room_tiered":
        tiered = _room_tiered(uow, apartment_id, participants, total,
                              _names(uow, apartment_id))
        return {"method": "custom",
                "shares": {row["resident_id"]: row["base_rent_paise"]
                           for row in tiered["shares"] if row["base_rent_paise"]},
                "basis": {"method": "custom", "split_mode": "room_tiered",
                          "rooms": {row["resident_id"]: row["room_number"]
                                    for row in tiered["shares"]}},
                "problem": tiered["blocked_reason"], "tiered": tiered}
    plan = plan_allocation(amount_paise=total, method="equal",
                           all_residents=resident_ids(uow, apartment_id),
                           participants=list(participants), payer_resident_id=payer_resident_id)
    return {"method": "equal", "shares": {rid: v for rid, v in plan.shares_paise.items() if v},
            "basis": dict(plan.basis), "problem": None}


def _rent_basis(split: Mapping[str, Any], split_mode: str, participants: Sequence[str],
                excluded: Sequence[str], override: str | None) -> dict[str, Any]:
    """Kept with each charge: how the month is split, who is on it, and whether
    the split was chosen for this month rather than taken from the lease."""
    return {**split["basis"], "source": "rent", "split_mode": split_mode,
            "participants": sorted(participants), "excluded_resident_ids": sorted(excluded),
            "split_mode_override": override}


def _stored_basis(allocations: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    for row in allocations:
        if row.get("allocation_basis_json"):
            try:
                return json.loads(row["allocation_basis_json"])
            except ValueError:
                return {}
    return {}


def _record_cycle(uow: UnitOfWork, apartment_id: str, settlement_month: str,
                  config: Mapping[str, Any], *, excluded_resident_ids: Sequence[str] | None,
                  split_mode: str | None, timestamp: str, actor: str) -> bool:
    """Write the month's rent cycle in the caller's transaction, adopting the
    month's rent expense if it already exists; returns whether it adopted one.
    Raises if the month cannot be charged."""
    override = split_mode if split_mode and split_mode != config["rent_split_mode"] else None
    split_mode = split_mode or config["rent_split_mode"]
    found = _existing_rent_expenses(uow, apartment_id, config["expense_definition_id"],
                                    settlement_month)
    if found:
        expense = found[0]
        charges = _charges(uow, expense["expense_id"])
        if len(found) > 1:
            raise ConflictError(
                "More than one rent expense exists for this month. Remove the duplicate "
                "before generating.", code="rent_expense_ambiguous",
                details={"expense_ids": [row["expense_id"] for row in found]})
        problem = _adoption_problem(expense, charges)
        if problem:
            raise ConflictError(problem, code="rent_expense_not_adoptable",
                                details={"expense_id": expense["expense_id"]})
        if split_mode != "equal" and split_mode != config["rent_split_mode"]:
            raise ConflictError(
                "Rent for this month already exists as an expense, so it keeps the split "
                "recorded on it. Change it on the expense itself.",
                code="rent_expense_exists", details={"expense_id": expense["expense_id"]})
        if excluded_resident_ids:
            raise ConflictError(
                "Rent for this month already exists as an expense, so its participants "
                "are the ones recorded on it. Change them on the expense itself.",
                code="rent_expense_exists", details={"expense_id": expense["expense_id"]})
        expense_id, adopted = expense["expense_id"], True
        total, count = expense["amount_paise"], len(charges)
    else:
        statement = _statement_row(uow, apartment_id, settlement_month)
        if statement and statement["status"] != "draft":
            raise ConflictError(
                f"The {settlement_month} statement has been finalized, so rent can no "
                "longer be added to it.", code="statement_not_draft",
                details={"statement_id": statement["statement_id"],
                         "status": statement["status"]})
        chosen, excluded = _participants(uow, apartment_id, excluded_resident_ids)
        if not chosen:
            raise ValidationError(
                "Nobody is taking part. At least one active resident must share the rent.",
                code="no_participants", details={"excluded_resident_ids": excluded})
        recurrence = uow.rent.definition_recurrence(config["expense_definition_id"])
        expense_id, adopted = new_id("rent"), False
        total, count = _rent_for_month(uow, config, settlement_month), len(chosen)
        split = _split(uow, apartment_id, chosen, total, split_mode)
        if split["problem"]:
            raise ValidationError(split["problem"], code="room_tiered_unavailable",
                                  details={"field": "split_mode",
                                           "room_total_paise": split["tiered"]["room_total_paise"],
                                           "lease_rent_paise": total})
        # No payer yet: who funds the payout is only known when it is made.
        expenses._create_expense(
            uow, apartment_id=apartment_id, expense_id=expense_id,
            description=f"Rent for {month_label(settlement_month)}",
            amount_paise=total, expense_date=f"{settlement_month}-01",
            allocation_method=split["method"],
            allocations_paise={rid: split["shares"].get(rid, 0)
                               for rid in resident_ids(uow, apartment_id)},
            allocation_basis=_rent_basis(split, split_mode, chosen, excluded, override),
            provider_name=config["landlord_name"],
            expense_definition_id=config["expense_definition_id"],
            statement_id=statement["statement_id"] if statement else None,
            period_rule="current_month", service_period=settlement_month,
            settlement_period=settlement_month,
            due_date=f"{settlement_month}-{config['due_day_of_month']:02d}",
            generated_from_recurrence=recurrence == "monthly",
            created_at=timestamp, actor=actor)

    cycle_id = new_id("rentcycle")
    uow.rent.insert_pending_cycle(
        cycle_id=cycle_id, apartment_id=apartment_id, settlement_month=settlement_month,
        expense_id=expense_id, total_rent_paise=total, participant_count=count,
        created_at=timestamp)
    audit.record(uow, entity_type="rent_cycle", entity_id=cycle_id, action="create",
                 actor=actor, apartment_id=apartment_id,
                 new_value={"settlement_month": settlement_month, "expense_id": expense_id,
                            "total_rent_paise": total, "participant_count": count,
                            "adopted_existing_expense": adopted,
                            "split_mode": "equal" if adopted else split_mode})
    return adopted


def generate_monthly_rent_cycle(
    db: Database, apartment_id: str, settlement_month: str,
    excluded_resident_ids: Sequence[str] | None = None, *, actor: str = "operator",
    split_mode: str | None = None,
) -> dict[str, Any]:
    """Charge the month's rent between the residents taking part.

    An open month's rent is charged by itself (see the live cycle above); this
    charges one by hand -- a month before the lease was set up, or one with
    somebody left out, or split another way than the agreement model.

    ``split_mode`` is ``equal`` or ``room_tiered``, and defaults to the lease's
    agreement model. Under ``room_tiered`` each participant is charged their
    room's base rent, which needs every participant in a room with a base rent
    and those rents to add up to the lease rent exactly.

    Creates the rent expense -- service period and settlement period both the
    given month, due on the lease's due day -- with one allocation per
    participant, and the pending rent cycle around it, in one transaction.

    If the month's statement exists it must still be a draft, and the expense is
    attached to it. If rent for the month already exists as an expense, the
    cycle adopts it unchanged instead; that needs no draft, because adopting
    changes nothing in the ledger.
    """
    settlement_month = normalize_month(settlement_month, "settlement_month")
    if split_mode is not None:
        split_mode = _split_mode(split_mode)
    timestamp = now_timestamp()
    with db.transaction() as uow:
        get_apartment(uow, apartment_id)
        config = _config_row(uow, apartment_id)
        if config is None:
            raise ConflictError("Set up the lease before generating rent.",
                                code="rent_not_configured")
        if _cycle_row(uow, apartment_id, settlement_month):
            raise ConflictError(
                f"Rent for {month_label(settlement_month)} has already been generated.",
                code="rent_cycle_exists", details={"settlement_month": settlement_month})
        adopted = _record_cycle(uow, apartment_id, settlement_month, config,
                                excluded_resident_ids=excluded_resident_ids,
                                split_mode=split_mode, timestamp=timestamp, actor=actor)
        summary = _summarize(uow, _cycle_row(uow, apartment_id, settlement_month), config)
    summary["adopted_existing_expense"] = adopted
    return summary


# ------------------------------------------------------ collecting the rent --

def record_rent_payment(
    db: Database, *, apartment_id: str, settlement_month: str, resident_id: str,
    amount_paise: int, payment_date: str, payment_mode: str | None = None,
    reference: str | None = None, note: str | None = None, actor: str = "operator",
) -> dict[str, Any]:
    """Record a resident paying their share of the month's rent into the pool.

    The payment is targeted at that resident's rent charge for the month, so it
    settles this rent first -- up to what is still owed on it -- regardless of
    older charges. Anything paid beyond the outstanding share is applied to
    their other charges oldest-first, and what is left after that stays an
    unapplied credit.

    It works whether or not the landlord has been paid yet: residents often
    settle up after the rent has gone out.
    """
    settlement_month = normalize_month(settlement_month, "settlement_month")
    ensure_paise(amount_paise, "amount_paise", allow_zero=False)
    timestamp = now_timestamp()
    with db.transaction() as uow:
        get_apartment(uow, apartment_id)
        resident = get_resident(uow, resident_id, apartment_id)
        cycle = _sync_live_rent_cycle(uow, apartment_id, settlement_month,
                                      check_reduction_guards=False, timestamp=timestamp,
                                      actor=actor)
        if cycle is None:
            raise NotFoundError(
                f"Rent for {month_label(settlement_month)} has not been generated.",
                code="rent_cycle_not_found", details={"settlement_month": settlement_month})
        open_charges = uow.rent.resident_charges_for_expense(cycle["expense_id"],
                                                             resident_id)
        if not open_charges:
            raise ValidationError(
                f"{resident['display_name']} is not taking part in this month's rent.",
                code="not_a_rent_participant",
                details={"field": "resident_id", "resident_id": resident_id})
        target = next((c for c in open_charges if c["outstanding_paise"] > 0), None)
        if target is None:
            raise ConflictError(
                f"{resident['display_name']} has already paid their share of this month's rent.",
                code="rent_share_already_paid", details={"resident_id": resident_id})
        own_advance = _advanced_by_resident(
            uow.rent.disbursements_for_cycle(cycle["cycle_id"])).get(resident_id, 0)
        if own_advance and own_advance >= sum(c["outstanding_paise"] for c in open_charges):
            raise ConflictError(
                f"{resident['display_name']} paid the landlord for this month, which already "
                "covers their share; the household owes them the rest.",
                code="rent_share_funded_by_payout", details={"resident_id": resident_id})
        payment = payments._record_payment(
            uow, apartment_id=apartment_id, payment_id=new_id("payment"),
            payer_resident_id=resident_id, amount_paise=amount_paise,
            payment_date=parse_iso_date(payment_date, "payment_date").isoformat(),
            payment_kind="to_pool", payee_or_provider=None, reference=reference,
            note=optional_text(note, "note") or f"Rent for {month_label(settlement_month)}",
            timestamp=timestamp, expense_id=cycle["expense_id"], actor=actor,
            target_allocation_id=target["allocation_id"], payment_mode=payment_mode)
        summary = _summarize(uow, _cycle_row(uow, apartment_id, settlement_month),
                             _config_row(uow, apartment_id))
    to_rent = sum(a["amount_paise"] for a in payment["applications"]
                  if a["allocation_id"] == target["allocation_id"])
    summary["payment"] = {
        **payment, "resident_id": resident_id,
        "applied_to_rent_paise": to_rent,
        "applied_to_other_charges_paise":
            amount_paise - to_rent - payment["unapplied_credit_paise"],
    }
    return summary


# ---------------------------------------------------------- paying landlord --

def _decode_landlord_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(receipt, Mapping):
        raise ValidationError("landlord_receipt must be an object.",
                              details={"field": "landlord_receipt"})
    filename = require_text(receipt.get("filename"), "landlord_receipt.filename",
                            max_length=200)
    filename = re.sub(r"[\x00-\x1f\x7f]", "", filename.replace("\\", "/").rsplit("/", 1)[-1])
    content_type = receipt.get("content_type")
    if content_type not in LANDLORD_RECEIPT_TYPES:
        raise ValidationError(
            "The landlord's receipt must be a PDF, JPEG, PNG, or WebP file.",
            details={"field": "landlord_receipt.content_type",
                     "allowed": list(LANDLORD_RECEIPT_TYPES)})
    try:
        content = base64.b64decode(receipt.get("content_base64") or "", validate=True)
    except (binascii.Error, ValueError, TypeError):
        raise ValidationError("The landlord's receipt could not be read.",
                              details={"field": "landlord_receipt.content_base64"}) from None
    if not filename or not content:
        raise ValidationError("The landlord's receipt file is empty.",
                              details={"field": "landlord_receipt"})
    if len(content) > MAX_LANDLORD_RECEIPT_BYTES:
        raise ValidationError(
            "The landlord's receipt is too large. The limit is 5 MB.",
            details={"field": "landlord_receipt", "size_bytes": len(content),
                     "max_bytes": MAX_LANDLORD_RECEIPT_BYTES})
    return {"filename": filename, "content_type": content_type, "content": content,
            "size_bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}


def _store_landlord_receipt(uow: UnitOfWork, cycle_id: str,
                            attachment: Mapping[str, Any], timestamp: str) -> None:
    uow.rent.delete_landlord_receipt(cycle_id)
    uow.rent.insert_landlord_receipt(
        cycle_id=cycle_id, filename=attachment["filename"],
        content_type=attachment["content_type"], size_bytes=attachment["size_bytes"],
        sha256=attachment["sha256"], content=attachment["content"], uploaded_at=timestamp)


def receipt_hash(receipt: Mapping[str, Any]) -> str:
    """SHA-256 over the facts a rent receipt states."""
    canonical = json.dumps(
        {key: receipt[key] for key in ("receipt_id", "cycle_id", "resident_id",
                                       "settlement_month", "amount_paise", "landlord_name",
                                       "landlord_pan", "created_at")},
        sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def record_landlord_disbursement(
    db: Database, *, apartment_id: str, settlement_month: str, disbursement_mode: str,
    reference: str, disbursed_date: str, amount_paise: int | None = None,
    funding_resident_id: str | None = None, landlord_receipt: Mapping[str, Any] | None = None,
    note: str | None = None, actor: str = "operator",
) -> dict[str, Any]:
    """Record a payment to the landlord towards the month's rent.

    The landlord can be paid in tranches -- what has been collected by the due
    date, and the rest when it comes in. ``amount_paise`` is this tranche;
    left out, it is everything still owed. Each tranche is kept with its own
    date and bank reference, and they can never add up to more than the rent.
    The month is ``partially_disbursed`` until they reach it, then
    ``disbursed`` and frozen.

    ``pool``
        The tranche left the apartment's pool. Nothing is added to the ledger:
        the expense was already a cost to the pool, and the residents' payments
        into the pool are what covered it.

    ``resident_advance``
        A resident paid it from their own account. What it covers of their own
        open share goes through the kitty, as everyone else's share does: a
        payment into the kitty settling their share, and a ``pool`` tranche
        paying it out. Only what they paid beyond their share is an advance: a
        ``provider_advance`` linked to the rent expense and a
        ``resident_advance`` tranche, which the kitty pays back to them (see
        :func:`reimburse_from_kitty`). When one payment covers the whole rent --
        while the statement is still a draft and nobody has paid their rent
        charge yet -- the shares are first re-split so that any remainder paisa
        avoids them.

    No HRA receipts are written (they are switched off; see DEBT-001). The
    landlord's own receipt never holds up a payout: when the lease says the
    landlord issues receipts, one may be attached here or later with
    :func:`attach_landlord_receipt`. When it says they do not, attaching one is
    refused.
    """
    settlement_month = normalize_month(settlement_month, "settlement_month")
    if disbursement_mode not in DISBURSEMENT_MODES:
        raise ValidationError("Unsupported disbursement_mode.",
                              details={"field": "disbursement_mode",
                                       "allowed": list(DISBURSEMENT_MODES)})
    reference = require_text(reference, "reference", max_length=200)
    disbursed_date = parse_iso_date(disbursed_date, "disbursed_date").isoformat()
    note = optional_text(note, "note", max_length=500)
    if amount_paise is not None:
        amount_paise = ensure_paise(amount_paise, "amount_paise", allow_zero=False)
    if disbursement_mode == "pool" and funding_resident_id:
        raise ValidationError(
            "A payout from the pool has no funding resident.",
            details={"field": "funding_resident_id"})
    if disbursement_mode == "resident_advance" and not funding_resident_id:
        raise ValidationError(
            "Choose the resident who paid the landlord.",
            details={"field": "funding_resident_id"})
    attachment = _decode_landlord_receipt(landlord_receipt) if landlord_receipt else None
    timestamp = now_timestamp()
    warnings: list[str] = []

    with db.transaction() as uow:
        get_apartment(uow, apartment_id)
        cycle = _sync_live_rent_cycle(uow, apartment_id, settlement_month,
                                      check_reduction_guards=False, timestamp=timestamp,
                                      actor=actor)
        if cycle is None:
            raise NotFoundError(
                f"Rent for {month_label(settlement_month)} has not been generated.",
                code="rent_cycle_not_found", details={"settlement_month": settlement_month})
        if cycle["disbursement_status"] == "disbursed":
            raise ConflictError(
                f"The rent for {month_label(settlement_month)} has already been paid to the "
                "landlord in full.", code="rent_already_disbursed",
                details={"cycle_id": cycle["cycle_id"], "disbursed_at": cycle["disbursed_at"]})
        config = _config_row(uow, apartment_id)
        if config is None:
            raise ConflictError("Set up the lease before recording a payout.",
                                code="rent_not_configured")
        if not config["requires_landlord_receipt"] and attachment is not None:
            raise ValidationError(
                "This lease is not set up to receive receipts from the landlord. Turn that "
                "on in the lease settings to attach one.", code="landlord_receipt_not_expected",
                details={"field": "landlord_receipt"})

        expense = get_expense(uow, cycle["expense_id"], apartment_id)
        total = expense["amount_paise"]
        tranches = uow.rent.disbursements_for_cycle(cycle["cycle_id"])
        already = sum(t["amount_paise"] for t in tranches)
        remaining = total - already
        if remaining <= 0:
            raise ConflictError(
                f"The rent for {month_label(settlement_month)} has already been paid to the "
                "landlord in full.", code="rent_already_disbursed",
                details={"cycle_id": cycle["cycle_id"], "disbursed_paise": already})
        amount = remaining if amount_paise is None else amount_paise
        if amount > remaining:
            raise ValidationError(
                f"That is more than the {format_inr(remaining)} still owed to the landlord "
                f"for {month_label(settlement_month)}.", code="tranche_exceeds_remaining",
                details={"field": "amount_paise", "amount_paise": amount,
                         "remaining_paise": remaining})
        whole = not tranches and amount == total

        # Advances linked to the expense other than this month's tranches mean
        # someone already paid the landlord outside this screen.
        tranche_advances = sum(t["amount_paise"] for t in tranches
                               if t["disbursed_by"] == "resident_advance")
        external = uow.rent.linked_advance_total_paise(expense["expense_id"]) - tranche_advances
        named_payer = expense["payer_resident_id"] if not tranches else None
        if external > 0 or (named_payer and not (disbursement_mode == "resident_advance"
                                                 and whole)):
            raise ConflictError(
                "The rent expense already records a resident as having paid it, so the "
                "payout cannot also have come from the pool or in parts. Record the whole "
                "rent as that resident's advance, or correct the expense first.",
                code="rent_already_funded",
                details={"expense_id": expense["expense_id"],
                         "payer_resident_id": expense["payer_resident_id"],
                         "linked_advance_paise": external})

        written: list[dict[str, Any]] = []
        if disbursement_mode == "pool":
            written.append(dict(disbursement_id=new_id("rentdisb"), amount_paise=amount,
                                disbursed_by="pool", funding_resident_id=None,
                                funding_payment_id=None, note=note))
        else:
            funder = get_resident(uow, funding_resident_id, apartment_id)
            if whole and statement_edit_mode(uow, expense["statement_id"]) == "free" \
                    and expense.get("allocation_method") == "equal":
                current = {c["resident_id"]: c["share_paise"]
                           for c in _charges(uow, expense["expense_id"])}
                plan = plan_allocation(
                    amount_paise=total, method="equal",
                    all_residents=resident_ids(uow, apartment_id),
                    participants=sorted(current), payer_resident_id=funding_resident_id)
                if {k: v for k, v in plan.shares_paise.items() if v} != current:
                    settled = sum(c["settled_paise"]
                                  for c in _charges(uow, expense["expense_id"]))
                    if settled:
                        warnings.append(
                            "The rent does not divide evenly, and the remainder paisa was "
                            "left where it was because payments have already been applied "
                            "to these charges.")
                    else:
                        expenses._reallocate(uow, expense, shares=plan.shares_paise,
                                             method="equal", basis=plan.basis)
                        audit.record(
                            uow, entity_type="expense", entity_id=expense["expense_id"],
                            action="reallocate", actor=actor, apartment_id=apartment_id,
                            field_name="allocations",
                            reason="Remainder paise moved away from the resident who paid "
                                   "the landlord",
                            new_value={k: v for k, v in plan.shares_paise.items() if v})
            name = funder["display_name"]
            open_charge = next((charge for charge in uow.rent.resident_charges_for_expense(
                expense["expense_id"], funding_resident_id) if charge["outstanding_paise"] > 0),
                None)
            share_paise = min(amount, open_charge["outstanding_paise"]) if open_charge else 0
            extra_paise = amount - share_paise
            if share_paise:
                # Their own share goes through the kitty, as everyone else's does:
                # a payment into it, and the kitty's payout of it to the landlord.
                share_payment_id = new_id("payment")
                payments._record_payment(
                    uow, apartment_id=apartment_id, payment_id=share_payment_id,
                    payer_resident_id=funding_resident_id, amount_paise=share_paise,
                    payment_date=disbursed_date, payment_kind="to_pool", payee_or_provider=None,
                    reference=reference, note=f"Rent for {month_label(settlement_month)}",
                    timestamp=timestamp, expense_id=expense["expense_id"], actor=actor,
                    target_allocation_id=open_charge["allocation_id"])
                written.append(dict(disbursement_id=_share_payout_id(share_payment_id),
                                    amount_paise=share_paise, disbursed_by="pool",
                                    funding_resident_id=None, funding_payment_id=None,
                                    note=f"{name}'s allotted share via House Kitty"))
            if extra_paise:
                # Only what they paid beyond their share is an advance, which the
                # kitty pays back out of what the others pay in.
                funding_payment_id = new_id("rent-advance")
                payments._record_payment(
                    uow, apartment_id=apartment_id, payment_id=funding_payment_id,
                    payer_resident_id=funding_resident_id, amount_paise=extra_paise,
                    payment_date=disbursed_date, payment_kind="provider_advance",
                    payee_or_provider=config["landlord_name"], reference=reference,
                    note=(f"Extra paid for rent for {month_label(settlement_month)} "
                          "to be reimbursed via House Kitty"),
                    timestamp=timestamp, expense_id=expense["expense_id"], actor=actor)
                written.append(dict(disbursement_id=new_id("rentdisb"),
                                    amount_paise=extra_paise, disbursed_by="resident_advance",
                                    funding_resident_id=funding_resident_id,
                                    funding_payment_id=funding_payment_id,
                                    note=note or (f"Extra above {name}'s share "
                                                  "(reimbursable via House Kitty)")))

        charges = _charges(uow, expense["expense_id"])
        if sum(c["share_paise"] for c in charges) != total or not charges:
            raise ConflictError(
                "The rent shares do not add up to the rent, so the payout was not recorded.",
                code="allocation_total_mismatch",
                details={"expected_paise": total,
                         "allocated_paise": sum(c["share_paise"] for c in charges)})

        paid = already + amount
        status = "disbursed" if paid >= total else "partially_disbursed"
        # The cycle shows the latest tranche by date; one paid the same day as an
        # earlier one is the later of the two.
        latest = max([*tranches, {"disbursed_at": disbursed_date,
                                  "disbursement_reference": reference, "seq": None}],
                     key=lambda t: (t["disbursed_at"], t["seq"] is None, t["seq"] or 0))
        uow.rent.update_cycle_disbursement_state(
            cycle["cycle_id"], status=status, last_disbursed_at=latest["disbursed_at"],
            last_reference=latest["disbursement_reference"], total_rent_paise=total,
            participant_count=len(charges))
        for tranche in written:
            uow.rent.insert_disbursement(
                cycle_id=cycle["cycle_id"], apartment_id=apartment_id,
                settlement_month=settlement_month, disbursed_at=disbursed_date,
                disbursement_reference=reference, created_at=timestamp, **tranche)
        disbursement_id = written[-1]["disbursement_id"]
        if attachment:
            _store_landlord_receipt(uow, cycle["cycle_id"], attachment, timestamp)
        audit.record(
            uow, entity_type="rent_cycle", entity_id=cycle["cycle_id"], action="disburse",
            actor=actor, apartment_id=apartment_id,
            new_value={"settlement_month": settlement_month, "disbursement_id": disbursement_id,
                       "amount_paise": amount, "total_rent_paise": total,
                       "disbursed_paise": paid, "remaining_paise": total - paid,
                       "disbursement_status": status, "disbursed_by": disbursement_mode,
                       "disbursed_at": disbursed_date, "disbursement_reference": reference,
                       "funding_resident_id": funding_resident_id, "note": note,
                       "tranches": [{key: tranche[key] for key in (
                           "disbursement_id", "amount_paise", "disbursed_by",
                           "funding_payment_id")} for tranche in written],
                       "landlord_receipt_sha256": attachment["sha256"] if attachment else None})
        summary = _summarize(uow, _cycle_row(uow, apartment_id, settlement_month), config)
    summary["warnings"] = warnings
    summary["disbursement_id"] = disbursement_id
    summary["disbursement_ids"] = [tranche["disbursement_id"] for tranche in written]
    return summary


def reimburse_from_kitty(
    db: Database, *, apartment_id: str, settlement_month: str, recipient_resident_id: str,
    amount_paise: int, payment_date: str, reference: str | None = None,
    note: str | None = None, actor: str = "operator",
) -> dict[str, Any]:
    """Pay back, from the kitty, a resident who paid the landlord more than their share.

    It is a ``refund`` to them out of what the kitty collected for the month. It
    can be no more than the kitty has collected for it and not yet paid out or
    back, nor more than they are still owed for it; part of it can wait until
    the rest of the flatmates have paid in.
    """
    settlement_month = normalize_month(settlement_month, "settlement_month")
    amount = ensure_paise(amount_paise, "amount_paise", allow_zero=False)
    payment_date = parse_iso_date(payment_date, "payment_date").isoformat()
    timestamp = now_timestamp()
    month_name = month_label(settlement_month)
    with db.transaction() as uow:
        get_apartment(uow, apartment_id)
        cycle = _sync_live_rent_cycle(uow, apartment_id, settlement_month,
                                      check_reduction_guards=False, timestamp=timestamp,
                                      actor=actor)
        if cycle is None:
            raise NotFoundError(
                f"Rent for {month_name} has not been generated.",
                code="rent_cycle_not_found", details={"settlement_month": settlement_month})
        config = _config_row(uow, apartment_id)
        owing = _summarize(uow, cycle, config)["reimbursements"]
        if not owing:
            raise ConflictError(
                f"Nobody has paid more than their share of {month_name} rent, so the kitty "
                "has nobody to pay back.",
                code="rent_no_advance", details={"settlement_month": settlement_month})
        owed = next((entry for entry in owing
                     if entry["recipient_resident_id"] == recipient_resident_id), None)
        if owed is None:
            names = ", ".join(entry["recipient_name"] for entry in owing)
            raise ValidationError(
                f"The kitty owes {names} for {month_name}, not this resident.",
                code="not_the_payout_resident",
                details={"field": "recipient_resident_id",
                         "payer_resident_ids": [entry["recipient_resident_id"]
                                                for entry in owing]})
        name = owed["recipient_name"]
        if amount > owed["pending_reimbursement_paise"]:
            raise ValidationError(
                f"That is more than the {format_inr(owed['pending_reimbursement_paise'])} "
                f"still owed to {name} for {month_name}.",
                code="exceeds_pending_reimbursement",
                details={"field": "amount_paise", "amount_paise": amount,
                         "pending_reimbursement_paise": owed["pending_reimbursement_paise"]})
        if amount > owed["kitty_cash_available_paise"]:
            raise ValidationError(
                f"The kitty holds only {format_inr(owed['kitty_cash_available_paise'])} from "
                f"flatmates for {month_name} rent that has not already gone to the landlord "
                f"or back to {name}.",
                code="exceeds_kitty_cash",
                details={"field": "amount_paise", "amount_paise": amount,
                         "kitty_cash_available_paise": owed["kitty_cash_available_paise"]})
        payment = payments._record_payment(
            uow, apartment_id=apartment_id,
            payment_id=new_id(_reimbursement_id_base(cycle["expense_id"])),
            payer_resident_id=recipient_resident_id, amount_paise=amount,
            payment_date=payment_date, payment_kind="refund", payee_or_provider=None,
            reference=optional_text(reference, "reference", max_length=200),
            note=(optional_text(note, "note", max_length=500)
                  or f"Reimbursement for rent for {month_name}, paid to the landlord on the "
                     "household's behalf"),
            timestamp=timestamp, expense_id=None, actor=actor)
        audit.record(uow, entity_type="rent_cycle", entity_id=cycle["cycle_id"],
                     action="reimburse", actor=actor, apartment_id=apartment_id,
                     new_value={"settlement_month": settlement_month,
                                "expense_id": cycle["expense_id"],
                                "recipient_resident_id": recipient_resident_id,
                                "amount_paise": amount, "payment_id": payment["payment_id"],
                                "payment_date": payment_date, "reference": reference})
        summary = _summarize(uow, _cycle_row(uow, apartment_id, settlement_month), config)
    summary["payment"] = payment
    return summary


def delete_rent_payment(db: Database, *, apartment_id: str, settlement_month: str,
                        payment_id: str, actor: str = "operator") -> dict[str, Any]:
    """Delete, while the month is open, a resident's payment into the kitty towards
    the month's rent or the kitty's reimbursement to a resident -- recorded by
    mistake. See :mod:`aem.services.deletions` for what goes with it."""
    settlement_month = normalize_month(settlement_month, "settlement_month")
    with db.transaction() as uow:
        get_apartment(uow, apartment_id)
        deleted = deletions.delete_payment_in(
            uow, apartment_id=apartment_id, scope="rent", payment_id=payment_id,
            settlement_month=settlement_month, actor=actor)
        summary = _summarize(uow, _cycle_row(uow, apartment_id, settlement_month),
                             _config_row(uow, apartment_id))
    summary["deleted"] = deleted
    return summary


def delete_landlord_disbursement(db: Database, *, apartment_id: str, settlement_month: str,
                                 disbursement_id: str,
                                 actor: str = "operator") -> dict[str, Any]:
    """Delete, while the month is open, a payout to the landlord recorded by
    mistake -- with the payments recorded alongside it -- and restate the month's
    payout status from the payouts left. See :mod:`aem.services.deletions`."""
    settlement_month = normalize_month(settlement_month, "settlement_month")
    with db.transaction() as uow:
        get_apartment(uow, apartment_id)
        deleted = deletions.delete_payout_in(
            uow, apartment_id=apartment_id, scope="rent", disbursement_id=disbursement_id,
            settlement_month=settlement_month, actor=actor)
        summary = _summarize(uow, _cycle_row(uow, apartment_id, settlement_month),
                             _config_row(uow, apartment_id))
    summary["deleted"] = deleted
    return summary


# ----------------------------------------------------------------- receipts --

def get_resident_rent_receipt(db: Database, *, apartment_id: str, settlement_month: str,
                              resident_id: str) -> dict[str, Any]:
    """One resident's HRA rent receipt for the month, ready to print.

    Receipts exist once the landlord payout has been recorded. The stored row
    is immutable; ``hash_matches`` being false means it no longer hashes to the
    value recorded with it, which is reported rather than corrected.

    The receipt states the resident's share of the rent paid to the landlord.
    Whether the resident has settled that share with the household is a
    separate fact, reported alongside as ``share_settled``.
    """
    settlement_month = normalize_month(settlement_month, "settlement_month")
    with db.read() as uow:
        apartment = get_apartment(uow, apartment_id)
        resident = get_resident(uow, resident_id, apartment_id)
        cycle = _cycle_row(uow, apartment_id, settlement_month)
        if cycle is None:
            raise NotFoundError(
                f"Rent for {month_label(settlement_month)} has not been generated.",
                code="rent_cycle_not_found", details={"settlement_month": settlement_month})
        if cycle["disbursement_status"] != "disbursed":
            raise ConflictError(
                "Receipts are issued once the landlord payout has been recorded.",
                code="rent_not_disbursed", details={"cycle_id": cycle["cycle_id"]})
        receipts = uow.rent.receipts_for_cycle(cycle["cycle_id"])
        receipt = next((r for r in receipts if r["resident_id"] == resident_id), None)
        if receipt is None:
            raise NotFoundError(
                f"{resident['display_name']} did not take part in this month's rent.",
                code="rent_receipt_not_found",
                details={"resident_id": resident_id, "settlement_month": settlement_month})
        charge = next((c for c in _charges(uow, cycle["expense_id"])
                       if c["resident_id"] == resident_id), None)
        funded = cycle["funding_resident_id"] == resident_id
    number = receipts.index(receipt) + 1
    return {
        "receipt_id": receipt["receipt_id"],
        "receipt_number": f"RCPT-{settlement_month}-{number:02d}",
        "cycle_id": cycle["cycle_id"],
        "settlement_month": settlement_month,
        "month_label": month_label(settlement_month),
        "resident_id": resident_id,
        "resident_name": resident["display_name"],
        "premises": premises_address(apartment),
        "amount_paise": receipt["amount_paise"],
        "amount_in_words": paise_in_words(receipt["amount_paise"]),
        "total_rent_paise": cycle["total_rent_paise"],
        "participant_count": cycle["participant_count"],
        "landlord_name": receipt["landlord_name"],
        "landlord_pan": receipt["landlord_pan"] or None,
        "landlord_pan_advised": (not receipt["landlord_pan"]
                                 and receipt["amount_paise"] * 12
                                 > PAN_REQUIRED_ABOVE_ANNUAL_PAISE),
        "disbursed_at": cycle["disbursed_at"],
        "disbursed_by": cycle["disbursed_by"],
        "disbursement_reference": cycle["disbursement_reference"],
        "share_settled": funded or bool(
            charge and charge["share_paise"] == charge["settled_paise"]),
        "issued_at": receipt["created_at"],
        "verification_hash": receipt["verification_hash"],
        "hash_matches": receipt["verification_hash"] == receipt_hash(receipt),
    }


def attach_landlord_receipt(db: Database, *, apartment_id: str, settlement_month: str,
                            landlord_receipt: Mapping[str, Any],
                            actor: str = "operator") -> dict[str, Any]:
    """Attach the landlord's receipt to a month whose payout is already recorded,
    or replace the one attached.

    Landlords send receipts when they get round to it, so this is separate from
    recording the payout. A replacement removes the earlier file; the audit log
    keeps the SHA-256 of both, so it stays visible that a file was swapped.
    Nothing else about the frozen cycle changes.
    """
    settlement_month = normalize_month(settlement_month, "settlement_month")
    attachment = _decode_landlord_receipt(landlord_receipt)
    timestamp = now_timestamp()
    with db.transaction() as uow:
        get_apartment(uow, apartment_id)
        cycle = _cycle_row(uow, apartment_id, settlement_month)
        if cycle is None:
            raise NotFoundError(
                f"Rent for {month_label(settlement_month)} has not been generated.",
                code="rent_cycle_not_found", details={"settlement_month": settlement_month})
        if cycle["disbursement_status"] == "pending":
            raise ConflictError(
                "The landlord has not been paid for this month yet. Record the payout first; "
                "the receipt can be attached then or afterwards.",
                code="rent_not_disbursed", details={"cycle_id": cycle["cycle_id"]})
        config = _config_row(uow, apartment_id)
        if config is None or not config["requires_landlord_receipt"]:
            raise ValidationError(
                "This lease is not set up to receive receipts from the landlord. Turn that "
                "on in the lease settings to attach one.", code="landlord_receipt_not_expected",
                details={"field": "landlord_receipt"})
        previous = uow.rent.landlord_receipt_identity(cycle["cycle_id"])
        _store_landlord_receipt(uow, cycle["cycle_id"], attachment, timestamp)
        audit.record(
            uow, entity_type="rent_cycle", entity_id=cycle["cycle_id"],
            action="replace_landlord_receipt" if previous else "attach_landlord_receipt",
            actor=actor, apartment_id=apartment_id, field_name="landlord_receipt",
            old_value=previous,
            new_value={"filename": attachment["filename"], "sha256": attachment["sha256"],
                       "size_bytes": attachment["size_bytes"]})
        summary = _summarize(uow, _cycle_row(uow, apartment_id, settlement_month), config)
    summary["replaced_landlord_receipt"] = previous is not None
    return summary


def get_landlord_receipt(db: Database, *, apartment_id: str,
                         settlement_month: str) -> dict[str, Any]:
    """The landlord's own receipt attached to a month's payout, with its bytes."""
    settlement_month = normalize_month(settlement_month, "settlement_month")
    with db.read() as uow:
        get_apartment(uow, apartment_id)
        row = uow.rent.landlord_receipt_for_month(apartment_id, settlement_month)
    if row is None:
        raise NotFoundError("No landlord receipt is attached to this month's rent.",
                            code="landlord_receipt_not_found",
                            details={"settlement_month": settlement_month})
    return row

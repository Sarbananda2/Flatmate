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
* **Landlord disbursement.** The whole rent goes to the landlord, either from
  the pool, or from one resident's own account. A resident who pays the
  landlord has funded the expense: that is recorded as a ``provider_advance``
  linked to the rent expense, which credits them the full rent while they are
  still charged their own share. Nothing is counted twice, because a linked
  advance replaces the expense's payer field as its funding record.

Rules this module holds to (see ``docs/ACCOUNTING_RULES.md``):

* **Nothing prorates.** A resident who takes part is charged a full equal
  share, whenever they moved in or out. Leaving somebody out of a month is an
  explicit participation decision made when the cycle is generated.
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
import sqlite3
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Mapping, Sequence

from .. import db
from ..db import connect, new_id, now_timestamp, transaction
from ..errors import ConflictError, NotFoundError, ValidationError
from ..money import ensure_paise, paise_in_words
from ..periods import normalize_month, parse_iso_date
from . import audit, definitions, expenses, payments
from .allocation import plan_allocation
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

#: What a landlord's receipt can be: a PDF or a photo of a paper slip.
LANDLORD_RECEIPT_TYPES = ("application/pdf", "image/jpeg", "image/png", "image/webp")
MAX_LANDLORD_RECEIPT_BYTES = 5 * 1024 * 1024

#: An employer asks for the landlord's PAN once rent passes ₹1,00,000 a year.
PAN_REQUIRED_ABOVE_ANNUAL_PAISE = 10_000_000

_PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
_IFSC_RE = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
_UPI_RE = re.compile(r"^[A-Za-z0-9._-]{2,256}@[A-Za-z][A-Za-z0-9.-]{1,63}$")
_ACCOUNT_RE = re.compile(r"^[0-9]{6,18}$")

_CONFIG_FIELDS = ("landlord_name", "landlord_phone", "landlord_email", "landlord_pan",
                  "landlord_bank_account", "landlord_bank_ifsc", "landlord_upi_id",
                  "monthly_rent_paise", "due_day_of_month", "requires_landlord_receipt",
                  "expense_definition_id")

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


def month_label(settlement_month: str) -> str:
    """``2026-10`` -> ``October 2026``."""
    year, month = settlement_month.split("-")
    return f"{calendar.month_name[int(month)]} {year}"


# ---------------------------------------------------------------- the lease --

def _reject_unknown(payload: Mapping[str, Any], allowed: Sequence[str], what: str) -> None:
    unknown = sorted(set(payload) - set(allowed))
    if unknown:
        raise ValidationError(f"Unsupported {what} fields: {', '.join(unknown)}.",
                              details={"unsupported_fields": unknown})


def _pattern(value: Any, label: str, pattern: re.Pattern[str], example: str, *,
             upper: bool = False, max_length: int = 64) -> str | None:
    text = optional_text(value, label, max_length=max_length)
    if text is None:
        return None
    if upper:
        text = text.upper().replace(" ", "")
    if not pattern.match(text):
        raise ValidationError(f"{label} does not look right. Expected something like {example}.",
                              details={"field": label})
    return text


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
    rent = ensure_paise(payload.get("monthly_rent_paise"), "monthly_rent_paise", allow_zero=False)
    due_day = payload.get("due_day_of_month", 5)
    if isinstance(due_day, bool) or not isinstance(due_day, int) or not 1 <= due_day <= 28:
        raise ValidationError(
            "due_day_of_month must be between 1 and 28 so every month has that day.",
            details={"field": "due_day_of_month"})
    requires = payload.get("requires_landlord_receipt", False)
    if not isinstance(requires, bool):
        raise ValidationError("requires_landlord_receipt must be true or false.",
                              details={"field": "requires_landlord_receipt"})
    email = optional_text(payload.get("landlord_email"), "landlord_email", max_length=254)
    if email is not None and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise ValidationError("landlord_email does not look like an email address.",
                              details={"field": "landlord_email"})
    return {
        "landlord_name": require_text(payload.get("landlord_name"), "landlord_name",
                                      max_length=200),
        "landlord_phone": optional_text(payload.get("landlord_phone"), "landlord_phone",
                                        max_length=40),
        "landlord_email": email,
        "landlord_pan": _pattern(payload.get("landlord_pan"), "landlord_pan", _PAN_RE,
                                 "ABCPS1234K", upper=True),
        "landlord_bank_account": _pattern(payload.get("landlord_bank_account"),
                                          "landlord_bank_account", _ACCOUNT_RE,
                                          "a 6 to 18 digit account number"),
        "landlord_bank_ifsc": _pattern(payload.get("landlord_bank_ifsc"), "landlord_bank_ifsc",
                                       _IFSC_RE, "HDFC0000128", upper=True),
        "landlord_upi_id": _pattern(payload.get("landlord_upi_id"), "landlord_upi_id", _UPI_RE,
                                    "name@bank", max_length=320),
        "monthly_rent_paise": rent,
        "due_day_of_month": due_day,
        "requires_landlord_receipt": requires,
        "expense_definition_id": optional_text(payload.get("expense_definition_id"),
                                               "expense_definition_id", max_length=120),
        **_clean_term(payload, existing),
    }


def _config_row(conn: sqlite3.Connection, apartment_id: str) -> dict[str, Any] | None:
    return db.fetch_one(
        conn, "SELECT * FROM rent_lease_config WHERE apartment_id=? AND is_active=1",
        (apartment_id,))


def _decode_config(conn: sqlite3.Connection, row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["requires_landlord_receipt"] = bool(row["requires_landlord_receipt"])
    out["is_active"] = bool(row["is_active"])
    out["expense_definition_name"] = db.scalar(
        conn, "SELECT name FROM expense_definition WHERE expense_definition_id=?",
        (row["expense_definition_id"],))
    out["term_number"] = 1 + int(db.scalar(
        conn, "SELECT COUNT(*) FROM rent_lease_term WHERE apartment_id=?",
        (row["apartment_id"],), default=0))
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


def get_lease_config(db_path, apartment_id: str) -> dict[str, Any] | None:
    """The apartment's lease, or ``None`` when rent has not been set up yet."""
    with connect(db_path, read_only=True) as conn:
        get_apartment(conn, apartment_id)
        row = _config_row(conn, apartment_id)
        return _decode_config(conn, row) if row else None


def _find_rent_definition(conn: sqlite3.Connection, apartment_id: str) -> str | None:
    """The definition rent is already recorded under, if the household has one."""
    return db.scalar(
        conn,
        """SELECT expense_definition_id FROM expense_definition
            WHERE apartment_id=? AND is_active=1 AND lower(category)=lower(?)
              AND (lower(expense_definition_id)='rent' OR lower(trim(name))='rent')
            ORDER BY sort_order, expense_definition_id LIMIT 1""",
        (apartment_id, RENT_CATEGORY))


def save_lease_config(db_path, apartment_id: str, payload: Mapping[str, Any], *,
                      actor: str = "operator") -> dict[str, Any]:
    """Create the apartment's lease, or update the one it has.

    Rent is recorded under an expense definition in the ``Housing`` category.
    The household's existing "Rent" definition is used when there is one, so
    rent generated here and rent generated by the recurring generator are the
    same expense rather than two. If there is none, a non-recurring one is
    created.

    Changing the rent does not touch a month that has already been generated:
    a cycle keeps the amount it was charged at.
    """
    timestamp = now_timestamp()

    with connect(db_path, read_only=True) as conn:
        get_apartment(conn, apartment_id)
        existing = _config_row(conn, apartment_id)
        cleaned = _clean_config(payload, existing)
        definition_id = cleaned.pop("expense_definition_id")
        if definition_id:
            owner = db.fetch_one(
                conn, "SELECT apartment_id, is_active FROM expense_definition "
                      "WHERE expense_definition_id=?", (definition_id,))
            if not owner or owner["apartment_id"] != apartment_id or not owner["is_active"]:
                raise ValidationError(
                    "The rent category must be an active expense definition of this apartment.",
                    details={"field": "expense_definition_id"})
        else:
            definition_id = (existing or {}).get("expense_definition_id") \
                or _find_rent_definition(conn, apartment_id)
        id_taken = definition_id is None and conn.execute(
            "SELECT 1 FROM expense_definition WHERE expense_definition_id='rent'").fetchone()

    if definition_id is None:
        definition_id = definitions.create_expense_definition(
            db_path, apartment_id=apartment_id,
            definition_id=new_id("rent") if id_taken else "rent", name="Rent",
            category=RENT_CATEGORY, period_rule="current_month",
            default_allocation_method="equal", is_recurring=False, recurrence="none",
            default_amount_paise=cleaned["monthly_rent_paise"],
            default_provider_name=cleaned["landlord_name"], day_of_month=1, sort_order=10,
            notes="Created by rent management. Rent is generated from the Rent screen.",
            actor=actor)["expense_definition_id"]

    values = {**cleaned, "expense_definition_id": definition_id,
              "requires_landlord_receipt": int(cleaned["requires_landlord_receipt"])}
    with transaction(db_path) as conn:
        before = _config_row(conn, apartment_id)
        if before is None:
            config_id = new_id("lease")
            columns = ["config_id", "apartment_id", *values, "created_at", "updated_at",
                       "is_active"]
            conn.execute(
                f"INSERT INTO rent_lease_config ({', '.join(columns)}) "
                f"VALUES ({', '.join('?' for _ in columns)})",
                (config_id, apartment_id, *values.values(), timestamp, timestamp, 1))
            audit.record(conn, entity_type="rent_lease_config", entity_id=config_id,
                         action="create", actor=actor, apartment_id=apartment_id,
                         new_value=values)
        else:
            config_id = before["config_id"]
            conn.execute(
                f"UPDATE rent_lease_config SET {', '.join(f'{c}=?' for c in values)}, "
                "updated_at=? WHERE config_id=?",
                (*values.values(), timestamp, config_id))
            audit.record_field_changes(
                conn, entity_type="rent_lease_config", entity_id=config_id, before=before,
                after={**before, **values}, fields=list(values), actor=actor,
                apartment_id=apartment_id)
        return _decode_config(conn, _config_row(conn, apartment_id))


# ------------------------------------------------------ renewing and leaving --

def renew_lease_contract(db_path, apartment_id: str, payload: Mapping[str, Any], *,
                         actor: str = "operator") -> dict[str, Any]:
    """Roll the lease into its next term and archive the one that is ending.

    Anything left out follows the lease: the new term starts the day after the
    current one ends, runs as long, and charges the rent the escalation clause
    projects. Months already generated keep the rent they were charged at.
    """
    _reject_unknown(payload, ("new_start_date", "duration_months", "new_monthly_rent_paise",
                              "escalation_type", "escalation_percent"), "renewal")
    timestamp = now_timestamp()
    with transaction(db_path) as conn:
        get_apartment(conn, apartment_id)
        before = _config_row(conn, apartment_id)
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

        term_number = 1 + int(db.scalar(
            conn, "SELECT COUNT(*) FROM rent_lease_term WHERE apartment_id=?",
            (apartment_id,), default=0))
        applied = float(round(Decimal(new_rent - old_rent) * 100 / Decimal(old_rent), 2))
        term_id = new_id("leaseterm")
        conn.execute(
            """INSERT INTO rent_lease_term
               (term_id, apartment_id, term_number, start_date, end_date, monthly_rent_paise,
                escalation_applied_percent, archived_at, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (term_id, apartment_id, term_number, before["lease_start_date"],
             before["lease_end_date"], old_rent, applied, timestamp, before["created_at"]))

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
        conn.execute(
            f"UPDATE rent_lease_config SET {', '.join(f'{c}=?' for c in values)}, "
            "updated_at=? WHERE config_id=?",
            (*values.values(), timestamp, before["config_id"]))
        audit.record(conn, entity_type="rent_lease_config", entity_id=before["config_id"],
                     action="renew", actor=actor, apartment_id=apartment_id,
                     old_value={field: before[field] for field in values},
                     new_value={**values, "archived_term_id": term_id,
                                "archived_term_number": term_number})
        return _decode_config(conn, _config_row(conn, apartment_id))


def serve_vacate_notice(db_path, apartment_id: str, payload: Mapping[str, Any], *,
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
    with transaction(db_path) as conn:
        get_apartment(conn, apartment_id)
        before = _config_row(conn, apartment_id)
        if before is None:
            raise ConflictError("Set up the lease before serving notice on it.",
                                code="rent_not_configured")
        values = {"lease_status": "notice_served", "notice_served_date": served.isoformat(),
                  "target_handover_date": handover.isoformat()}
        conn.execute(
            "UPDATE rent_lease_config SET lease_status=?, notice_served_date=?, "
            "target_handover_date=?, updated_at=? WHERE config_id=?",
            (*values.values(), now_timestamp(), before["config_id"]))
        audit.record(conn, entity_type="rent_lease_config", entity_id=before["config_id"],
                     action="notice_served", actor=actor, apartment_id=apartment_id,
                     old_value={field: before[field] for field in values},
                     new_value=values, reason=notes)
        return _decode_config(conn, _config_row(conn, apartment_id))


def list_lease_terms(db_path, apartment_id: str) -> list[dict[str, Any]]:
    """Completed terms, oldest first."""
    with connect(db_path, read_only=True) as conn:
        get_apartment(conn, apartment_id)
        return db.fetch_all(
            conn, "SELECT * FROM rent_lease_term WHERE apartment_id=? ORDER BY term_number",
            (apartment_id,))


# ---------------------------------------------------------------- the cycle --

def _cycle_row(conn: sqlite3.Connection, apartment_id: str,
               settlement_month: str) -> dict[str, Any] | None:
    return db.fetch_one(
        conn, "SELECT * FROM rent_cycle WHERE apartment_id=? AND settlement_month=?",
        (apartment_id, settlement_month))


def _statement_row(conn: sqlite3.Connection, apartment_id: str,
                   settlement_month: str) -> dict[str, Any] | None:
    return db.fetch_one(
        conn, "SELECT statement_id, settlement_month, status FROM monthly_statement "
              "WHERE apartment_id=? AND settlement_month=?", (apartment_id, settlement_month))


def _names(conn: sqlite3.Connection, apartment_id: str) -> dict[str, str]:
    return {row["resident_id"]: row["display_name"] for row in conn.execute(
        "SELECT resident_id, display_name FROM resident WHERE apartment_id=?",
        (apartment_id,)).fetchall()}


def _charges(conn: sqlite3.Connection, expense_id: str) -> list[dict[str, Any]]:
    """Each resident's rent charge for one expense, with what has been paid off."""
    return db.fetch_all(
        conn,
        """SELECT a.resident_id, r.display_name,
                  SUM(a.amount_paise) AS share_paise,
                  COALESCE(SUM((SELECT SUM(pa.amount_paise) FROM payment_application pa
                                 WHERE pa.allocation_id = a.allocation_id)), 0) AS settled_paise,
                  (SELECT MAX(p.payment_date) FROM payment_application pa
                     JOIN payment p ON p.payment_id = pa.payment_id
                     JOIN allocation a2 ON a2.allocation_id = pa.allocation_id
                    WHERE a2.expense_id = a.expense_id
                      AND a2.resident_id = a.resident_id) AS last_payment_date
             FROM allocation a JOIN resident r ON r.resident_id = a.resident_id
            WHERE a.expense_id = ?
            GROUP BY a.resident_id, r.display_name
            ORDER BY r.display_name, a.resident_id""", (expense_id,))


def _existing_rent_expenses(conn: sqlite3.Connection, apartment_id: str, definition_id: str,
                            settlement_month: str) -> list[dict[str, Any]]:
    return db.fetch_all(
        conn, """SELECT * FROM expense_instance
                  WHERE apartment_id=? AND expense_definition_id=? AND settlement_period=?
                  ORDER BY rowid""", (apartment_id, definition_id, settlement_month))


def _adoption_problem(expense: dict[str, Any], charges: list[dict[str, Any]]) -> str | None:
    """Why an existing rent expense cannot be taken over as the month's rent."""
    if expense["amount_paise"] <= 0 or not charges:
        return "The existing rent expense has no amount or no shares."
    if (expense.get("allocation_method") or "") != "equal":
        return ("The existing rent expense is not split equally. This screen manages an "
                "equal-split lease; change the expense to an equal split, or remove it.")
    return None


def _summarize(conn: sqlite3.Connection, cycle: dict[str, Any],
               config: dict[str, Any] | None) -> dict[str, Any]:
    """A rent cycle as the screen shows it.

    Amounts are read from the expense and its allocations, which are the
    accounting record, so an edit made to the rent expense elsewhere is
    reflected here. ``collected_paise`` counts what residents have paid into
    the pool against their rent charge, plus the own share of a resident who
    paid the landlord directly -- their share was never going to come through
    the pool.
    """
    expense = get_expense(conn, cycle["expense_id"])
    charges = _charges(conn, cycle["expense_id"])
    disbursed = cycle["disbursement_status"] == "disbursed"
    funder = cycle["funding_resident_id"]
    participants: list[dict[str, Any]] = []
    collected = 0
    for charge in charges:
        outstanding = charge["share_paise"] - charge["settled_paise"]
        if funder == charge["resident_id"]:
            status, covered = "funded_payout", charge["share_paise"]
        elif outstanding == 0:
            status, covered = "paid", charge["share_paise"]
        else:
            status = "partial" if charge["settled_paise"] else "unpaid"
            covered = charge["settled_paise"]
        collected += covered
        participants.append({
            **charge, "outstanding_paise": outstanding, "collection_status": status,
            "receipt_available": disbursed})
    total = expense["amount_paise"]
    count = len(participants)
    names = _names(conn, cycle["apartment_id"])
    taking_part = {p["resident_id"] for p in participants}
    statement = db.fetch_one(
        conn, "SELECT statement_id, settlement_month, status FROM monthly_statement "
              "WHERE statement_id=?", (expense["statement_id"],)) \
        if expense["statement_id"] else None
    landlord_receipt = db.fetch_one(
        conn, "SELECT filename, content_type, size_bytes, sha256, uploaded_at "
              "FROM rent_landlord_receipt WHERE cycle_id=?", (cycle["cycle_id"],))
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
        "disbursed_at": cycle["disbursed_at"],
        "disbursement_reference": cycle["disbursement_reference"],
        "disbursed_by": cycle["disbursed_by"],
        "funding_resident_id": funder,
        "funding_resident_name": names.get(funder) if funder else None,
        "funding_payment_id": cycle["funding_payment_id"],
        "lease_rent_paise": config["monthly_rent_paise"] if config else None,
        "matches_lease_rent": config is None or config["monthly_rent_paise"] == total,
        "landlord_receipt": landlord_receipt,
        # 'pending' only once the landlord has been paid: there is nothing to
        # chase before that.
        "landlord_receipt_status": (
            "attached" if landlord_receipt
            else "pending" if disbursed and config and config["requires_landlord_receipt"]
            else "not_required"),
        "statement": statement,
        "created_at": cycle["created_at"],
    }


def get_rent_cycle(db_path, apartment_id: str, settlement_month: str) -> dict[str, Any] | None:
    """The month's rent cycle, or ``None`` if rent has not been generated for it."""
    settlement_month = normalize_month(settlement_month, "settlement_month")
    with connect(db_path, read_only=True) as conn:
        get_apartment(conn, apartment_id)
        cycle = _cycle_row(conn, apartment_id, settlement_month)
        return _summarize(conn, cycle, _config_row(conn, apartment_id)) if cycle else None


def _participants(conn: sqlite3.Connection, apartment_id: str,
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
    unknown = sorted(set(excluded) - set(resident_ids(conn, apartment_id)))
    if unknown:
        raise ValidationError(
            "excluded_resident_ids must all be residents of this apartment.",
            details={"field": "excluded_resident_ids", "unknown_resident_ids": unknown})
    chosen = [rid for rid in resident_ids(conn, apartment_id, active_only=True)
              if rid not in excluded]
    return chosen, excluded


def _preview(conn: sqlite3.Connection, apartment_id: str, settlement_month: str,
             config: dict[str, Any], excluded_resident_ids: Sequence[str] | None,
             ) -> dict[str, Any]:
    """What generating the month's rent would do, without doing it."""
    names = _names(conn, apartment_id)
    statement = _statement_row(conn, apartment_id, settlement_month)
    found = _existing_rent_expenses(conn, apartment_id, config["expense_definition_id"],
                                    settlement_month)
    base = {"settlement_month": settlement_month, "statement": statement,
            "due_date": f"{settlement_month}-{config['due_day_of_month']:02d}"}
    if found:
        expense = found[0]
        charges = _charges(conn, expense["expense_id"])
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
            "matches_lease_rent": total == config["monthly_rent_paise"],
        }

    chosen, excluded = _participants(conn, apartment_id, excluded_resident_ids)
    total = config["monthly_rent_paise"]
    problem = None
    shares: dict[str, int] = {}
    if statement and statement["status"] != "draft":
        problem = (f"The {settlement_month} statement has been finalized, so rent can no "
                   "longer be added to it.")
    elif not chosen:
        problem = "Nobody is taking part. At least one active resident must share the rent."
    else:
        shares = plan_allocation(amount_paise=total, method="equal",
                                 all_residents=resident_ids(conn, apartment_id),
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
    }


def rent_month_overview(db_path, apartment_id: str, settlement_month: str, *,
                        excluded_resident_ids: Sequence[str] | None = None) -> dict[str, Any]:
    """Everything the Rent screen needs for one month.

    ``cycle`` is the generated rent, when there is one. Otherwise ``preview``
    describes what generating it would do -- including, for an equal split with
    somebody left out, the exact share each remaining resident would be charged.
    """
    settlement_month = normalize_month(settlement_month, "settlement_month")
    with connect(db_path, read_only=True) as conn:
        get_apartment(conn, apartment_id)
        config = _config_row(conn, apartment_id)
        cycle = _cycle_row(conn, apartment_id, settlement_month)
        return {
            "settlement_month": settlement_month,
            "month_label": month_label(settlement_month),
            "config": _decode_config(conn, config) if config else None,
            "cycle": _summarize(conn, cycle, config) if cycle else None,
            "preview": _preview(conn, apartment_id, settlement_month, config,
                                excluded_resident_ids) if config and not cycle else None,
        }


def generate_monthly_rent_cycle(
    db_path, apartment_id: str, settlement_month: str,
    excluded_resident_ids: Sequence[str] | None = None, *, actor: str = "operator",
) -> dict[str, Any]:
    """Charge the month's rent, split equally between the residents taking part.

    Creates the rent expense -- service period and settlement period both the
    given month, due on the lease's due day -- with one allocation per
    participant, and the pending rent cycle around it, in one transaction.

    If the month's statement exists it must still be a draft, and the expense is
    attached to it. If rent for the month already exists as an expense, the
    cycle adopts it unchanged instead; that needs no draft, because adopting
    changes nothing in the ledger.
    """
    settlement_month = normalize_month(settlement_month, "settlement_month")
    timestamp = now_timestamp()
    with transaction(db_path) as conn:
        get_apartment(conn, apartment_id)
        config = _config_row(conn, apartment_id)
        if config is None:
            raise ConflictError("Set up the lease before generating rent.",
                                code="rent_not_configured")
        if _cycle_row(conn, apartment_id, settlement_month):
            raise ConflictError(
                f"Rent for {month_label(settlement_month)} has already been generated.",
                code="rent_cycle_exists", details={"settlement_month": settlement_month})

        found = _existing_rent_expenses(conn, apartment_id, config["expense_definition_id"],
                                        settlement_month)
        if found:
            expense = found[0]
            charges = _charges(conn, expense["expense_id"])
            if len(found) > 1:
                raise ConflictError(
                    "More than one rent expense exists for this month. Remove the duplicate "
                    "before generating.", code="rent_expense_ambiguous",
                    details={"expense_ids": [row["expense_id"] for row in found]})
            problem = _adoption_problem(expense, charges)
            if problem:
                raise ConflictError(problem, code="rent_expense_not_adoptable",
                                    details={"expense_id": expense["expense_id"]})
            if excluded_resident_ids:
                raise ConflictError(
                    "Rent for this month already exists as an expense, so its participants "
                    "are the ones recorded on it. Change them on the expense itself.",
                    code="rent_expense_exists", details={"expense_id": expense["expense_id"]})
            expense_id, adopted = expense["expense_id"], True
            total, count = expense["amount_paise"], len(charges)
        else:
            statement = _statement_row(conn, apartment_id, settlement_month)
            if statement and statement["status"] != "draft":
                raise ConflictError(
                    f"The {settlement_month} statement has been finalized, so rent can no "
                    "longer be added to it.", code="statement_not_draft",
                    details={"statement_id": statement["statement_id"],
                             "status": statement["status"]})
            chosen, excluded = _participants(conn, apartment_id, excluded_resident_ids)
            if not chosen:
                raise ValidationError(
                    "Nobody is taking part. At least one active resident must share the rent.",
                    code="no_participants", details={"excluded_resident_ids": excluded})
            recurrence = db.scalar(
                conn, "SELECT recurrence FROM expense_definition WHERE expense_definition_id=?",
                (config["expense_definition_id"],))
            expense_id, adopted = new_id("rent"), False
            total, count = config["monthly_rent_paise"], len(chosen)
            # No payer yet: who funds the payout is only known when it is made.
            expenses._create_expense(
                conn, apartment_id=apartment_id, expense_id=expense_id,
                description=f"Rent for {month_label(settlement_month)}",
                amount_paise=total, expense_date=f"{settlement_month}-01",
                allocation_method="equal", participants=chosen,
                provider_name=config["landlord_name"],
                expense_definition_id=config["expense_definition_id"],
                statement_id=statement["statement_id"] if statement else None,
                period_rule="current_month", service_period=settlement_month,
                settlement_period=settlement_month,
                due_date=f"{settlement_month}-{config['due_day_of_month']:02d}",
                generated_from_recurrence=recurrence == "monthly",
                created_at=timestamp, actor=actor)

        cycle_id = new_id("rentcycle")
        conn.execute(
            """INSERT INTO rent_cycle
               (cycle_id, apartment_id, settlement_month, expense_id, total_rent_paise,
                participant_count, disbursement_status, created_at)
               VALUES (?,?,?,?,?,?,'pending',?)""",
            (cycle_id, apartment_id, settlement_month, expense_id, total, count, timestamp))
        audit.record(conn, entity_type="rent_cycle", entity_id=cycle_id, action="create",
                     actor=actor, apartment_id=apartment_id,
                     new_value={"settlement_month": settlement_month, "expense_id": expense_id,
                                "total_rent_paise": total, "participant_count": count,
                                "adopted_existing_expense": adopted})
        summary = _summarize(conn, _cycle_row(conn, apartment_id, settlement_month), config)
    summary["adopted_existing_expense"] = adopted
    return summary


# ------------------------------------------------------ collecting the rent --

def record_rent_payment(
    db_path, *, apartment_id: str, settlement_month: str, resident_id: str,
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
    with transaction(db_path) as conn:
        get_apartment(conn, apartment_id)
        resident = get_resident(conn, resident_id, apartment_id)
        cycle = _cycle_row(conn, apartment_id, settlement_month)
        if cycle is None:
            raise NotFoundError(
                f"Rent for {month_label(settlement_month)} has not been generated.",
                code="rent_cycle_not_found", details={"settlement_month": settlement_month})
        open_charges = db.fetch_all(
            conn,
            """SELECT a.allocation_id,
                      a.amount_paise - COALESCE((SELECT SUM(pa.amount_paise)
                                                   FROM payment_application pa
                                                  WHERE pa.allocation_id = a.allocation_id), 0)
                          AS outstanding_paise
                 FROM allocation a
                WHERE a.expense_id = ? AND a.resident_id = ? ORDER BY a.rowid""",
            (cycle["expense_id"], resident_id))
        if not open_charges:
            raise ValidationError(
                f"{resident['display_name']} is not taking part in this month's rent.",
                code="not_a_rent_participant",
                details={"field": "resident_id", "resident_id": resident_id})
        if cycle["funding_resident_id"] == resident_id:
            raise ConflictError(
                f"{resident['display_name']} paid the landlord for this month, which already "
                "covers their share; the household owes them the rest.",
                code="rent_share_funded_by_payout", details={"resident_id": resident_id})
        target = next((c for c in open_charges if c["outstanding_paise"] > 0), None)
        if target is None:
            raise ConflictError(
                f"{resident['display_name']} has already paid their share of this month's rent.",
                code="rent_share_already_paid", details={"resident_id": resident_id})
        payment = payments._record_payment(
            conn, apartment_id=apartment_id, payment_id=new_id("payment"),
            payer_resident_id=resident_id, amount_paise=amount_paise,
            payment_date=parse_iso_date(payment_date, "payment_date").isoformat(),
            payment_kind="to_pool", payee_or_provider=None, reference=reference,
            note=optional_text(note, "note") or f"Rent for {month_label(settlement_month)}",
            timestamp=timestamp, expense_id=None, actor=actor,
            target_allocation_id=target["allocation_id"], payment_mode=payment_mode)
        summary = _summarize(conn, _cycle_row(conn, apartment_id, settlement_month),
                             _config_row(conn, apartment_id))
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


def _store_landlord_receipt(conn: sqlite3.Connection, cycle_id: str,
                            attachment: Mapping[str, Any], timestamp: str) -> None:
    conn.execute("DELETE FROM rent_landlord_receipt WHERE cycle_id=?", (cycle_id,))
    conn.execute(
        """INSERT INTO rent_landlord_receipt
           (cycle_id, filename, content_type, size_bytes, sha256, content, uploaded_at)
           VALUES (?,?,?,?,?,?,?)""",
        (cycle_id, attachment["filename"], attachment["content_type"],
         attachment["size_bytes"], attachment["sha256"], attachment["content"], timestamp))


def receipt_hash(receipt: Mapping[str, Any]) -> str:
    """SHA-256 over the facts a rent receipt states."""
    canonical = json.dumps(
        {key: receipt[key] for key in ("receipt_id", "cycle_id", "resident_id",
                                       "settlement_month", "amount_paise", "landlord_name",
                                       "landlord_pan", "created_at")},
        sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def record_landlord_disbursement(
    db_path, *, apartment_id: str, settlement_month: str, disbursement_mode: str,
    reference: str, disbursed_date: str, funding_resident_id: str | None = None,
    landlord_receipt: Mapping[str, Any] | None = None, actor: str = "operator",
) -> dict[str, Any]:
    """Record that the landlord has been paid the month's rent.

    ``pool``
        The rent left the apartment's pool. Nothing is added to the ledger: the
        expense was already a cost to the pool, and the residents' payments into
        the pool are what covered it.

    ``resident_advance``
        One resident paid the landlord from their own account. A
        ``provider_advance`` for the whole rent is linked to the rent expense,
        crediting them the full amount while they keep their own share. While
        the statement is still a draft and nobody has paid their rent charge
        yet, the shares are also re-split so that any remainder paisa avoids
        the resident who paid.

    Either way the cycle becomes ``disbursed`` and is frozen, and one immutable
    HRA receipt is written for each participant.

    The landlord's own receipt never holds up a payout. When the lease says the
    landlord issues receipts, one may be attached here or later with
    :func:`attach_landlord_receipt`, whenever it arrives. When the lease says
    the landlord does not, attaching one is refused.
    """
    settlement_month = normalize_month(settlement_month, "settlement_month")
    if disbursement_mode not in DISBURSEMENT_MODES:
        raise ValidationError("Unsupported disbursement_mode.",
                              details={"field": "disbursement_mode",
                                       "allowed": list(DISBURSEMENT_MODES)})
    reference = require_text(reference, "reference", max_length=200)
    disbursed_date = parse_iso_date(disbursed_date, "disbursed_date").isoformat()
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

    with transaction(db_path) as conn:
        get_apartment(conn, apartment_id)
        cycle = _cycle_row(conn, apartment_id, settlement_month)
        if cycle is None:
            raise NotFoundError(
                f"Rent for {month_label(settlement_month)} has not been generated.",
                code="rent_cycle_not_found", details={"settlement_month": settlement_month})
        if cycle["disbursement_status"] != "pending":
            raise ConflictError(
                f"The landlord payout for {month_label(settlement_month)} has already been "
                "recorded.", code="rent_already_disbursed",
                details={"cycle_id": cycle["cycle_id"], "disbursed_at": cycle["disbursed_at"]})
        config = _config_row(conn, apartment_id)
        if config is None:
            raise ConflictError("Set up the lease before recording a payout.",
                                code="rent_not_configured")
        if not config["requires_landlord_receipt"] and attachment is not None:
            raise ValidationError(
                "This lease is not set up to receive receipts from the landlord. Turn that "
                "on in the lease settings to attach one.", code="landlord_receipt_not_expected",
                details={"field": "landlord_receipt"})

        expense = get_expense(conn, cycle["expense_id"], apartment_id)
        total = expense["amount_paise"]
        linked = int(db.scalar(
            conn, """SELECT COALESCE(SUM(amount_paise), 0) FROM payment
                      WHERE expense_id=? AND payment_kind='provider_advance'
                        AND status='active'""", (expense["expense_id"],), default=0))
        funding_payment_id = None
        if disbursement_mode == "pool":
            if expense["payer_resident_id"] or linked:
                raise ConflictError(
                    "The rent expense already records a resident as having paid it, so the "
                    "payout cannot also have come from the pool. Record it as a resident "
                    "advance, or correct the expense first.", code="rent_already_funded",
                    details={"expense_id": expense["expense_id"],
                             "payer_resident_id": expense["payer_resident_id"],
                             "linked_advance_paise": linked})
        else:
            get_resident(conn, funding_resident_id, apartment_id)
            if statement_edit_mode(conn, expense["statement_id"]) == "free":
                current = {c["resident_id"]: c["share_paise"]
                           for c in _charges(conn, expense["expense_id"])}
                plan = plan_allocation(
                    amount_paise=total, method="equal",
                    all_residents=resident_ids(conn, apartment_id),
                    participants=sorted(current), payer_resident_id=funding_resident_id)
                if {k: v for k, v in plan.shares_paise.items() if v} != current:
                    settled = sum(c["settled_paise"]
                                  for c in _charges(conn, expense["expense_id"]))
                    if settled or expense.get("allocation_method") != "equal":
                        warnings.append(
                            "The rent does not divide evenly, and the remainder paisa was "
                            "left where it was because payments have already been applied "
                            "to these charges.")
                    else:
                        expenses._reallocate(conn, expense, shares=plan.shares_paise,
                                             method="equal", basis=plan.basis)
                        audit.record(
                            conn, entity_type="expense", entity_id=expense["expense_id"],
                            action="reallocate", actor=actor, apartment_id=apartment_id,
                            field_name="allocations",
                            reason="Remainder paise moved away from the resident who paid "
                                   "the landlord",
                            new_value={k: v for k, v in plan.shares_paise.items() if v})
                conn.execute(
                    "UPDATE expense_instance SET payer_resident_id=?, updated_at=? "
                    "WHERE expense_id=?", (funding_resident_id, timestamp, expense["expense_id"]))
            funding_payment_id = new_id("rent-advance")
            payments._record_payment(
                conn, apartment_id=apartment_id, payment_id=funding_payment_id,
                payer_resident_id=funding_resident_id, amount_paise=total,
                payment_date=disbursed_date, payment_kind="provider_advance",
                payee_or_provider=config["landlord_name"], reference=reference,
                note=f"Rent for {month_label(settlement_month)} paid to the landlord",
                timestamp=timestamp, expense_id=expense["expense_id"], actor=actor)

        charges = _charges(conn, expense["expense_id"])
        if sum(c["share_paise"] for c in charges) != total or not charges:
            raise ConflictError(
                "The rent shares do not add up to the rent, so the payout was not recorded.",
                code="allocation_total_mismatch",
                details={"expected_paise": total,
                         "allocated_paise": sum(c["share_paise"] for c in charges)})

        conn.execute(
            """UPDATE rent_cycle SET disbursement_status='disbursed', disbursed_at=?,
                      disbursement_reference=?, disbursed_by=?, funding_resident_id=?,
                      funding_payment_id=?, total_rent_paise=?, participant_count=?
                WHERE cycle_id=?""",
            (disbursed_date, reference, disbursement_mode, funding_resident_id,
             funding_payment_id, total, len(charges), cycle["cycle_id"]))
        for charge in charges:
            receipt = {
                "receipt_id": new_id("rentreceipt"), "cycle_id": cycle["cycle_id"],
                "resident_id": charge["resident_id"], "settlement_month": settlement_month,
                "amount_paise": charge["share_paise"],
                "landlord_name": config["landlord_name"],
                "landlord_pan": config["landlord_pan"] or "", "created_at": timestamp}
            conn.execute(
                """INSERT INTO rent_receipt
                   (receipt_id, cycle_id, resident_id, settlement_month, amount_paise,
                    landlord_name, landlord_pan, verification_hash, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (receipt["receipt_id"], receipt["cycle_id"], receipt["resident_id"],
                 receipt["settlement_month"], receipt["amount_paise"],
                 receipt["landlord_name"], receipt["landlord_pan"], receipt_hash(receipt),
                 receipt["created_at"]))
        if attachment:
            _store_landlord_receipt(conn, cycle["cycle_id"], attachment, timestamp)
        audit.record(
            conn, entity_type="rent_cycle", entity_id=cycle["cycle_id"], action="disburse",
            actor=actor, apartment_id=apartment_id,
            new_value={"settlement_month": settlement_month, "total_rent_paise": total,
                       "disbursed_by": disbursement_mode, "disbursed_at": disbursed_date,
                       "disbursement_reference": reference,
                       "funding_resident_id": funding_resident_id,
                       "funding_payment_id": funding_payment_id,
                       "receipts_issued": len(charges),
                       "landlord_receipt_sha256": attachment["sha256"] if attachment else None})
        summary = _summarize(conn, _cycle_row(conn, apartment_id, settlement_month), config)
    summary["warnings"] = warnings
    return summary


# ----------------------------------------------------------------- receipts --

def get_resident_rent_receipt(db_path, *, apartment_id: str, settlement_month: str,
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
    with connect(db_path, read_only=True) as conn:
        apartment = get_apartment(conn, apartment_id)
        resident = get_resident(conn, resident_id, apartment_id)
        cycle = _cycle_row(conn, apartment_id, settlement_month)
        if cycle is None:
            raise NotFoundError(
                f"Rent for {month_label(settlement_month)} has not been generated.",
                code="rent_cycle_not_found", details={"settlement_month": settlement_month})
        if cycle["disbursement_status"] != "disbursed":
            raise ConflictError(
                "Receipts are issued once the landlord payout has been recorded.",
                code="rent_not_disbursed", details={"cycle_id": cycle["cycle_id"]})
        receipts = db.fetch_all(
            conn, "SELECT * FROM rent_receipt WHERE cycle_id=? ORDER BY resident_id",
            (cycle["cycle_id"],))
        receipt = next((r for r in receipts if r["resident_id"] == resident_id), None)
        if receipt is None:
            raise NotFoundError(
                f"{resident['display_name']} did not take part in this month's rent.",
                code="rent_receipt_not_found",
                details={"resident_id": resident_id, "settlement_month": settlement_month})
        charge = next((c for c in _charges(conn, cycle["expense_id"])
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
        "premises": apartment["name"],
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


def attach_landlord_receipt(db_path, *, apartment_id: str, settlement_month: str,
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
    with transaction(db_path) as conn:
        get_apartment(conn, apartment_id)
        cycle = _cycle_row(conn, apartment_id, settlement_month)
        if cycle is None:
            raise NotFoundError(
                f"Rent for {month_label(settlement_month)} has not been generated.",
                code="rent_cycle_not_found", details={"settlement_month": settlement_month})
        if cycle["disbursement_status"] != "disbursed":
            raise ConflictError(
                "The landlord has not been paid for this month yet. Record the payout first; "
                "the receipt can be attached then or afterwards.",
                code="rent_not_disbursed", details={"cycle_id": cycle["cycle_id"]})
        config = _config_row(conn, apartment_id)
        if config is None or not config["requires_landlord_receipt"]:
            raise ValidationError(
                "This lease is not set up to receive receipts from the landlord. Turn that "
                "on in the lease settings to attach one.", code="landlord_receipt_not_expected",
                details={"field": "landlord_receipt"})
        previous = db.fetch_one(
            conn, "SELECT filename, sha256, uploaded_at FROM rent_landlord_receipt "
                  "WHERE cycle_id=?", (cycle["cycle_id"],))
        _store_landlord_receipt(conn, cycle["cycle_id"], attachment, timestamp)
        audit.record(
            conn, entity_type="rent_cycle", entity_id=cycle["cycle_id"],
            action="replace_landlord_receipt" if previous else "attach_landlord_receipt",
            actor=actor, apartment_id=apartment_id, field_name="landlord_receipt",
            old_value=previous,
            new_value={"filename": attachment["filename"], "sha256": attachment["sha256"],
                       "size_bytes": attachment["size_bytes"]})
        summary = _summarize(conn, _cycle_row(conn, apartment_id, settlement_month), config)
    summary["replaced_landlord_receipt"] = previous is not None
    return summary


def get_landlord_receipt(db_path, *, apartment_id: str, settlement_month: str) -> dict[str, Any]:
    """The landlord's own receipt attached to a month's payout, with its bytes."""
    settlement_month = normalize_month(settlement_month, "settlement_month")
    with connect(db_path, read_only=True) as conn:
        get_apartment(conn, apartment_id)
        row = db.fetch_one(
            conn, """SELECT lr.* FROM rent_landlord_receipt lr
                       JOIN rent_cycle c ON c.cycle_id = lr.cycle_id
                      WHERE c.apartment_id=? AND c.settlement_month=?""",
            (apartment_id, settlement_month))
    if row is None:
        raise NotFoundError("No landlord receipt is attached to this month's rent.",
                            code="landlord_receipt_not_found",
                            details={"settlement_month": settlement_month})
    return row

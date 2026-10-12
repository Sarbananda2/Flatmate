"""Opening balances: money owed before the household started using the app.

A household rarely starts recording on a clean slate. Somebody already owes
for last month's groceries; somebody else covered a bill and is still owed for
it. An opening balance brings that history in as one signed amount per
resident, using the ledger's own sign convention:

* **positive** -- the household owes the resident (a credit they bring in);
* **negative** -- the resident owes the household (a debt they bring in).

How each side behaves afterwards:

* A **debt** is treated as the resident's oldest charge, due on its
  ``as_of_date``. Pool payments are applied to it before anything else, exactly
  as they are to an ordinary charge, so "pay off the old balance first" happens
  without anyone having to remember it.
* A **credit** counts toward the resident's net position like a provider
  advance does, and is settled through the same suggested transfers.

The counterpart of every opening balance is the household pool, so the ledger
still sums to zero. If the per-resident amounts entered already net to zero
(Alice owes Bob ₹500: enter -500 for Alice and +500 for Bob) the pool is left
untouched; if they do not, the pool position shows the difference, which is
usually cash already sitting in a shared kitty or a missing entry.

Editing rules. An opening balance describes the past, so it can be changed only
while every statement is still a draft. Once any statement has been finalized,
its snapshot was computed from the opening balances as they stood, and a later
change must be a ledger adjustment instead, so that the change is visible as its
own dated event.
"""
from __future__ import annotations

from typing import Any

from .. import db
from ..db import connect, new_id, now_timestamp, transaction
from ..errors import ConflictError, NotFoundError, ValidationError
from ..periods import parse_iso_date
from . import audit
from .common import get_apartment, get_resident, optional_text

#: Prefix that marks an opening debt in the oldest-first charge list, so a
#: payment application can tell it apart from an ordinary allocation.
OPENING_CHARGE_PREFIX = "opening:"


def opening_charge_id(opening_balance_id: str) -> str:
    return f"{OPENING_CHARGE_PREFIX}{opening_balance_id}"


def split_charge_id(charge_id: str) -> tuple[str | None, str | None]:
    """Return ``(allocation_id, opening_balance_id)`` for a charge ID."""
    if charge_id.startswith(OPENING_CHARGE_PREFIX):
        return None, charge_id[len(OPENING_CHARGE_PREFIX):]
    return charge_id, None


def _applied_paise(conn, opening_balance_id: str) -> int:
    return int(db.scalar(
        conn, "SELECT COALESCE(SUM(amount_paise), 0) FROM payment_application "
              "WHERE opening_balance_id=?", (opening_balance_id,), default=0))


def _decorate(conn, row: dict[str, Any]) -> dict[str, Any]:
    applied = _applied_paise(conn, row["opening_balance_id"])
    out = dict(row)
    out["direction"] = "owed_to_resident" if row["amount_paise"] > 0 else "owed_by_resident"
    out["settled_paise"] = applied
    # Only a debt is settled by payments; a credit is settled through transfers.
    out["outstanding_paise"] = (-row["amount_paise"] - applied) if row["amount_paise"] < 0 else 0
    return out


def _assert_editable(conn, apartment_id: str) -> None:
    locked = db.fetch_all(
        conn, "SELECT statement_id, settlement_month, status FROM monthly_statement "
              "WHERE apartment_id=? AND status <> 'draft' ORDER BY settlement_month",
        (apartment_id,))
    if locked:
        raise ConflictError(
            "Opening balances can only be changed while every statement is still a draft. "
            f"The {locked[0]['settlement_month']} statement has already been finalized, so "
            "record the change as a ledger adjustment instead.",
            code="opening_balance_locked",
            details={"finalized_statements": locked})


def set_opening_balance(
    db_path, *, apartment_id: str, resident_id: str, amount_paise: int, as_of_date: str,
    note: str | None = None, actor: str = "operator",
) -> dict[str, Any]:
    """Create or replace one resident's opening balance.

    Replacing keeps any payments already applied to the old debt when they
    still fit the new one. If they no longer fit -- the debt shrank below what
    has been paid, or became a credit -- those applications are released and
    the money shows up again as the payer's unapplied credit, ready to be
    re-applied explicitly. Nothing is silently lost.
    """
    if isinstance(amount_paise, bool) or not isinstance(amount_paise, int) or amount_paise == 0:
        raise ValidationError(
            "An opening balance must be a non-zero whole number of paise. "
            "To remove one, clear it instead.", details={"field": "amount_paise"})
    parse_iso_date(as_of_date, "as_of_date")
    note = optional_text(note, "note")
    timestamp = now_timestamp()
    with transaction(db_path) as conn:
        get_apartment(conn, apartment_id)
        get_resident(conn, resident_id, apartment_id)
        _assert_editable(conn, apartment_id)
        before = db.fetch_one(conn, "SELECT * FROM opening_balance WHERE resident_id=?",
                              (resident_id,))
        released: list[dict[str, Any]] = []
        if before is None:
            opening_balance_id = new_id("opening")
            conn.execute(
                """INSERT INTO opening_balance
                   (opening_balance_id, apartment_id, resident_id, amount_paise, as_of_date,
                    note, created_at, created_by, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (opening_balance_id, apartment_id, resident_id, amount_paise, as_of_date,
                 note, timestamp, actor, timestamp))
            action = "create"
        else:
            opening_balance_id = before["opening_balance_id"]
            applied = _applied_paise(conn, opening_balance_id)
            if applied and (amount_paise > 0 or applied > -amount_paise):
                released = db.fetch_all(
                    conn, "SELECT payment_id, amount_paise FROM payment_application "
                          "WHERE opening_balance_id=?", (opening_balance_id,))
                conn.execute("DELETE FROM payment_application WHERE opening_balance_id=?",
                             (opening_balance_id,))
            conn.execute(
                """UPDATE opening_balance SET amount_paise=?, as_of_date=?, note=?,
                          updated_at=? WHERE opening_balance_id=?""",
                (amount_paise, as_of_date, note, timestamp, opening_balance_id))
            action = "update"
        audit.record(
            conn, entity_type="opening_balance", entity_id=opening_balance_id, action=action,
            actor=actor, apartment_id=apartment_id,
            old_value=None if before is None else {
                "amount_paise": before["amount_paise"], "as_of_date": before["as_of_date"],
                "note": before["note"]},
            new_value={"resident_id": resident_id, "amount_paise": amount_paise,
                       "as_of_date": as_of_date, "note": note,
                       "released_applications": released})
        row = db.fetch_one(
            conn, """SELECT ob.*, r.display_name FROM opening_balance ob
                       JOIN resident r ON r.resident_id = ob.resident_id
                      WHERE ob.opening_balance_id=?""", (opening_balance_id,))
        result = _decorate(conn, row)
    result["released_applications"] = released
    result["released_paise"] = sum(r["amount_paise"] for r in released)
    return result


def clear_opening_balance(db_path, *, apartment_id: str, resident_id: str,
                          actor: str = "operator") -> dict[str, Any]:
    """Remove a resident's opening balance.

    Payments that had been applied to it are released back to unapplied credit.
    """
    with transaction(db_path) as conn:
        get_apartment(conn, apartment_id)
        get_resident(conn, resident_id, apartment_id)
        _assert_editable(conn, apartment_id)
        before = db.fetch_one(conn, "SELECT * FROM opening_balance WHERE resident_id=?",
                              (resident_id,))
        if before is None:
            raise NotFoundError("This resident has no opening balance.",
                                details={"resident_id": resident_id})
        released = db.fetch_all(
            conn, "SELECT payment_id, amount_paise FROM payment_application "
                  "WHERE opening_balance_id=?", (before["opening_balance_id"],))
        conn.execute("DELETE FROM payment_application WHERE opening_balance_id=?",
                     (before["opening_balance_id"],))
        conn.execute("DELETE FROM opening_balance WHERE opening_balance_id=?",
                     (before["opening_balance_id"],))
        audit.record(
            conn, entity_type="opening_balance", entity_id=before["opening_balance_id"],
            action="delete", actor=actor, apartment_id=apartment_id,
            old_value={"resident_id": resident_id, "amount_paise": before["amount_paise"],
                       "as_of_date": before["as_of_date"], "note": before["note"]},
            new_value={"released_applications": released})
    return {"resident_id": resident_id, "opening_balance_id": before["opening_balance_id"],
            "released_applications": released,
            "released_paise": sum(r["amount_paise"] for r in released)}


def list_opening_balances(db_path, apartment_id: str) -> dict[str, Any]:
    """Every resident's opening balance, plus whether they can still be edited."""
    with connect(db_path, read_only=True) as conn:
        get_apartment(conn, apartment_id)
        rows = db.fetch_all(
            conn, """SELECT ob.*, r.display_name FROM opening_balance ob
                       JOIN resident r ON r.resident_id = ob.resident_id
                      WHERE ob.apartment_id = ? ORDER BY r.display_name""", (apartment_id,))
        items = [_decorate(conn, row) for row in rows]
        locked = db.scalar(
            conn, "SELECT settlement_month FROM monthly_statement "
                  "WHERE apartment_id=? AND status <> 'draft' ORDER BY settlement_month LIMIT 1",
            (apartment_id,))
    return {
        "items": items,
        "total": len(items),
        "net_paise": sum(item["amount_paise"] for item in items),
        "owed_by_residents_paise": sum(-i["amount_paise"] for i in items if i["amount_paise"] < 0),
        "owed_to_residents_paise": sum(i["amount_paise"] for i in items if i["amount_paise"] > 0),
        "is_editable": locked is None,
        "locked_by_month": locked,
    }


def read_opening_balance(db_path, *, apartment_id: str, resident_id: str) -> dict[str, Any] | None:
    with connect(db_path, read_only=True) as conn:
        get_resident(conn, resident_id, apartment_id)
        row = db.fetch_one(
            conn, """SELECT ob.*, r.display_name FROM opening_balance ob
                       JOIN resident r ON r.resident_id = ob.resident_id
                      WHERE ob.resident_id=?""", (resident_id,))
        return _decorate(conn, row) if row else None


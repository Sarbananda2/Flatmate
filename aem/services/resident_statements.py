"""One resident's statement for one settlement month.

The household statement answers "what did the flat spend"; this answers the
question each flatmate actually asks: "what is my share, line by line, and what
do I pay". It is a reading of the reconciliation's own ledger entries, never a
second calculation, so for every resident::

    net due = brought forward + month charges - payments and credits

holds to the paisa and one month's net due is the next month's brought-forward
figure. Amounts here are stated the way a bill states them: a positive net due
is owed by the resident to the pool, a negative one is owed to the resident.

Building a statement never writes anything, and a UPI link in it is only a
convenience: following it does not record a payment.
"""
from __future__ import annotations

from typing import Any

from ..errors import ReconciliationError
from ..money import format_inr
from ..periods import normalize_month
from ..persistence import Database, UnitOfWork
from .common import get_apartment, get_resident, list_residents
from .reconciliation import (
    ENTRY_ADJUSTMENT,
    ENTRY_EXPENSE_FUNDED,
    ENTRY_EXPENSE_SHARE,
    ENTRY_OPENING_BALANCE,
    build_ledger_entries,
    carry_forward,
)
from .sharing import _month_label, upi_uri

STATUS_NOT_CREATED = "not_created"
_RULE = "──────────────────────────────"
_CATEGORY_BY_KIND = {"electricity": "Electricity", "rent": "Rent"}


def _charge_items(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Each resident's share of each expense, with its component working."""
    by_resident: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        items = by_resident.setdefault(row["resident_id"], {})
        item = items.get(row["expense_id"])
        if item is None:
            item = items[row["expense_id"]] = {
                "expense_id": row["expense_id"],
                "category": (row["definition_name"]
                             or _CATEGORY_BY_KIND.get(row["expense_kind"], "Other")),
                "description": row["description"],
                "expense_date": row["expense_date"],
                "service_period": row["service_period"] or row["settlement_period"],
                "allocation_method": (row["allocation_method"]
                                      or ("measured" if row["expense_kind"] == "electricity"
                                          else "equal")),
                "total_expense_paise": row["expense_amount_paise"],
                "resident_share_paise": 0,
                "notes": row["notes"],
                "components": [],
            }
        item["resident_share_paise"] += row["amount_paise"]
        if row["component_name"]:
            item["components"].append({
                "name": row["component_name"],
                "allocation_method": row["component_method"],
                "total_paise": row["component_amount_paise"],
                "resident_share_paise": row["amount_paise"]})
    return {rid: list(items.values()) for rid, items in by_resident.items()}


def _credit_items(entries: list[dict[str, Any]], month: str,
                  payments: dict[str, dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Everything other than a charge that moved a resident's balance in the month.

    A positive amount reduces what the resident owes. A refund paid back to
    them, or an adjustment against them, is listed with a negative amount.
    """
    by_resident: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        period = entry["settlement_period"] or (entry["date"] or "")[:7]
        if period != month or entry["kind"] in (ENTRY_EXPENSE_SHARE, ENTRY_OPENING_BALANCE):
            continue
        payment = payments.get(entry["source_id"]) if entry["source_type"] == "payment" else None
        if payment is not None:
            kind, mode, reference = (payment["payment_kind"], payment["payment_mode"],
                                     payment["reference"])
        elif entry["kind"] == ENTRY_EXPENSE_FUNDED:
            kind, mode, reference = "expense_funded", None, None
        elif entry["kind"] == ENTRY_ADJUSTMENT:
            kind, mode, reference = "ledger_adjustment", None, None
        else:
            kind, mode, reference = entry["kind"], None, None
        by_resident.setdefault(entry["account_id"], []).append({
            "payment_id": entry["source_id"],
            "payment_date": entry["date"],
            "payment_kind": kind,
            "payment_mode": mode or "unspecified",
            "amount_paise": entry["amount_paise"],
            "reference": reference,
            "description": entry["description"],
        })
    for items in by_resident.values():
        items.sort(key=lambda item: (item["payment_date"] or "", item["payment_id"]))
    return by_resident


def _direction(net_due_paise: int) -> str:
    return ("owed_to_pool" if net_due_paise > 0
            else "owed_to_resident" if net_due_paise < 0 else "settled")


def _whatsapp_text(statement: dict[str, Any], apartment_name: str) -> str:
    who = statement["display_name"]
    if statement["room_label"]:
        who += f" ({statement['room_label']})"
    lines = [
        f"🏠 {apartment_name} — {_month_label(statement['settlement_month'])} Statement",
        f"👤 Resident: {who}",
        _RULE,
        "📋 Itemized Breakdown:",
    ]
    if not statement["items"]:
        lines.append("• No charges this month")
    for item in statement["items"]:
        label = item["category"]
        if label == "Other":
            label = item["description"]
        elif item["description"].strip().lower() != label.strip().lower():
            label += f" ({item['description']})"
        lines.append(f"• {label}: {format_inr(item['resident_share_paise'])}")
    lines += [_RULE, f"Total Charges: {format_inr(statement['total_charges_paise'])}"]
    brought = statement["brought_forward_paise"]
    if brought > 0:
        lines.append(f"Brought Forward (due): {format_inr(brought)}")
    elif brought < 0:
        lines.append(f"Brought Forward (credit): -{format_inr(-brought)}")
    credited = statement["total_payments_paise"]
    lines.append("Payments Credited: "
                 + (f"-{format_inr(credited)}" if credited > 0
                    else f"+{format_inr(-credited)}" if credited < 0 else "₹0.00"))
    lines.append(_RULE)

    net = statement["net_due_paise"]
    if net > 0:
        lines.append(f"👉 Net Due: {format_inr(net)} (Owed to Pool)")
    elif net < 0:
        lines.append(f"👉 Net Due: {format_inr(-net)} (Owed to You by the Pool)")
    else:
        lines.append("👉 Net Due: ₹0.00 (Settled)")
    payable = statement["payable_now_paise"]
    if net > 0 and payable != net:
        lines.append("✅ Paid in full since this statement" if payable == 0
                     else f"⏳ Still to pay after later payments: {format_inr(payable)}")
    if payable > 0 and statement["recipient_upi_id"]:
        lines += ["", f"💳 Pay via UPI: {statement['recipient_upi_id']}"
                      + (f" ({statement['recipient_name']})"
                         if statement["recipient_name"] else ""),
                  "(Or scan QR code on the apartment dashboard)"]
    if statement["statement_status"] in ("draft", STATUS_NOT_CREATED):
        lines += ["", "📝 Draft — figures can still change"]
    return "\n".join(lines)


def _build(uow: UnitOfWork, apartment_id: str, settlement_month: str,
           only: set[str] | None = None) -> list[dict[str, Any]]:
    apartment = get_apartment(uow, apartment_id)
    residents = {row["resident_id"]: row for row in list_residents(uow, apartment_id)}
    statement = uow.statements.find_for_month(apartment_id, settlement_month)
    status = statement["status"] if statement else STATUS_NOT_CREATED

    entries = build_ledger_entries(uow, apartment_id)
    movement = {row["resident_id"]: row
                for row in carry_forward(uow, apartment_id, settlement_month)["residents"]}
    today = {rid: 0 for rid in residents}
    for entry in entries:
        if entry["account_id"] in today:
            today[entry["account_id"]] += entry["amount_paise"]
    charges = _charge_items(
        uow.statements.resident_charges_for_month(apartment_id, settlement_month))
    payments = {row["payment_id"]: row
                for row in uow.reconciliation.active_payments_for_ledger(apartment_id, None)}
    credits = _credit_items(entries, settlement_month, payments)

    lease = uow.rent.active_lease(apartment_id)
    signatory = residents.get(lease["deposit_primary_signatory_resident_id"]) if lease else None
    # The room they live in, or failing that the deposit slot they hold.
    rooms = {slot["assigned_resident_id"]: slot["slot_label"]
             for slot in uow.deposits.slots_for_apartment(apartment_id)
             if slot["assigned_resident_id"]}
    rooms.update({room["assigned_resident_id"]: room["room_number"]
                  for room in uow.rooms.list_for_apartment(apartment_id)
                  if room["assigned_resident_id"]})

    statements: list[dict[str, Any]] = []
    for rid, resident in residents.items():
        if only is not None and rid not in only:
            continue
        row = movement[rid]
        items = charges.get(rid, [])
        paid = credits.get(rid, [])
        total_charges = sum(item["resident_share_paise"] for item in items)
        total_paid = sum(item["amount_paise"] for item in paid)
        brought_forward = -row["brought_forward_paise"]
        net_due = brought_forward + total_charges - total_paid
        # The itemized lines and the ledger are two readings of the same
        # records. If they ever disagree, refuse to bill anyone from them.
        if net_due != -row["carried_forward_paise"]:
            raise ReconciliationError(
                "A resident's itemized statement does not match their ledger balance.",
                code="resident_statement_mismatch",
                details={"resident_id": rid, "settlement_month": settlement_month,
                         "itemized_net_due_paise": net_due,
                         "ledger_net_due_paise": -row["carried_forward_paise"]})

        # Never ask for more than is owed today: a payment made after the month
        # closed has already settled part of this statement.
        payable = max(0, min(net_due, -today[rid]))
        recipient = signatory if signatory and signatory["resident_id"] != rid else None
        recipient_upi = recipient["upi_id"] if recipient else None
        result = {
            "resident_id": rid,
            "display_name": resident["display_name"],
            "is_active": bool(resident["is_active"]),
            "room_label": rooms.get(rid),
            "apartment_name": apartment["name"],
            "settlement_month": settlement_month,
            "statement_id": statement["statement_id"] if statement else None,
            "statement_status": status,
            "items": items,
            "total_charges_paise": total_charges,
            "payments": paid,
            "total_payments_paise": total_paid,
            "brought_forward_paise": brought_forward,
            "net_due_paise": net_due,
            "direction": _direction(net_due),
            "payable_now_paise": payable,
            "recipient_upi_id": recipient_upi,
            "recipient_name": recipient["display_name"] if recipient else None,
            "upi_uri": (upi_uri(
                upi_id=recipient_upi, payee_name=recipient["display_name"],
                amount_paise=payable,
                note=f"{_month_label(settlement_month)} {apartment['name']}"[:60])
                if recipient_upi and payable > 0 else None),
        }
        result["whatsapp_text"] = _whatsapp_text(result, apartment["name"])
        statements.append(result)
    statements.sort(key=lambda item: (item["display_name"].lower(), item["resident_id"]))
    return statements


def get_resident_monthly_statement(db: Database, apartment_id: str, resident_id: str,
                                   settlement_month: str) -> dict[str, Any]:
    """One resident's itemized charges, credits and net due for one month."""
    month = normalize_month(settlement_month, "settlement_month")
    with db.read() as uow:
        get_apartment(uow, apartment_id)
        get_resident(uow, resident_id, apartment_id)
        return _build(uow, apartment_id, month, only={resident_id})[0]


def list_flatmate_statements(db: Database, apartment_id: str,
                             settlement_month: str) -> dict[str, Any]:
    """Every flatmate's statement for one month, for review and sharing.

    A former resident is listed only while the month still concerns them: a
    charge, a credit, or a balance that is not yet square.
    """
    month = normalize_month(settlement_month, "settlement_month")
    with db.read() as uow:
        items = [item for item in _build(uow, apartment_id, month)
                 if item["is_active"] or item["items"] or item["payments"]
                 or item["net_due_paise"] != 0]
    return {
        "settlement_month": month,
        "statement_status": items[0]["statement_status"] if items else STATUS_NOT_CREATED,
        "items": items,
        "totals": {
            "total_charges_paise": sum(item["total_charges_paise"] for item in items),
            "total_payments_paise": sum(item["total_payments_paise"] for item in items),
            "brought_forward_paise": sum(item["brought_forward_paise"] for item in items),
            "net_due_paise": sum(item["net_due_paise"] for item in items),
        },
    }

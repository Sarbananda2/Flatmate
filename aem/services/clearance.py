"""A resident's guided exit clearance.

Moving out touches four things that otherwise have to be squared by hand: the
move-out date, the resident's deposit slot, what they owe (or are owed) on the
operating ledger, and the cash that actually changes hands. This module settles
all four in one transaction and leaves one record of it behind.

The cash figure is::

    net settlement = deposit put in - deposit deductions + operating balance

where the operating balance is the resident's **net position** on the ledger:
negative while they owe the household, positive while it owes them. For a
resident who has only been charged and has paid into the pool, that is exactly
"unapplied credits less outstanding charges". Using the net position also gets
it right for someone who paid a bill on the household's behalf, whose charges
look outstanding even though the household owes them money.

**This is the one place deposit money and operating money meet**, and they meet
only as cash. The deposit slot is settled for its full value -- refund plus
deductions equals what was put in. If the operator chooses to net the operating
balance against it, a single ledger payment is recorded for the resident,
referenced ``Move-out deposit offset``, that brings their net position to
exactly zero: a payment into the pool when they owed money, a refund out of it
when they were owed. The deposit slot itself still never appears in the ledger.

That ledger payment says the pool received (or paid) the money. In practice the
cash sits with whoever held the deposit, so it is for them to pass it on.
"""
from __future__ import annotations

import calendar
from typing import Any, Mapping

from ..errors import ConflictError, ReconciliationError, ValidationError
from ..ids import new_id, now_timestamp
from ..money import ensure_paise, format_inr
from ..periods import parse_iso_date
from ..persistence import Database, UnitOfWork
from . import audit, deposits, reconciliation
from .common import get_apartment, get_resident, optional_text
from .payments import _record_payment
from .residents import update_resident_in
from .sharing import upi_uri

SETTLEMENT_PATHS = ("signatory_refund", "direct_replacement_transfer", "standalone_clearance")
OFFSET_REFERENCE = "Move-out deposit offset"
ENTITY = "resident_clearance"
_FIELDS = ("move_out_date", "deductions_paise", "deduction_reason", "net_ledger_offset",
           "settlement_path", "incoming_resident_id", "settlement_date",
           "settlement_reference", "notes")


# ------------------------------------------------------------------- helpers --

def _date_not_in_future(value: Any, label: str) -> str:
    parsed = parse_iso_date(value, label)
    if parsed.isoformat() > now_timestamp()[:10]:
        raise ValidationError(f"{label} cannot be in the future.", details={"field": label})
    return parsed.isoformat()


def _readable_date(iso_date: str) -> str:
    """``2026-10-12`` -> ``12 Oct 2026``."""
    year, month, day = iso_date.split("-")
    return f"{int(day)} {calendar.month_abbr[int(month)]} {year}"


def _net_position(uow: UnitOfWork, apartment_id: str, resident_id: str) -> int:
    """The resident's operating balance: negative while they owe the household."""
    return sum(entry["amount_paise"]
               for entry in reconciliation.build_ledger_entries(uow, apartment_id)
               if entry["account_id"] == resident_id)


def _slot_of(uow: UnitOfWork, apartment_id: str, resident_id: str) -> dict[str, Any] | None:
    return next((slot for slot in uow.deposits.slots_for_apartment(apartment_id)
                 if slot["assigned_resident_id"] == resident_id), None)


def _direction(net_paise: int) -> str:
    if net_paise > 0:
        return "refund_to_resident"
    return "payment_from_resident" if net_paise < 0 else "zero_balanced"


def _blocking_reason(resident: Mapping[str, Any], lease: Mapping[str, Any] | None,
                     slot: Mapping[str, Any] | None) -> str | None:
    """Why this resident cannot be cleared here, if they cannot."""
    if not resident["is_active"]:
        return "This resident has already moved out."
    if slot and lease and resident["resident_id"] == \
            lease["deposit_primary_signatory_resident_id"]:
        return ("The primary signatory's own deposit slot is settled with the landlord, "
                "so they cannot be cleared through this workflow.")
    return None


# ------------------------------------------------------------------- preview --

def get_clearance_preview(db: Database, apartment_id: str,
                          resident_id: str) -> dict[str, Any]:
    """What a clearance would settle for this resident, as things stand now."""
    settlement = reconciliation.reconcile(db, apartment_id=apartment_id)
    account = next((row for row in settlement["accounts"]
                    if row["account_id"] == resident_id), None)
    with db.read() as uow:
        get_apartment(uow, apartment_id)
        resident = get_resident(uow, resident_id, apartment_id)
        lease = uow.rent.active_lease(apartment_id)
        slot = _slot_of(uow, apartment_id, resident_id)
        signatory_id = lease["deposit_primary_signatory_resident_id"] if lease else None
        occupants = {row["assigned_resident_id"]
                     for row in uow.deposits.slots_for_apartment(apartment_id)}
        candidates = [
            {"resident_id": row["resident_id"], "display_name": row["display_name"]}
            for row in uow.residents.list_for_apartment(apartment_id, active_only=True)
            if row["resident_id"] != resident_id and row["resident_id"] not in occupants]
        signatory_name = (get_resident(uow, signatory_id)["display_name"]
                          if signatory_id else None)
        return {
            "resident_id": resident_id,
            "display_name": resident["display_name"],
            "is_active": bool(resident["is_active"]),
            "slot": ({**slot, "outstanding_paise":
                      slot["target_deposit_paise"] - slot["funded_amount_paise"]}
                     if slot else None),
            "funding_mode": lease["deposit_funding_mode"] if lease else None,
            "primary_signatory_id": signatory_id,
            "primary_signatory_name": signatory_name,
            "ledger_outstanding_charges_paise":
                (account or {}).get("outstanding_charges_paise", 0),
            "ledger_unapplied_credits_paise":
                (account or {}).get("unapplied_credit_paise", 0),
            # Negative while they owe the household; this is what an offset clears.
            "operating_net_balance_paise": (account or {}).get("net_position_paise", 0),
            "default_move_out_date": now_timestamp()[:10],
            # Residents who could take the slot over: here now, with no slot.
            "incoming_candidates": sorted(candidates, key=lambda row: row["display_name"]),
            "allowed_settlement_paths": (["signatory_refund", "direct_replacement_transfer"]
                                         if slot else ["standalone_clearance"]),
            "blocking_reason": _blocking_reason(resident, lease, slot),
        }


# -------------------------------------------------------------------- record --

def _summary_text(record: Mapping[str, Any], *, apartment_name: str, resident_name: str,
                  slot_label: str | None) -> str:
    """The clearance as a short message, ready to paste into a chat."""
    net = record["net_settlement_paise"]
    lines = [
        f"🏠 {apartment_name} — Move-Out Clearance Statement",
        f"Resident: {resident_name}" + (f" ({slot_label})" if slot_label else ""),
        f"Effective Date: {_readable_date(record['move_out_date'])}",
        "",
    ]
    if record["slot_id"]:
        lines.append(f"💰 Deposit Refund: {format_inr(record['deposit_funded_paise'])}")
        if record["deposit_deductions_paise"]:
            reason = f" ({record['deduction_reason']})" if record["deduction_reason"] else ""
            lines.append(f"🛠️ Deductions{reason}: "
                         f"-{format_inr(record['deposit_deductions_paise'])}")
    if record["net_ledger_offset_applied"]:
        if record["ledger_outstanding_paise"]:
            lines.append("📊 Operating Ledger Dues: "
                         f"-{format_inr(record['ledger_outstanding_paise'])}")
        elif record["ledger_credit_paise"]:
            lines.append("📊 Operating Ledger Credit: "
                         f"+{format_inr(record['ledger_credit_paise'])}")
    elif record["ledger_outstanding_paise"] or record["ledger_credit_paise"]:
        lines.append("📊 Operating ledger balance not included; it is settled separately.")
    who = ("Nothing to pay" if net == 0
           else f"Payable to {resident_name}" if net > 0 else f"Payable by {resident_name}")
    lines += [
        "────────────────────────────────",
        f"💵 Net Settlement: {format_inr(abs(net))} ({who})",
        "Status: Settled" + (f" (UTR: {record['settlement_reference']})"
                             if record["settlement_reference"] else ""),
    ]
    return "\n".join(lines)


def _decode(uow: UnitOfWork, record: Mapping[str, Any]) -> dict[str, Any]:
    """A clearance record with the names, the message, and the payment link."""
    apartment = get_apartment(uow, record["apartment_id"])
    resident = get_resident(uow, record["resident_id"])
    counterparty = (get_resident(uow, record["counterparty_resident_id"])
                    if record["counterparty_resident_id"] else None)
    slot = uow.deposits.slot(record["slot_id"]) if record["slot_id"] else None
    net = record["net_settlement_paise"]

    # Who is paid: the leaver when money is due to them, otherwise whoever
    # settled with them. A link needs that person's UPI ID to be on file.
    payee = resident if net > 0 else counterparty if net < 0 else None
    link = None
    if payee and payee.get("upi_id"):
        link = upi_uri(upi_id=payee["upi_id"], payee_name=payee["display_name"],
                       amount_paise=abs(net),
                       note=f"Move-out clearance {resident['display_name']}"[:60])
    return {
        **record,
        "net_ledger_offset_applied": bool(record["net_ledger_offset_applied"]),
        "apartment_name": apartment["name"],
        "resident_display_name": resident["display_name"],
        "counterparty_display_name": counterparty["display_name"] if counterparty else None,
        "slot_number": slot["slot_number"] if slot else None,
        "slot_label": slot["slot_label"] if slot else None,
        "deposit_refund_paise": (record["deposit_funded_paise"]
                                 - record["deposit_deductions_paise"]),
        # Signed: what the operating ledger added to, or took from, the cash.
        "ledger_offset_paise": ((record["ledger_credit_paise"]
                                 - record["ledger_outstanding_paise"])
                                if record["net_ledger_offset_applied"] else 0),
        "payee_resident_id": payee["resident_id"] if payee else None,
        "payee_display_name": payee["display_name"] if payee else None,
        "payee_upi_id": payee.get("upi_id") if payee else None,
        "upi_uri": link,
        "summary_text": _summary_text(
            record, apartment_name=apartment["name"],
            resident_name=resident["display_name"],
            slot_label=slot["slot_label"] if slot else None),
    }


def get_clearance(db: Database, apartment_id: str, resident_id: str) -> dict[str, Any] | None:
    """The resident's latest clearance, or ``None`` if they have never been cleared."""
    with db.read() as uow:
        get_apartment(uow, apartment_id)
        get_resident(uow, resident_id, apartment_id)
        record = uow.clearance.latest_for_resident(resident_id)
        return _decode(uow, record) if record else None


# ------------------------------------------------------------------- execute --

def execute_clearance(db: Database, apartment_id: str, resident_id: str,
                      request: Mapping[str, Any], *,
                      actor: str = "operator") -> dict[str, Any]:
    """Settle a resident's exit, all at once or not at all.

    In one transaction: their deposit slot is vacated or handed to a
    replacement; if ``net_ledger_offset`` is set, their operating balance is
    cleared to zero with one ledger payment; they are marked as moved out on
    ``move_out_date``; and the clearance is recorded and audited.
    """
    unknown = sorted(set(request) - set(_FIELDS))
    if unknown:
        raise ValidationError(f"Unsupported clearance fields: {', '.join(unknown)}.",
                              details={"unsupported_fields": unknown})
    path = request.get("settlement_path")
    if path not in SETTLEMENT_PATHS:
        raise ValidationError(
            "Choose how the exit is settled: signatory_refund, "
            "direct_replacement_transfer, or standalone_clearance.",
            details={"field": "settlement_path", "allowed": list(SETTLEMENT_PATHS)})
    move_out = _date_not_in_future(request.get("move_out_date"), "move_out_date")
    settled_on = _date_not_in_future(request.get("settlement_date"), "settlement_date")
    deductions = ensure_paise(request.get("deductions_paise", 0), "deductions_paise")
    reason = optional_text(request.get("deduction_reason"), "deduction_reason",
                           max_length=2000)
    if deductions > 0 and not reason:
        raise ValidationError("Say what the deductions are for.",
                              details={"field": "deduction_reason"})
    offset = request.get("net_ledger_offset", True)
    if not isinstance(offset, bool):
        raise ValidationError("net_ledger_offset must be true or false.",
                              details={"field": "net_ledger_offset"})
    incoming_id = optional_text(request.get("incoming_resident_id"),
                                "incoming_resident_id", max_length=64)
    reference = optional_text(request.get("settlement_reference"),
                              "settlement_reference", max_length=200)
    notes = optional_text(request.get("notes"), "notes", max_length=2000)
    direct = path == "direct_replacement_transfer"
    if direct and not incoming_id:
        raise ValidationError("Choose the resident taking the slot over.",
                              details={"field": "incoming_resident_id"})
    if not direct and incoming_id:
        raise ValidationError(
            "An incoming resident is only named for a direct replacement transfer.",
            details={"field": "incoming_resident_id"})
    timestamp = now_timestamp()

    with db.transaction() as uow:
        get_apartment(uow, apartment_id)
        resident = get_resident(uow, resident_id, apartment_id)
        if not resident["is_active"]:
            raise ConflictError("This resident has already moved out.",
                                code="resident_already_moved_out")
        slot = _slot_of(uow, apartment_id, resident_id)
        if slot is None and path != "standalone_clearance":
            raise ValidationError(
                "This resident holds no deposit slot, so there is nothing to refund or "
                "transfer. Use standalone_clearance.", details={"field": "settlement_path"})
        if slot is not None and path == "standalone_clearance":
            raise ValidationError(
                "This resident holds a deposit slot. It has to be refunded or "
                "transferred as part of the clearance.",
                details={"field": "settlement_path"})
        if slot is None and deductions > 0:
            raise ValidationError("There is no deposit to deduct from.",
                                  details={"field": "deductions_paise"})

        # 1. The deposit slot, settled for its full value.
        funded = slot["funded_amount_paise"] if slot else 0
        counterparty_id = None
        if slot is not None:
            settled = deposits.settle_slot_in(
                uow, slot["slot_id"], apartment_id=apartment_id,
                settlement_path="direct_transfer" if direct else "signatory_refund",
                deductions_paise=deductions, refund_amount_paise=funded - deductions,
                settled_on=settled_on, deduction_reason=reason, reference=reference,
                incoming_resident_id=incoming_id, actor=actor)
            counterparty_id = settled["counterparty_resident_id"]

        # 2. The operating ledger: one payment that takes their balance to zero.
        balance = _net_position(uow, apartment_id, resident_id)
        payment_id = None
        if offset and balance != 0:
            payment_id = new_id("payment")
            _record_payment(
                uow, apartment_id=apartment_id, payment_id=payment_id,
                payer_resident_id=resident_id, amount_paise=abs(balance),
                payment_date=settled_on,
                payment_kind="to_pool" if balance < 0 else "refund",
                payee_or_provider=None, reference=OFFSET_REFERENCE,
                note=f"Exit clearance for {resident['display_name']}",
                timestamp=timestamp, expense_id=None, actor=actor)
            after = _net_position(uow, apartment_id, resident_id)
            if after != 0:
                raise ReconciliationError(
                    "The clearing payment did not bring the resident's balance to zero. "
                    "Nothing was saved.", code="clearance_not_zeroed",
                    details={"balance_before_paise": balance, "balance_after_paise": after})

        # 3. The resident has moved out.
        update_resident_in(uow, resident_id, apartment_id=apartment_id, actor=actor,
                           reason="exit clearance", is_active=False, active_until=move_out)

        # 4. The record.
        net = (funded - deductions) + (balance if offset else 0)
        record = {
            "clearance_id": new_id("clearance"), "apartment_id": apartment_id,
            "resident_id": resident_id, "move_out_date": move_out,
            "slot_id": slot["slot_id"] if slot else None,
            "deposit_funded_paise": funded, "deposit_deductions_paise": deductions,
            "deduction_reason": reason if deductions > 0 else None,
            "ledger_outstanding_paise": max(-balance, 0),
            "ledger_credit_paise": max(balance, 0),
            "net_ledger_offset_applied": 1 if offset else 0,
            "net_settlement_paise": net, "settlement_direction": _direction(net),
            "settlement_path": path, "settlement_date": settled_on,
            "settlement_reference": reference,
            "counterparty_resident_id": counterparty_id,
            "clearing_payment_id": payment_id, "notes": notes, "created_at": timestamp,
        }
        uow.clearance.insert(**record)
        audit.record(uow, entity_type=ENTITY, entity_id=record["clearance_id"],
                     action="execute", actor=actor, apartment_id=apartment_id,
                     reason=notes, new_value=record)
        return _decode(uow, uow.clearance.latest_for_resident(resident_id))

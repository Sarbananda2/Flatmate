"""Household security deposit slots and the sub-escrow between flatmates.

The landlord holds one deposit for the lease. It belongs to the household, so
it is divided into permanent **slots** -- one per space in the flat -- whose
targets add up to exactly the deposit on the lease. People come and go; a slot
is filled, funded, vacated and filled again, and is never deleted.

**A deposit never touches the operating ledger.** It is long-term capital held
in escrow, not a household expense: nothing here creates an expense, a charge,
a payment, or a ledger adjustment, and no figure here appears in a statement or
a resident's balance.

Two ways of funding the deposit, and the operator must say which:

``primary_signatory``
    One resident paid the landlord in full. Each other occupant owes that
    resident their slot's target. The signatory's own slot needs no payment --
    it is part of what they fronted.
``direct_co_tenants``
    Each flatmate pays the landlord, or a common escrow, directly.

When an occupant leaves, their slot is settled one of two ways. Under
``signatory_refund`` their money is handed back less deductions, and the slot
stands vacant until somebody is assigned to it. Under ``direct_transfer`` the
person replacing them pays them directly and takes the slot over. Either way,
what the leaver is handed plus what is kept back equals what they had put in.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..errors import ConflictError, NotFoundError, ValidationError
from ..ids import new_id, now_timestamp
from ..money import ensure_paise
from ..periods import parse_iso_date
from ..persistence import Database, UnitOfWork
from . import audit, rooms
from .common import get_apartment, get_resident, optional_text, require_text

FUNDING_MODES = ("primary_signatory", "direct_co_tenants")
PAYMENT_METHODS = ("upi", "bank_transfer", "cash", "direct_to_landlord")
SETTLEMENT_PATHS = ("signatory_refund", "direct_transfer")
ENTITY = "household_deposit_slot"
MAX_SLOTS = 50


# ------------------------------------------------------------------- helpers --

def _lease(uow: UnitOfWork, apartment_id: str) -> dict[str, Any]:
    lease = uow.rent.active_lease(apartment_id)
    if lease is None:
        raise ConflictError("Set up the lease before recording security deposits.",
                            code="rent_not_configured")
    return lease


def _configured_lease(uow: UnitOfWork, apartment_id: str) -> dict[str, Any]:
    lease = _lease(uow, apartment_id)
    if not lease["deposit_funding_mode"]:
        raise ConflictError(
            "Choose how the security deposit was funded before setting up its slots.",
            code="deposit_mode_not_set")
    return lease


def _slot(uow: UnitOfWork, slot_id: str, apartment_id: str) -> dict[str, Any]:
    slot = uow.deposits.slot(slot_id)
    if slot is None or slot["apartment_id"] != apartment_id:
        raise NotFoundError("Deposit slot not found.", details={"slot_id": slot_id})
    return slot


def _status_for(resident_id: str | None, funded_paise: int, target_paise: int) -> str:
    if resident_id is None:
        return "vacant"
    return "occupied_funded" if funded_paise >= target_paise else "occupied_pending"


def _date_not_in_future(value: Any, label: str) -> str:
    parsed = parse_iso_date(value, label)
    if parsed.isoformat() > now_timestamp()[:10]:
        raise ValidationError(f"{label} cannot be in the future.", details={"field": label})
    return parsed.isoformat()


def _split_equally(total_paise: int, count: int) -> list[int]:
    """An equal split to the paisa: spare paise go one each to the first slots."""
    if count <= 0 or total_paise <= 0:
        return []
    base, spare = divmod(total_paise, count)
    return [base + (1 if index < spare else 0) for index in range(count)]


def _transaction(uow: UnitOfWork, slot: Mapping[str, Any], *, resident_id: str,
                 tx_type: str, amount_paise: int, tx_date: str, timestamp: str,
                 **extra: Any) -> None:
    uow.deposits.insert_transaction(
        tx_id=new_id("deptx"), slot_id=slot["slot_id"], apartment_id=slot["apartment_id"],
        resident_id=resident_id, tx_type=tx_type, amount_paise=amount_paise,
        tx_date=tx_date, created_at=timestamp, **extra)


# ------------------------------------------------------------------- summary --

def _summary(uow: UnitOfWork, apartment_id: str) -> dict[str, Any]:
    lease = uow.rent.active_lease(apartment_id)
    slots = uow.deposits.slots_for_apartment(apartment_id) if lease else []
    transactions = uow.deposits.transactions_for_apartment(apartment_id) if lease else []
    mode = lease["deposit_funding_mode"] if lease else None
    signatory_id = lease["deposit_primary_signatory_resident_id"] if lease else None
    master = lease["security_deposit_paise"] if lease else None
    residents = {row["resident_id"]: row
                 for row in uow.residents.list_for_apartment(apartment_id)}
    rooms = {row["room_id"]: row for row in uow.rooms.list_for_apartment(apartment_id)}

    def name(resident_id: str | None) -> str | None:
        return (residents.get(resident_id) or {}).get("display_name")

    total_target = sum(slot["target_deposit_paise"] for slot in slots)
    total_funded = sum(slot["funded_amount_paise"] for slot in slots)
    # What the signatory has had back: the other occupants' funding still held,
    # plus anything kept back from people who have left.
    reimbursements = sum(slot["funded_amount_paise"] for slot in slots
                         if slot["assigned_resident_id"] != signatory_id)
    retained = sum(tx["amount_paise"] for tx in transactions
                   if tx["tx_type"] == "move_out_deduction")
    exposure = None
    if mode == "primary_signatory" and master is not None:
        exposure = max(master - reimbursements - retained, 0)

    occupants = {slot["assigned_resident_id"] for slot in slots}
    unassigned = sorted(
        (row for row in residents.values()
         if row["is_active"] and row["resident_id"] not in occupants),
        key=lambda row: (row["display_name"], row["resident_id"]))

    # Before any slot exists: one slot per current resident, the deposit split
    # equally between them. The operator may relabel, re-amount, or reassign.
    proposal: list[dict[str, Any]] = []
    if not slots and master is not None:
        current = sorted((row for row in residents.values() if row["is_active"]),
                         key=lambda row: row["resident_id"])
        for index, (resident, share) in enumerate(
                zip(current, _split_equally(master, len(current))), start=1):
            proposal.append({
                "slot_number": index, "slot_label": f"Room {index}",
                "target_deposit_paise": share,
                "assigned_resident_id": resident["resident_id"],
                "assigned_display_name": resident["display_name"]})

    history: dict[str, list[dict[str, Any]]] = {}
    for tx in transactions:
        history.setdefault(tx["slot_id"], []).append({
            **tx, "resident_display_name": name(tx["resident_id"]),
            "counterparty_display_name": name(tx["counterparty_resident_id"])})

    return {
        "is_lease_configured": lease is not None,
        "master_deposit_paise": master,
        "funding_mode": mode,
        "primary_signatory_id": signatory_id,
        "primary_signatory_name": name(signatory_id),
        "signatory_capital_exposure_paise": exposure,
        "reimbursements_received_paise": reimbursements if mode == "primary_signatory" else 0,
        "retained_deductions_paise": retained,
        "total_slots_count": len(slots),
        "vacant_slots_count": sum(1 for slot in slots if slot["assigned_resident_id"] is None),
        "total_target_paise": total_target,
        "total_funded_paise": total_funded,
        "unfunded_gap_paise": total_target - total_funded,
        # Invariant B2. False only if the lease's deposit was changed afterwards.
        "is_balanced": master is not None and total_target == master,
        "unassigned_residents": [
            {"resident_id": row["resident_id"], "display_name": row["display_name"]}
            for row in unassigned],
        "proposal": proposal,
        "slots": [
            {**slot,
             "assigned_display_name": name(slot["assigned_resident_id"]),
             "is_primary_signatory": (slot["assigned_resident_id"] is not None
                                      and slot["assigned_resident_id"] == signatory_id),
             "outstanding_paise": (slot["target_deposit_paise"] - slot["funded_amount_paise"]
                                   if slot["assigned_resident_id"] else 0),
             "paid_to_display_name": name(slot["paid_to_resident_id"]),
             # The room the slot backs, if it is linked to one.
             **_room_details(rooms.get(slot.get("room_id"))),
             "transactions": history.get(slot["slot_id"], [])}
            for slot in slots
        ],
    }


def _room_details(room: Mapping[str, Any] | None) -> dict[str, Any]:
    return {
        "room_number": room["room_number"] if room else None,
        "has_attached_washroom": bool(room["has_attached_washroom"]) if room else None,
        "has_balcony": bool(room["has_balcony"]) if room else None,
        "base_rent_paise": room["base_rent_paise"] if room else None,
    }


def deposit_summary(db: Database, apartment_id: str) -> dict[str, Any]:
    """Everything the deposit screens show: the funding mode, the totals, every
    slot with its occupant and history, and -- before any slot exists -- a
    proposed equal split."""
    with db.read() as uow:
        get_apartment(uow, apartment_id)
        return _summary(uow, apartment_id)


# ---------------------------------------------------------------- funding mode --

def configure_mode(db: Database, apartment_id: str, mode: Any,
                   primary_signatory_id: Any = None, *,
                   actor: str = "operator") -> dict[str, Any]:
    """Say how the lease's deposit was funded. There is no default: the mode
    must be given. It can be changed only while no slot has been set up."""
    if mode not in FUNDING_MODES:
        raise ValidationError(
            "Choose how the deposit was funded: primary_signatory or direct_co_tenants.",
            details={"field": "funding_mode", "allowed": list(FUNDING_MODES)})
    signatory_id = optional_text(primary_signatory_id, "primary_signatory_resident_id",
                                 max_length=64)
    if mode == "primary_signatory" and not signatory_id:
        raise ValidationError("Choose the resident who paid the deposit to the landlord.",
                              details={"field": "primary_signatory_resident_id"})
    if mode == "direct_co_tenants" and signatory_id:
        raise ValidationError(
            "There is no primary signatory when flatmates pay the landlord directly.",
            details={"field": "primary_signatory_resident_id"})

    with db.transaction() as uow:
        get_apartment(uow, apartment_id)
        lease = _lease(uow, apartment_id)
        if signatory_id:
            signatory = get_resident(uow, signatory_id, apartment_id)
            if not signatory["is_active"]:
                raise ValidationError("The primary signatory must be a current resident.",
                                      details={"field": "primary_signatory_resident_id"})
        before = {"funding_mode": lease["deposit_funding_mode"],
                  "primary_signatory_resident_id":
                      lease["deposit_primary_signatory_resident_id"]}
        after = {"funding_mode": mode, "primary_signatory_resident_id": signatory_id}
        if before != after and uow.deposits.slots_for_apartment(apartment_id):
            raise ConflictError(
                "The funding mode cannot be changed once deposit slots have been set up.",
                code="deposit_mode_locked")
        uow.deposits.set_funding_mode(lease["config_id"], mode=mode,
                                      primary_signatory_resident_id=signatory_id,
                                      updated_at=now_timestamp())
        audit.record(uow, entity_type=ENTITY, entity_id=lease["config_id"],
                     action="configure_mode", actor=actor, apartment_id=apartment_id,
                     old_value=before, new_value=after)
        return _summary(uow, apartment_id)


# ----------------------------------------------------------------------- slots --

def _clean_slot_definitions(definitions: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if not definitions:
        raise ValidationError("Give at least one deposit slot.", details={"field": "slots"})
    if len(definitions) > MAX_SLOTS:
        raise ValidationError(f"At most {MAX_SLOTS} slots.", details={"field": "slots"})
    cleaned: list[dict[str, Any]] = []
    numbers: set[int] = set()
    occupants: set[str] = set()
    for index, item in enumerate(definitions):
        number = item.get("slot_number")
        if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
            raise ValidationError("A slot number is a whole number above zero.",
                                  details={"field": f"slots[{index}].slot_number"})
        if number in numbers:
            raise ValidationError(f"Slot {number} appears twice.",
                                  details={"field": f"slots[{index}].slot_number"})
        numbers.add(number)
        resident_id = optional_text(item.get("assigned_resident_id"),
                                    f"slots[{index}].assigned_resident_id", max_length=64)
        if resident_id:
            if resident_id in occupants:
                raise ValidationError("A resident can occupy only one slot.",
                                      details={"field": f"slots[{index}].assigned_resident_id"})
            occupants.add(resident_id)
        cleaned.append({
            "slot_number": number,
            "slot_label": require_text(item.get("slot_label"), f"slots[{index}].slot_label",
                                       max_length=100),
            "target_deposit_paise": ensure_paise(
                item.get("target_deposit_paise"), f"slots[{index}].target_deposit_paise",
                allow_zero=False),
            "assigned_resident_id": resident_id,
        })
        # The room the slot backs. Left out, the link is untouched; null unlinks.
        if "room_id" in item:
            cleaned[-1]["room_id"] = optional_text(item["room_id"], f"slots[{index}].room_id",
                                                   max_length=64)
    return cleaned


def initialize_or_update_slots(db: Database, apartment_id: str,
                               slot_definitions: Sequence[Mapping[str, Any]], *,
                               actor: str = "operator") -> dict[str, Any]:
    """Create the household's deposit slots, or change the ones named.

    Slots are matched by number: a new number makes a slot, an existing one is
    changed, and slots left out are untouched. Afterwards every slot's target
    together must equal the deposit on the lease, exactly.

    A slot's label and target can be changed freely, though the target never
    below what its occupant has put in. Its occupant can be set while the slot
    is vacant, or swapped while nothing has been paid; a funded slot has to be
    vacated and settled first. Under ``primary_signatory`` the signatory's own
    slot counts as funded from the start -- it is part of what they fronted.
    """
    wanted = _clean_slot_definitions(slot_definitions)
    timestamp = now_timestamp()
    today = timestamp[:10]

    with db.transaction() as uow:
        get_apartment(uow, apartment_id)
        lease = _configured_lease(uow, apartment_id)
        master = lease["security_deposit_paise"]
        if master is None:
            raise ConflictError(
                "Enter the security deposit on the lease before dividing it into slots.",
                code="deposit_amount_not_set")
        mode = lease["deposit_funding_mode"]
        signatory_id = lease["deposit_primary_signatory_resident_id"]
        by_number = {slot["slot_number"]: slot
                     for slot in uow.deposits.slots_for_apartment(apartment_id)}
        touched = {item["slot_number"] for item in wanted}
        # Who already occupies a slot this request does not touch.
        taken = {slot["assigned_resident_id"]: number for number, slot in by_number.items()
                 if slot["assigned_resident_id"] and number not in touched}

        targets = {number: slot["target_deposit_paise"] for number, slot in by_number.items()}
        for item in wanted:
            number, resident_id = item["slot_number"], item["assigned_resident_id"]
            existing = by_number.get(number)
            keeps_occupant = bool(existing) and existing["assigned_resident_id"] == resident_id
            if resident_id and not keeps_occupant:
                resident = get_resident(uow, resident_id, apartment_id)
                if not resident["is_active"]:
                    raise ValidationError(
                        f"{resident['display_name']} has moved out and cannot be given a slot.",
                        details={"resident_id": resident_id})
                if resident_id in taken:
                    raise ConflictError(
                        f"{resident['display_name']} already occupies slot {taken[resident_id]}.",
                        code="deposit_slot_already_held",
                        details={"resident_id": resident_id})
            fronted = mode == "primary_signatory" and resident_id == signatory_id \
                and resident_id is not None
            target = item["target_deposit_paise"]

            if existing is None:
                funded = target if fronted else 0
                slot_id = new_id("depslot")
                uow.deposits.insert_slot(
                    slot_id=slot_id, apartment_id=apartment_id, config_id=lease["config_id"],
                    slot_number=number, slot_label=item["slot_label"],
                    target_deposit_paise=target, assigned_resident_id=resident_id,
                    assigned_since=today if resident_id else None,
                    status=_status_for(resident_id, funded, target),
                    funded_amount_paise=funded,
                    payment_method="direct_to_landlord" if fronted else None,
                    created_at=timestamp)
                audit.record(uow, entity_type=ENTITY, entity_id=slot_id, action="create",
                             actor=actor, apartment_id=apartment_id,
                             new_value={**item, "funded_amount_paise": funded})
                if "room_id" in item:
                    rooms.link_slot_in(uow, apartment_id, slot_id, item["room_id"], actor=actor)
            else:
                if not keeps_occupant and existing["funded_amount_paise"] > 0:
                    raise ConflictError(
                        f"Slot {number} holds its occupant's deposit. Vacate and settle it "
                        "before assigning somebody else.",
                        code="deposit_slot_funded", details={"slot_number": number})
                funded = target if fronted else (
                    existing["funded_amount_paise"] if keeps_occupant else 0)
                if target < funded:
                    raise ValidationError(
                        f"Slot {number}'s occupant has already put in more than that.",
                        details={"slot_number": number, "funded_amount_paise": funded})
                changes: dict[str, Any] = {
                    "slot_label": item["slot_label"], "target_deposit_paise": target,
                    "assigned_resident_id": resident_id,
                    "assigned_since": (existing["assigned_since"] if keeps_occupant
                                       else (today if resident_id else None)),
                    "status": _status_for(resident_id, funded, target),
                    "funded_amount_paise": funded,
                }
                if not keeps_occupant:
                    changes.update(
                        funded_date=None, payment_reference=None, paid_to_resident_id=None,
                        payment_method="direct_to_landlord" if fronted else None)
                uow.deposits.update_slot(existing["slot_id"], updated_at=timestamp, **changes)
                audit.record_field_changes(
                    uow, entity_type=ENTITY, entity_id=existing["slot_id"], before=existing,
                    after={**existing, **changes}, fields=list(changes), actor=actor,
                    apartment_id=apartment_id)
                if "room_id" in item:
                    rooms.link_slot_in(uow, apartment_id, existing["slot_id"], item["room_id"],
                                       actor=actor)
            targets[number] = target

        total = sum(targets.values())
        if total != master:
            raise ValidationError(
                "The slots' targets must add up to exactly the deposit on the lease.",
                details={"field": "slots", "total_target_paise": total,
                         "master_deposit_paise": master,
                         "difference_paise": total - master})
        return _summary(uow, apartment_id)


def link_slot_room(db: Database, apartment_id: str, slot_id: str, room_id: str | None, *,
                   actor: str = "operator") -> dict[str, Any]:
    """Say which room a slot backs, or with ``None`` that it backs none."""
    with db.transaction() as uow:
        get_apartment(uow, apartment_id)
        _slot(uow, slot_id, apartment_id)
        rooms.link_slot_in(uow, apartment_id, slot_id,
                           optional_text(room_id, "room_id", max_length=64), actor=actor)
        return _summary(uow, apartment_id)


# --------------------------------------------------------------------- funding --

def record_slot_funding(
    db: Database, slot_id: str, *, apartment_id: str, amount_paise: Any, paid_date: Any,
    payment_method: Any, payment_reference: Any = None, paid_to_resident_id: Any = None,
    actor: str = "operator",
) -> dict[str, Any]:
    """Record money the occupant paid towards their slot: to the primary
    signatory, or straight to the landlord. Part payments add up; the slot keeps
    the date, method and reference of the latest, and each one is a transaction."""
    amount = ensure_paise(amount_paise, "amount_paise", allow_zero=False)
    paid_on = _date_not_in_future(paid_date, "paid_date")
    if payment_method not in PAYMENT_METHODS:
        raise ValidationError("Unsupported payment method.",
                              details={"field": "payment_method",
                                       "allowed": list(PAYMENT_METHODS)})
    reference = optional_text(payment_reference, "payment_reference", max_length=200)
    paid_to = optional_text(paid_to_resident_id, "paid_to_resident_id", max_length=64)
    timestamp = now_timestamp()

    with db.transaction() as uow:
        slot = _slot(uow, slot_id, apartment_id)
        lease = _configured_lease(uow, apartment_id)
        occupant = slot["assigned_resident_id"]
        if occupant is None:
            raise ConflictError("This slot is vacant. Assign an occupant first.",
                                code="deposit_slot_vacant")
        remaining = slot["target_deposit_paise"] - slot["funded_amount_paise"]
        if remaining <= 0:
            raise ConflictError("This slot is already fully funded.",
                                code="deposit_slot_fully_funded")
        if amount > remaining:
            raise ValidationError("That is more than is still owed on this slot.",
                                  details={"field": "amount_paise",
                                           "outstanding_paise": remaining})

        if lease["deposit_funding_mode"] == "primary_signatory":
            signatory_id = lease["deposit_primary_signatory_resident_id"]
            if payment_method == "direct_to_landlord":
                raise ValidationError(
                    "The signatory already paid the landlord; occupants reimburse "
                    "the signatory.", details={"field": "payment_method"})
            if paid_to and paid_to != signatory_id:
                raise ValidationError("Reimbursements go to the primary signatory.",
                                      details={"field": "paid_to_resident_id"})
            paid_to = signatory_id
        elif paid_to:
            get_resident(uow, paid_to, apartment_id)
        if paid_to == occupant:
            raise ValidationError("An occupant cannot pay their deposit to themselves.",
                                  details={"field": "paid_to_resident_id"})

        funded = slot["funded_amount_paise"] + amount
        status = _status_for(occupant, funded, slot["target_deposit_paise"])
        uow.deposits.update_slot(
            slot_id, updated_at=timestamp, funded_amount_paise=funded, funded_date=paid_on,
            payment_method=payment_method, payment_reference=reference,
            paid_to_resident_id=paid_to, status=status)
        _transaction(uow, slot, resident_id=occupant, tx_type="funding_payment",
                     amount_paise=amount, tx_date=paid_on, timestamp=timestamp,
                     counterparty_resident_id=paid_to, payment_method=payment_method,
                     reference=reference)
        audit.record(uow, entity_type=ENTITY, entity_id=slot_id, action="funding_payment",
                     actor=actor, apartment_id=apartment_id,
                     old_value={"funded_amount_paise": slot["funded_amount_paise"],
                                "status": slot["status"]},
                     new_value={"resident_id": occupant, "amount_paise": amount,
                                "funded_amount_paise": funded, "paid_date": paid_on,
                                "payment_method": payment_method,
                                "payment_reference": reference,
                                "paid_to_resident_id": paid_to, "status": status})
        return _summary(uow, apartment_id)


# ------------------------------------------------------------------ moving out --

def settle_slot_in(
    uow: UnitOfWork, slot_id: str, *, apartment_id: str, settlement_path: str,
    deductions_paise: int, refund_amount_paise: int, settled_on: str,
    deduction_reason: str | None, reference: str | None, incoming_resident_id: str | None,
    actor: str,
) -> dict[str, Any]:
    """Settle a leaving occupant's slot inside the caller's transaction.

    The values must already be cleaned (``vacate_and_settle_slot`` and the exit
    clearance both do that). Returns what was settled: the leaver, what they had
    put in, and who the money moved through.
    """
    direct = settlement_path == "direct_transfer"
    deductions, refund, reason = deductions_paise, refund_amount_paise, deduction_reason
    incoming_id = incoming_resident_id
    timestamp = now_timestamp()

    slot = _slot(uow, slot_id, apartment_id)
    lease = _configured_lease(uow, apartment_id)
    leaver = slot["assigned_resident_id"]
    if leaver is None:
        raise ConflictError("This slot is already vacant.", code="deposit_slot_vacant")
    signatory_id = lease["deposit_primary_signatory_resident_id"]
    if leaver == signatory_id:
        raise ConflictError(
            "The primary signatory's own slot cannot be vacated here: their share is "
            "settled with the landlord when the lease ends.",
            code="deposit_signatory_slot")
    funded = slot["funded_amount_paise"]
    if deductions > funded:
        raise ValidationError("Deductions cannot be more than the deposit put in.",
                              details={"field": "deductions_paise",
                                       "funded_amount_paise": funded})
    if refund + deductions != funded:
        raise ValidationError(
            "The refund and the deductions must add up to what was put in.",
            details={"field": "refund_amount_paise", "funded_amount_paise": funded,
                     "expected_refund_paise": funded - deductions})

    if direct:
        if incoming_id == leaver:
            raise ValidationError("The incoming resident must be somebody else.",
                                  details={"field": "incoming_resident_id"})
        incoming = get_resident(uow, incoming_id, apartment_id)
        if not incoming["is_active"]:
            raise ValidationError("The incoming resident must be a current resident.",
                                  details={"field": "incoming_resident_id"})
        if any(other["assigned_resident_id"] == incoming_id
               for other in uow.deposits.slots_for_apartment(apartment_id)):
            raise ConflictError(
                f"{incoming['display_name']} already occupies a deposit slot.",
                code="deposit_slot_already_held", details={"resident_id": incoming_id})
        if refund <= 0:
            raise ValidationError(
                "Nothing would be handed over. Settle the slot as a refund instead.",
                details={"field": "refund_amount_paise"})

    if deductions > 0:
        _transaction(uow, slot, resident_id=leaver, tx_type="move_out_deduction",
                     amount_paise=deductions, tx_date=settled_on, timestamp=timestamp,
                     notes=reason)
    if direct:
        _transaction(uow, slot, resident_id=leaver,
                     tx_type="direct_replacement_transfer", amount_paise=refund,
                     tx_date=settled_on, timestamp=timestamp,
                     counterparty_resident_id=incoming_id, reference=reference)
        target = slot["target_deposit_paise"]
        changes = {
            "assigned_resident_id": incoming_id, "assigned_since": settled_on,
            "status": _status_for(incoming_id, refund, target),
            "funded_amount_paise": refund, "funded_date": settled_on,
            "payment_method": None, "payment_reference": reference,
            "paid_to_resident_id": leaver,
        }
    else:
        _transaction(uow, slot, resident_id=leaver, tx_type="move_out_refund",
                     amount_paise=refund, tx_date=settled_on, timestamp=timestamp,
                     counterparty_resident_id=signatory_id, reference=reference)
        changes = {
            "assigned_resident_id": None, "assigned_since": None, "status": "vacant",
            "funded_amount_paise": 0, "funded_date": None, "payment_method": None,
            "payment_reference": None, "paid_to_resident_id": None,
        }
    uow.deposits.update_slot(slot_id, updated_at=timestamp, **changes)
    audit.record(uow, entity_type=ENTITY, entity_id=slot_id, action="vacate",
                 actor=actor, apartment_id=apartment_id, reason=reason,
                 old_value={"assigned_resident_id": leaver,
                            "funded_amount_paise": funded, "status": slot["status"]},
                 new_value={"settlement_path": settlement_path,
                            "deductions_paise": deductions,
                            "refund_amount_paise": refund, "refund_date": settled_on,
                            "refund_reference": reference,
                            "incoming_resident_id": incoming_id,
                            "status": changes["status"]})
    return {"slot": slot, "leaver_resident_id": leaver, "funded_amount_paise": funded,
            "counterparty_resident_id": incoming_id if direct else signatory_id}


def vacate_and_settle_slot(
    db: Database, slot_id: str, *, apartment_id: str, settlement_path: Any,
    deductions_paise: Any, refund_amount_paise: Any, refund_date: Any,
    deduction_reason: Any = None, refund_reference: Any = None,
    incoming_resident_id: Any = None, actor: str = "operator",
) -> dict[str, Any]:
    """Settle a leaving occupant's deposit and free, or hand over, their slot.

    The refund and the deductions must add up to exactly what the occupant had
    put in. With ``signatory_refund`` the money is handed back (by the primary
    signatory, where there is one) and the slot stands vacant. With
    ``direct_transfer`` the incoming resident pays the leaver directly and takes
    the slot; they have then funded it by what they paid, so if anything was
    deducted they still owe that much to make the slot whole.
    """
    if settlement_path not in SETTLEMENT_PATHS:
        raise ValidationError(
            "Choose how the slot is settled: signatory_refund or direct_transfer.",
            details={"field": "settlement_path", "allowed": list(SETTLEMENT_PATHS)})
    refund = ensure_paise(refund_amount_paise, "refund_amount_paise")
    deductions = ensure_paise(deductions_paise, "deductions_paise")
    settled_on = _date_not_in_future(refund_date, "refund_date")
    reference = optional_text(refund_reference, "refund_reference", max_length=200)
    reason = optional_text(deduction_reason, "deduction_reason", max_length=2000)
    incoming_id = optional_text(incoming_resident_id, "incoming_resident_id", max_length=64)
    if deductions > 0 and not reason:
        raise ValidationError("Say what the deductions are for.",
                              details={"field": "deduction_reason"})
    direct = settlement_path == "direct_transfer"
    if direct and not incoming_id:
        raise ValidationError("Choose the resident taking the slot over.",
                              details={"field": "incoming_resident_id"})
    if not direct and incoming_id:
        raise ValidationError(
            "An incoming resident is only named for a direct transfer.",
            details={"field": "incoming_resident_id"})

    with db.transaction() as uow:
        settle_slot_in(
            uow, slot_id, apartment_id=apartment_id, settlement_path=settlement_path,
            deductions_paise=deductions, refund_amount_paise=refund, settled_on=settled_on,
            deduction_reason=reason, reference=reference, incoming_resident_id=incoming_id,
            actor=actor)
        return _summary(uow, apartment_id)

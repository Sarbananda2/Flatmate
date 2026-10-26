"""Payment Pools: a passbook for the household pool and one for the landlord.

Each is a read-only statement, like a bank's, built from records kept
elsewhere; nothing here is stored, and building it writes nothing.

The **House Kitty** (the household pool) takes in residents' payments --
their shares, rent among them -- and security deposits paid to it, and pays out
refunds to residents, the household expenses nobody else paid for, rent to the
landlord, and deposits it hands back. A resident who paid the landlord or a
provider themselves is not in it: the share of theirs recorded as passing
through the kitty comes in and goes straight out, so it is left out on both
sides (see ``resident_payouts``).

The **landlord** account counts what has been paid to the landlord -- each
month's rent, by the kitty or by a resident, one entry for each payout whatever
it is made up of; advances paid to them directly; and security deposits -- and what
they have handed back or kept from a deposit. Its balance is what the landlord
has had, net; ``deposit_held_paise`` is the part of it that is deposit. The
account goes by the name it is held in: the lease's payout beneficiary when
there is one, otherwise the landlord's own; ``owner_name`` is always the
landlord's.

Entries are put in date order to work out the running balance -- payments,
then deposit movements, then rent payouts, then expenses on the same day, each
in the order recorded -- and returned newest first.
"""
from __future__ import annotations

from typing import Any

from ..persistence import Database
from ..periods import month_label
from . import deposits, resident_payouts
from .common import DEFAULT_POOL_NAME, get_apartment

POOL_INSTANCE_ID = "__pool__"
LANDLORD_INSTANCE_ID = "__landlord__"
_SOURCE_ORDER = {"payment": 0, "deposit": 1, "rent_cycle": 2, "expense": 3}
DEFAULT_KITTY_NAME = DEFAULT_POOL_NAME


def _entry(*, entry_id: str, account: str, date: str, source_type: str, order: Any,
           category: str, description: str, counterparty_name: str | None,
           direction: str, amount_paise: int, counterparty_resident_id: str | None = None,
           payment_mode: str | None = None, reference: str | None = None,
           payment_id: str | None = None, note: str | None = None) -> dict[str, Any]:
    return {
        "entry_id": entry_id, "account": account, "date": date, "source_type": source_type,
        "category": category, "description": description,
        "counterparty_name": counterparty_name,
        "counterparty_resident_id": counterparty_resident_id,
        "direction": direction, "amount_paise": amount_paise,
        "payment_mode": payment_mode, "reference": reference, "payment_id": payment_id,
        "note": note, "_order": order,
    }


def _passbook(entries: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int, int]:
    """Number the entries in date order, give each the balance after it, and
    return them newest first with the money in and out."""
    entries.sort(key=lambda e: (e["date"], _SOURCE_ORDER[e["source_type"]], e["_order"]))
    balance = total_in = total_out = 0
    for seq, entry in enumerate(entries, start=1):
        amount = entry["amount_paise"]
        if entry["direction"] == "in":
            balance += amount
            total_in += amount
        else:
            balance -= amount
            total_out += amount
        entry["seq"] = seq
        entry["balance_paise"] = balance
        del entry["_order"]
    return list(reversed(entries)), total_in, total_out


def _room(slot: dict[str, Any]) -> str:
    return slot.get("room_number") or slot["slot_label"]


def get_payment_pools_statement(db: Database, apartment_id: str) -> dict[str, Any]:
    """Both passbooks: the House Kitty's and the landlord's."""
    with db.read() as uow:
        apartment = get_apartment(uow, apartment_id)
        lease = uow.rent.active_lease(apartment_id)
        payments = uow.payments.statement_payments(apartment_id)
        tranches = uow.rent.disbursements_for_apartment(apartment_id)
        expenses = uow.expenses.kitty_paid_expenses(apartment_id)
        service_tranches = uow.furniture.service_disbursements_for_apartment(apartment_id)
        deposit = deposits.summary_in(uow, apartment_id)

    pool_name = apartment.get("pool_name") or DEFAULT_KITTY_NAME
    landlord_name = (lease or {}).get("landlord_name") or "Landlord"
    account_name = (lease or {}).get("payout_beneficiary_name") or landlord_name
    # A resident who paid the landlord may have named either of them.
    landlord_payees = {landlord_name.strip().lower(), account_name.strip().lower()}
    pool: list[dict[str, Any]] = []
    landlord: list[dict[str, Any]] = []
    by_id = {payment["payment_id"]: payment for payment in payments}
    rent_payouts, kitty_rent = resident_payouts.group_payouts("rent", tranches, by_id)
    service_payouts, kitty_service = resident_payouts.group_payouts(
        "service", service_tranches, by_id)
    passed_through = {payout["share_payment_id"] for payout in rent_payouts + service_payouts}

    # Residents' payments into the kitty, and the kitty's refunds to them.
    for payment in payments:
        if payment["payment_id"] in passed_through:
            continue
        name = payment["payer_display_name"] or payment["payer_resident_id"]
        common = dict(date=payment["payment_date"], source_type="payment",
                      order=payment["recorded_order"], counterparty_name=name,
                      counterparty_resident_id=payment["payer_resident_id"],
                      amount_paise=payment["amount_paise"],
                      payment_mode=payment["payment_mode"], reference=payment["reference"],
                      payment_id=payment["payment_id"], note=payment["note"])
        if payment["payment_kind"] == "to_pool":
            rent = payment["settles_rent"] or (payment["note"] or "").startswith("Rent for ")
            pool.append(_entry(
                entry_id=f"payment:{payment['payment_id']}", account="pool", direction="in",
                category="Rent Collection" if rent else "Kitty Payment",
                description=payment["note"] or "Payment into House Kitty", **common))
        elif payment["payment_kind"] == "refund":
            pool.append(_entry(
                entry_id=f"payment:{payment['payment_id']}", account="pool", direction="out",
                category="Resident Refund",
                description=payment["note"] or "Refund from House Kitty", **common))

    # Rent paid to the landlord: out of the kitty, tranche by tranche, or by a
    # resident from their own account, as one payout of the whole amount.
    advanced = {t["funding_payment_id"] for t in tranches if t["funding_payment_id"]}
    for tranche in kitty_rent:
        month = month_label(tranche["settlement_month"])
        common = dict(date=tranche["disbursed_at"], source_type="rent_cycle",
                      order=tranche["seq"], amount_paise=tranche["amount_paise"],
                      reference=tranche["disbursement_reference"], note=tranche["note"])
        pool.append(_entry(
            entry_id=f"rent:{tranche['disbursement_id']}", account="pool", direction="out",
            category="Rent Payout", description=f"Rent payout · {month}",
            counterparty_name=landlord_name, **common))
        landlord.append(_entry(
            entry_id=f"rent:{tranche['disbursement_id']}", account="landlord", direction="in",
            category="Rent Payout", description=f"Monthly rent · {month}",
            counterparty_name=DEFAULT_KITTY_NAME, **common))
    for payout in rent_payouts:
        landlord.append(_entry(
            entry_id=f"rent:{payout['tranches'][0]['disbursement_id']}", account="landlord",
            direction="in", date=payout["disbursed_at"], source_type="rent_cycle",
            order=payout["seq"], category="Rent Payout",
            description=f"Monthly rent · {month_label(payout['settlement_month'])}",
            counterparty_name=f"{payout['resident_name']} (Advance)",
            counterparty_resident_id=payout["resident_id"],
            amount_paise=payout["amount_paise"], reference=payout["reference"],
            payment_id=payout["payment_id"], note=payout["note"]))

    # Household expenses the kitty paid itself. A month's rent is not among them:
    # its payout above is the same money.
    for expense in expenses:
        pool.append(_entry(
            entry_id=f"expense:{expense['expense_id']}", account="pool", direction="out",
            date=expense["expense_date"], source_type="expense", order=expense["recorded_order"],
            category="House Expense", description=expense["description"],
            counterparty_name=expense.get("provider_name") or "House Expense",
            amount_paise=expense["amount_paise"], reference=expense.get("reference"),
            note=expense.get("notes")))

    # A household service's bill, paid out of the kitty payout by payout. What a
    # resident paid the provider themselves never passed through it.
    for tranche in kitty_service:
        pool.append(_entry(
            entry_id=f"service:{tranche['disbursement_id']}", account="pool", direction="out",
            date=tranche["disbursed_at"], source_type="expense", order=tranche["seq"],
            category="House Expense",
            description=f"{tranche['display_name']} payout · "
                        f"{month_label(tranche['settlement_month'])}",
            counterparty_name=tranche["provider_name"] or tranche["display_name"],
            amount_paise=tranche["amount_paise"], reference=tranche["disbursement_reference"],
            note=tranche["note"]))

    # Anything else a resident paid the landlord directly.
    for payment in payments:
        if (payment["payment_kind"] != "provider_advance"
                or payment["payment_id"] in advanced
                or (payment["payee_or_provider"] or "").strip().lower()
                not in landlord_payees):
            continue
        landlord.append(_entry(
            entry_id=f"payment:{payment['payment_id']}", account="landlord", direction="in",
            date=payment["payment_date"], source_type="payment", order=payment["recorded_order"],
            category="Direct Advance",
            description=(payment["note"] or payment["linked_expense_description"]
                         or "Direct advance to Landlord"),
            counterparty_name=payment["payer_display_name"] or payment["payer_resident_id"],
            counterparty_resident_id=payment["payer_resident_id"],
            amount_paise=payment["amount_paise"], payment_mode=payment["payment_mode"],
            reference=payment["reference"], payment_id=payment["payment_id"],
            note=payment["note"]))

    # Security deposits: what the kitty or the landlord received, handed back or kept.
    deposit_in = deposit_out = 0
    for slot in deposit["slots"]:
        room = _room(slot)
        for tx in slot["transactions"]:
            party, kind = tx["counterparty_party"], tx["tx_type"]
            resident = tx["resident_display_name"] or tx["resident_id"]
            common = dict(date=tx["tx_date"], source_type="deposit", order=tx["recorded_order"],
                          counterparty_name=resident, counterparty_resident_id=tx["resident_id"],
                          amount_paise=tx["amount_paise"], payment_mode=tx["payment_method"],
                          reference=tx["reference"], note=tx["notes"])
            entry_id = f"deposit:{tx['tx_id']}"
            if party == "pool" and kind == "funding_payment":
                pool.append(_entry(entry_id=entry_id, account="pool", direction="in",
                                   category="Security Deposit",
                                   description=f"Security deposit funded · {room}", **common))
            elif party == "pool" and kind == "move_out_refund":
                pool.append(_entry(entry_id=entry_id, account="pool", direction="out",
                                   category="Deposit Refund",
                                   description=f"Security deposit refunded · {room}", **common))
            elif party == "landlord" and kind == "funding_payment":
                deposit_in += tx["amount_paise"]
                landlord.append(_entry(entry_id=entry_id, account="landlord", direction="in",
                                       category="Security Deposit",
                                       description=f"Security deposit · {room}", **common))
            elif party == "landlord" and kind == "move_out_refund":
                deposit_out += tx["amount_paise"]
                landlord.append(_entry(entry_id=entry_id, account="landlord", direction="out",
                                       category="Deposit Refund",
                                       description=f"Deposit refunded by landlord · {room}",
                                       **common))
            elif party == "landlord" and kind == "move_out_deduction":
                deposit_out += tx["amount_paise"]
                landlord.append(_entry(entry_id=entry_id, account="landlord", direction="out",
                                       category="Deposit Deduction",
                                       description=f"Deposit deduction · {room}", **common))

    # A primary signatory paid the landlord the whole deposit up front; that
    # payment has no transaction of its own, so it is entered from the lease.
    signatory_id = deposit["primary_signatory_id"]
    if (deposit["funding_mode"] == "primary_signatory" and signatory_id
            and deposit["master_deposit_paise"] and deposit["slots"]):
        own = next((slot for slot in deposit["slots"]
                    if slot["assigned_resident_id"] == signatory_id), deposit["slots"][0])
        signatory = deposit["primary_signatory_name"] or signatory_id
        deposit_in += deposit["master_deposit_paise"]
        landlord.append(_entry(
            entry_id="deposit:fronted", account="landlord", direction="in",
            date=(lease or {}).get("lease_start_date") or own["created_at"][:10],
            source_type="deposit", order=0, category="Security Deposit",
            description=f"Security deposit fronted by {signatory}",
            counterparty_name=signatory, counterparty_resident_id=signatory_id,
            amount_paise=deposit["master_deposit_paise"]))

    pool_items, pool_in, pool_out = _passbook(pool)
    landlord_items, landlord_in, landlord_out = _passbook(landlord)
    return {
        "apartment_id": apartment_id,
        "pool_statement": {
            # The House Kitty, and whose bank account or UPI ID holds it.
            "account": {"instance_id": POOL_INSTANCE_ID, "name": pool_name,
                        "account_holder": apartment.get("pool_account_holder")},
            "items": pool_items,
            "total_in_paise": pool_in,
            "total_out_paise": pool_out,
            "closing_balance_paise": pool_in - pool_out,
        },
        "landlord_statement": {
            "account": {"instance_id": LANDLORD_INSTANCE_ID, "name": account_name,
                        "owner_name": landlord_name},
            "items": landlord_items,
            "total_rent_paid_paise": sum(item["amount_paise"] for item in landlord_items
                                         if item["category"] == "Rent Payout"),
            "deposit_held_paise": deposit_in - deposit_out,
            "total_in_paise": landlord_in,
            "total_out_paise": landlord_out,
            "closing_balance_paise": landlord_in - landlord_out,
        },
    }

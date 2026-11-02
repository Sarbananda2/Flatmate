"""What every household service's monthly bill shares: paying it in two steps.

A household service -- furniture rental, the water supply -- bills each month
as one ordinary expense split between the residents taking part. Residents pay
their shares into the House Kitty, and the provider is paid from it in one
payout or several (``service_disbursement`` tranches); a resident who pays the
provider from their own account pays their own share through the kitty, and
only what they paid beyond it is an advance the kitty pays back to them (see
:mod:`aem.services.bill_shares`). These are the parts of that every service
reads the same way.
"""
from __future__ import annotations

from typing import Any, Mapping

from ..persistence import UnitOfWork
from . import bill_shares
from .common import list_residents


def reimbursement_id_base(expense_id: str) -> str:
    """What a kitty reimbursement's payment ID starts with, ahead of its own suffix.

    Only a provider advance may name the expense it is for, so a refund carries
    its link to the month's bill in its ID instead, as a reversal or a direct
    settlement carries its own.
    """
    return f"{expense_id}-reimbursement"


def share_payout_id(payment_id: str) -> str:
    """The kitty's payout of a share a resident paid the provider directly.

    It is kept with the payment into the kitty that settled the share, so a
    payout towards the bill can be told apart from the kitty paying out what
    others paid in. Migration 0023 links its tranches to their payments alike.
    """
    return f"svcdisb_{payment_id}"


def share_status(owed: int, paid: int, advanced: int) -> str | None:
    """Where a resident's charge on the bill stands. ``funded_payout``: they paid
    the provider, which covers their share without anything into the kitty."""
    if owed <= 0:
        return None
    if paid >= owed:
        return "paid"
    if advanced and paid + advanced >= owed:
        return "funded_payout"
    return "partial" if paid + advanced else "unpaid"


def public_tranche(tranche: Mapping[str, Any]) -> dict[str, Any]:
    return {key: tranche.get(key) for key in (
        "disbursement_id", "service_id", "provider_name", "amount_paise", "disbursed_at",
        "disbursement_reference", "disbursed_by", "funding_resident_id",
        "funding_resident_name", "funding_payment_id", "note", "created_at")}


def funding(uow: UnitOfWork, apartment_id: str, expense: Mapping[str, Any]) -> dict[str, Any]:
    """The recorded bill's two steps: what the kitty has collected from residents,
    and what has been paid to the provider -- and what the kitty owes whoever
    paid the provider from their own account.

    ``collected_paise`` counts what residents have paid into the kitty against
    their charge, plus what a resident paid the provider directly towards their
    own share; ``collected_count`` is how many charges are covered in full. The
    provider may be paid in tranches: ``disbursements`` lists them, and
    ``payout_status`` is 'pending' until the first, 'partially_disbursed' while
    some is still owed, and 'disbursed' once all of it has been paid.

    ``reimbursements`` is what is owed back to each resident who paid more than
    their own share -- to a provider, however many they paid, or into the kitty
    for the bill -- and ``reimbursement`` the first of them; see
    :func:`aem.services.bill_shares.reimbursements`.
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
        "disbursements": [public_tranche(tranche) for tranche in tranches],
        "tranches": tranches,
        "reimbursement": None,
        "reimbursements": [],
    }
    names = {row["resident_id"]: row["display_name"]
             for row in list_residents(uow, apartment_id)}
    result["reimbursements"] = bill_shares.reimbursements(
        uow, expense_id=expense_id, charges=list(charges.values()), tranches=tranches,
        names=names)
    result["reimbursement"] = result["reimbursements"][0] if result["reimbursements"] else None
    return result

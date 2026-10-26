"""A resident's own payout to the landlord or a provider, read back as the one
payment it was.

A resident who pays the landlord, or a household service's provider, from their
own account is recorded in parts (``rent.record_landlord_disbursement``,
``furniture_rental.record_provider_payout``): their open share as a
payment into the House Kitty and the kitty's payout of it -- a ``pool`` tranche
whose ID is that payment's, prefixed -- and anything beyond their share as a
``provider_advance`` and the ``resident_advance`` tranche it funds. The parts
keep the kitty's books; whoever reads a statement sees one payment, the
resident's, of the whole amount to the payee.

Nothing here reads the database: the callers pass the tranches and the active
payments by ID.
"""
from __future__ import annotations

from typing import Any, Iterable, Literal, Mapping

#: Which bills a payout is for.
Scope = Literal["rent", "service"]

SHARE_PREFIX = {"rent": "rentdisb_", "service": "svcdisb_"}
_GENERATED_NOTE = "'s share (reimbursable via House Kitty)"


def share_payment_id(scope: Scope, tranche: Mapping[str, Any],
                     payments: Mapping[str, Mapping[str, Any]]) -> str | None:
    """The resident's payment into the kitty that a ``pool`` tranche paid out as
    their own share, if that is what it is."""
    prefix = SHARE_PREFIX[scope]
    if tranche["disbursed_by"] != "pool" or not tranche["disbursement_id"].startswith(prefix):
        return None
    candidate = tranche["disbursement_id"][len(prefix):]
    payment = payments.get(candidate)
    return candidate if payment and payment["payment_kind"] == "to_pool" else None


def group_payouts(scope: Scope, tranches: Iterable[Mapping[str, Any]],
                  payments: Mapping[str, Mapping[str, Any]],
                  ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The residents' own payouts among ``tranches``, oldest first, and the
    payouts the kitty made itself.

    Tranches written together -- for the same bill, recorded at the same moment,
    paid the same day under the same reference, by the same resident -- are one
    payout. Each comes back with the resident, the whole amount, the date and
    reference, its tranches, and the payment that stands for it:
    ``advance_payment_id`` when they paid beyond their share, otherwise
    ``share_payment_id``.
    """
    groups: dict[tuple[Any, ...], dict[str, Any]] = {}
    kitty: list[dict[str, Any]] = []
    for tranche in tranches:
        share = share_payment_id(scope, tranche, payments)
        if share:
            resident_id = payments[share]["payer_resident_id"]
            resident_name = payments[share].get("payer_display_name")
        elif tranche["disbursed_by"] == "resident_advance":
            resident_id = tranche["funding_resident_id"]
            resident_name = tranche.get("funding_resident_name")
        else:
            kitty.append(dict(tranche))
            continue
        bill = tranche["cycle_id"] if scope == "rent" else tranche["expense_id"]
        key = (bill, tranche["created_at"], tranche["disbursed_at"],
               tranche["disbursement_reference"], resident_id)
        group = groups.setdefault(key, {
            "scope": scope, "resident_id": resident_id, "resident_name": None,
            "settlement_month": tranche["settlement_month"],
            "disbursed_at": tranche["disbursed_at"],
            "reference": tranche["disbursement_reference"],
            "created_at": tranche["created_at"], "seq": tranche["seq"],
            "amount_paise": 0, "share_payment_id": None, "advance_payment_id": None,
            "note": None, "tranches": []})
        group["resident_name"] = group["resident_name"] or resident_name
        group["amount_paise"] += tranche["amount_paise"]
        group["tranches"].append(dict(tranche))
        if share:
            group["share_payment_id"] = share
        else:
            group["advance_payment_id"] = tranche["funding_payment_id"]
            # The advance carries the note entered with the payout; one written
            # for it only says how the payout was split.
            note = tranche.get("note")
            if note and not (note.startswith("Extra above ") and note.endswith(_GENERATED_NOTE)):
                group["note"] = note
    for group in groups.values():
        group["payment_id"] = group["advance_payment_id"] or group["share_payment_id"]
        group["resident_name"] = group["resident_name"] or group["resident_id"]
    return list(groups.values()), kitty

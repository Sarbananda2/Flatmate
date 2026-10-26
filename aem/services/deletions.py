"""Deleting a transaction recorded by mistake, while its month is still open.

A resident's payment into the House Kitty, the kitty's reimbursement to a
resident, or a payout to the landlord or a household service's provider,
entered wrongly -- the wrong amount, the wrong resident, twice -- can be deleted
outright while the month's statement is a draft (or not created yet). Once the
statement is finalized the month's transactions are locked
(``statement_not_draft``); correct them with a reversal or a ledger adjustment.

What was recorded together is deleted together, in one transaction:

* a resident's payout from their own account is the ``resident_advance``
  tranche and the ``provider_advance`` payment behind it, together with the
  kitty's payout of their own share and their payment into the kitty that it
  paid out (see ``rent.record_landlord_disbursement``);
* deleting that payment into the kitty, from the resident's statement, deletes
  the payout it was part of.

Nothing the kitty has paid back is left owing nothing: a payout whose advance
has been reimbursed, or a payment into the kitty whose money has gone back out
as a reimbursement, is deleted only after the reimbursement is
(``payout_reimbursed``, ``kitty_reimbursement_depends``).

Each row is written to the audit trail, as it was, before it is deleted. These
functions work inside the caller's transaction; the rent and furniture modules
call them and return the month as it then stands.
"""
from __future__ import annotations

import calendar
from typing import Any, Literal, Mapping

from ..errors import ConflictError, NotFoundError
from ..persistence import UnitOfWork
from . import audit
from .resident_payouts import SHARE_PREFIX as _SHARE_PREFIX

#: Which bills a payment or payout belongs to.
Scope = Literal["rent", "service"]

_REIMBURSEMENT = "-reimbursement_"


def _month_name(month: str) -> str:
    year, number = month.split("-")
    return f"{calendar.month_name[int(number)]} {year}"


def require_open_month(uow: UnitOfWork, apartment_id: str, month: str) -> None:
    """Refuse unless the month's statement is a draft, or not created yet."""
    status = uow.payments.statement_status_for_month(apartment_id, month)
    if status is not None and status != "draft":
        raise ConflictError(
            f"The {_month_name(month)} statement has been finalized, so its transactions are "
            "locked. Correct them with a reversal or a ledger adjustment instead.",
            code="statement_not_draft",
            details={"settlement_month": month, "statement_status": status})


def month_is_open(uow: UnitOfWork, apartment_id: str, month: str) -> bool:
    """Whether the month's transactions can still be deleted."""
    status = uow.payments.statement_status_for_month(apartment_id, month)
    return status is None or status == "draft"


# ------------------------------------------------------------------- payouts --

def _bill_tranches(uow: UnitOfWork, scope: Scope,
                   tranche: Mapping[str, Any]) -> list[dict[str, Any]]:
    if scope == "rent":
        return uow.rent.disbursements_for_cycle(tranche["cycle_id"])
    return uow.furniture.service_disbursements(tranche["expense_id"])


def _bill_expense_id(uow: UnitOfWork, scope: Scope, tranche: Mapping[str, Any]) -> str:
    if scope == "rent":
        return uow.rent.cycle(tranche["cycle_id"])["expense_id"]
    return tranche["expense_id"]


def _share_payment_id(uow: UnitOfWork, scope: Scope,
                      tranche: Mapping[str, Any]) -> str | None:
    """The resident's payment into the kitty that a ``pool`` tranche paid out as
    their own share, when they paid the landlord or provider themselves."""
    prefix = _SHARE_PREFIX[scope]
    if tranche["disbursed_by"] != "pool" or not tranche["disbursement_id"].startswith(prefix):
        return None
    candidate = tranche["disbursement_id"][len(prefix):]
    payment = uow.payments.get(candidate)
    return candidate if payment and payment["payment_kind"] == "to_pool" else None


def _paid_by(uow: UnitOfWork, scope: Scope, tranche: Mapping[str, Any]) -> str | None:
    """The resident behind a tranche: who advanced it, or whose share the kitty paid out."""
    if tranche["disbursed_by"] == "resident_advance":
        return tranche["funding_resident_id"]
    share = _share_payment_id(uow, scope, tranche)
    return uow.payments.get(share)["payer_resident_id"] if share else None


def _payout_group(uow: UnitOfWork, scope: Scope,
                  tranche: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The tranches recorded with this one: a resident's payout is its advance and
    the kitty's payout of their own share, written together; a payout from the
    kitty is itself alone."""
    payer = _paid_by(uow, scope, tranche)
    if payer is None:
        return [dict(tranche)]
    return [row for row in _bill_tranches(uow, scope, tranche)
            if row["created_at"] == tranche["created_at"]
            and row["disbursed_at"] == tranche["disbursed_at"]
            and row["disbursement_reference"] == tranche["disbursement_reference"]
            and _paid_by(uow, scope, row) == payer]


def _audit_payment(uow: UnitOfWork, payment: Mapping[str, Any], actor: str,
                   reason: str) -> None:
    audit.record(uow, entity_type="payment", entity_id=payment["payment_id"],
                 action="delete", actor=actor, apartment_id=payment["apartment_id"],
                 reason=reason,
                 old_value={**{key: payment.get(key) for key in (
                     "payment_kind", "payer_resident_id", "amount_paise", "payment_date",
                     "expense_id", "reference", "note", "payment_mode", "created_at")},
                     "applications": uow.payments.released_applications(payment["payment_id"])})


def _restate_rent_cycle(uow: UnitOfWork, cycle_id: str, deleted_payment_ids: set[str]) -> None:
    """The month's payout state from the payouts left standing."""
    cycle = uow.rent.cycle(cycle_id)
    left = uow.rent.disbursements_for_cycle(cycle_id)
    paid = sum(row["amount_paise"] for row in left)
    status = ("disbursed" if paid >= cycle["total_rent_paise"]
              else "partially_disbursed" if paid else "pending")
    latest = max(left, key=lambda row: (row["disbursed_at"], row["seq"])) if left else None
    uow.rent.restate_cycle_disbursement(
        cycle_id, status=status,
        last_disbursed_at=latest["disbursed_at"] if latest else None,
        last_reference=latest["disbursement_reference"] if latest else None,
        clear_funding=cycle["funding_payment_id"] in deleted_payment_ids)


def delete_payout_in(uow: UnitOfWork, *, apartment_id: str, scope: Scope,
                     disbursement_id: str, settlement_month: str | None = None,
                     actor: str = "operator") -> dict[str, Any]:
    """Delete a payout to the landlord (``rent``) or a provider (``service``), and
    whatever was recorded with it. Returns what was deleted and the month."""
    tranche = (uow.rent.disbursement(disbursement_id) if scope == "rent"
               else uow.furniture.service_disbursement(disbursement_id))
    if (tranche is None or tranche["apartment_id"] != apartment_id
            or (settlement_month and tranche["settlement_month"] != settlement_month)):
        raise NotFoundError("No such payout for this month.", code="payout_not_found",
                            details={"disbursement_id": disbursement_id})
    month = tranche["settlement_month"]
    require_open_month(uow, apartment_id, month)
    group = _payout_group(uow, scope, tranche)
    advances = [row for row in group if row["disbursed_by"] == "resident_advance"]
    if advances:
        # Whatever the kitty has paid back to them must still be owed after this.
        funder = advances[0]["funding_resident_id"]
        expense_id = _bill_expense_id(uow, scope, tranche)
        reimbursed = sum(row["amount_paise"]
                         for row in uow.rent.reimbursements(f"{expense_id}{_REIMBURSEMENT}")
                         if row["payer_resident_id"] == funder)
        in_group = {row["disbursement_id"] for row in group}
        still_advanced = sum(row["amount_paise"] for row in _bill_tranches(uow, scope, tranche)
                             if row["disbursed_by"] == "resident_advance"
                             and row["funding_resident_id"] == funder
                             and row["disbursement_id"] not in in_group)
        if reimbursed > still_advanced:
            raise ConflictError(
                "Delete the reimbursement paid from the House Kitty first before deleting "
                "this payout.", code="payout_reimbursed",
                details={"disbursement_id": disbursement_id, "resident_id": funder,
                         "reimbursed_paise": reimbursed})

    payment_ids = [row["funding_payment_id"] for row in advances]
    payment_ids += [share for row in group if (share := _share_payment_id(uow, scope, row))]
    entity = ("rent_disbursement", "rent_cycle") if scope == "rent" else (
        "service_disbursement", "household_service")
    for row in group:
        audit.record(uow, entity_type=entity[0], entity_id=row["disbursement_id"],
                     action="delete", actor=actor, apartment_id=apartment_id,
                     reason="Payout recorded by mistake",
                     old_value={key: row.get(key) for key in (
                         "settlement_month", "amount_paise", "disbursed_at",
                         "disbursement_reference", "disbursed_by", "funding_resident_id",
                         "funding_payment_id", "note", "created_at",
                         "cycle_id" if scope == "rent" else "service_id")})
        if scope == "rent":
            uow.rent.delete_disbursement(row["disbursement_id"])
        else:
            uow.furniture.delete_service_disbursement(row["disbursement_id"])
    if scope == "rent":
        # Before the payments go: the month may still name one as its single payout.
        _restate_rent_cycle(uow, tranche["cycle_id"], set(payment_ids))
    for payment_id in payment_ids:
        payment = uow.payments.get(payment_id)
        _audit_payment(uow, payment, actor, "Recorded with a payout deleted by mistake")
        uow.payments.delete_payment(payment_id)
    return {"settlement_month": month,
            "deleted_disbursement_ids": [row["disbursement_id"] for row in group],
            "deleted_payment_ids": payment_ids}


# ------------------------------------------------------------------ payments --

def delete_payment_in(uow: UnitOfWork, *, apartment_id: str, scope: Scope, payment_id: str,
                      settlement_month: str | None = None,
                      actor: str = "operator") -> dict[str, Any]:
    """Delete a resident's payment into the kitty towards a month's rent or a
    service's bill, or the kitty's reimbursement to a resident for one.

    A payment the kitty paid out as part of a resident's payout deletes that
    payout (see :func:`delete_payout_in`). Returns what was deleted and the month.
    """
    payment = uow.payments.get(payment_id)
    if payment is None or payment["apartment_id"] not in (None, apartment_id):
        raise NotFoundError("No such payment.", code="payment_not_found",
                            details={"payment_id": payment_id})
    if payment["status"] != "active":
        raise ConflictError(
            "A reversed payment, or a reversal, stays as recorded and cannot be deleted.",
            code="payment_not_active",
            details={"payment_id": payment_id, "status": payment["status"]})

    payout = (uow.payments.rent_payout_of(payment_id) if scope == "rent"
              else uow.payments.service_payout_of(payment_id))
    if payout is not None:
        return delete_payout_in(uow, apartment_id=apartment_id, scope=scope,
                                disbursement_id=payout["disbursement_id"],
                                settlement_month=settlement_month, actor=actor)

    # The bills it was towards: one it reimburses, or those whose charges it settled.
    bills: list[tuple[dict[str, Any], int]] = []
    if payment["payment_kind"] == "refund" and _REIMBURSEMENT in payment_id:
        bill = uow.payments.kitty_for_expense(payment_id.split(_REIMBURSEMENT)[0])
        if bill is not None:
            bills.append((bill, 0))
    elif payment["payment_kind"] == "to_pool":
        for row in uow.payments.applied_by_expense(payment_id):
            bill = uow.payments.kitty_for_expense(row["expense_id"])
            bills.append((bill, row["amount_paise"]))
    bills = [(bill, applied) for bill, applied in bills
             if bill["is_rent" if scope == "rent" else "is_service"]]
    months = sorted({bill["settlement_period"] for bill, _ in bills})
    if not bills or (settlement_month and settlement_month not in months):
        what = "rent" if scope == "rent" else "a household service's bill"
        raise NotFoundError(f"That payment is not towards {what}"
                            + (f" for {_month_name(settlement_month)}." if settlement_month
                               else "."),
                            code="payment_not_found", details={"payment_id": payment_id})
    for month in months:
        require_open_month(uow, apartment_id, month)
    if payment["payment_kind"] == "to_pool":
        # What the kitty has paid back must still have come from flatmates' payments.
        for bill, applied in bills:
            left = (bill["collected_paise"] - applied - bill["paid_out_paise"]
                    - bill["reimbursed_paise"])
            if bill["reimbursed_paise"] and left < 0:
                raise ConflictError(
                    "The House Kitty has paid this money back out as a reimbursement. Delete "
                    "the reimbursement first before deleting this payment.",
                    code="kitty_reimbursement_depends",
                    details={"payment_id": payment_id, "expense_id": bill["expense_id"],
                             "reimbursed_paise": bill["reimbursed_paise"]})
    _audit_payment(uow, payment, actor, "Payment recorded by mistake")
    uow.payments.delete_payment(payment_id)
    return {"settlement_month": months[0], "deleted_disbursement_ids": [],
            "deleted_payment_ids": [payment_id]}

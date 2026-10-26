"""A month's bill while its month is open: its shares follow the bill whoever
has paid, and what the House Kitty owes back for it.

Rent and a household service's bill (furniture rental) are each one expense,
charged to the residents as allocations. While the month's statement is a
draft their shares follow the lease or the items rented, even once residents
have paid:

* what a resident paid **into the House Kitty** for the bill stays on the bill.
  It settles their new share, up to what they paid: a share gone down leaves the
  rest on their payment, owed back to them from the kitty; a share gone up takes
  it back first -- what the kitty has not already paid back -- and stands part
  paid for whatever is still owed;
* a resident who paid **the landlord or the provider** from their own account
  keeps their payout whole. It is split again between their open share, paid
  through the kitty, and the extra the kitty owes them (see
  ``resident_payouts``); what they paid into the kitty settles their share first.

A change is refused when the kitty has already paid a resident back more than
it would then owe them (``reimbursement_exceeds_new_extra``). Nothing here
refuses a bill below what the landlord or provider has been paid: the rent and
furniture modules do.

:func:`reimbursements` is what the kitty owes back to each resident for a bill:
everything they paid towards it -- into the kitty, and to the landlord or
provider -- beyond their share, less what it has already paid back.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Mapping, Sequence

from ..errors import AemError, ConflictError
from ..ids import new_id
from ..money import format_inr
from ..persistence import UnitOfWork
from . import audit
from .resident_payouts import SHARE_PREFIX, group_payouts

Scope = Literal["rent", "service"]

_REIMBURSEMENT = "-reimbursement_"
#: The IDs a resident's advance and its tranche are given, as when first recorded.
_ADVANCE_PREFIX = {"rent": "rent-advance", "service": "furniture-payout"}
_TRANCHE_PREFIX = {"rent": "rentdisb", "service": "svcdisb"}


def _refunds(uow: UnitOfWork, expense_id: str) -> list[dict[str, Any]]:
    """The kitty's reimbursements for a bill, oldest first."""
    return uow.rent.reimbursements(f"{expense_id}{_REIMBURSEMENT}")


# ------------------------------------------------------- what is owed back --

def reimbursements(uow: UnitOfWork, *, expense_id: str, charges: Sequence[Mapping[str, Any]],
                   tranches: Sequence[Mapping[str, Any]],
                   names: Mapping[str, str]) -> list[dict[str, Any]]:
    """What the kitty owes back to each resident who paid more than their share
    of a bill, and what it can pay now.

    A resident is owed when they paid the landlord or a provider beyond their
    share (a ``resident_advance`` tranche), or paid into the kitty for the bill
    more than their share now takes. ``total_fronted_paise`` is everything they
    paid for it, both ways; ``net_reimbursement_owed_paise`` what of it is
    beyond their share. ``kitty_cash_available_paise`` is what the kitty holds
    for the bill: what residents paid into it for the bill, less what it paid
    the landlord or providers and has already paid back -- so what a resident
    overpaid into it can be handed straight back.

    ``charges`` are the bill's charges by resident (``resident_id``,
    ``share_paise``, ``settled_paise``) and ``tranches`` its payouts.
    """
    share = {row["resident_id"]: row["share_paise"] for row in charges}
    settled = {row["resident_id"]: row["settled_paise"] for row in charges}
    excess: dict[str, int] = {}
    for payment in uow.payments.kitty_payments_for_bill(expense_id):
        left = payment["amount_paise"] - payment["applied_paise"]
        if left > 0:
            rid = payment["payer_resident_id"]
            excess[rid] = excess.get(rid, 0) + left
    advanced: dict[str, int] = {}
    for tranche in tranches:
        if tranche["disbursed_by"] == "resident_advance":
            rid = tranche["funding_resident_id"]
            advanced[rid] = advanced.get(rid, 0) + tranche["amount_paise"]
    owed_to = [*advanced, *sorted((rid for rid in excess if rid not in advanced),
                                  key=lambda rid: (names.get(rid, rid), rid))]
    if not owed_to:
        return []

    refunds_all = _refunds(uow, expense_id)
    from_kitty = sum(t["amount_paise"] for t in tranches if t["disbursed_by"] == "pool")
    kitty = max(0, sum(settled.values()) + sum(excess.values()) - from_kitty
                - sum(row["amount_paise"] for row in refunds_all))
    owing: list[dict[str, Any]] = []
    for rid in owed_to:
        fronted = settled.get(rid, 0) + excess.get(rid, 0) + advanced.get(rid, 0)
        owed_back = max(0, fronted - share.get(rid, 0))
        if owed_back <= 0:
            continue
        refunds = [row for row in refunds_all if row["payer_resident_id"] == rid]
        reimbursed = sum(row["amount_paise"] for row in refunds)
        pending = max(0, owed_back - reimbursed)
        reimbursable = min(kitty, pending)
        owing.append({
            "recipient_resident_id": rid,
            "recipient_name": names.get(rid, rid),
            "total_fronted_paise": fronted,
            "payer_share_paise": share.get(rid, 0),
            "net_reimbursement_owed_paise": owed_back,
            "reimbursed_so_far_paise": reimbursed,
            "pending_reimbursement_paise": pending,
            "kitty_cash_available_paise": kitty,
            "reimbursable_paise": reimbursable,
            "can_reimburse": reimbursable > 0,
            "reimbursements": [{key: row[key] for key in (
                "payment_id", "amount_paise", "payment_date", "reference")} for row in refunds],
        })
    return owing


# ------------------------------------------------- following new shares --

@dataclass
class _Payout:
    """A resident's payout to the landlord or provider, and how it is split now."""
    group: dict[str, Any]
    share: dict[str, Any] | None            # the kitty's tranche of their own share
    advance: dict[str, Any] | None          # the tranche of what they paid beyond it
    to_share: int = 0
    to_advance: int = 0

    @property
    def share_now(self) -> int:
        return self.share["amount_paise"] if self.share else 0

    @property
    def advance_now(self) -> int:
        return self.advance["amount_paise"] if self.advance else 0


@dataclass
class _Resident:
    resident_id: str
    share: int
    trims: list[tuple[dict[str, Any], int]] = field(default_factory=list)
    top_ups: list[tuple[str, int]] = field(default_factory=list)
    payouts: list[_Payout] = field(default_factory=list)


@dataclass
class Rebalance:
    """How a bill's payments follow its new shares; ``problem`` when they cannot."""
    scope: Scope
    expense_id: str
    apartment_id: str
    residents: list[_Resident]
    problem: AemError | None = None


def plan(uow: UnitOfWork, *, scope: Scope, apartment_id: str, expense_id: str,
         shares: Mapping[str, int], charged: Sequence[str],
         tranches: Sequence[Mapping[str, Any]], names: Mapping[str, str],
         what: str) -> Rebalance:
    """Work out how the bill's payments follow ``shares`` (resident -> new share);
    ``charged`` are the residents the bill charges now.

    Each payment into the kitty that settles a charge on the bill and is not for
    any other bill is first named as being for this one, so what it paid
    beyond a share it no longer covers stays with the bill. ``what`` names the
    bill in a refusal: "October 2026 rent".
    """
    uow.payments.link_kitty_payments_to_bill(expense_id)
    active = {row["payment_id"]: row
              for row in uow.payments.active_payments(apartment_id)}
    groups, _ = group_payouts(scope, tranches, active)
    in_payouts = {group["share_payment_id"] for group in groups}
    applications = uow.payments.bill_applications(expense_id)
    kitty = [row for row in uow.payments.kitty_payments_for_bill(expense_id)
             if row["payment_id"] not in in_payouts]
    reimbursed: dict[str, int] = {}
    for row in _refunds(uow, expense_id):
        reimbursed[row["payer_resident_id"]] = (reimbursed.get(row["payer_resident_id"], 0)
                                                + row["amount_paise"])

    everyone = (set(shares) | set(charged) | {row["resident_id"] for row in applications}
                | {group["resident_id"] for group in groups}
                | {row["payer_resident_id"] for row in kitty})
    rebalance = Rebalance(scope, expense_id, apartment_id, [])
    for rid in sorted(everyone):
        share = shares.get(rid, 0)
        own = [row for row in applications
               if row["resident_id"] == rid and row["payment_id"] not in in_payouts]
        applied = sum(row["amount_paise"] for row in own)
        unapplied = [(row["payment_id"], row["amount_paise"] - row["applied_paise"])
                     for row in kitty if row["payer_resident_id"] == rid
                     and row["amount_paise"] > row["applied_paise"]]
        excess = sum(amount for _, amount in unapplied)
        payouts = [group for group in groups if group["resident_id"] == rid]
        paid = applied + excess + sum(group["amount_paise"] for group in payouts)
        back = reimbursed.get(rid, 0)
        if back > max(0, paid - share):
            name = names.get(rid, rid)
            rebalance.problem = ConflictError(
                f"The House Kitty has already paid {name} back {format_inr(back)} for "
                f"{what}, more than they would then be owed "
                f"({format_inr(max(0, paid - share))}). Delete the reimbursement first.",
                code="reimbursement_exceeds_new_extra",
                details={"resident_id": rid, "reimbursed_paise": back,
                         "net_reimbursement_owed_paise": max(0, paid - share),
                         "share_paise": share})
            return rebalance

        resident = _Resident(rid, share)
        if applied > share:
            # Newest first; a payment made for another bill gives back last.
            cut = applied - share
            for row in sorted(own, key=lambda r: (r["payment_expense_id"] == expense_id,
                                                  r["created_at"] or "", r["payment_seq"]),
                              reverse=True):
                if cut <= 0:
                    break
                take = min(cut, row["amount_paise"])
                resident.trims.append((row, row["amount_paise"] - take))
                cut -= take
            covered = share
        else:
            # What the kitty has already paid them back is not there to take.
            room = min(share - applied, max(0, excess - back))
            for payment_id, amount in unapplied:
                if room <= 0:
                    break
                take = min(room, amount)
                resident.top_ups.append((payment_id, take))
                room -= take
            covered = applied + sum(amount for _, amount in resident.top_ups)
        still_open = share - covered
        for group in sorted(payouts, key=lambda g: (g["created_at"], g["seq"])):
            payout = _Payout(
                group,
                share=next((t for t in group["tranches"] if t["disbursed_by"] == "pool"), None),
                advance=next((t for t in group["tranches"]
                              if t["disbursed_by"] == "resident_advance"), None))
            payout.to_share = min(group["amount_paise"], still_open)
            payout.to_advance = group["amount_paise"] - payout.to_share
            still_open -= payout.to_share
            resident.payouts.append(payout)
        rebalance.residents.append(resident)
    return rebalance


def apply(uow: UnitOfWork, rebalance: Rebalance, *, set_charge: Callable[[str, int], None],
          payee: Callable[[Mapping[str, Any]], str], share_note: str, advance_note: str,
          timestamp: str, actor: str) -> None:
    """Move the bill's payments to its new shares, ``set_charge(resident, share)``
    writing each resident's charge (deleting it at zero).

    Everything taken off a charge or a payout goes first, then the charges are
    set, then everything added: a charge is never settled beyond its amount,
    nor the landlord or provider paid beyond the bill, at any step.
    ``payee(group)`` names whom a new advance was paid to.
    """
    if rebalance.problem is not None:
        raise rebalance.problem
    expense_id = rebalance.expense_id
    allocation = {row["resident_id"]: row["allocation_id"]
                  for row in uow.rent.allocations_for_expense(expense_id)}
    for resident in rebalance.residents:
        for row, keep in resident.trims:
            if keep:
                uow.payments.update_application_amount(row["payment_application_id"], keep)
            else:
                uow.payments.delete_application(row["payment_application_id"])
        for payout in resident.payouts:
            if payout.to_share < payout.share_now:
                _resize_share(uow, rebalance, resident.resident_id, payout, payout.to_share,
                              allocation.get(resident.resident_id), actor)
            if payout.to_advance < payout.advance_now:
                _resize_advance(uow, rebalance, payout, payout.to_advance, actor)

    for resident in rebalance.residents:
        set_charge(resident.resident_id, resident.share)
    allocation = {row["resident_id"]: row["allocation_id"]
                  for row in uow.rent.allocations_for_expense(expense_id)}

    for resident in rebalance.residents:
        rid = resident.resident_id
        for payout in resident.payouts:
            if payout.to_share > payout.share_now:
                if payout.share is None:
                    _new_share(uow, rebalance, rid, payout, allocation[rid], share_note,
                               timestamp, actor)
                else:
                    _resize_share(uow, rebalance, rid, payout, payout.to_share,
                                  allocation[rid], actor)
            if payout.to_advance > payout.advance_now:
                if payout.advance is None:
                    _new_advance(uow, rebalance, rid, payout, payee(payout.group),
                                 advance_note, timestamp, actor)
                else:
                    _resize_advance(uow, rebalance, payout, payout.to_advance, actor)
        for payment_id, amount in resident.top_ups:
            _apply(uow, payment_id, allocation[rid], amount, timestamp)

    audit.record(
        uow, entity_type="expense", entity_id=expense_id, action="rebalance", actor=actor,
        apartment_id=rebalance.apartment_id,
        reason="The bill's shares changed after residents had paid",
        new_value={resident.resident_id: {
            "share_paise": resident.share,
            "trimmed_paise": sum(row["amount_paise"] - keep for row, keep in resident.trims),
            "topped_up_paise": sum(amount for _, amount in resident.top_ups),
            "payouts": [{"share_paise": p.to_share, "advance_paise": p.to_advance}
                        for p in resident.payouts]}
            for resident in rebalance.residents})


def _apply(uow: UnitOfWork, payment_id: str, allocation_id: str, amount: int,
           timestamp: str) -> None:
    """Settle ``amount`` more of a charge out of a payment."""
    existing = uow.payments.application_id_for_allocation(payment_id, allocation_id)
    if existing:
        uow.payments.add_to_application(existing, amount)
    else:
        uow.payments.insert_application(
            payment_application_id=new_id("application"), payment_id=payment_id,
            allocation_id=allocation_id, opening_balance_id=None, amount_paise=amount,
            charge_due_date=uow.payments.allocation_due_date(allocation_id),
            created_at=timestamp)


def _set_tranche(uow: UnitOfWork, scope: Scope, disbursement_id: str, amount: int) -> None:
    if scope == "rent":
        uow.rent.update_disbursement_amount(disbursement_id, amount)
    else:
        uow.furniture.update_service_disbursement_amount(disbursement_id, amount)


def _drop_tranche(uow: UnitOfWork, scope: Scope, disbursement_id: str) -> None:
    if scope == "rent":
        uow.rent.delete_disbursement(disbursement_id)
    else:
        uow.furniture.delete_service_disbursement(disbursement_id)


def _add_tranche(uow: UnitOfWork, scope: Scope, like: Mapping[str, Any], **fields: Any) -> None:
    """A tranche of the same payout as ``like``: the same bill, day, reference and moment."""
    common = dict(apartment_id=like["apartment_id"], settlement_month=like["settlement_month"],
                  disbursed_at=like["disbursed_at"],
                  disbursement_reference=like["disbursement_reference"],
                  created_at=like["created_at"], **fields)
    if scope == "rent":
        uow.rent.insert_disbursement(cycle_id=like["cycle_id"], **common)
    else:
        uow.furniture.insert_service_disbursement(service_id=like["service_id"],
                                                  expense_id=like["expense_id"], **common)


def _audit_payment(uow: UnitOfWork, rebalance: Rebalance, payment_id: str, action: str,
                   actor: str, **value: Any) -> None:
    audit.record(uow, entity_type="payment", entity_id=payment_id, action=action,
                 actor=actor, apartment_id=rebalance.apartment_id,
                 reason="A resident's payout split again as the bill's shares changed",
                 new_value={"expense_id": rebalance.expense_id, **value})


def _resize_share(uow: UnitOfWork, rebalance: Rebalance, rid: str, payout: _Payout,
                  amount: int, allocation_id: str | None, actor: str) -> None:
    """The kitty's payout of a resident's own share, and their payment into the
    kitty behind it, at ``amount`` -- gone at zero."""
    tranche = payout.share
    payment_id = tranche["disbursement_id"][len(SHARE_PREFIX[rebalance.scope]):]
    application = (uow.payments.application_id_for_allocation(payment_id, allocation_id)
                   if allocation_id else None)
    if amount > payout.share_now:
        uow.payments.update_payment_amount(payment_id, amount)
        if application:
            uow.payments.update_application_amount(application, amount)
        else:
            _apply(uow, payment_id, allocation_id, amount, tranche["created_at"])
        _set_tranche(uow, rebalance.scope, tranche["disbursement_id"], amount)
    elif amount:
        if application:
            uow.payments.update_application_amount(application, amount)
        uow.payments.update_payment_amount(payment_id, amount)
        _set_tranche(uow, rebalance.scope, tranche["disbursement_id"], amount)
    else:
        uow.payments.delete_applications(payment_id)
        _drop_tranche(uow, rebalance.scope, tranche["disbursement_id"])
        uow.payments.delete_payment(payment_id)
        payout.share = None
        _audit_payment(uow, rebalance, payment_id, "delete", actor, resident_id=rid,
                       amount_paise=0)
        return
    tranche["amount_paise"] = amount
    _audit_payment(uow, rebalance, payment_id, "resize", actor, resident_id=rid,
                   amount_paise=amount)


def _resize_advance(uow: UnitOfWork, rebalance: Rebalance, payout: _Payout, amount: int,
                    actor: str) -> None:
    """What a resident paid beyond their share, at ``amount`` -- gone at zero."""
    tranche = payout.advance
    payment_id = tranche["funding_payment_id"]
    if amount:
        uow.payments.update_payment_amount(payment_id, amount)
        _set_tranche(uow, rebalance.scope, tranche["disbursement_id"], amount)
        tranche["amount_paise"] = amount
    else:
        _drop_tranche(uow, rebalance.scope, tranche["disbursement_id"])
        uow.payments.delete_payment(payment_id)
        payout.advance = None
    _audit_payment(uow, rebalance, payment_id, "resize" if amount else "delete", actor,
                   resident_id=tranche["funding_resident_id"], amount_paise=amount)


def _new_share(uow: UnitOfWork, rebalance: Rebalance, rid: str, payout: _Payout,
               allocation_id: str, note: str, timestamp: str, actor: str) -> None:
    """Their own share, now open, paid through the kitty out of their payout."""
    like = payout.group["tranches"][0]
    payment_id = new_id("payment")
    uow.payments.insert_payment(
        payment_id=payment_id, payer_resident_id=rid, amount_paise=payout.to_share,
        payment_date=like["disbursed_at"], payment_kind="to_pool",
        expense_id=rebalance.expense_id, payee_or_provider=None,
        reference=like["disbursement_reference"], note=note,
        apartment_id=rebalance.apartment_id, created_at=like["created_at"], created_by=actor,
        payment_mode=None)
    _apply(uow, payment_id, allocation_id, payout.to_share, timestamp)
    name = payout.group["resident_name"]
    _add_tranche(uow, rebalance.scope, like,
                 disbursement_id=f"{SHARE_PREFIX[rebalance.scope]}{payment_id}",
                 amount_paise=payout.to_share, disbursed_by="pool", funding_resident_id=None,
                 funding_payment_id=None, note=f"{name}'s allotted share via House Kitty")
    _audit_payment(uow, rebalance, payment_id, "create", actor, resident_id=rid,
                   amount_paise=payout.to_share, payment_kind="to_pool")


def _new_advance(uow: UnitOfWork, rebalance: Rebalance, rid: str, payout: _Payout,
                 payee: str, note: str, timestamp: str, actor: str) -> None:
    """What they paid beyond their share, now that there is some: an advance the
    kitty owes them."""
    like = payout.group["tranches"][0]
    payment_id = new_id(_ADVANCE_PREFIX[rebalance.scope])
    uow.payments.insert_payment(
        payment_id=payment_id, payer_resident_id=rid, amount_paise=payout.to_advance,
        payment_date=like["disbursed_at"], payment_kind="provider_advance",
        expense_id=rebalance.expense_id, payee_or_provider=payee,
        reference=like["disbursement_reference"], note=note,
        apartment_id=rebalance.apartment_id, created_at=like["created_at"], created_by=actor,
        payment_mode=None)
    name = payout.group["resident_name"]
    _add_tranche(uow, rebalance.scope, like,
                 disbursement_id=new_id(_TRANCHE_PREFIX[rebalance.scope]),
                 amount_paise=payout.to_advance, disbursed_by="resident_advance",
                 funding_resident_id=rid, funding_payment_id=payment_id,
                 note=f"Extra above {name}'s share (reimbursable via House Kitty)")
    _audit_payment(uow, rebalance, payment_id, "create", actor, resident_id=rid,
                   amount_paise=payout.to_advance, payment_kind="provider_advance")

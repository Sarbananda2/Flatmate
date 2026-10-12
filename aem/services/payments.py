"""Payments, credits, and provider advances.

Four transaction types that are *not* interchangeable, and none of which is a
household expense:

``to_pool``
    A resident hands money to the household pool. It is applied to that
    resident's oldest outstanding charges first; anything left over stays an
    unapplied credit and carries forward until a charge exists to meet it.

    A pool payment may instead be **targeted** at one charge, when the resident
    is paying for something in particular ("this is my October rent"). The
    named charge is settled first, up to what is still open on it, and only the
    excess falls back to oldest-first. Without a target nothing changes.

``provider_advance``
    A resident pays an external provider on the household's behalf. This funds
    an expense; it does not create one. Link it to the expense it funds with
    ``expense_id`` and the reconciliation will credit the advance **instead of**
    the expense's ``payer_resident_id``, so the same rupee is never counted
    twice.

``refund``
    The pool pays money back out to a resident.

``adjustment``
    Retained from the prototype, where it meant "an extra amount due from this
    resident". New corrections should use a signed ``ledger_adjustment``
    instead, which carries a reason and a reversal trail.

A payment between two residents directly is recorded as the payer's
``to_pool`` payment plus a ``refund`` to the receiver, or as a pair of signed
ledger adjustments. Either way it moves money between accounts; it never
increases household expenses.

Corrections are reversals, never edits: ``reverse_payment`` marks the original
``reversed``, writes a mirrored ``reversal`` row that points back at it, and
removes the original's applications so the charges reopen. Both rows stay in
the ledger and both are excluded from net positions, which keeps the audit
trail complete while the money nets to nothing.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from ..engine.ledger import (
    Charge,
    PaymentAllocationResult,
    PaymentApplication,
    apply_payment_oldest_first,
)
from ..errors import ConflictError, NotFoundError, ValidationError
from ..ids import new_id, now_timestamp
from ..money import ensure_paise
from ..periods import normalize_month, parse_iso_date
from ..persistence import Database, UnitOfWork
from . import audit
from .common import (
    PAYMENT_KINDS,
    PAYMENT_MODES,
    get_apartment,
    get_resident,
    optional_text,
    paginate,
    resident_ids,
)
from .opening_balances import opening_charge_id, split_charge_id

APPLICABLE_KINDS = ("to_pool",)

#: Sort keys ``list_payments`` accepts; the repository owns the ORDER BY for each.
PAYMENT_SORTS = ("payment_date_desc", "payment_date_asc", "amount_desc", "amount_asc")


def _open_charges(uow: UnitOfWork, resident_id: str) -> tuple[list[Charge], dict[str, int]]:
    """Every charge belonging to one resident, with what is already applied.

    Ordering inputs are (due date, insertion sequence, allocation ID). The
    sequence is the allocation row's ``row_seq``, which is monotonic, so the
    oldest-first result never depends on a random identifier.

    An opening debt (a negative opening balance) is included as a charge due on
    its ``as_of_date`` with sequence 0, so it is settled before any ordinary
    charge due the same day. Its charge ID carries the ``opening:`` prefix.
    """
    rows = uow.payments.charges_for_resident(resident_id)
    already: dict[str, int] = {}
    for row in uow.payments.applied_by_allocation():
        already[row["allocation_id"]] = int(row["applied"])
    charges = [
        Charge(
            charge_id=row["allocation_id"],
            resident_id=resident_id,
            amount_paise=row["amount_paise"],
            due_date=date.fromisoformat(row["due_date"] or row["expense_date"]),
            description=row["description"],
            statement_id=row["statement_id"],
            sequence=int(row["seq"]),
        )
        for row in rows
    ]
    opening = uow.payments.opening_debt_for_resident(resident_id)
    if opening is not None:
        charge_id = opening_charge_id(opening["opening_balance_id"])
        charges.append(Charge(
            charge_id=charge_id, resident_id=resident_id,
            amount_paise=-int(opening["amount_paise"]),
            due_date=date.fromisoformat(opening["as_of_date"]),
            description="Opening balance", statement_id=None, sequence=0))
        already[charge_id] = int(opening["applied"])
    return charges, {cid: already.get(cid, 0) for cid in (c.charge_id for c in charges)}


def _charge_due_date(uow: UnitOfWork, charge_id: str) -> str | None:
    allocation_id, opening_balance_id = split_charge_id(charge_id)
    if opening_balance_id:
        return uow.payments.opening_balance_as_of_date(opening_balance_id)
    return uow.payments.allocation_due_date(allocation_id)


def _write_application(uow: UnitOfWork, *, payment_id: str, charge_id: str,
                       amount_paise: int, timestamp: str, accumulate: bool = False) -> None:
    """Record one payment application against a charge or an opening debt.

    With ``accumulate`` an existing application of the same payment to the same
    target is topped up rather than duplicated (used when re-applying credit).
    """
    allocation_id, opening_balance_id = split_charge_id(charge_id)
    if accumulate:
        if opening_balance_id:
            existing = uow.payments.application_id_for_opening_balance(
                payment_id, opening_balance_id)
        else:
            existing = uow.payments.application_id_for_allocation(payment_id, allocation_id)
        if existing:
            uow.payments.add_to_application(existing, amount_paise)
            return
    uow.payments.insert_application(
        payment_application_id=new_id("application"), payment_id=payment_id,
        allocation_id=allocation_id, opening_balance_id=opening_balance_id,
        amount_paise=amount_paise, charge_due_date=_charge_due_date(uow, charge_id),
        created_at=timestamp)


def _application_summary(charge_id: str, amount_paise: int) -> dict[str, Any]:
    allocation_id, opening_balance_id = split_charge_id(charge_id)
    summary: dict[str, Any] = {"allocation_id": allocation_id, "amount_paise": amount_paise}
    if opening_balance_id:
        summary["opening_balance_id"] = opening_balance_id
    return summary


def _apply_targeted(amount_paise: int, charges: list[Charge], already: dict[str, int],
                    target_allocation_id: str) -> PaymentAllocationResult:
    """Settle one named charge first, then the rest oldest-first.

    ``charges`` are the payer's own, so a target that is not among them belongs
    to somebody else or does not exist, and is refused rather than ignored. The
    target takes at most what is still open on it; a target that is already
    settled takes nothing and the whole payment is applied oldest-first.
    """
    target = next((c for c in charges if c.charge_id == target_allocation_id), None)
    if target is None:
        raise ValidationError(
            "The charge this payment is for does not belong to the resident paying.",
            code="target_charge_not_payers",
            details={"field": "target_allocation_id", "value": target_allocation_id})
    first = min(target.amount_paise - already.get(target.charge_id, 0), amount_paise)
    # With the target counted as settled this far, oldest-first cannot return to
    # it, so each charge still gets at most one application from this payment.
    rest = apply_payment_oldest_first(
        amount_paise - first, charges,
        {**already, target.charge_id: already.get(target.charge_id, 0) + first})
    head = [PaymentApplication(target.charge_id, first)] if first else []
    return PaymentAllocationResult(head + rest.applications, rest.unapplied_credit_paise)


def _record_payment(
    uow: UnitOfWork, *, apartment_id: str, payment_id: str, payer_resident_id: str,
    amount_paise: int, payment_date: str, payment_kind: str, payee_or_provider: str | None,
    reference: str | None, note: str | None, timestamp: str, expense_id: str | None,
    actor: str, target_allocation_id: str | None = None, payment_mode: str | None = None,
) -> dict[str, Any]:
    """Write one payment inside the caller's transaction."""
    if payment_mode is not None and payment_mode not in PAYMENT_MODES:
        raise ValidationError("Unsupported payment_mode.",
                              details={"field": "payment_mode",
                                       "allowed": list(PAYMENT_MODES)})
    if target_allocation_id and payment_kind not in APPLICABLE_KINDS:
        raise ValidationError(
            "Only a payment into the pool can be targeted at a charge.",
            details={"field": "target_allocation_id"})
    ensure_paise(amount_paise, "amount_paise", allow_zero=False)
    if payment_kind not in PAYMENT_KINDS:
        raise ValidationError("Unsupported payment_kind.",
                              details={"field": "payment_kind",
                                       "allowed": list(PAYMENT_KINDS)})
    if not isinstance(payment_id, str) or not payment_id.strip():
        raise ValidationError("payment_id is required.", details={"field": "payment_id"})
    try:
        parse_iso_date(payment_date, "payment_date")
    except ValidationError:
        raise ValidationError("payment_date must use YYYY-MM-DD format.",
                              details={"field": "payment_date"}) from None

    get_apartment(uow, apartment_id)
    residents = resident_ids(uow, apartment_id)
    if payer_resident_id not in residents:
        raise ValidationError("Payer must belong to this apartment.",
                              details={"field": "payer_resident_id",
                                       "value": payer_resident_id})
    if uow.payments.exists(payment_id.strip()):
        raise ConflictError("A payment with this ID already exists.",
                            code="payment_exists",
                            details={"payment_id": payment_id.strip()})
    if expense_id:
        if payment_kind != "provider_advance":
            raise ValidationError("Only provider advances may be linked to an expense.",
                                  details={"field": "expense_id"})
        expense = uow.payments.expense_for_link(expense_id)
        if not expense:
            raise NotFoundError("Linked expense not found.",
                                details={"expense_id": expense_id})
        owner = expense["apartment_id"]
        if owner is None and expense["statement_id"]:
            owner = uow.payments.statement_apartment_id(expense["statement_id"])
        if owner is None:
            owner = uow.payments.expense_apartment_id_from_allocations(expense_id)
        if owner != apartment_id:
            raise ValidationError("Linked expense must belong to this apartment.",
                                  details={"expense_id": expense_id})
        already_linked = uow.payments.linked_advances_paise(expense_id)
        if already_linked + amount_paise > expense["amount_paise"]:
            raise ValidationError(
                "Linked provider advances would exceed the expense they fund. "
                "An advance funds an expense; it cannot exceed it.",
                code="advance_exceeds_expense",
                details={"expense_id": expense_id,
                         "expense_amount_paise": expense["amount_paise"],
                         "already_linked_paise": already_linked,
                         "requested_paise": amount_paise})

    applied = None
    if payment_kind in APPLICABLE_KINDS:
        charges, already = _open_charges(uow, payer_resident_id)
        if target_allocation_id:
            applied = _apply_targeted(amount_paise, charges, already, target_allocation_id)
        else:
            applied = apply_payment_oldest_first(amount_paise, charges, already)

    uow.payments.insert_payment(
        payment_id=payment_id.strip(), payer_resident_id=payer_resident_id,
        amount_paise=amount_paise, payment_date=payment_date, payment_kind=payment_kind,
        expense_id=expense_id,
        payee_or_provider=optional_text(payee_or_provider, "payee_or_provider", max_length=200),
        reference=optional_text(reference, "reference", max_length=200),
        note=optional_text(note, "note"), apartment_id=apartment_id, created_at=timestamp,
        created_by=actor, payment_mode=payment_mode)
    if applied:
        for application in applied.applications:
            _write_application(uow, payment_id=payment_id.strip(),
                               charge_id=application.charge_id,
                               amount_paise=application.amount_paise, timestamp=timestamp)
    audit.record(uow, entity_type="payment", entity_id=payment_id.strip(),
                 action="create", actor=actor, apartment_id=apartment_id,
                 new_value={"payer_resident_id": payer_resident_id,
                            "amount_paise": amount_paise,
                            "payment_kind": payment_kind,
                            "payment_date": payment_date,
                            "linked_expense_id": expense_id,
                            "target_allocation_id": target_allocation_id,
                            "applications": [
                                _application_summary(a.charge_id, a.amount_paise)
                                for a in (applied.applications if applied else [])],
                            "unapplied_credit_paise":
                                applied.unapplied_credit_paise if applied else 0})
    return {
        "payment_id": payment_id.strip(),
        "amount_paise": amount_paise,
        "payment_kind": payment_kind,
        "applications": [
            _application_summary(a.charge_id, a.amount_paise)
            for a in (applied.applications if applied else [])
        ],
        "unapplied_credit_paise": applied.unapplied_credit_paise if applied else 0,
    }


def record_payment(
    db: Database, *, apartment_id: str, payment_id: str, payer_resident_id: str,
    amount_paise: int, payment_date: str, payment_kind: str = "to_pool",
    payee_or_provider: str | None = None, reference: str | None = None,
    note: str | None = None, created_at: str | None = None,
    expense_id: str | None = None, actor: str = "operator",
    target_allocation_id: str | None = None, payment_mode: str | None = None,
) -> dict[str, Any]:
    """Record a payment and, for pool payments, apply it oldest-charge-first.

    ``target_allocation_id`` names one of the payer's own charges to settle
    before anything else, for a payment made for something in particular. It
    takes up to what is still open on that charge; any excess is applied
    oldest-first across the payer's other charges as usual.

    Returns the applications that were created and any amount left over as an
    unapplied credit. Nothing is estimated: the applications written here are
    exactly the ones reported.
    """
    with db.transaction() as uow:
        return _record_payment(
            uow, apartment_id=apartment_id, payment_id=payment_id,
            payer_resident_id=payer_resident_id, amount_paise=amount_paise,
            payment_date=payment_date, payment_kind=payment_kind,
            payee_or_provider=payee_or_provider, reference=reference, note=note,
            timestamp=created_at or now_timestamp(), expense_id=expense_id, actor=actor,
            target_allocation_id=target_allocation_id, payment_mode=payment_mode)


def settle_peer_to_peer(
    db: Database, *, apartment_id: str, from_resident_id: str, to_resident_id: str,
    amount_paise: int, payment_date: str, reference: str | None = None,
    note: str | None = None, settlement_id: str | None = None, actor: str = "operator",
) -> dict[str, Any]:
    """Record one resident paying another directly, as a single unit of work.

    The money never touches the pool in real life, but the ledger has no
    resident-to-resident entry: it is the payer's ``to_pool`` payment plus a
    ``refund`` to the receiver, for the same amount on the same date. Both rows
    are written in one transaction, so a settlement can never be half recorded.
    The payer's leg is applied to their oldest charges like any pool payment.

    Each leg is an ordinary payment afterwards. Undoing a settlement means
    reversing both.
    """
    if from_resident_id == to_resident_id:
        raise ValidationError("A resident cannot settle with themselves.",
                              details={"field": "to_resident_id"})
    settlement_id = (settlement_id or new_id("p2p")).strip()
    if not settlement_id:
        raise ValidationError("settlement_id is required.", details={"field": "settlement_id"})
    timestamp = now_timestamp()
    with db.transaction() as uow:
        get_apartment(uow, apartment_id)
        payer = get_resident(uow, from_resident_id, apartment_id)
        receiver = get_resident(uow, to_resident_id, apartment_id)
        shared = dict(apartment_id=apartment_id, amount_paise=amount_paise,
                      payment_date=payment_date, reference=reference, timestamp=timestamp,
                      expense_id=None, actor=actor)
        label = optional_text(note, "note")
        paid = _record_payment(
            uow, payment_id=f"{settlement_id}-paid", payer_resident_id=from_resident_id,
            payment_kind="to_pool", payee_or_provider=receiver["display_name"],
            note=label or f"Direct settlement to {receiver['display_name']} ({settlement_id})",
            **shared)
        received = _record_payment(
            uow, payment_id=f"{settlement_id}-received", payer_resident_id=to_resident_id,
            payment_kind="refund", payee_or_provider=payer["display_name"],
            note=label or f"Direct settlement from {payer['display_name']} ({settlement_id})",
            **shared)
        audit.record(uow, entity_type="p2p_settlement", entity_id=settlement_id,
                     action="create", actor=actor, apartment_id=apartment_id,
                     new_value={"from_resident_id": from_resident_id,
                                "to_resident_id": to_resident_id,
                                "amount_paise": amount_paise,
                                "payment_date": payment_date,
                                "payment_ids": [paid["payment_id"], received["payment_id"]]})
    return {
        "settlement_id": settlement_id,
        "from_resident_id": from_resident_id,
        "to_resident_id": to_resident_id,
        "amount_paise": amount_paise,
        "payment_date": payment_date,
        "paid": paid,
        "received": received,
    }


def reapply_unapplied_credit(db: Database, *, apartment_id: str, resident_id: str,
                             actor: str = "operator") -> dict[str, Any]:
    """Apply a resident's existing unapplied credit to charges that now exist.

    This is an explicit operator action, never automatic: an overpayment stays a
    visible credit until somebody decides to use it. Credits are consumed
    oldest-payment-first, and each one is applied oldest-charge-first.
    """
    with db.transaction() as uow:
        get_apartment(uow, apartment_id)
        get_resident(uow, resident_id, apartment_id)
        rows = uow.payments.pool_payments_with_applied(resident_id)
        created: list[dict[str, Any]] = []
        total_applied = 0
        for row in rows:
            remaining = int(row["amount_paise"]) - int(row["applied"])
            if remaining <= 0:
                continue
            charges, already = _open_charges(uow, resident_id)
            result = apply_payment_oldest_first(remaining, charges, already)
            for application in result.applications:
                _write_application(uow, payment_id=row["payment_id"],
                                   charge_id=application.charge_id,
                                   amount_paise=application.amount_paise,
                                   timestamp=now_timestamp(), accumulate=True)
                created.append({"payment_id": row["payment_id"],
                                **_application_summary(application.charge_id,
                                                       application.amount_paise)})
                total_applied += application.amount_paise
        if created:
            audit.record(uow, entity_type="resident", entity_id=resident_id,
                         action="apply_credit", actor=actor, apartment_id=apartment_id,
                         new_value={"applications": created,
                                    "total_applied_paise": total_applied})
        return {"resident_id": resident_id, "applications": created,
                "total_applied_paise": total_applied}


def reverse_payment(db: Database, payment_id: str, *, reason: str, actor: str = "operator",
                    apartment_id: str | None = None,
                    reversal_payment_id: str | None = None,
                    reversal_date: str | None = None) -> dict[str, Any]:
    """Reverse a payment through an auditable mirror entry.

    The original row is kept and marked ``reversed``; a ``reversal`` row records
    the correction and points at the original. Both are excluded from net
    positions so the money nets to zero, and the charges the original settled
    reopen.
    """
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("A reason is required to reverse a payment.",
                              details={"field": "reason"})
    with db.transaction() as uow:
        original = uow.payments.get(payment_id)
        if original is None:
            raise NotFoundError("Payment not found.", details={"payment_id": payment_id})
        if apartment_id is not None and original["apartment_id"] not in (None, apartment_id):
            raise ValidationError("Payment must belong to this apartment.",
                                  details={"payment_id": payment_id})
        rent_cycle = uow.payments.rent_cycle_funded_by(payment_id)
        if rent_cycle is not None:
            # The cycle is frozen once the landlord is paid, and it names this
            # advance as how. Reversing it would leave the two disagreeing.
            raise ConflictError(
                f"This advance is the recorded landlord payout for {rent_cycle['settlement_month']} "
                "rent and cannot be reversed. If the wrong resident was credited, correct it "
                "with a pair of signed ledger adjustments.",
                code="rent_funding_payment",
                details={"payment_id": payment_id, "cycle_id": rent_cycle["cycle_id"]})
        if original["status"] != "active":
            raise ConflictError(
                f"This payment is already {original['status']} and cannot be reversed again.",
                code="payment_not_active",
                details={"payment_id": payment_id, "status": original["status"]})
        reversal_id = (reversal_payment_id or f"{payment_id}-reversal").strip()
        if uow.payments.exists(reversal_id):
            reversal_id = new_id("reversal")
        timestamp = now_timestamp()
        released = uow.payments.released_applications(payment_id)
        uow.payments.delete_applications(payment_id)
        uow.payments.mark_reversed(payment_id)
        uow.payments.insert_reversal(
            payment_id=reversal_id, payer_resident_id=original["payer_resident_id"],
            amount_paise=original["amount_paise"],
            payment_date=reversal_date or timestamp[:10],
            payment_kind=original["payment_kind"], expense_id=original["expense_id"],
            payee_or_provider=original["payee_or_provider"], reference=original["reference"],
            note=f"Reversal of {payment_id}: {reason}", apartment_id=original["apartment_id"],
            reverses_payment_id=payment_id, created_at=timestamp, created_by=actor)
        audit.record(uow, entity_type="payment", entity_id=payment_id, action="reverse",
                     actor=actor, apartment_id=original["apartment_id"], reason=reason,
                     old_value={"status": "active",
                                "applications": released,
                                "amount_paise": original["amount_paise"]},
                     new_value={"status": "reversed", "reversal_payment_id": reversal_id})
        return {"payment_id": payment_id, "reversal_payment_id": reversal_id,
                "released_applications": released, "reason": reason}


def read_payment(db: Database, payment_id: str) -> dict[str, Any]:
    with db.read() as uow:
        payment = uow.payments.get(payment_id)
        if payment is None:
            raise NotFoundError("Payment not found.", details={"payment_id": payment_id})
        payment["payer_display_name"] = uow.payments.payer_display_name(
            payment["payer_resident_id"])
        payment["applications"] = uow.payments.applications_for_payment(payment_id)
        applied = sum(row["amount_paise"] for row in payment["applications"])
        payment["applied_paise"] = applied
        payment["unapplied_paise"] = (payment["amount_paise"] - applied
                                      if payment["payment_kind"] == "to_pool"
                                      and payment["status"] == "active" else 0)
        payment["linked_expense"] = uow.payments.linked_expense_summary(payment["expense_id"]) \
            if payment["expense_id"] else None
        payment["reversal"] = uow.payments.reversal_of(payment_id)
        return payment


def list_payments(
    db: Database, *, apartment_id: str, resident_id: str | None = None,
    payment_kind: str | None = None, status: str | None = None,
    settlement_period: str | None = None, date_from: str | None = None,
    date_to: str | None = None, expense_id: str | None = None,
    unapplied_only: bool = False, search: str | None = None,
    limit: int | None = 50, offset: int | None = 0, sort: str = "payment_date_desc",
) -> dict[str, Any]:
    limit, offset = paginate(limit, offset)
    filters: dict[str, Any] = {
        "apartment_id": apartment_id, "resident_id": resident_id, "payment_kind": None,
        "status": status, "settlement_period": None, "date_from": None, "date_to": None,
        "expense_id": expense_id, "unapplied_only": bool(unapplied_only), "search": None,
    }
    if payment_kind:
        if payment_kind not in PAYMENT_KINDS:
            raise ValidationError("Unsupported payment_kind.",
                                  details={"field": "payment_kind"})
        filters["payment_kind"] = payment_kind
    if settlement_period:
        filters["settlement_period"] = normalize_month(settlement_period, "settlement_period")
    if date_from:
        filters["date_from"] = parse_iso_date(date_from, "date_from").isoformat()
    if date_to:
        filters["date_to"] = parse_iso_date(date_to, "date_to").isoformat()
    if search:
        filters["search"] = search.strip()

    if sort not in PAYMENT_SORTS:
        raise ValidationError(f"Unsupported sort: {sort}",
                              details={"field": "sort", "allowed": sorted(PAYMENT_SORTS)})
    with db.read() as uow:
        get_apartment(uow, apartment_id)
        total = uow.payments.count_payments(**filters)
        total_amount = uow.payments.total_amount_paise(**filters)
        rows = uow.payments.list_payments(sort=sort, limit=limit, offset=offset, **filters)
    for row in rows:
        row["unapplied_paise"] = (row["amount_paise"] - row["applied_paise"]
                                  if row["payment_kind"] == "to_pool"
                                  and row["status"] == "active" else 0)
    return {"items": rows, "total": total, "total_amount_paise": total_amount,
            "limit": limit, "offset": offset}


def unapplied_credits(db: Database, apartment_id: str) -> list[dict[str, Any]]:
    """Every pool payment with money left over, by resident.

    Credits are reported from the records themselves -- payment amount minus the
    applications written against it -- so a credit can never be lost between
    months or quietly absorbed.
    """
    with db.read() as uow:
        get_apartment(uow, apartment_id)
        return uow.payments.unapplied_credits(apartment_id)


def provider_advances(db: Database, apartment_id: str) -> list[dict[str, Any]]:
    """Provider advances with their link status.

    ``is_linked`` is the field that decides whether the funded expense's
    ``payer_resident_id`` is used or ignored, so it is reported explicitly
    rather than inferred by the reader.
    """
    with db.read() as uow:
        get_apartment(uow, apartment_id)
        rows = uow.payments.provider_advances(apartment_id)
    for row in rows:
        row["is_linked"] = row["expense_id"] is not None
    return rows


def resident_charge_ledger(db: Database, *, apartment_id: str, resident_id: str,
                           include_settled: bool = True) -> list[dict[str, Any]]:
    """One resident's charges, oldest first, with how much of each is settled."""
    with db.read() as uow:
        get_apartment(uow, apartment_id)
        get_resident(uow, resident_id, apartment_id)
        rows = uow.payments.charge_ledger_for_resident(resident_id)
        opening = uow.payments.opening_debt_ledger_row(resident_id)
    for row in rows:
        row["is_opening_balance"] = False
    if opening:
        rows.insert(0, {
            "allocation_id": None, "opening_balance_id": opening["opening_balance_id"],
            "is_opening_balance": True, "amount_paise": opening["amount_paise"],
            "expense_id": None, "description": "Opening balance",
            "expense_date": opening["as_of_date"], "due_date": opening["as_of_date"],
            "settlement_period": opening["as_of_date"][:7], "service_period": None,
            "statement_id": None, "statement_status": None, "note": opening["note"],
            "settled_paise": opening["settled_paise"]})
    for row in rows:
        row["outstanding_paise"] = row["amount_paise"] - row["settled_paise"]
    if not include_settled:
        rows = [row for row in rows if row["outstanding_paise"] > 0]
    return rows

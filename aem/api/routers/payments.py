"""Payments, credits, provider advances, and ledger adjustments."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Path, Query

from ...ids import new_id
from ...errors import AemError
from ...schemas import (
    AdjustmentCreate,
    AdjustmentReverse,
    CreditApplyRequest,
    PaymentCreate,
    PaymentReverse,
    PeerSettlementCreate,
)
from ...services import adjustments, payments, pool_statements
from ..deps import ApartmentId, AppSettings, Db, csv_response, to_http

router = APIRouter(prefix="/api", tags=["payments"])

MONTH = r"^\d{4}-(0[1-9]|1[0-2])$"
DATE = r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$"


@router.get("/payments", summary="List and filter payments")
def list_payments(
    db: Db, apartment_id: ApartmentId,
    resident_id: Annotated[str | None, Query()] = None,
    payment_kind: Annotated[str | None, Query()] = None,
    status: Annotated[str | None, Query()] = None,
    settlement_period: Annotated[str | None, Query(pattern=MONTH)] = None,
    date_from: Annotated[str | None, Query(pattern=DATE)] = None,
    date_to: Annotated[str | None, Query(pattern=DATE)] = None,
    expense_id: Annotated[str | None, Query()] = None,
    unapplied_only: Annotated[bool, Query()] = False,
    search: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort: Annotated[str, Query()] = "payment_date_desc",
    view: Annotated[str, Query()] = "records",
) -> dict[str, Any]:
    """The payment records as stored, or with ``view=ledger`` the All Payments
    table: active payments, a resident's payout to the landlord or a provider as
    one payment of the whole amount, and the House Kitty's own payouts as
    ``kitty_payout``. Every ledger row carries ``description``, ``paid_by_name``
    and ``paid_to_name``."""
    try:
        return payments.list_payments(
            db, apartment_id=apartment_id, resident_id=resident_id,
            payment_kind=payment_kind, status=status, settlement_period=settlement_period,
            date_from=date_from, date_to=date_to, expense_id=expense_id,
            unapplied_only=unapplied_only, search=search, limit=limit, offset=offset,
            sort=sort, view=view)
    except AemError as exc:
        raise to_http(exc) from exc


@router.get("/payments/pools/statement",
            summary="Passbooks for the household pool and the landlord")
def payment_pools_statement(db: Db, apartment_id: ApartmentId) -> dict[str, Any]:
    """Every movement into and out of the household pool, and everything paid
    to and returned by the landlord, each with a running balance, newest first.
    Built from the records; nothing is stored."""
    try:
        return pool_statements.get_payment_pools_statement(db, apartment_id)
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/payments", status_code=201, summary="Record a payment")
def create_payment(payload: PaymentCreate, db: Db, apartment_id: ApartmentId,
                   settings: AppSettings) -> dict[str, Any]:
    """Record a payment and, for a pool payment, apply it oldest-charge-first.

    A payment is never a household expense. Link a ``provider_advance`` to the
    expense it funds so the expense's payer field is not credited as well.
    """
    data = payload.model_dump(exclude_none=True)
    payment_id = data.pop("payment_id", None) or new_id("payment")
    try:
        result = payments.record_payment(db, apartment_id=apartment_id,
                                         payment_id=payment_id,
                                         actor=settings.operator_name, **data)
        return {**result, "payment": payments.read_payment(db, payment_id)}
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/payments/settle-p2p", status_code=201,
             summary="Record one resident paying another directly")
def settle_peer_to_peer(payload: PeerSettlementCreate, db: Db,
                        apartment_id: ApartmentId, settings: AppSettings) -> dict[str, Any]:
    """Settle a suggested transfer between two residents in one step.

    Writes the payer's pool payment and the receiver's refund together, or
    neither. Neither row is a household expense.
    """
    try:
        return payments.settle_peer_to_peer(
            db, apartment_id=apartment_id, actor=settings.operator_name,
            **payload.model_dump(exclude_none=True))
    except AemError as exc:
        raise to_http(exc) from exc


@router.get("/payments/{payment_id}", summary="Read one payment with its allocations")
def read_payment(db: Db, payment_id: str = Path()) -> dict[str, Any]:
    """Shows precisely which charges this payment settles, and what is left over."""
    try:
        return payments.read_payment(db, payment_id)
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/payments/{payment_id}/reverse", summary="Reverse a payment")
def reverse_payment(payload: PaymentReverse, db: Db, apartment_id: ApartmentId,
                    settings: AppSettings, payment_id: str = Path()) -> dict[str, Any]:
    """Keep the original, mark it reversed, and write a mirrored reversal entry.

    The charges it settled reopen. Both rows stay in the ledger and both are
    excluded from net positions, so the money nets to nothing and the audit
    trail stays complete.
    """
    try:
        return payments.reverse_payment(db, payment_id, apartment_id=apartment_id,
                                        actor=settings.operator_name,
                                        **payload.model_dump(exclude_none=True))
    except AemError as exc:
        raise to_http(exc) from exc


@router.get("/credits", summary="Unapplied credits and overpayments")
def list_credits(db: Db, apartment_id: ApartmentId) -> dict[str, Any]:
    """Every pool payment with money left over. A credit stays visible and
    carries forward until the operator applies it."""
    try:
        rows = payments.unapplied_credits(db, apartment_id)
    except AemError as exc:
        raise to_http(exc) from exc
    by_resident: dict[str, int] = {}
    for row in rows:
        by_resident[row["payer_resident_id"]] = by_resident.get(
            row["payer_resident_id"], 0) + row["unapplied_paise"]
    return {"items": rows, "total": len(rows),
            "total_paise": sum(row["unapplied_paise"] for row in rows),
            "by_resident_paise": by_resident}


@router.post("/credits/apply", summary="Apply a resident's credit to open charges")
def apply_credit(payload: CreditApplyRequest, db: Db, apartment_id: ApartmentId,
                 settings: AppSettings) -> dict[str, Any]:
    """An explicit operator action. Credits are never applied automatically:
    an overpayment stays a visible credit until somebody decides to use it."""
    try:
        return payments.reapply_unapplied_credit(
            db, apartment_id=apartment_id, resident_id=payload.resident_id,
            actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc


@router.get("/provider-advances", summary="Provider advances and their link status")
def list_provider_advances(db: Db, apartment_id: ApartmentId) -> dict[str, Any]:
    """``is_linked`` decides whether the funded expense's payer field is used or
    ignored, so it is reported rather than left to be inferred."""
    try:
        rows = payments.provider_advances(db, apartment_id)
    except AemError as exc:
        raise to_http(exc) from exc
    return {"items": rows, "total": len(rows),
            "total_paise": sum(row["amount_paise"] for row in rows
                               if row["status"] == "active"),
            "unlinked_count": sum(1 for row in rows
                                  if not row["is_linked"] and row["status"] == "active")}


@router.get("/adjustments", summary="List ledger adjustments")
def list_adjustments(db: Db, apartment_id: ApartmentId,
                     resident_id: Annotated[str | None, Query()] = None,
                     statement_id: Annotated[str | None, Query()] = None,
                     limit: Annotated[int, Query(ge=1, le=500)] = 100,
                     offset: Annotated[int, Query(ge=0)] = 0) -> dict[str, Any]:
    try:
        return adjustments.list_adjustments(db, apartment_id=apartment_id,
                                            resident_id=resident_id,
                                            statement_id=statement_id,
                                            limit=limit, offset=offset)
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/adjustments", status_code=201, summary="Record a signed ledger adjustment")
def create_adjustment(payload: AdjustmentCreate, db: Db, apartment_id: ApartmentId,
                      settings: AppSettings) -> dict[str, Any]:
    """Positive credits the resident; negative debits them. This is the only way
    to correct a closed statement, and it never rewrites the original."""
    data = payload.model_dump(exclude_none=True)
    try:
        adjustment_id = adjustments.record_adjustment(
            db, apartment_id=apartment_id, created_by=settings.operator_name, **data)
    except AemError as exc:
        raise to_http(exc) from exc
    return {"adjustment_id": adjustment_id}


@router.post("/adjustments/{adjustment_id}/reverse", summary="Reverse an adjustment")
def reverse_adjustment(payload: AdjustmentReverse, db: Db,
                       apartment_id: ApartmentId, settings: AppSettings,
                       adjustment_id: str = Path()) -> dict[str, Any]:
    """Adjustments are append-only; a mistake is corrected by its exact negative."""
    try:
        new_adjustment_id = adjustments.reverse_adjustment(
            db, adjustment_id, apartment_id=apartment_id, reason=payload.reason,
            created_by=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc
    return {"adjustment_id": new_adjustment_id, "reverses_adjustment_id": adjustment_id}


@router.get("/payments/export/csv", summary="Export payments as CSV")
def export_payments(db: Db, apartment_id: ApartmentId):
    """The All Payments table, as the ``ledger`` view lists it."""
    try:
        rows = payments.list_payments(db, apartment_id=apartment_id, limit=500,
                                      view="ledger")["items"]
    except AemError as exc:
        raise to_http(exc) from exc
    return csv_response(rows, "payments")

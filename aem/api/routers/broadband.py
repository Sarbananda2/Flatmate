"""Household services: Broadband & WiFi on a fixed monthly plan."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Path, Query

from ...errors import AemError
from ...schemas import (
    MONTH_PATTERN,
    BroadbandPaymentCreate,
    BroadbandPayoutRequest,
    BroadbandReimburseRequest,
    BroadbandSetupRequest,
)
from ...services import broadband
from ..deps import ApartmentId, AppSettings, Db, to_http

router = APIRouter(prefix="/api/services/broadband", tags=["services"])

Month = Annotated[str, Query(pattern=MONTH_PATTERN, description="YYYY-MM")]
OptionalMonth = Annotated[str | None, Query(pattern=MONTH_PATTERN,
                                            description="YYYY-MM; the current month if left out")]


@router.get("", summary="Broadband, its plan and a month's bill")
def get_broadband(db: Db, apartment_id: ApartmentId, month: Month) -> dict[str, Any]:
    """``service`` is null until broadband is set up. ``month`` is what the month
    bills: the fixed rate split equally, who has paid into the kitty, the
    payouts to the ISP, and what the kitty owes back to whoever paid it."""
    try:
        return broadband.get_overview(db, apartment_id, month)
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/setup", summary="Set up or change the broadband plan")
def setup_broadband(payload: BroadbandSetupRequest, db: Db, apartment_id: ApartmentId,
                    settings: AppSettings, month: OptionalMonth = None) -> dict[str, Any]:
    """Only the fields sent change. A new rate moves the open month's bill with it."""
    try:
        return broadband.setup_broadband(db, apartment_id,
                                         payload.model_dump(exclude_unset=True),
                                         settlement_month=month, actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/record-payment", status_code=201,
             summary="Record a resident paying their broadband share into the kitty")
def record_payment(payload: BroadbandPaymentCreate, db: Db, apartment_id: ApartmentId,
                   settings: AppSettings) -> dict[str, Any]:
    """Settles their share of the month's bill first; anything over goes to their
    other charges, oldest first."""
    try:
        return broadband.record_payment(db, apartment_id, payload.model_dump(),
                                        actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/disburse-payout", status_code=201,
             summary="Record a payment to the ISP towards a month's bill")
def disburse_payout(payload: BroadbandPayoutRequest, db: Db, apartment_id: ApartmentId,
                    settings: AppSettings) -> dict[str, Any]:
    """From the House Kitty, or by a resident from their own account: whatever
    they paid beyond their own share the kitty pays back to them."""
    try:
        return broadband.disburse_payout(db, apartment_id, payload.model_dump(),
                                         actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/reimburse-from-kitty", status_code=201,
             summary="Pay back, from the kitty, a resident who paid the ISP")
def reimburse_from_kitty(payload: BroadbandReimburseRequest, db: Db,
                         apartment_id: ApartmentId, settings: AppSettings) -> dict[str, Any]:
    """A refund out of what flatmates paid in for the bill: never more than the
    kitty holds for it, nor more than the resident is still owed."""
    try:
        return broadband.reimburse_from_kitty(db, apartment_id, payload.model_dump(),
                                              actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc


@router.delete("/payments/{payment_id}",
               summary="Delete a broadband payment or reimbursement recorded by mistake")
def delete_payment(db: Db, apartment_id: ApartmentId, settings: AppSettings,
                   payment_id: str = Path()) -> dict[str, Any]:
    """A resident's payment into the kitty towards a broadband bill, or the
    kitty's reimbursement for one, deleted outright while the bill's month is
    open. Returns that month's overview."""
    try:
        return broadband.delete_payment(db, apartment_id, payment_id,
                                        actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc


@router.delete("/disbursements/{disbursement_id}",
               summary="Delete a payout to the ISP recorded by mistake")
def delete_disbursement(db: Db, apartment_id: ApartmentId, settings: AppSettings,
                        disbursement_id: str = Path()) -> dict[str, Any]:
    """Deleted outright while the bill's month is open, with whatever was
    recorded with it. Refused while the kitty has reimbursed its advance.
    Returns that month's overview."""
    try:
        return broadband.delete_disbursement(db, apartment_id, disbursement_id,
                                             actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc

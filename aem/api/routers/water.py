"""Household services: the water supply on a fixed monthly contract."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query

from ...errors import AemError
from ...schemas import (
    MONTH_PATTERN,
    WaterPaymentCreate,
    WaterPayoutRequest,
    WaterReimburseRequest,
    WaterSetupRequest,
)
from ...services import water
from ..deps import ApartmentId, AppSettings, Db, to_http

router = APIRouter(prefix="/api/services/water", tags=["services"])

Month = Annotated[str, Query(pattern=MONTH_PATTERN, description="YYYY-MM")]
OptionalMonth = Annotated[str | None, Query(pattern=MONTH_PATTERN,
                                            description="YYYY-MM; the current month if left out")]


@router.get("", summary="The water supply and a month's bill")
def get_water(db: Db, apartment_id: ApartmentId, month: Month) -> dict[str, Any]:
    """``service`` is null until the water supply is set up. ``month`` is what the
    month bills: the fixed rate split equally, who has paid into the kitty, and
    the payouts to the supplier."""
    try:
        return water.get_overview(db, apartment_id, month)
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/setup", summary="Set up or change the water contract")
def setup_water(payload: WaterSetupRequest, db: Db, apartment_id: ApartmentId,
                settings: AppSettings, month: OptionalMonth = None) -> dict[str, Any]:
    """Only the fields sent change. A new rate moves the open month's bill with it."""
    try:
        return water.setup_water(db, apartment_id, payload.model_dump(exclude_unset=True),
                                 settlement_month=month, actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/record-payment", status_code=201,
             summary="Record a resident paying their water share into the kitty")
def record_payment(payload: WaterPaymentCreate, db: Db, apartment_id: ApartmentId,
                   settings: AppSettings) -> dict[str, Any]:
    """Settles their share of the month's bill first; anything over goes to their
    other charges, oldest first."""
    try:
        return water.record_payment(db, apartment_id, payload.model_dump(),
                                    actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/disburse-payout", status_code=201,
             summary="Record a payment to the water supplier towards a month's bill")
def disburse_payout(payload: WaterPayoutRequest, db: Db, apartment_id: ApartmentId,
                    settings: AppSettings) -> dict[str, Any]:
    """From the House Kitty, or by a resident from their own account: whatever
    they paid beyond their own share the kitty pays back to them."""
    try:
        return water.disburse_payout(db, apartment_id, payload.model_dump(),
                                     actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/reimburse-from-kitty", status_code=201,
             summary="Pay back, from the kitty, a resident who paid the supplier")
def reimburse_from_kitty(payload: WaterReimburseRequest, db: Db, apartment_id: ApartmentId,
                         settings: AppSettings) -> dict[str, Any]:
    """A refund out of what flatmates paid in for the bill: never more than the
    kitty holds for it, nor more than the resident is still owed."""
    try:
        return water.reimburse_from_kitty(db, apartment_id, payload.model_dump(),
                                          actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc

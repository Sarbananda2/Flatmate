"""Household services: furniture rental (Furlenco, Rentomojo and the like)."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Path, Query, Response

from ...errors import AemError
from ...schemas import (
    MONTH_PATTERN,
    FurnitureGenerateRequest,
    FurnitureItemCreate,
    FurnitureItemUpdate,
    FurniturePaymentCreate,
    FurniturePayoutRequest,
    FurnitureReimburseRequest,
    FurnitureSetupRequest,
)
from ...services import furniture_rental
from ..deps import ApartmentId, AppSettings, Db, to_http

router = APIRouter(prefix="/api/services/furniture", tags=["services"])

Month = Annotated[str, Query(pattern=MONTH_PATTERN, description="YYYY-MM")]
OptionalMonth = Annotated[str | None, Query(pattern=MONTH_PATTERN,
                                            description="YYYY-MM; the current month if left out")]
ItemId = Annotated[str, Path(min_length=1, max_length=64)]


@router.get("", summary="The furniture rental subscription and a month's bill")
def get_furniture(db: Db, apartment_id: ApartmentId, month: Month) -> dict[str, Any]:
    """``service`` is null until furniture rental is set up. ``month`` is what
    the month bills: the items in the flat, the common share, each resident's
    own items, and the expense once it is recorded."""
    try:
        return furniture_rental.get_overview(db, apartment_id, month)
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/setup", summary="Set up or update the furniture rental subscription")
def setup_furniture(payload: FurnitureSetupRequest, db: Db, apartment_id: ApartmentId,
                    settings: AppSettings, month: OptionalMonth = None) -> dict[str, Any]:
    try:
        return furniture_rental.setup_furniture_rental(
            db, apartment_id, payload.model_dump(exclude_unset=True), settlement_month=month,
            actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/items", status_code=201, summary="Add a rented item")
def add_item(payload: FurnitureItemCreate, db: Db, apartment_id: ApartmentId,
             settings: AppSettings) -> dict[str, Any]:
    try:
        return furniture_rental.add_item(db, apartment_id, payload.model_dump(),
                                         actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc


@router.patch("/items/{item_id}", summary="Change a rented item, or record its return")
def update_item(payload: FurnitureItemUpdate, db: Db, apartment_id: ApartmentId,
                settings: AppSettings, item_id: ItemId) -> dict[str, Any]:
    try:
        return furniture_rental.update_item(db, apartment_id, item_id,
                                            payload.model_dump(exclude_unset=True),
                                            actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc


@router.delete("/items/{item_id}", status_code=204,
               summary="Remove an item entered by mistake")
def delete_item(db: Db, apartment_id: ApartmentId, settings: AppSettings,
                item_id: ItemId) -> Response:
    """To stop paying for an item, return it instead: a returned item keeps its
    history."""
    try:
        furniture_rental.delete_item(db, apartment_id, item_id, actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc
    return Response(status_code=204)


@router.post("/generate-cycle", status_code=201, summary="Record a month's furniture bill")
def generate_cycle(payload: FurnitureGenerateRequest, db: Db, apartment_id: ApartmentId,
                   settings: AppSettings) -> dict[str, Any]:
    """One expense for the month: common items split equally between the
    residents taking part, each resident's own items charged to them. A month is
    billed once."""
    try:
        return furniture_rental.generate_month(
            db, apartment_id, payload.settlement_month, payload.excluded_resident_ids,
            actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/record-payment", status_code=201,
             summary="Record a resident paying their share of a month's bill into the kitty")
def record_payment(payload: FurniturePaymentCreate, db: Db, apartment_id: ApartmentId,
                   settings: AppSettings) -> dict[str, Any]:
    """Settles their share of the month's bill first; anything over goes to their
    other charges, oldest first. The bill must be recorded first."""
    try:
        return furniture_rental.record_furniture_payment(
            db, apartment_id, payload.settlement_month, payload.resident_id,
            amount_paise=payload.amount_paise, payment_date=payload.payment_date,
            payment_mode=payload.payment_mode, reference=payload.reference, note=payload.note,
            actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/mark-paid-by-resident", status_code=201,
             summary="Record a resident paying the provider a month's bill")
def mark_paid_by_resident(payload: FurniturePayoutRequest, db: Db, apartment_id: ApartmentId,
                          settings: AppSettings) -> dict[str, Any]:
    """A provider advance linked to the month's bill: the resident is credited
    the whole bill and still owes their own share, so the kitty owes them the
    rest. The bill must be recorded first, and is paid once, in full."""
    try:
        return furniture_rental.mark_paid_by_resident(
            db, apartment_id, payload.settlement_month, payload.payer_resident_id,
            payment_date=payload.payment_date, amount_paise=payload.amount_paise,
            reference=payload.reference, notes=payload.notes, actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/reimburse-from-kitty", status_code=201,
             summary="Pay back the resident who paid a month's bill, from the kitty")
def reimburse_from_kitty(payload: FurnitureReimburseRequest, db: Db, apartment_id: ApartmentId,
                         settings: AppSettings) -> dict[str, Any]:
    """A refund from the kitty, out of what flatmates paid in for the bill. It
    can be no more than that, less what has been paid back already, nor more
    than the resident is still owed."""
    try:
        return furniture_rental.reimburse_from_kitty(
            db, apartment_id, payload.settlement_month, payload.recipient_resident_id,
            amount_paise=payload.amount_paise, payment_date=payload.payment_date,
            reference=payload.reference, notes=payload.notes, actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/retire", summary="End the furniture rental subscription")
def retire_furniture(db: Db, apartment_id: ApartmentId, settings: AppSettings,
                     month: OptionalMonth = None) -> dict[str, Any]:
    """Every item must have been returned first."""
    try:
        return furniture_rental.retire_furniture_rental(db, apartment_id, settlement_month=month,
                                                        actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc

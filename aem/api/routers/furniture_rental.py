"""Household services: furniture rental (Furlenco, Rentomojo and the like)."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Path, Query, Response

from ...errors import AemError
from ...schemas import (
    MONTH_PATTERN,
    FurnitureDisburseRequest,
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


@router.post("/disburse-payout", status_code=201,
             summary="Record a payment to the provider towards a month's bill")
def disburse_payout(payload: FurnitureDisburseRequest, db: Db, apartment_id: ApartmentId,
                    settings: AppSettings) -> dict[str, Any]:
    """From the House Kitty, or a resident advance: a provider advance linked to
    the month's bill, crediting the resident while they still owe their own
    share. The bill must be recorded first; it can be paid in tranches, which
    never add up to more than the bill."""
    try:
        return furniture_rental.record_provider_payout(
            db, apartment_id, payload.settlement_month,
            disbursement_mode=payload.disbursement_mode, reference=payload.reference,
            disbursed_date=payload.disbursed_date, amount_paise=payload.amount_paise,
            funding_resident_id=payload.funding_resident_id, service_id=payload.service_id,
            notes=payload.notes, actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/mark-paid-by-resident", status_code=201,
             summary="Record a resident paying the provider towards a month's bill")
def mark_paid_by_resident(payload: FurniturePayoutRequest, db: Db, apartment_id: ApartmentId,
                          settings: AppSettings) -> dict[str, Any]:
    """A resident-advance payout, as ``/disburse-payout`` records one."""
    try:
        return furniture_rental.mark_paid_by_resident(
            db, apartment_id, payload.settlement_month, payload.payer_resident_id,
            payment_date=payload.payment_date, amount_paise=payload.amount_paise,
            reference=payload.reference, service_id=payload.service_id, notes=payload.notes,
            actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc


@router.delete("/payments/{payment_id}",
               summary="Delete a furniture payment or reimbursement recorded by mistake")
def delete_payment(db: Db, apartment_id: ApartmentId, settings: AppSettings,
                   payment_id: str = Path()) -> dict[str, Any]:
    """A resident's payment into the kitty towards a furniture bill, or the
    kitty's reimbursement for one, deleted outright while the bill's month is
    open. Returns that month's overview."""
    try:
        return furniture_rental.delete_payment(db, apartment_id, payment_id,
                                               actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc


@router.delete("/disbursements/{disbursement_id}",
               summary="Delete a payout to a provider recorded by mistake")
def delete_disbursement(db: Db, apartment_id: ApartmentId, settings: AppSettings,
                        disbursement_id: str = Path()) -> dict[str, Any]:
    """Deleted outright while the bill's month is open, with whatever was
    recorded with it. Refused while the kitty has reimbursed its advance.
    Returns that month's overview."""
    try:
        return furniture_rental.delete_provider_payout(db, apartment_id, disbursement_id,
                                                       actor=settings.operator_name)
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


@router.post("/retire", summary="End a furniture rental subscription")
def retire_furniture(db: Db, apartment_id: ApartmentId, settings: AppSettings,
                     month: OptionalMonth = None,
                     service_id: Annotated[str | None, Query(
                         max_length=64, description="The subscription to end; the first if "
                                                    "left out")] = None) -> dict[str, Any]:
    """Every item on it must have been returned first."""
    try:
        return furniture_rental.retire_furniture_rental(db, apartment_id, service_id=service_id,
                                                        settlement_month=month,
                                                        actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc

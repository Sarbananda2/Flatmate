"""Rent: the master lease, each month's equal-split rent, and HRA receipts."""
from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Path, Query
from fastapi.responses import Response

from ...errors import AemError
from ...schemas import (
    MONTH_PATTERN,
    LandlordReceiptUpload,
    RentDisburseRequest,
    RentGenerateRequest,
    RentLeaseConfigSave,
    RentLeaseRenewRequest,
    RentLeaseVacateRequest,
    RentPaymentCreate,
    RentReimburseRequest,
)
from ...services import rent
from ..deps import ApartmentId, AppSettings, Db, to_http

router = APIRouter(prefix="/api/rent", tags=["rent"])

SettlementMonth = Annotated[str, Path(pattern=MONTH_PATTERN, description="YYYY-MM")]


@router.get("/config", summary="Read the lease")
def get_config(db: Db, apartment_id: ApartmentId) -> dict[str, Any]:
    """``config`` is null until the lease has been set up."""
    try:
        return {"config": rent.get_lease_config(db, apartment_id)}
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/config", summary="Set up or update the lease")
def save_config(payload: RentLeaseConfigSave, db: Db, apartment_id: ApartmentId,
                settings: AppSettings) -> dict[str, Any]:
    """Landlord details, the monthly rent, the due day, and whether the
    landlord issues receipts. Only the fields sent are changed, so one screen
    can save just its part of the lease. A month already generated keeps its
    amount."""
    try:
        # Only what was sent: a field left out keeps its value.
        return rent.save_lease_config(db, apartment_id,
                                      payload.model_dump(exclude_unset=True),
                                      actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/lease/renew", summary="Renew the lease for another term")
def renew_lease(payload: RentLeaseRenewRequest, db: Db, apartment_id: ApartmentId,
                settings: AppSettings) -> dict[str, Any]:
    """Archive the term that is ending and start the next one. Anything omitted
    follows the lease: the day after the current term, the same duration, and
    the rent its escalation clause projects."""
    try:
        return rent.renew_lease_contract(db, apartment_id,
                                         payload.model_dump(exclude_none=True),
                                         actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/lease/vacate", summary="Record notice to vacate")
def vacate_lease(payload: RentLeaseVacateRequest, db: Db, apartment_id: ApartmentId,
                 settings: AppSettings) -> dict[str, Any]:
    """Mark the lease as under notice, with the date notice was served and the
    planned handover date. Rent for the remaining months is unaffected."""
    try:
        return rent.serve_vacate_notice(db, apartment_id, payload.model_dump(),
                                        actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc


@router.get("/lease/terms", summary="List completed lease terms")
def lease_terms(db: Db, apartment_id: ApartmentId) -> dict[str, Any]:
    try:
        return {"items": rent.list_lease_terms(db, apartment_id)}
    except AemError as exc:
        raise to_http(exc) from exc


@router.get("/cycle/{settlement_month}", summary="Read a month's rent")
def get_cycle(
    db: Db, apartment_id: ApartmentId, settlement_month: SettlementMonth,
    exclude: Annotated[str | None, Query(
        description="Comma-separated resident IDs to leave out of the preview.")] = None,
    split_mode: Annotated[Literal["equal", "room_tiered"] | None, Query(
        description="equal, or room_tiered to preview each share as its room's base rent. "
                    "Omit to preview the lease's agreement model."
    )] = None,
) -> dict[str, Any]:
    """The month's rent cycle with each resident's share and what they have
    paid. Before the rent is generated, ``cycle`` is null and ``preview`` shows
    the shares generating it would charge."""
    excluded = [rid.strip() for rid in (exclude or "").split(",") if rid.strip()]
    try:
        return rent.rent_month_overview(db, apartment_id, settlement_month,
                                        excluded_resident_ids=excluded,
                                        split_mode=split_mode)
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/cycle/{settlement_month}/generate", status_code=201,
             summary="Generate a month's rent")
def generate_cycle(payload: RentGenerateRequest, db: Db, apartment_id: ApartmentId,
                   settings: AppSettings, settlement_month: SettlementMonth) -> dict[str, Any]:
    """Charge the rent equally to every active resident not left out. Rent
    already recorded for the month is adopted rather than duplicated."""
    try:
        return rent.generate_monthly_rent_cycle(
            db, apartment_id, settlement_month, payload.excluded_resident_ids,
            actor=settings.operator_name, split_mode=payload.split_mode)
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/cycle/{settlement_month}/pay", summary="Record a resident's rent payment")
def pay_rent(payload: RentPaymentCreate, db: Db, apartment_id: ApartmentId,
             settings: AppSettings, settlement_month: SettlementMonth) -> dict[str, Any]:
    """A payment into the pool that settles this resident's rent share for the
    month first, whatever older charges they have. Excess goes to their other
    charges oldest-first. Returns the updated rent cycle."""
    try:
        return rent.record_rent_payment(
            db, apartment_id=apartment_id, settlement_month=settlement_month,
            actor=settings.operator_name, **payload.model_dump())
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/cycle/{settlement_month}/disburse",
             summary="Record a landlord payout (the whole rent, or a tranche of it)")
def disburse_cycle(payload: RentDisburseRequest, db: Db, apartment_id: ApartmentId,
                   settings: AppSettings, settlement_month: SettlementMonth) -> dict[str, Any]:
    """Paid from the pool, or by a resident (recorded as a provider advance
    linked to the rent). ``amount_paise`` is this tranche; left out, it is
    everything still owed. Tranches can never add up to more than the rent.

    The landlord's receipt is optional here even when the lease expects one;
    attach it later with ``POST .../landlord-receipt``."""
    try:
        return rent.record_landlord_disbursement(
            db, apartment_id=apartment_id, settlement_month=settlement_month,
            actor=settings.operator_name, **payload.model_dump())
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/cycle/{settlement_month}/reimburse", status_code=201,
             summary="Pay back, from the kitty, a resident who paid the landlord extra")
def reimburse_cycle(payload: RentReimburseRequest, db: Db, apartment_id: ApartmentId,
                    settings: AppSettings,
                    settlement_month: SettlementMonth) -> dict[str, Any]:
    """A ``refund`` from the kitty to a resident who paid the landlord more than
    their own share, out of what the others paid in for the month. Never more
    than the kitty holds for the month, nor than they are still owed."""
    try:
        return rent.reimburse_from_kitty(
            db, apartment_id=apartment_id, settlement_month=settlement_month,
            actor=settings.operator_name, **payload.model_dump())
    except AemError as exc:
        raise to_http(exc) from exc


@router.delete("/cycle/{settlement_month}/payments/{payment_id}",
               summary="Delete a rent payment or reimbursement recorded by mistake")
def delete_cycle_payment(db: Db, apartment_id: ApartmentId, settings: AppSettings,
                         settlement_month: SettlementMonth,
                         payment_id: str = Path()) -> dict[str, Any]:
    """A resident's payment into the kitty towards the month's rent, or the
    kitty's reimbursement to a resident, deleted outright while the month's
    statement is a draft. One the kitty paid out as part of a resident's payout
    to the landlord deletes that payout. Returns the month as it then stands."""
    try:
        return rent.delete_rent_payment(db, apartment_id=apartment_id,
                                        settlement_month=settlement_month,
                                        payment_id=payment_id, actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc


@router.delete("/cycle/{settlement_month}/disbursements/{disbursement_id}",
               summary="Delete a landlord payout recorded by mistake")
def delete_cycle_disbursement(db: Db, apartment_id: ApartmentId, settings: AppSettings,
                              settlement_month: SettlementMonth,
                              disbursement_id: str = Path()) -> dict[str, Any]:
    """Deleted outright while the month's statement is a draft, with whatever was
    recorded with it: a resident's payout takes its advance, and the kitty's
    payout of their own share with their payment into the kitty. Refused while
    the kitty has reimbursed that advance. Returns the month as it then stands."""
    try:
        return rent.delete_landlord_disbursement(
            db, apartment_id=apartment_id, settlement_month=settlement_month,
            disbursement_id=disbursement_id, actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc


@router.get("/cycle/{settlement_month}/receipt/{resident_id}",
            summary="Read a resident's HRA rent receipt")
def get_receipt(db: Db, apartment_id: ApartmentId, settlement_month: SettlementMonth,
                resident_id: str = Path()) -> dict[str, Any]:
    try:
        return rent.get_resident_rent_receipt(
            db, apartment_id=apartment_id, settlement_month=settlement_month,
            resident_id=resident_id)
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/cycle/{settlement_month}/landlord-receipt",
             summary="Attach or replace the landlord's receipt")
def attach_landlord_receipt(payload: LandlordReceiptUpload, db: Db,
                            apartment_id: ApartmentId, settings: AppSettings,
                            settlement_month: SettlementMonth) -> dict[str, Any]:
    """For a month whose payout is already recorded and whose lease expects a
    receipt. Posting again replaces the file; both are noted in the audit log."""
    try:
        return rent.attach_landlord_receipt(
            db, apartment_id=apartment_id, settlement_month=settlement_month,
            landlord_receipt=payload.model_dump(), actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc


@router.get("/cycle/{settlement_month}/landlord-receipt",
            summary="Download the landlord's receipt")
def get_landlord_receipt(db: Db, apartment_id: ApartmentId,
                         settlement_month: SettlementMonth) -> Response:
    """The file attached when the payout was recorded, exactly as uploaded."""
    try:
        receipt = rent.get_landlord_receipt(db, apartment_id=apartment_id,
                                            settlement_month=settlement_month)
    except AemError as exc:
        raise to_http(exc) from exc
    safe = "".join(ch for ch in receipt["filename"] if ch.isalnum() or ch in "-_.") or "receipt"
    # Always a download, never rendered inline: the content is whatever was uploaded.
    return Response(
        content=receipt["content"], media_type=receipt["content_type"],
        headers={"Content-Disposition": f'attachment; filename="{safe}"',
                 "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})

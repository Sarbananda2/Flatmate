"""Electricity bills: a dedicated module over the electricity engine."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Path, Query

from ...ids import new_id
from ...errors import AemError
from ...schemas import (
    ElectricityCreate,
    ElectricityMonthBill,
    ElectricityMonthInput,
    ElectricityPaymentCreate,
    ElectricityPayoutRequest,
    ElectricityPreviewRequest,
    ElectricityRecalculate,
    ElectricityReimburseRequest,
    MONTH_PATTERN,
    TapoSyncRequest,
)
from ...services import electricity, tapo_sync
from ..deps import ApartmentId, AppSettings, Db, csv_response, to_http

router = APIRouter(prefix="/api/electricity", tags=["electricity"])

SettlementMonth = Annotated[str, Path(pattern=MONTH_PATTERN, description="YYYY-MM")]


def _engine_payload(payload: Any) -> dict[str, Any]:
    data = payload.model_dump(exclude_none=True)
    readings = data.get("smart_plug_readings")
    if readings is not None:
        data["smart_plug_readings"] = [dict(reading) for reading in readings]
    components = data.get("components")
    if components is not None:
        data["components"] = [dict(component) for component in components]
    return data


@router.post("/preview", summary="Review an electricity calculation before committing it")
def preview(payload: ElectricityPreviewRequest, db: Db,
            apartment_id: ApartmentId) -> dict[str, Any]:
    """Run the engine and return the full breakdown without saving anything.

    The utility bill total is authoritative. Component amounts must sum to it
    exactly, and resident allocations must sum to it exactly; the response says
    whether both reconcile.
    """
    try:
        return electricity.calculate_preview(db, apartment_id=apartment_id,
                                             **_engine_payload(payload))
    except AemError as exc:
        raise to_http(exc) from exc


@router.get("/bills", summary="List electricity bills")
def list_bills(db: Db, apartment_id: ApartmentId,
               limit: Annotated[int, Query(ge=1, le=500)] = 50,
               offset: Annotated[int, Query(ge=0)] = 0) -> dict[str, Any]:
    try:
        return electricity.list_electricity_bills(db, apartment_id, limit=limit,
                                                  offset=offset)
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/bills", status_code=201, summary="Record an electricity bill")
def create_bill(payload: ElectricityMonthBill | ElectricityCreate, db: Db,
                apartment_id: ApartmentId, settings: AppSettings) -> dict[str, Any]:
    """Set a month's AC: what each flatmate's AC cost, out of what the prepaid
    meters were recharged (``POST /disburse-payout``). Each flatmate is charged
    their AC and an equal share of the rest, and pays it into the House Kitty;
    what has been paid moves with the shares. Returns the month as
    ``GET /month/{settlement_month}`` gives it.

    A bill split by smart-plug readings is still taken in its own shape --
    ``description``, ``expense_date``, ``total_amount_paise`` and readings --
    and returned as ``GET /bills/{expense_id}`` gives it. Guest electricity
    belongs in the host resident's reading; guests are never billed residents.
    """
    if isinstance(payload, ElectricityMonthBill):
        try:
            return electricity.record_month_bill(db, apartment_id, payload.model_dump(),
                                                 actor=settings.operator_name)
        except AemError as exc:
            raise to_http(exc) from exc
    data = _engine_payload(payload)
    expense_id = data.pop("expense_id", None) or new_id("electricity")
    try:
        electricity.create_electricity_expense(
            db, apartment_id=apartment_id, expense_id=expense_id,
            actor=settings.operator_name, **data)
        return electricity.get_electricity_expense(db, expense_id)
    except AemError as exc:
        raise to_http(exc) from exc


@router.get("/bills/{expense_id}", summary="Read one electricity bill")
def read_bill(db: Db, expense_id: str = Path()) -> dict[str, Any]:
    saved = electricity.get_electricity_expense(db, expense_id)
    if saved is None:
        from ...errors import NotFoundError
        raise to_http(NotFoundError("Electricity bill not found.",
                                    details={"expense_id": expense_id}))
    return saved


@router.post("/bills/{expense_id}/recalculate", summary="Correct a saved electricity bill")
def recalculate(payload: ElectricityRecalculate, db: Db, settings: AppSettings,
                expense_id: str = Path()) -> dict[str, Any]:
    """Replace a saved calculation auditably. A reason is required, the previous
    calculation is written to the audit trail, and a closed statement is refused:
    that correction belongs in a ledger adjustment."""
    data = _engine_payload(payload)
    reason = data.pop("reason")
    data.pop("expense_id", None)
    try:
        return electricity.recalculate_electricity_expense(
            db, expense_id, reason=reason, actor=settings.operator_name, **data)
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/sync-tapo", summary="Fetch smart-plug energy for a billing period")
def sync_tapo(payload: TapoSyncRequest, db: Db, apartment_id: ApartmentId,
              settings: AppSettings) -> dict[str, Any]:
    """Read each configured Tapo plug's energy for the period.

    Nothing is saved: the readings come back for review and are recorded only
    as part of the electricity bill they belong to. A plug that could not be
    reached is listed under ``errors``, and days a plug did not report are
    listed against its reading.
    """
    try:
        return tapo_sync.sync_readings(
            db, apartment_id=apartment_id, settings=settings,
            period_start=payload.period_start, period_end=payload.period_end)
    except AemError as exc:
        raise to_http(exc) from exc


@router.get("/consumption", summary="Consumption and allocation history")
def consumption(db: Db, apartment_id: ApartmentId) -> dict[str, Any]:
    try:
        rows = electricity.consumption_history(db, apartment_id)
    except AemError as exc:
        raise to_http(exc) from exc
    return {"items": rows, "total": len(rows)}


@router.get("/consumption/export/csv", summary="Export consumption history as CSV")
def export_consumption(db: Db, apartment_id: ApartmentId):
    try:
        rows = electricity.consumption_history(db, apartment_id)
    except AemError as exc:
        raise to_http(exc) from exc
    return csv_response(rows, "electricity-consumption")


# ------------------------------------------------------- the month's bill --

@router.get("/month/{settlement_month}", summary="Read a month's electricity")
def read_month(db: Db, apartment_id: ApartmentId,
               settlement_month: SettlementMonth) -> dict[str, Any]:
    """The month's UPPCL and DG bills and each flatmate's AC; what each flatmate
    is responsible for -- their common share and their AC -- and has paid into
    the kitty; and what the kitty has paid UPPCL and DG."""
    try:
        return electricity.get_month(db, apartment_id, settlement_month)
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/month/{settlement_month}/preview",
             summary="Split a month's bills and AC without recording them")
def preview_month(payload: ElectricityMonthInput, db: Db, apartment_id: ApartmentId,
                  settlement_month: SettlementMonth) -> dict[str, Any]:
    try:
        return electricity.preview_month_bill(db, apartment_id, settlement_month,
                                              payload.model_dump())
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/record-payment", summary="Record a flatmate paying their electricity into the kitty")
def record_payment(payload: ElectricityPaymentCreate, db: Db, apartment_id: ApartmentId,
                   settings: AppSettings) -> dict[str, Any]:
    try:
        return electricity.record_month_payment(db, apartment_id,
                                                payload.model_dump(exclude_none=True),
                                                actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/disburse-payout", summary="Record a recharge of the UPPCL or DG prepaid meter")
def disburse_payout(payload: ElectricityPayoutRequest, db: Db, apartment_id: ApartmentId,
                    settings: AppSettings) -> dict[str, Any]:
    try:
        return electricity.disburse_month_payout(db, apartment_id,
                                                 payload.model_dump(exclude_none=True),
                                                 actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/reimburse-from-kitty",
             summary="Pay back a flatmate who paid a provider beyond their share")
def reimburse_from_kitty(payload: ElectricityReimburseRequest, db: Db,
                         apartment_id: ApartmentId, settings: AppSettings) -> dict[str, Any]:
    try:
        return electricity.reimburse_month_from_kitty(db, apartment_id,
                                                      payload.model_dump(exclude_none=True),
                                                      actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc


@router.delete("/month/{settlement_month}/top-ups/{top_up_id}",
               summary="Remove a meter recharge logged by mistake")
def delete_top_up(db: Db, apartment_id: ApartmentId, settings: AppSettings,
                  settlement_month: SettlementMonth, top_up_id: str = Path()) -> dict[str, Any]:
    try:
        return electricity.delete_month_top_up(db, apartment_id, settlement_month, top_up_id,
                                               actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc

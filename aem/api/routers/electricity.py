"""Electricity bills: a dedicated module over the electricity engine."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Path, Query

from ...ids import new_id
from ...errors import AemError
from ...schemas import (
    ElectricityCreate,
    ElectricityPreviewRequest,
    ElectricityRecalculate,
    TapoSyncRequest,
)
from ...services import electricity, tapo_sync
from ..deps import ApartmentId, AppSettings, Db, csv_response, to_http

router = APIRouter(prefix="/api/electricity", tags=["electricity"])


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
def create_bill(payload: ElectricityCreate, db: Db, apartment_id: ApartmentId,
                settings: AppSettings) -> dict[str, Any]:
    """Persist the bill, its components, the readings, and the allocations atomically.

    Guest electricity belongs in the host resident's reading; guests are never
    created as billed residents.
    """
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

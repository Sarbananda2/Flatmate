"""Exit clearance: settling a resident's move-out in one step."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path

from ...errors import AemError
from ...schemas import ExitClearanceExecuteRequest, ExitClearancePreview, ExitClearanceResponse
from ...services import clearance
from ..deps import ApartmentId, AppSettings, Db, to_http

router = APIRouter(prefix="/api/residents", tags=["exit clearance"])


@router.get("/{resident_id}/clearance/preview", response_model=ExitClearancePreview,
            summary="Preview a resident's exit clearance")
def preview(db: Db, apartment_id: ApartmentId, resident_id: str = Path()) -> dict[str, Any]:
    """Their deposit slot and their operating balance as they stand now, the
    ways the exit can be settled, and why it cannot be, if it cannot."""
    try:
        return clearance.get_clearance_preview(db, apartment_id, resident_id)
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/{resident_id}/clearance", response_model=ExitClearanceResponse,
             summary="Settle a resident's exit")
def execute(payload: ExitClearanceExecuteRequest, db: Db, apartment_id: ApartmentId,
            settings: AppSettings, resident_id: str = Path()) -> dict[str, Any]:
    """Vacate or transfer the deposit slot, optionally clear the operating
    balance to zero, mark the resident as moved out, and record the clearance
    -- all in one transaction, or not at all."""
    try:
        return clearance.execute_clearance(
            db, apartment_id, resident_id, payload.model_dump(),
            actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc


@router.get("/{resident_id}/clearance", summary="Read a resident's clearance slip")
def read(db: Db, apartment_id: ApartmentId, resident_id: str = Path()) -> dict[str, Any]:
    """``clearance`` is null for a resident who has never been cleared."""
    try:
        return {"clearance": clearance.get_clearance(db, apartment_id, resident_id)}
    except AemError as exc:
        raise to_http(exc) from exc

"""The audit trail."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query

from ...errors import AemError
from ...services import audit as audit_service
from ..deps import ApartmentId, Db, csv_response, to_http

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("", summary="Read the audit trail")
def read_audit(db: Db, apartment_id: ApartmentId,
               entity_type: Annotated[str | None, Query()] = None,
               entity_id: Annotated[str | None, Query()] = None,
               limit: Annotated[int, Query(ge=1, le=1000)] = 100,
               offset: Annotated[int, Query(ge=0)] = 0) -> dict[str, Any]:
    """Append-only history of every change: old value, new value, timestamp,
    actor, and the reason where one was required."""
    try:
        page = audit_service.read_history(db, entity_type=entity_type,
                                          entity_id=entity_id,
                                          apartment_id=apartment_id,
                                          limit=limit, offset=offset)
    except AemError as exc:
        raise to_http(exc) from exc
    return {**page, "limit": limit, "offset": offset}


@router.get("/export/csv", summary="Export the audit trail as CSV")
def export_audit(db: Db, apartment_id: ApartmentId):
    from ...services import reports
    try:
        rows = reports.audit_trail_rows(db, apartment_id=apartment_id)
    except AemError as exc:
        raise to_http(exc) from exc
    return csv_response(rows, "audit-trail")

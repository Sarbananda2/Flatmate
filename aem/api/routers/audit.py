"""The audit trail."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query

from ...db import connect
from ...errors import AemError
from ...services import audit as audit_service
from ..deps import ApartmentId, DbPath, csv_response, to_http

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("", summary="Read the audit trail")
def read_audit(db_path: DbPath, apartment_id: ApartmentId,
               entity_type: Annotated[str | None, Query()] = None,
               entity_id: Annotated[str | None, Query()] = None,
               limit: Annotated[int, Query(ge=1, le=1000)] = 100,
               offset: Annotated[int, Query(ge=0)] = 0) -> dict[str, Any]:
    """Append-only history of every change: old value, new value, timestamp,
    actor, and the reason where one was required."""
    try:
        with connect(db_path, read_only=True) as conn:
            items = audit_service.history(conn, entity_type=entity_type,
                                          entity_id=entity_id,
                                          apartment_id=apartment_id,
                                          limit=limit, offset=offset)
            total = audit_service.count(conn, apartment_id=apartment_id,
                                        entity_type=entity_type, entity_id=entity_id)
    except AemError as exc:
        raise to_http(exc) from exc
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/export/csv", summary="Export the audit trail as CSV")
def export_audit(db_path: DbPath, apartment_id: ApartmentId):
    from ...services import reports
    try:
        rows = reports.audit_trail_rows(db_path, apartment_id=apartment_id)
    except AemError as exc:
        raise to_http(exc) from exc
    return csv_response(rows, "audit-trail")

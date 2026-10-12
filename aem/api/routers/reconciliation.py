"""Full household reconciliation and accounting validation."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query

from ...errors import AemError
from ...services import reconciliation, validation
from ..deps import ApartmentId, DbPath, csv_response, to_http

router = APIRouter(prefix="/api/reconciliation", tags=["reconciliation"])

DATE = r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$"
MONTH = r"^\d{4}-(0[1-9]|1[0-2])$"


@router.get("", summary="Reconcile the full household ledger")
def reconcile(db_path: DbPath, apartment_id: ApartmentId,
              as_of_date: Annotated[str | None, Query(pattern=DATE)] = None,
              require_clean: Annotated[bool, Query()] = False) -> dict[str, Any]:
    """One auditable calculation across every recorded transaction and month.

    Positive means the account is owed money; negative means it owes money. The
    synthetic ``__pool__`` account makes the result zero-sum. Suggested transfers
    are advice only -- nothing in this call changes a record.
    """
    try:
        return reconciliation.reconcile(db_path, apartment_id=apartment_id,
                                        as_of_date=as_of_date,
                                        require_clean=require_clean)
    except AemError as exc:
        raise to_http(exc) from exc


@router.get("/entries", summary="The ledger entries behind the reconciliation")
def entries(db_path: DbPath, apartment_id: ApartmentId,
            as_of_date: Annotated[str | None, Query(pattern=DATE)] = None,
            account_id: Annotated[str | None, Query()] = None,
            kind: Annotated[str | None, Query()] = None,
            settlement_period: Annotated[str | None, Query(pattern=MONTH)] = None,
            limit: Annotated[int, Query(ge=1, le=2000)] = 500,
            offset: Annotated[int, Query(ge=0)] = 0) -> dict[str, Any]:
    """Every signed entry, naming the record it came from, so any figure on the
    dashboard can be traced back to a row."""
    try:
        return reconciliation.ledger_entries(
            db_path, apartment_id=apartment_id, as_of_date=as_of_date,
            account_id=account_id, kind=kind, settlement_period=settlement_period,
            limit=limit, offset=offset)
    except AemError as exc:
        raise to_http(exc) from exc


@router.get("/validation", summary="Accounting validation issues")
def validate(db_path: DbPath, apartment_id: ApartmentId) -> dict[str, Any]:
    """Named, record-level exceptions. ``error`` blocks settlement figures;
    ``warning`` reconciles but needs operator review."""
    try:
        return validation.validate(db_path, apartment_id)
    except AemError as exc:
        raise to_http(exc) from exc


@router.get("/entries/export/csv", summary="Export the ledger entries as CSV")
def export_entries(db_path: DbPath, apartment_id: ApartmentId,
                   as_of_date: Annotated[str | None, Query(pattern=DATE)] = None):
    try:
        rows = reconciliation.ledger_entries(db_path, apartment_id=apartment_id,
                                             as_of_date=as_of_date, limit=2000)["items"]
    except AemError as exc:
        raise to_http(exc) from exc
    return csv_response(rows, "ledger-entries")

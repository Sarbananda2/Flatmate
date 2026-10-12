"""The searchable transaction ledger."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query

from ...errors import AemError
from ...services import ledger_view
from ..deps import ApartmentId, Db, csv_response, to_http

router = APIRouter(prefix="/api/transactions", tags=["transactions"])

MONTH = r"^\d{4}-(0[1-9]|1[0-2])$"
DATE = r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$"


@router.get("", summary="Search the transaction ledger")
def list_transactions(
    db: Db, apartment_id: ApartmentId,
    settlement_period: Annotated[str | None, Query(pattern=MONTH)] = None,
    resident_id: Annotated[str | None, Query()] = None,
    category: Annotated[str | None, Query()] = None,
    transaction_type: Annotated[str | None, Query()] = None,
    status: Annotated[str | None, Query()] = None,
    date_from: Annotated[str | None, Query(pattern=DATE)] = None,
    date_to: Annotated[str | None, Query(pattern=DATE)] = None,
    search: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort: Annotated[str, Query()] = "date_desc",
) -> dict[str, Any]:
    """Expenses, payments, and adjustments in one list.

    Filtering by resident matches both the transactions they made and the
    expenses they hold a share of.
    """
    try:
        return ledger_view.list_transactions(
            db, apartment_id=apartment_id, settlement_period=settlement_period,
            resident_id=resident_id, category=category,
            transaction_type=transaction_type, status=status, date_from=date_from,
            date_to=date_to, search=search, limit=limit, offset=offset, sort=sort)
    except AemError as exc:
        raise to_http(exc) from exc


@router.get("/filter-options", summary="Values that appear in this apartment's data")
def filter_options(db: Db, apartment_id: ApartmentId) -> dict[str, Any]:
    try:
        return ledger_view.filter_options(db, apartment_id)
    except AemError as exc:
        raise to_http(exc) from exc


@router.get("/export/csv", summary="Export the transaction ledger as CSV")
def export_transactions(
    db: Db, apartment_id: ApartmentId,
    settlement_period: Annotated[str | None, Query(pattern=MONTH)] = None,
    resident_id: Annotated[str | None, Query()] = None,
    transaction_type: Annotated[str | None, Query()] = None,
):
    try:
        rows = ledger_view.list_transactions(
            db, apartment_id=apartment_id, settlement_period=settlement_period,
            resident_id=resident_id, transaction_type=transaction_type, limit=500)["items"]
    except AemError as exc:
        raise to_http(exc) from exc
    return csv_response(rows, f"transactions-{settlement_period or 'all'}")

"""The dashboard."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query

from ...errors import AemError
from ...services import dashboard, statements
from ..deps import ApartmentId, DbPath, to_http

router = APIRouter(prefix="/api", tags=["dashboard"])

MONTH = r"^\d{4}-(0[1-9]|1[0-2])$"
DATE = r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$"


@router.get("/dashboard", summary="Dashboard figures for one settlement month")
def read_dashboard(db_path: DbPath, apartment_id: ApartmentId,
                   settlement_period: Annotated[str | None, Query(pattern=MONTH)] = None,
                   as_of_date: Annotated[str | None, Query(pattern=DATE)] = None
                   ) -> dict[str, Any]:
    """Every figure here is derived from records and from the reconciliation.

    Household expenses, resident transfers, and payments to external providers
    are reported separately, because they are different kinds of transaction and
    adding them together would double count.
    """
    try:
        payload = dashboard.dashboard(db_path, apartment_id=apartment_id,
                                      settlement_period=settlement_period,
                                      as_of_date=as_of_date)
        payload["available_months"] = statements.available_months(db_path, apartment_id)
        return payload
    except AemError as exc:
        raise to_http(exc) from exc

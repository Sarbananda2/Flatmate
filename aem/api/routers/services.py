"""Housing & Utilities: the summary behind the services hub screen."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query

from ...errors import AemError
from ...schemas import MONTH_PATTERN
from ...services import housing
from ..deps import ApartmentId, Db, to_http

router = APIRouter(prefix="/api/services", tags=["services"])


@router.get("", summary="Summarise rent and electricity for a month")
def get_services(
    db: Db, apartment_id: ApartmentId,
    month: Annotated[str, Query(pattern=MONTH_PATTERN, description="YYYY-MM")],
) -> dict[str, Any]:
    """``rent.config`` is null until the lease is set up and ``rent.cycle`` is
    null until the month's rent is generated. ``electricity.bill`` is the bill
    settling in the month; ``latest_bill`` is the newest bill for any month."""
    try:
        return housing.services_overview(db, apartment_id, month)
    except AemError as exc:
        raise to_http(exc) from exc

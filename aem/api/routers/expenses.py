"""General expenses and allocation previews."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Path, Query

from ...db import new_id
from ...errors import AemError
from ...schemas import AllocationPreviewRequest, ExpenseCreate, ExpenseUpdate
from ...services import expenses
from ..deps import ApartmentId, AppSettings, DbPath, csv_response, to_http

router = APIRouter(prefix="/api/expenses", tags=["expenses"])

MONTH = r"^\d{4}-(0[1-9]|1[0-2])$"
DATE = r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$"


@router.post("/allocation-preview", summary="Calculate shares without saving")
def allocation_preview(payload: AllocationPreviewRequest, db_path: DbPath,
                       apartment_id: ApartmentId) -> dict[str, Any]:
    """Show the allocation breakdown before saving, including who is excluded.

    The returned shares are exactly what would be written: the same engine
    functions produce both, so the preview can never disagree with the save.
    """
    try:
        return expenses.preview_allocation(db_path, apartment_id=apartment_id,
                                           **payload.model_dump())
    except AemError as exc:
        raise to_http(exc) from exc


@router.get("", summary="List and filter expenses")
def list_expenses(
    db_path: DbPath, apartment_id: ApartmentId,
    settlement_period: Annotated[str | None, Query(pattern=MONTH)] = None,
    service_period: Annotated[str | None, Query(pattern=MONTH)] = None,
    statement_id: Annotated[str | None, Query()] = None,
    resident_id: Annotated[str | None, Query()] = None,
    expense_definition_id: Annotated[str | None, Query()] = None,
    expense_kind: Annotated[str | None, Query()] = None,
    category: Annotated[str | None, Query()] = None,
    search: Annotated[str | None, Query(max_length=200)] = None,
    date_from: Annotated[str | None, Query(pattern=DATE)] = None,
    date_to: Annotated[str | None, Query(pattern=DATE)] = None,
    unattached_only: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort: Annotated[str, Query()] = "expense_date_desc",
) -> dict[str, Any]:
    try:
        return expenses.list_expenses(
            db_path, apartment_id=apartment_id, settlement_period=settlement_period,
            service_period=service_period, statement_id=statement_id,
            resident_id=resident_id, expense_definition_id=expense_definition_id,
            expense_kind=expense_kind, category=category, search=search,
            date_from=date_from, date_to=date_to, unattached_only=unattached_only,
            limit=limit, offset=offset, sort=sort)
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("", status_code=201, summary="Record an expense")
def create_expense(payload: ExpenseCreate, db_path: DbPath, apartment_id: ApartmentId,
                   settings: AppSettings) -> dict[str, Any]:
    """Create the expense and its allocations in one transaction.

    The allocations must name every resident of the apartment and sum exactly to
    the expense amount; a resident who does not take part is given 0. Pass
    ``allocation_method`` of ``equal``/``weighted`` with participants or weights
    to have the shares calculated instead.
    """
    data = payload.model_dump(exclude_none=True)
    expense_id = data.pop("expense_id", None) or new_id("expense")
    try:
        created = expenses.create_expense(
            db_path, apartment_id=apartment_id, expense_id=expense_id,
            actor=settings.operator_name, **data)
        return expenses.read_expense(db_path, created["expense_id"])
    except AemError as exc:
        raise to_http(exc) from exc


@router.get("/{expense_id}", summary="Read one expense")
def read_expense(db_path: DbPath, expense_id: str = Path()) -> dict[str, Any]:
    try:
        return expenses.read_expense(db_path, expense_id)
    except AemError as exc:
        raise to_http(exc) from exc


@router.patch("/{expense_id}", summary="Edit an expense")
def update_expense(payload: ExpenseUpdate, db_path: DbPath, apartment_id: ApartmentId,
                   settings: AppSettings, expense_id: str = Path()) -> dict[str, Any]:
    """Editable while the statement is a draft. Inside the correction window a
    ``reason`` is required and the edit becomes a statement revision. Once the
    statement is closed the expense is immutable."""
    data = payload.model_dump(exclude_unset=True)
    reason = data.pop("reason", None)
    try:
        expenses.update_expense(db_path, expense_id, apartment_id=apartment_id,
                                actor=settings.operator_name, reason=reason, **data)
        return expenses.read_expense(db_path, expense_id)
    except AemError as exc:
        raise to_http(exc) from exc


@router.delete("/{expense_id}", summary="Delete a draft expense")
def delete_expense(db_path: DbPath, apartment_id: ApartmentId, settings: AppSettings,
                   expense_id: str = Path(),
                   reason: Annotated[str | None, Query(max_length=2000)] = None
                   ) -> dict[str, Any]:
    """Only an unattached or draft expense can be deleted, and only while no
    payment has been applied to its charges."""
    try:
        return expenses.delete_expense(db_path, expense_id, apartment_id=apartment_id,
                                       reason=reason, actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc


@router.get("/export/csv", summary="Export expenses as CSV")
def export_expenses(db_path: DbPath, apartment_id: ApartmentId,
                    settlement_period: Annotated[str | None, Query(pattern=MONTH)] = None):
    try:
        rows = expenses.list_expenses(db_path, apartment_id=apartment_id,
                                      settlement_period=settlement_period,
                                      limit=500)["items"]
    except AemError as exc:
        raise to_http(exc) from exc
    name = f"expenses-{settlement_period or 'all'}"
    return csv_response(rows, name)

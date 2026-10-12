"""Expense definitions and recurring generation."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Path, Query

from ...errors import AemError
from ...periods import PERIOD_RULES
from ...schemas import DefinitionCreate, DefinitionUpdate, RecurringGenerateRequest
from ...services import definitions, recurring
from ...services.common import ALLOCATION_METHODS, RECURRENCES
from ..deps import ApartmentId, AppSettings, DbPath, to_http

router = APIRouter(prefix="/api", tags=["expense definitions"])


@router.get("/expense-definitions", summary="List expense definitions")
def list_definitions(db_path: DbPath, apartment_id: ApartmentId,
                     include_inactive: Annotated[bool, Query()] = False) -> dict[str, Any]:
    try:
        items = definitions.list_expense_definitions(db_path, apartment_id,
                                                     include_inactive=include_inactive)
    except AemError as exc:
        raise to_http(exc) from exc
    return {"items": items, "total": len(items),
            "period_rules": list(PERIOD_RULES),
            "allocation_methods": list(ALLOCATION_METHODS),
            "recurrences": list(RECURRENCES)}


@router.post("/expense-definitions", status_code=201, summary="Create an expense definition")
def create_definition(payload: DefinitionCreate, db_path: DbPath, apartment_id: ApartmentId,
                      settings: AppSettings) -> dict[str, Any]:
    data = payload.model_dump()
    try:
        return definitions.create_expense_definition(
            db_path, apartment_id=apartment_id, actor=settings.operator_name,
            is_recurring=data["recurrence"] != "none", **data)
    except AemError as exc:
        raise to_http(exc) from exc


@router.get("/expense-definitions/{definition_id}", summary="Read one expense definition")
def read_definition(db_path: DbPath, definition_id: str = Path()) -> dict[str, Any]:
    try:
        return definitions.read_expense_definition(db_path, definition_id)
    except AemError as exc:
        raise to_http(exc) from exc


@router.patch("/expense-definitions/{definition_id}", summary="Update an expense definition")
def update_definition(payload: DefinitionUpdate, db_path: DbPath, apartment_id: ApartmentId,
                      settings: AppSettings, definition_id: str = Path()) -> dict[str, Any]:
    data = payload.model_dump(exclude_unset=True)
    reason = data.pop("reason", None)
    try:
        return definitions.update_expense_definition(
            db_path, definition_id, apartment_id=apartment_id,
            actor=settings.operator_name, reason=reason, **data)
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/expense-definitions/{definition_id}/deactivate",
             summary="Deactivate an expense definition")
def deactivate_definition(db_path: DbPath, apartment_id: ApartmentId, settings: AppSettings,
                          definition_id: str = Path()) -> dict[str, Any]:
    """Deactivate rather than delete, so expenses already recorded against it
    keep their link and stay auditable."""
    try:
        return definitions.deactivate_expense_definition(
            db_path, definition_id, apartment_id=apartment_id, actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc


@router.get("/recurring/preview", summary="Preview the recurring expenses for a month")
def preview_recurring(db_path: DbPath, apartment_id: ApartmentId,
                      settlement_period: Annotated[str, Query(
                          pattern=r"^\d{4}-(0[1-9]|1[0-2])$")]) -> dict[str, Any]:
    """What generation *would* create, and why each definition is or is not
    eligible. Nothing is written by this call."""
    try:
        return recurring.preview(db_path, apartment_id=apartment_id,
                                 settlement_period=settlement_period)
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/recurring/generate", status_code=201,
             summary="Generate the confirmed recurring expenses")
def generate_recurring(payload: RecurringGenerateRequest, db_path: DbPath,
                       apartment_id: ApartmentId, settings: AppSettings) -> dict[str, Any]:
    """Create only the confirmed lines. A definition that already has an expense
    for the month is skipped, never duplicated."""
    try:
        return recurring.generate(
            db_path, apartment_id=apartment_id,
            settlement_period=payload.settlement_period,
            lines=[line.model_dump(exclude_unset=True) for line in payload.lines],
            statement_id=payload.statement_id,
            attach_to_statement=payload.attach_to_statement,
            actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc

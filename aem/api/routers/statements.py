"""Monthly statements: draft, review, finalize, correct, close."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Path, Query

from ...errors import AemError
from ...schemas import (
    StatementAttachRequest,
    StatementCloseRequest,
    StatementCreate,
    StatementFinalizeRequest,
    StatementReviseRequest,
)
from ...services import statements
from ..deps import ApartmentId, AppSettings, Db, csv_response, to_http

router = APIRouter(prefix="/api/statements", tags=["statements"])


@router.get("", summary="List statements")
def list_statements(db: Db, apartment_id: ApartmentId,
                    status: Annotated[str | None, Query()] = None,
                    limit: Annotated[int, Query(ge=1, le=500)] = 50,
                    offset: Annotated[int, Query(ge=0)] = 0) -> dict[str, Any]:
    try:
        result = statements.list_statements(db, apartment_id=apartment_id,
                                            status=status, limit=limit, offset=offset)
        result["available_months"] = statements.available_months(db, apartment_id)
        return result
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("", status_code=201, summary="Create a draft statement")
def create_statement(payload: StatementCreate, db: Db, apartment_id: ApartmentId,
                     settings: AppSettings) -> dict[str, Any]:
    try:
        statement_id = statements.create_monthly_statement(
            db, apartment_id=apartment_id, actor=settings.operator_name,
            **payload.model_dump(exclude_none=True))
        return statements.read_statement(db, statement_id)
    except AemError as exc:
        raise to_http(exc) from exc


@router.get("/{statement_id}", summary="Read one statement")
def read_statement(db: Db, statement_id: str = Path()) -> dict[str, Any]:
    try:
        return statements.read_statement(db, statement_id)
    except AemError as exc:
        raise to_http(exc) from exc


@router.get("/{statement_id}/preview", summary="Review the statement before finalizing")
def preview_statement(db: Db, statement_id: str = Path()) -> dict[str, Any]:
    """The detailed review screen: attached expenses, resident charges, the full
    cross-month reconciliation, expenses still eligible to be attached, and the
    accounting errors that would block finalization."""
    try:
        return statements.preview_statement(db, statement_id)
    except AemError as exc:
        raise to_http(exc) from exc


@router.get("/{statement_id}/snapshot", summary="Read the stored finalized snapshot")
def read_snapshot(db: Db, statement_id: str = Path()) -> dict[str, Any]:
    """The snapshot exactly as it was saved, with its SHA-256 hash verified.

    ``hash_matches: false`` means the stored bytes were changed outside the
    application. It is reported, never corrected.
    """
    try:
        return statements.read_finalized_snapshot(db, statement_id)
    except AemError as exc:
        raise to_http(exc) from exc


@router.get("/{statement_id}/share/whatsapp", summary="Settlement summary to share")
def share_whatsapp(db: Db, statement_id: str = Path()) -> dict[str, Any]:
    """A ready-to-paste message: what each resident was charged, who should pay
    whom, a UPI link where the receiver has a UPI ID, and the figures that let a
    reader check it. Reading it changes nothing."""
    from ...services import sharing
    try:
        return sharing.whatsapp_summary(db, statement_id)
    except AemError as exc:
        raise to_http(exc) from exc


@router.get("/{statement_id}/revisions", summary="Statement revision history")
def list_revisions(db: Db, statement_id: str = Path()) -> dict[str, Any]:
    try:
        rows = statements.list_statement_revisions(db, statement_id)
    except AemError as exc:
        raise to_http(exc) from exc
    return {"items": rows, "total": len(rows)}


@router.post("/{statement_id}/attach-expenses", summary="Attach this month's expenses")
def attach_expenses(payload: StatementAttachRequest, db: Db,
                    settings: AppSettings, statement_id: str = Path()) -> dict[str, Any]:
    """Collect expenses whose settlement period is this statement's month, by each
    expense's configured period rule rather than by transaction date."""
    try:
        return statements.attach_eligible_expenses(
            db, statement_id, actor=settings.operator_name,
            expense_ids=payload.expense_ids)
    except AemError as exc:
        raise to_http(exc) from exc


@router.delete("/{statement_id}/expenses/{expense_id}", summary="Detach an expense")
def detach_expense(db: Db, settings: AppSettings, statement_id: str = Path(),
                   expense_id: str = Path()) -> dict[str, Any]:
    try:
        return statements.detach_expense(db, statement_id, expense_id,
                                         actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/{statement_id}/finalize", summary="Finalize a statement")
def finalize_statement(payload: StatementFinalizeRequest, db: Db,
                       settings: AppSettings, statement_id: str = Path()) -> dict[str, Any]:
    """Take the snapshot and start the correction window. Requires explicit
    confirmation, and by default refuses while accounting errors remain."""
    try:
        return statements.finalize_statement(
            db, statement_id=statement_id, finalized_by=payload.finalized_by,
            correction_window_hours=payload.correction_window_hours,
            require_clean=payload.require_clean)
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/{statement_id}/revise", summary="Record a correction during the window")
def revise_statement(payload: StatementReviseRequest, db: Db,
                     settings: AppSettings, statement_id: str = Path()) -> dict[str, Any]:
    """Append a numbered, immutable revision and re-take the snapshot. Refused
    once the 72-hour window has expired."""
    try:
        return statements.revise_statement(
            db, statement_id, actor=settings.operator_name,
            reason=payload.reason, change_note=payload.change_note)
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/{statement_id}/close", summary="Close a statement")
def close_statement(payload: StatementCloseRequest, db: Db,
                    settings: AppSettings, statement_id: str = Path()) -> dict[str, Any]:
    """After closing, the snapshot is immutable and corrections must be ledger
    adjustments."""
    try:
        return statements.close_statement(db, statement_id,
                                          actor=settings.operator_name, force=payload.force)
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/close-expired", summary="Close every statement whose window has elapsed")
def close_expired(db: Db, settings: AppSettings) -> dict[str, Any]:
    closed = statements.close_expired_statements(db, actor=settings.operator_name)
    return {"closed_statement_ids": closed, "closed_count": len(closed)}


@router.get("/{statement_id}/export/csv", summary="Export statement detail as CSV")
def export_statement(db: Db, statement_id: str = Path()):
    from ...services import reports
    try:
        rows = reports.statement_detail_rows(db, statement_id)
    except AemError as exc:
        raise to_http(exc) from exc
    return csv_response(rows, f"statement-{statement_id}")


@router.get("/{statement_id}/export", summary="Printable statement export")
def export_statement_json(db: Db, statement_id: str = Path()) -> dict[str, Any]:
    """Built from the stored snapshot when one exists, so an exported finalized
    statement reproduces exactly what was finalized."""
    from ...services import reports
    try:
        return reports.statement_export(db, statement_id)
    except AemError as exc:
        raise to_http(exc) from exc

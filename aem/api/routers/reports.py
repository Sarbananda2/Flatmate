"""Reports and CSV exports."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Path, Query

from ...errors import AemError
from ...services import reports
from ..deps import ApartmentId, DbPath, csv_response, to_http

router = APIRouter(prefix="/api/reports", tags=["reports"])

MONTH = r"^\d{4}-(0[1-9]|1[0-2])$"

REPORT_LABELS = {
    "monthly_household_expenses": "Monthly household expenses",
    "resident_expense_breakdown": "Resident-wise expense breakdown",
    "resident_payments_and_balances": "Resident payments and outstanding balances",
    "provider_advances": "Provider advances",
    "electricity_consumption": "Electricity consumption and allocation",
    "credits_and_adjustments": "Credits and adjustments",
    "statement_summary": "Statement summaries",
    "statement_detail": "Detailed statement export",
    "transaction_ledger": "Transaction ledger",
    "audit_trail": "Audit trail",
}


@router.get("", summary="List the available reports")
def list_reports() -> dict[str, Any]:
    return {"items": [
        {"kind": kind, "label": label,
         "needs_statement": kind == "statement_detail",
         "supports_period": kind in {"monthly_household_expenses",
                                     "resident_expense_breakdown",
                                     "transaction_ledger"}}
        for kind, label in REPORT_LABELS.items()]}


@router.get("/{kind}", summary="Run one report")
def run_report(db_path: DbPath, apartment_id: ApartmentId, kind: str = Path(),
               settlement_period: Annotated[str | None, Query(pattern=MONTH)] = None,
               statement_id: Annotated[str | None, Query()] = None) -> dict[str, Any]:
    """Reads the same records every other screen reads, so a report figure always
    matches what the dashboard and the statement show."""
    try:
        return reports.build_report(db_path, kind, apartment_id=apartment_id,
                                     settlement_period=settlement_period,
                                     statement_id=statement_id)
    except AemError as exc:
        raise to_http(exc) from exc


@router.get("/{kind}/csv", summary="Download one report as CSV")
def download_report(db_path: DbPath, apartment_id: ApartmentId, kind: str = Path(),
                    settlement_period: Annotated[str | None, Query(pattern=MONTH)] = None,
                    statement_id: Annotated[str | None, Query()] = None):
    try:
        report = reports.build_report(db_path, kind, apartment_id=apartment_id,
                                       settlement_period=settlement_period,
                                       statement_id=statement_id)
    except AemError as exc:
        raise to_http(exc) from exc
    suffix = settlement_period or statement_id or "all"
    return csv_response(report["rows"], f"{kind}-{suffix}")

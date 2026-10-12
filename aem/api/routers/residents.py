"""Residents."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Path, Query

from ...errors import AemError
from ...schemas import ResidentCreate, ResidentDeactivate, ResidentUpdate
from ...services import payments, reconciliation, residents
from ..deps import ApartmentId, AppSettings, Db, to_http

router = APIRouter(prefix="/api/residents", tags=["residents"])


@router.get("", summary="List residents")
def list_residents(db: Db, apartment_id: ApartmentId,
                   active_only: Annotated[bool, Query()] = False) -> dict[str, Any]:
    try:
        items = residents.read_residents(db, apartment_id, active_only=active_only)
    except AemError as exc:
        raise to_http(exc) from exc
    return {"items": items, "total": len(items)}


@router.post("", status_code=201, summary="Create a resident")
def create_resident(payload: ResidentCreate, db: Db, apartment_id: ApartmentId,
                    settings: AppSettings) -> dict[str, Any]:
    try:
        return residents.create_resident(db, apartment_id=apartment_id,
                                         actor=settings.operator_name,
                                         **payload.model_dump())
    except AemError as exc:
        raise to_http(exc) from exc


@router.get("/{resident_id}", summary="Read one resident with their financial history")
def read_resident(db: Db, apartment_id: ApartmentId,
                  resident_id: str = Path()) -> dict[str, Any]:
    """The resident record plus every charge, payment, credit, and balance.

    Deactivating a resident never removes any of this; a former resident keeps
    their complete history and stays visible while they have an open position.
    Their shares are never prorated for a partial month.
    """
    try:
        resident = residents.read_resident(db, resident_id, apartment_id)
        charges = payments.resident_charge_ledger(db, apartment_id=apartment_id,
                                                  resident_id=resident_id)
        resident_payments = payments.list_payments(db, apartment_id=apartment_id,
                                                   resident_id=resident_id, limit=200)
        credits = [row for row in payments.unapplied_credits(db, apartment_id)
                   if row["payer_resident_id"] == resident_id]
        settlement = reconciliation.reconcile(db, apartment_id=apartment_id)
    except AemError as exc:
        raise to_http(exc) from exc
    account = next((a for a in settlement["accounts"] if a["account_id"] == resident_id), None)
    by_period: dict[str, dict[str, int]] = {}
    for charge in charges:
        bucket = by_period.setdefault(charge["settlement_period"],
                                      {"charged_paise": 0, "settled_paise": 0,
                                       "outstanding_paise": 0})
        bucket["charged_paise"] += charge["amount_paise"]
        bucket["settled_paise"] += charge["settled_paise"]
        bucket["outstanding_paise"] += charge["outstanding_paise"]
    return {
        "resident": resident,
        "account": account,
        "charges": charges,
        "charges_by_settlement_period": dict(sorted(by_period.items(), reverse=True)),
        "payments": resident_payments["items"],
        "payment_total_paise": resident_payments["total_amount_paise"],
        "unapplied_credits": credits,
        "unapplied_credit_total_paise": sum(row["unapplied_paise"] for row in credits),
    }


@router.patch("/{resident_id}", summary="Update a resident")
def update_resident(payload: ResidentUpdate, db: Db, apartment_id: ApartmentId,
                    settings: AppSettings, resident_id: str = Path()) -> dict[str, Any]:
    data = payload.model_dump(exclude_unset=True)
    reason = data.pop("reason", None)
    try:
        return residents.update_resident(db, resident_id, apartment_id=apartment_id,
                                         actor=settings.operator_name, reason=reason, **data)
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/{resident_id}/deactivate", summary="Record that a resident moved out")
def deactivate_resident(payload: ResidentDeactivate, db: Db,
                        apartment_id: ApartmentId, settings: AppSettings,
                        resident_id: str = Path()) -> dict[str, Any]:
    """Mark the resident inactive. No financial history is deleted, and no
    proration of rent or shared charges is applied: the household's rule is that
    nothing prorates, so the move-out date is recorded for reference and
    allocations are left exactly as entered."""
    try:
        return residents.deactivate_resident(
            db, resident_id, apartment_id=apartment_id,
            actor=settings.operator_name, **payload.model_dump(exclude_unset=True))
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/{resident_id}/reactivate", summary="Reactivate a resident")
def reactivate_resident(db: Db, apartment_id: ApartmentId, settings: AppSettings,
                        resident_id: str = Path()) -> dict[str, Any]:
    try:
        return residents.reactivate_resident(db, resident_id, apartment_id=apartment_id,
                                             actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc

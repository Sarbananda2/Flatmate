"""Opening balances: what each resident brought in from before the app."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path

from ...errors import AemError
from ...schemas import OpeningBalanceCreate, OpeningBalanceItem
from ...services import opening_balances
from ..deps import ApartmentId, AppSettings, Db, to_http

router = APIRouter(prefix="/api/opening-balances", tags=["opening balances"])


@router.get("", summary="List opening balances")
def list_opening_balances(db: Db, apartment_id: ApartmentId) -> dict[str, Any]:
    """Every resident's opening balance, how much of each debt has been paid
    off, and whether they can still be changed."""
    try:
        return opening_balances.list_opening_balances(db, apartment_id)
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("", response_model=OpeningBalanceItem,
             summary="Set a resident's opening balance")
def set_opening_balance(payload: OpeningBalanceCreate, db: Db,
                        apartment_id: ApartmentId, settings: AppSettings) -> dict[str, Any]:
    """Create the resident's opening balance, or replace the one they have.

    Allowed only while every statement is still a draft; after that the change
    belongs in a ledger adjustment."""
    try:
        return opening_balances.set_opening_balance(
            db, apartment_id=apartment_id, actor=settings.operator_name,
            **payload.model_dump())
    except AemError as exc:
        raise to_http(exc) from exc


@router.delete("/{resident_id}", summary="Clear a resident's opening balance")
def clear_opening_balance(db: Db, apartment_id: ApartmentId, settings: AppSettings,
                          resident_id: str = Path()) -> dict[str, Any]:
    """Payments that had been applied to it go back to unapplied credit."""
    try:
        return opening_balances.clear_opening_balance(
            db, apartment_id=apartment_id, resident_id=resident_id,
            actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc

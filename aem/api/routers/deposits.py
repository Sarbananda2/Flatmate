"""Household security deposit slots: how the deposit was funded, and each slot."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path

from ...errors import AemError
from ...schemas import (
    DepositConfigUpdate,
    DepositSlotFundingRequest,
    DepositSlotInitRequest,
    DepositSlotLinkRoomRequest,
    DepositSlotVacateRequest,
)
from ...services import deposits
from ..deps import ApartmentId, AppSettings, Db, to_http

router = APIRouter(prefix="/api/rent/deposits", tags=["security deposits"])


@router.get("", summary="Read the household deposit position")
def get_deposits(db: Db, apartment_id: ApartmentId) -> dict[str, Any]:
    """The funding mode, the totals, every slot with its occupant and
    transactions, and -- before any slot exists -- a proposed equal split.
    Deposits are escrow, not operating money: nothing here appears in a
    statement or a balance."""
    try:
        return deposits.deposit_summary(db, apartment_id)
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/config", summary="Say how the deposit was funded")
def configure(payload: DepositConfigUpdate, db: Db, apartment_id: ApartmentId,
              settings: AppSettings) -> dict[str, Any]:
    """Choose between one resident fronting the whole deposit and every
    flatmate paying the landlord directly. It can be changed only while no
    slot has been set up."""
    try:
        return deposits.configure_mode(
            db, apartment_id, payload.funding_mode, payload.primary_signatory_resident_id,
            actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/slots", summary="Create or change deposit slots")
def save_slots(payload: DepositSlotInitRequest, db: Db, apartment_id: ApartmentId,
               settings: AppSettings) -> dict[str, Any]:
    """Slots are matched by number. This is also how an occupant is assigned to
    a vacant slot. All targets together must equal the deposit on the lease."""
    try:
        return deposits.initialize_or_update_slots(
            db, apartment_id, [slot.model_dump(exclude_unset=True) for slot in payload.slots],
            actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/slots/{slot_id}/link-room", summary="Link a slot to the room it backs")
def link_room(payload: DepositSlotLinkRoomRequest, db: Db, apartment_id: ApartmentId,
              settings: AppSettings, slot_id: str = Path()) -> dict[str, Any]:
    """``room_id: null`` unlinks it. A room is backed by one slot at most."""
    try:
        return deposits.link_slot_room(db, apartment_id, slot_id, payload.room_id,
                                       actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/slots/{slot_id}/fund", summary="Record a payment towards a slot")
def fund_slot(payload: DepositSlotFundingRequest, db: Db, apartment_id: ApartmentId,
              settings: AppSettings, slot_id: str = Path()) -> dict[str, Any]:
    """A reimbursement to the primary signatory, or a payment straight to the
    landlord. Part payments add up."""
    try:
        return deposits.record_slot_funding(
            db, slot_id, apartment_id=apartment_id, actor=settings.operator_name,
            **payload.model_dump())
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/slots/{slot_id}/vacate", summary="Settle a leaver and free the slot")
def vacate_slot(payload: DepositSlotVacateRequest, db: Db, apartment_id: ApartmentId,
                settings: AppSettings, slot_id: str = Path()) -> dict[str, Any]:
    """Hand the leaver's money back less deductions and leave the slot vacant,
    or have the incoming resident pay them directly and take the slot. The
    refund and the deductions must add up to exactly what the leaver put in."""
    try:
        return deposits.vacate_and_settle_slot(
            db, slot_id, apartment_id=apartment_id, actor=settings.operator_name,
            **payload.model_dump())
    except AemError as exc:
        raise to_http(exc) from exc

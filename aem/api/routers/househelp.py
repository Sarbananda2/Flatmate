"""Household services: house help and cooks -- the workers, their roles, each
month's attendance and salaries, and paying them through the kitty."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Path, Query

from ...errors import AemError
from ...schemas import (
    MONTH_PATTERN,
    HouseHelpAttendanceSaveRequest,
    HouseHelpPaymentCreate,
    HouseHelpPayoutRequest,
    HouseHelpReimburseRequest,
    HouseHelpRoleCreate,
    HouseHelpRoleParticipantsSet,
    HouseHelpRoleUpdate,
    HouseHelpWorkerCreate,
    HouseHelpWorkerUpdate,
)
from ...services import househelp
from ..deps import ApartmentId, AppSettings, Db, to_http

router = APIRouter(prefix="/api/services/househelp", tags=["services"])

SettlementMonth = Annotated[str, Path(pattern=MONTH_PATTERN, description="YYYY-MM")]


# ------------------------------------------------------------- the workers --

@router.get("/workers", summary="The household's house help, with each worker's roles")
def list_workers(db: Db, apartment_id: ApartmentId,
                 include_inactive: Annotated[bool, Query(
                     description="Workers who have left too.")] = True) -> dict[str, Any]:
    """Active workers first, under ``items``; each role says who shares it. The
    roles not yet given a worker are under ``unassigned_roles``."""
    try:
        return househelp.list_staff(db, apartment_id, include_inactive=include_inactive)
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/workers", status_code=201, summary="Add a maid, cook or cleaner")
def create_worker(payload: HouseHelpWorkerCreate, db: Db, apartment_id: ApartmentId,
                  settings: AppSettings) -> dict[str, Any]:
    """House help is set up with the household's first worker."""
    try:
        return househelp.create_worker(db, apartment_id, payload.model_dump(exclude_unset=True),
                                       actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc


@router.patch("/workers/{worker_id}", summary="Change a worker's details")
def update_worker(payload: HouseHelpWorkerUpdate, db: Db, apartment_id: ApartmentId,
                  settings: AppSettings, worker_id: str = Path()) -> dict[str, Any]:
    """Only the fields sent change. An inactive worker joins no month saved after."""
    try:
        return househelp.update_worker(db, apartment_id, worker_id,
                                       payload.model_dump(exclude_unset=True),
                                       actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc


# --------------------------------------------------------------- the roles --

@router.post("/roles", status_code=201, summary="Add a role, on its own salary")
def create_role(payload: HouseHelpRoleCreate, db: Db, apartment_id: ApartmentId,
                settings: AppSettings) -> dict[str, Any]:
    """Shared by every flatmate, or only by those who opted in. With no
    ``worker_id`` it waits for one, and joins no month until then."""
    data = payload.model_dump(exclude_unset=True)
    if "participants" in data:
        data["participant_ids"] = data.pop("participants")
    try:
        return househelp.create_role(db, apartment_id, data, actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc


@router.patch("/roles/{role_id}", summary="Change a role's salary or split, or who does it")
def update_role(payload: HouseHelpRoleUpdate, db: Db, apartment_id: ApartmentId,
                settings: AppSettings, role_id: str = Path()) -> dict[str, Any]:
    """Only the fields sent change. A month already saved keeps the role as it was
    saved until it is saved again."""
    try:
        return househelp.update_role(db, apartment_id, role_id,
                                     payload.model_dump(exclude_unset=True),
                                     actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc


@router.put("/roles/{role_id}/participants", summary="Choose who shares an opt-in role")
def set_role_participants(payload: HouseHelpRoleParticipantsSet, db: Db,
                          apartment_id: ApartmentId, settings: AppSettings,
                          role_id: str = Path()) -> dict[str, Any]:
    try:
        return househelp.set_role_participants(db, apartment_id, role_id, payload.participants,
                                               actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc


@router.delete("/roles/{role_id}", summary="Delete a role")
def delete_role(db: Db, apartment_id: ApartmentId, settings: AppSettings,
                role_id: str = Path()) -> dict[str, Any]:
    """It comes off every open month it was saved in. Refused once it is on a
    month whose statement has been finalized. Returns the staff."""
    try:
        return househelp.delete_role(db, apartment_id, role_id, actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc


# --------------------------------------------------------------- the month --

@router.get("/month/{settlement_month}", summary="A month's house help and paying it")
def get_month(db: Db, apartment_id: ApartmentId,
              settlement_month: SettlementMonth) -> dict[str, Any]:
    """Each role's attendance and salary, each worker's pay, and each flatmate's
    share. A month not saved yet is a preview: nobody absent."""
    try:
        return househelp.get_month_overview(db, apartment_id, settlement_month)
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/month/{settlement_month}/attendance",
             summary="Save a month's attendance and salaries, and bill it")
def save_attendance(payload: HouseHelpAttendanceSaveRequest, db: Db, apartment_id: ApartmentId,
                    settings: AppSettings, settlement_month: SettlementMonth) -> dict[str, Any]:
    """The roles sent are the month's. Until its statement is finalized it can be
    saved again: what each flatmate paid follows their new share."""
    try:
        return househelp.save_monthly_attendance(
            db, apartment_id, settlement_month,
            [record.model_dump() for record in payload.records], actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc


# --------------------------------------------------------------- paying it --

@router.post("/record-payment", status_code=201,
             summary="Record a flatmate paying their house help share into the kitty")
def record_payment(payload: HouseHelpPaymentCreate, db: Db, apartment_id: ApartmentId,
                   settings: AppSettings) -> dict[str, Any]:
    """Settles their share of the month first; anything over goes to their other
    charges, oldest first."""
    try:
        return househelp.record_payment(db, apartment_id, payload.model_dump(),
                                        actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/disburse-payout", status_code=201,
             summary="Record paying a worker their salary for a month")
def disburse_payout(payload: HouseHelpPayoutRequest, db: Db, apartment_id: ApartmentId,
                    settings: AppSettings) -> dict[str, Any]:
    """From the House Kitty, or by a flatmate from their own account: whatever
    they paid beyond their own share the kitty pays back to them."""
    try:
        return househelp.disburse_worker_payout(db, apartment_id, payload.model_dump(),
                                                actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/reimburse-from-kitty", status_code=201,
             summary="Pay back, from the kitty, a flatmate who paid a worker")
def reimburse_from_kitty(payload: HouseHelpReimburseRequest, db: Db, apartment_id: ApartmentId,
                         settings: AppSettings) -> dict[str, Any]:
    """A refund out of what flatmates paid in for the month: never more than the
    kitty holds for it, nor more than the flatmate is still owed."""
    data = payload.model_dump()
    data["payment_date"] = data.pop("disbursed_date")
    try:
        return househelp.reimburse_from_kitty(db, apartment_id, data,
                                              actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc


@router.delete("/payments/{payment_id}",
               summary="Delete a house help payment or reimbursement recorded by mistake")
def delete_payment(db: Db, apartment_id: ApartmentId, settings: AppSettings,
                   payment_id: str = Path()) -> dict[str, Any]:
    """A flatmate's payment into the kitty, or the kitty's reimbursement, deleted
    outright while its month is open. Returns that month's overview."""
    try:
        return househelp.delete_payment(db, apartment_id, payment_id,
                                        actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc


@router.delete("/month/{settlement_month}/disbursements/{disbursement_id}",
               summary="Delete a payout to a worker recorded by mistake")
def delete_disbursement(db: Db, apartment_id: ApartmentId, settings: AppSettings,
                        settlement_month: SettlementMonth,
                        disbursement_id: str = Path()) -> dict[str, Any]:
    """Deleted outright while the month is open, with whatever was recorded with
    it. Refused while the kitty has reimbursed its advance. Returns the month."""
    try:
        return househelp.delete_disbursement(db, apartment_id, disbursement_id,
                                             settlement_month=settlement_month,
                                             actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc

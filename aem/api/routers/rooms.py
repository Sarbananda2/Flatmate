"""Property & Rooms: the household's room inventory and its occupants."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path

from ...errors import AemError
from ...schemas import (
    RoomAssignRequest,
    RoomCreate,
    RoomResponse,
    RoomUpdate,
    RoomVacateRequest,
)
from ...services import rooms
from ..deps import ApartmentId, AppSettings, Db, to_http

router = APIRouter(prefix="/api/property/rooms", tags=["property & rooms"])


@router.get("", summary="List the rooms and who occupies them")
def list_rooms(db: Db, apartment_id: ApartmentId) -> dict[str, Any]:
    """Every room in the order it was added, with counts of occupied and
    vacant rooms and of each amenity."""
    try:
        return rooms.list_rooms(db, apartment_id)
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("", status_code=201, response_model=RoomResponse, summary="Add a room")
def create_room(payload: RoomCreate, db: Db, apartment_id: ApartmentId,
                settings: AppSettings) -> dict[str, Any]:
    try:
        return rooms.create_room(db, apartment_id, payload.model_dump(exclude_none=True),
                                 actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc


@router.patch("/{room_id}", response_model=RoomResponse, summary="Change a room")
def update_room(payload: RoomUpdate, db: Db, apartment_id: ApartmentId,
                settings: AppSettings, room_id: str = Path()) -> dict[str, Any]:
    """Only the fields sent are changed. ``assigned_resident_id: null`` vacates."""
    try:
        return rooms.update_room(db, apartment_id, room_id,
                                 payload.model_dump(exclude_unset=True),
                                 actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/{room_id}/assign", response_model=RoomResponse,
             summary="Assign or swap a room's occupant")
def assign(payload: RoomAssignRequest, db: Db, apartment_id: ApartmentId,
           settings: AppSettings, room_id: str = Path()) -> dict[str, Any]:
    """The resident must be active and not already in another room. Whoever
    occupied this room moves out of it."""
    try:
        return rooms.assign_resident(db, apartment_id, room_id, payload.resident_id,
                                     effective_date=payload.effective_date,
                                     actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/{room_id}/vacate", response_model=RoomResponse, summary="Vacate a room")
def vacate(db: Db, apartment_id: ApartmentId, settings: AppSettings,
           room_id: str = Path(), payload: RoomVacateRequest | None = None) -> dict[str, Any]:
    try:
        return rooms.vacate_room(db, apartment_id, room_id, actor=settings.operator_name,
                                 reason=payload.reason if payload else None)
    except AemError as exc:
        raise to_http(exc) from exc


@router.delete("/{room_id}", status_code=204, summary="Remove a vacant room")
def delete_room(db: Db, apartment_id: ApartmentId, settings: AppSettings,
                room_id: str = Path()) -> None:
    try:
        rooms.delete_room(db, apartment_id, room_id, actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc

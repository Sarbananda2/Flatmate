"""The household's rooms: what each one has, and who lives in it.

This is the physical inventory, not money. A room has a number that is unique
in the flat (ignoring case), an attached washroom or not, a balcony or not,
and at most one occupant. A resident occupies at most one room, and only an
active resident can be given one. Moving somebody into an occupied room moves
its current occupant out; moving somebody who already has a room elsewhere is
refused, so a room is never emptied as a side effect.

Every change is written to the audit trail. A move-out clearance frees the
leaver's room in the same transaction, through :func:`vacate_resident_in`.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Mapping

from ..errors import ConflictError, NotFoundError, ValidationError
from ..ids import new_id, now_timestamp
from ..periods import parse_iso_date
from ..persistence import Database, UnitOfWork
from . import audit
from .common import get_apartment, get_resident, require_text

ENTITY = "household_room"
EARLIEST_DATE = "2000-01-01"
_FIELDS = ("room_number", "has_attached_washroom", "has_balcony", "assigned_resident_id")


# ------------------------------------------------------------------- helpers --

def _present(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "room_id": row["room_id"],
        "apartment_id": row["apartment_id"],
        "room_number": row["room_number"],
        "has_attached_washroom": bool(row["has_attached_washroom"]),
        "has_balcony": bool(row["has_balcony"]),
        "assigned_resident_id": row["assigned_resident_id"],
        "assigned_display_name": row["assigned_display_name"],
        "assigned_since": row["assigned_since"],
        "status": "occupied" if row["assigned_resident_id"] else "vacant",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _audit_value(row: Mapping[str, Any]) -> dict[str, Any]:
    return {"room_number": row["room_number"],
            "has_attached_washroom": bool(row["has_attached_washroom"]),
            "has_balcony": bool(row["has_balcony"]),
            "assigned_resident_id": row["assigned_resident_id"],
            "assigned_since": row["assigned_since"]}


def _room(uow: UnitOfWork, room_id: str, apartment_id: str) -> dict[str, Any]:
    row = uow.rooms.get(room_id)
    if row is None or row["apartment_id"] != apartment_id:
        raise NotFoundError("Room not found.", details={"room_id": room_id})
    return row


def _flag(value: Any, field: str) -> int:
    if not isinstance(value, bool):
        raise ValidationError(f"{field} must be true or false.", details={"field": field})
    return 1 if value else 0


def _room_number(uow: UnitOfWork, apartment_id: str, value: Any,
                 room_id: str | None = None) -> str:
    number = require_text(value, "room_number", max_length=50)
    clash = uow.rooms.find_by_number(apartment_id, number)
    if clash and clash["room_id"] != room_id:
        raise ConflictError(f"{clash['room_number']} already exists in this flat.",
                            code="room_number_taken",
                            details={"field": "room_number", "room_id": clash["room_id"]})
    return number


def _effective_date(value: Any) -> str:
    if value is None or value == "":
        return date.today().isoformat()
    if not isinstance(value, str):
        raise ValidationError("effective_date must use YYYY-MM-DD format.",
                              details={"field": "effective_date"})
    parsed = parse_iso_date(value, "effective_date").isoformat()
    if parsed > date.today().isoformat():
        raise ValidationError("effective_date cannot be in the future.",
                              details={"field": "effective_date"})
    if parsed < EARLIEST_DATE:
        raise ValidationError("effective_date is too far in the past.",
                              details={"field": "effective_date"})
    return parsed


def _occupant(uow: UnitOfWork, apartment_id: str, resident_id: str,
              room_id: str | None) -> str:
    """A resident who may move into this room: active, and not housed elsewhere."""
    resident = get_resident(uow, resident_id, apartment_id)
    if not resident["is_active"]:
        raise ValidationError(f"{resident['display_name']} has moved out, so cannot be "
                              "given a room.", code="resident_inactive",
                              details={"field": "assigned_resident_id"})
    current = uow.rooms.for_resident(apartment_id, resident_id)
    if current and current["room_id"] != room_id:
        raise ConflictError(
            f"{resident['display_name']} already occupies {current['room_number']}. "
            "Vacate that room first.", code="resident_has_room",
            details={"field": "assigned_resident_id", "room_id": current["room_id"]})
    return resident_id


def _save(uow: UnitOfWork, before: dict[str, Any], after: dict[str, Any], *,
          action: str, actor: str, reason: str | None = None) -> dict[str, Any]:
    uow.rooms.update(
        before["room_id"], room_number=after["room_number"],
        has_attached_washroom=after["has_attached_washroom"],
        has_balcony=after["has_balcony"],
        assigned_resident_id=after["assigned_resident_id"],
        assigned_since=after["assigned_since"], updated_at=now_timestamp())
    audit.record(uow, entity_type=ENTITY, entity_id=before["room_id"], action=action,
                 actor=actor, apartment_id=before["apartment_id"], reason=reason,
                 old_value=_audit_value(before), new_value=_audit_value(after))
    return _present(uow.rooms.get(before["room_id"]))


# ----------------------------------------------------------------- reading --

def summarize(rooms: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total_rooms": len(rooms),
        "occupied_rooms": sum(1 for room in rooms if room["status"] == "occupied"),
        "vacant_rooms": sum(1 for room in rooms if room["status"] == "vacant"),
        "with_attached_washroom": sum(1 for room in rooms if room["has_attached_washroom"]),
        "with_balcony": sum(1 for room in rooms if room["has_balcony"]),
    }


def list_rooms(db: Database, apartment_id: str) -> dict[str, Any]:
    """Every room with its occupant, and counts for the hub card."""
    with db.read() as uow:
        get_apartment(uow, apartment_id)
        items = [_present(row) for row in uow.rooms.list_for_apartment(apartment_id)]
    return {"items": items, "total": len(items), "summary": summarize(items)}


def room_for_resident(db: Database, apartment_id: str,
                      resident_id: str) -> dict[str, Any] | None:
    with db.read() as uow:
        get_resident(uow, resident_id, apartment_id)
        row = uow.rooms.for_resident(apartment_id, resident_id)
    return _present(row) if row else None


# ----------------------------------------------------------------- writing --

def create_room(db: Database, apartment_id: str, payload: Mapping[str, Any], *,
                actor: str = "operator") -> dict[str, Any]:
    unknown = sorted(set(payload) - {*_FIELDS, "effective_date"})
    if unknown:
        raise ValidationError(f"Unsupported room fields: {', '.join(unknown)}.",
                              details={"unsupported_fields": unknown})
    with db.transaction() as uow:
        get_apartment(uow, apartment_id)
        number = _room_number(uow, apartment_id, payload.get("room_number"))
        occupant = payload.get("assigned_resident_id") or None
        since = None
        if occupant:
            occupant = _occupant(uow, apartment_id, occupant, None)
            since = _effective_date(payload.get("effective_date"))
        room_id = new_id("room")
        uow.rooms.insert(
            room_id=room_id, apartment_id=apartment_id, room_number=number,
            has_attached_washroom=_flag(payload.get("has_attached_washroom", False),
                                        "has_attached_washroom"),
            has_balcony=_flag(payload.get("has_balcony", False), "has_balcony"),
            assigned_resident_id=occupant, assigned_since=since, created_at=now_timestamp())
        row = uow.rooms.get(room_id)
        audit.record(uow, entity_type=ENTITY, entity_id=room_id, action="create",
                     actor=actor, apartment_id=apartment_id, new_value=_audit_value(row))
        return _present(row)


def update_room(db: Database, apartment_id: str, room_id: str,
                changes: Mapping[str, Any], *, actor: str = "operator") -> dict[str, Any]:
    """Change the number or amenities. ``assigned_resident_id`` set to a resident
    assigns them; set to null, it vacates the room."""
    unknown = sorted(set(changes) - {*_FIELDS, "effective_date"})
    if unknown:
        raise ValidationError(f"Unsupported room fields: {', '.join(unknown)}.",
                              details={"unsupported_fields": unknown})
    with db.transaction() as uow:
        before = _room(uow, room_id, apartment_id)
        after = dict(before)
        if "room_number" in changes:
            after["room_number"] = _room_number(uow, apartment_id, changes["room_number"],
                                                room_id)
        for field in ("has_attached_washroom", "has_balcony"):
            if field in changes:
                after[field] = _flag(changes[field], field)
        if "assigned_resident_id" in changes:
            occupant = changes["assigned_resident_id"] or None
            if occupant is None:
                after["assigned_resident_id"] = after["assigned_since"] = None
            elif occupant != before["assigned_resident_id"]:
                after["assigned_resident_id"] = _occupant(uow, apartment_id, occupant, room_id)
                after["assigned_since"] = _effective_date(changes.get("effective_date"))
        if all(after[key] == before[key] for key in (*_FIELDS, "assigned_since")):
            return _present(before)
        return _save(uow, before, after, action="update", actor=actor)


def assign_resident(db: Database, apartment_id: str, room_id: str, resident_id: str, *,
                    effective_date: str | None = None,
                    actor: str = "operator") -> dict[str, Any]:
    """Move a resident into the room. Whoever was in it moves out of it."""
    if not isinstance(resident_id, str) or not resident_id.strip():
        raise ValidationError("resident_id is required.", details={"field": "resident_id"})
    with db.transaction() as uow:
        before = _room(uow, room_id, apartment_id)
        if before["assigned_resident_id"] == resident_id:
            raise ConflictError(f"{before['assigned_display_name']} already occupies "
                                f"{before['room_number']}.", code="already_assigned")
        after = {**before, "assigned_resident_id": _occupant(uow, apartment_id, resident_id,
                                                             room_id),
                 "assigned_since": _effective_date(effective_date)}
        return _save(uow, before, after,
                     action="swap_occupant" if before["assigned_resident_id"] else "assign",
                     actor=actor)


def vacate_room(db: Database, apartment_id: str, room_id: str, *,
                actor: str = "operator", reason: str | None = None) -> dict[str, Any]:
    with db.transaction() as uow:
        before = _room(uow, room_id, apartment_id)
        if not before["assigned_resident_id"]:
            raise ConflictError(f"{before['room_number']} is already vacant.",
                                code="room_vacant")
        return _save(uow, before, {**before, "assigned_resident_id": None,
                                   "assigned_since": None},
                     action="vacate", actor=actor, reason=reason)


def vacate_resident_in(uow: UnitOfWork, apartment_id: str, resident_id: str, *,
                       actor: str = "operator", reason: str) -> dict[str, Any] | None:
    """Free whatever room the resident occupies, inside the caller's transaction.
    Returns the room as it was, or None if they had none."""
    before = uow.rooms.for_resident(apartment_id, resident_id)
    if before is None:
        return None
    _save(uow, before, {**before, "assigned_resident_id": None, "assigned_since": None},
          action="vacate", actor=actor, reason=reason)
    return _present(before)


def link_slot_to_room(db: Database, apartment_id: str, slot_id: str, room_id: str, *,
                      actor: str = "operator") -> bool:
    """Say which room a deposit slot belongs to. False if it already said so."""
    with db.transaction() as uow:
        room = _room(uow, room_id, apartment_id)
        slot = uow.deposits.slot(slot_id)
        if slot is None or slot["apartment_id"] != apartment_id:
            raise NotFoundError("Deposit slot not found.", details={"slot_id": slot_id})
        if slot.get("room_id") == room_id:
            return False
        uow.rooms.link_slot(slot_id, room_id, updated_at=now_timestamp())
        audit.record(uow, entity_type="household_deposit_slot", entity_id=slot_id,
                     action="link_room", actor=actor, apartment_id=apartment_id,
                     old_value={"room_id": slot.get("room_id")},
                     new_value={"room_id": room_id, "room_number": room["room_number"]})
        return True


def delete_room(db: Database, apartment_id: str, room_id: str, *,
                actor: str = "operator") -> None:
    """Remove a room from the inventory. Only a vacant room can be removed."""
    with db.transaction() as uow:
        before = _room(uow, room_id, apartment_id)
        if before["assigned_resident_id"]:
            raise ConflictError(
                f"{before['room_number']} is occupied by {before['assigned_display_name']}. "
                "Vacate it before removing it.", code="room_occupied")
        uow.rooms.unlink_slots(room_id)
        uow.rooms.delete(room_id)
        audit.record(uow, entity_type=ENTITY, entity_id=room_id, action="delete",
                     actor=actor, apartment_id=apartment_id, old_value=_audit_value(before))

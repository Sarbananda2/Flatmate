"""The household's rooms and who occupies them."""
from __future__ import annotations

from typing import Any

from .base import Repository

_SELECT = """SELECT h.*, r.display_name AS assigned_display_name,
                    s.slot_id AS slot_id, s.slot_number AS slot_number,
                    s.slot_label AS slot_label, s.target_deposit_paise AS slot_target_paise,
                    s.funded_amount_paise AS slot_funded_paise, s.status AS slot_status
               FROM household_room h
               LEFT JOIN resident r ON r.resident_id = h.assigned_resident_id
               LEFT JOIN household_deposit_slot s ON s.room_id = h.room_id"""


class RoomsRepository(Repository):
    def list_for_apartment(self, apartment_id: str) -> list[dict[str, Any]]:
        """Rooms in the order they were added."""
        return self._s.fetch_all(
            f"{_SELECT} WHERE h.apartment_id = %s ORDER BY h.row_seq", (apartment_id,))

    def get(self, room_id: str) -> dict[str, Any] | None:
        return self._s.fetch_one(f"{_SELECT} WHERE h.room_id = %s", (room_id,))

    def for_resident(self, apartment_id: str, resident_id: str) -> dict[str, Any] | None:
        return self._s.fetch_one(
            f"{_SELECT} WHERE h.apartment_id = %s AND h.assigned_resident_id = %s",
            (apartment_id, resident_id))

    def find_by_number(self, apartment_id: str, room_number: str) -> dict[str, Any] | None:
        """A room with this number, ignoring case and surrounding spaces."""
        return self._s.fetch_one(
            "SELECT room_id, room_number FROM household_room "
            "WHERE apartment_id = %s AND lower(trim(room_number)) = lower(trim(%s))",
            (apartment_id, room_number))

    def insert(self, *, room_id: str, apartment_id: str, room_number: str,
               has_attached_washroom: int, has_balcony: int, base_rent_paise: int | None,
               assigned_resident_id: str | None, assigned_since: str | None,
               created_at: str) -> None:
        self._s.execute(
            """INSERT INTO household_room (room_id, apartment_id, room_number,
                   has_attached_washroom, has_balcony, base_rent_paise,
                   assigned_resident_id, assigned_since, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (room_id, apartment_id, room_number, has_attached_washroom, has_balcony,
             base_rent_paise, assigned_resident_id, assigned_since, created_at, created_at))

    def update(self, room_id: str, *, room_number: str, has_attached_washroom: int,
               has_balcony: int, base_rent_paise: int | None,
               assigned_resident_id: str | None, assigned_since: str | None,
               updated_at: str) -> None:
        self._s.execute(
            """UPDATE household_room
                  SET room_number = %s, has_attached_washroom = %s, has_balcony = %s,
                      base_rent_paise = %s, assigned_resident_id = %s,
                      assigned_since = %s, updated_at = %s
                WHERE room_id = %s""",
            (room_number, has_attached_washroom, has_balcony, base_rent_paise,
             assigned_resident_id, assigned_since, updated_at, room_id))

    def slot_for_room(self, room_id: str) -> dict[str, Any] | None:
        return self._s.fetch_one(
            "SELECT * FROM household_deposit_slot WHERE room_id = %s", (room_id,))

    def link_slot(self, slot_id: str, room_id: str | None, *, updated_at: str) -> None:
        self._s.execute(
            "UPDATE household_deposit_slot SET room_id = %s, updated_at = %s WHERE slot_id = %s",
            (room_id, updated_at, slot_id))

    def unlink_slots(self, room_id: str) -> None:
        self._s.execute(
            "UPDATE household_deposit_slot SET room_id = NULL WHERE room_id = %s", (room_id,))

    def delete(self, room_id: str) -> None:
        self._s.execute("DELETE FROM household_room WHERE room_id = %s", (room_id,))

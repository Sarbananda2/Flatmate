"""Residents."""
from __future__ import annotations

from typing import Any

from .base import Repository


class ResidentsRepository(Repository):
    def get(self, resident_id: str) -> dict[str, Any] | None:
        return self._s.fetch_one("SELECT * FROM resident WHERE resident_id = %s",
                                 (resident_id,))

    def list_for_apartment(self, apartment_id: str, *,
                           active_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM resident WHERE apartment_id = %s"
        if active_only:
            sql += " AND is_active = 1"
        sql += " ORDER BY display_name, resident_id"
        return self._s.fetch_all(sql, (apartment_id,))

    def ids_for_apartment(self, apartment_id: str, *, active_only: bool = False) -> list[str]:
        """Resident IDs, in no particular order."""
        sql = "SELECT resident_id FROM resident WHERE apartment_id = %s"
        if active_only:
            sql += " AND is_active = 1"
        return [row["resident_id"] for row in self._s.fetch_all(sql, (apartment_id,))]

    def exists(self, resident_id: str) -> bool:
        return self._s.fetch_one("SELECT 1 AS present FROM resident WHERE resident_id=%s",
                                 (resident_id,)) is not None

    def insert(self, *, resident_id: str, apartment_id: str, display_name: str,
               active_from: str | None, active_until: str | None, is_active: int,
               email: str | None, phone: str | None, notes: str | None,
               upi_id: str | None, created_at: str, updated_at: str) -> None:
        self._s.execute(
            """INSERT INTO resident (resident_id, apartment_id, display_name, active_from,
                                     active_until, is_active, email, phone, notes,
                                     upi_id, created_at, updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (resident_id, apartment_id, display_name, active_from, active_until,
             is_active, email, phone, notes, upi_id, created_at, updated_at))

    def update(self, resident_id: str, *, display_name: str, active_from: str | None,
               active_until: str | None, is_active: int, email: str | None,
               phone: str | None, notes: str | None, upi_id: str | None,
               updated_at: str) -> None:
        self._s.execute(
            """UPDATE resident SET display_name=%s, active_from=%s, active_until=%s,
                   is_active=%s, email=%s, phone=%s, notes=%s, upi_id=%s, updated_at=%s
               WHERE resident_id=%s""",
            (display_name, active_from, active_until, is_active, email, phone, notes,
             upi_id, updated_at, resident_id))

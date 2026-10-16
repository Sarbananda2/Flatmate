"""Apartments and operator settings."""
from __future__ import annotations

from typing import Any

from .base import Repository


class ApartmentsRepository(Repository):
    def get(self, apartment_id: str) -> dict[str, Any] | None:
        return self._s.fetch_one("SELECT * FROM apartment WHERE apartment_id = %s",
                                 (apartment_id,))

    def exists(self, apartment_id: str) -> bool:
        return self._s.fetch_one("SELECT 1 AS present FROM apartment WHERE apartment_id = %s",
                                 (apartment_id,)) is not None

    def insert(self, *, apartment_id: str, name: str, currency_code: str, timezone: str,
               created_at: str) -> None:
        self._s.execute(
            """INSERT INTO apartment (apartment_id, name, currency_code, timezone, created_at)
               VALUES (%s,%s,%s,%s,%s)""",
            (apartment_id, name, currency_code, timezone, created_at))

    def update(self, apartment_id: str, *, name: str, currency_code: str, timezone: str,
               flat_number: str | None, tower_block: str | None, society_name: str | None,
               locality_street: str | None, city: str | None, state: str | None,
               pincode: str | None, pool_name: str, pool_bank_account: str | None,
               pool_bank_ifsc: str | None, pool_upi_id: str | None) -> None:
        self._s.execute(
            """UPDATE apartment SET name=%s, currency_code=%s, timezone=%s, flat_number=%s,
                                    tower_block=%s, society_name=%s, locality_street=%s,
                                    city=%s, state=%s, pincode=%s, pool_name=%s,
                                    pool_bank_account=%s, pool_bank_ifsc=%s, pool_upi_id=%s
               WHERE apartment_id=%s""",
            (name, currency_code, timezone, flat_number, tower_block, society_name,
             locality_street, city, state, pincode, pool_name, pool_bank_account,
             pool_bank_ifsc, pool_upi_id, apartment_id))

    def list_all(self) -> list[dict[str, Any]]:
        return self._s.fetch_all("SELECT * FROM apartment ORDER BY name, apartment_id")

    def count(self) -> int:
        return int(self._s.scalar("SELECT COUNT(*) FROM apartment", (), 0))

    def first_apartment_id(self) -> str | None:
        """The earliest-created apartment, or ``None`` when there is none."""
        row = self._s.fetch_one(
            "SELECT apartment_id FROM apartment ORDER BY created_at, apartment_id")
        return row["apartment_id"] if row else None

    def get_setting(self, key: str) -> str | None:
        row = self._s.fetch_one(
            "SELECT setting_value FROM app_setting WHERE setting_key=%s", (key,))
        return row["setting_value"] if row else None

    def set_setting(self, key: str, value: str, *, updated_at: str) -> None:
        self._s.execute(
            """INSERT INTO app_setting (setting_key, setting_value, updated_at) VALUES (%s,%s,%s)
               ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value,
                                                     updated_at=excluded.updated_at""",
            (key, value, updated_at))

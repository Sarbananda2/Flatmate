"""Broadband & WiFi: a household service on a fixed monthly plan.

The service row and its plan live here. A month's bill, its charges and its
payouts to the ISP are the ones every household service shares, read through
:class:`~.furniture_rental.FurnitureRentalRepository` (``uow.furniture``).
"""
from __future__ import annotations

from typing import Any

from .base import Repository

#: The household service type broadband is (migration 0022).
SERVICE_TYPE = "broadband"

#: Plan columns a caller may change. Anything else is refused, so no
#: caller-supplied name ever reaches the SQL.
CONFIG_COLUMNS = ("provider_name", "account_number", "monthly_rate_paise", "speed_mbps",
                  "billing_day_of_month", "expense_definition_id", "wifi_ssid", "notes")


class BroadbandRepository(Repository):
    # ----------------------------------------------------------- the service --

    def live_service(self, apartment_id: str) -> dict[str, Any] | None:
        """The flat's broadband, if it has one that has not been retired."""
        return self._s.fetch_one(
            """SELECT * FROM household_service
                WHERE apartment_id=%s AND service_type=%s AND status <> 'retired'
                ORDER BY row_seq LIMIT 1""",
            (apartment_id, SERVICE_TYPE))

    def insert_service(self, *, service_id: str, apartment_id: str, display_name: str,
                       provider_name: str, billing_day_of_month: int | None,
                       created_at: str) -> None:
        self._s.execute(
            """INSERT INTO household_service (service_id, apartment_id, service_type,
                   display_name, provider_name, billing_day_of_month, created_at, updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (service_id, apartment_id, SERVICE_TYPE, display_name, provider_name,
             billing_day_of_month, created_at, created_at))

    def update_service(self, service_id: str, *, display_name: str, provider_name: str,
                       billing_day_of_month: int | None, updated_at: str) -> None:
        self._s.execute(
            """UPDATE household_service SET display_name=%s, provider_name=%s,
                   billing_day_of_month=%s, updated_at=%s WHERE service_id=%s""",
            (display_name, provider_name, billing_day_of_month, updated_at, service_id))

    # -------------------------------------------------------------- the plan --

    def config(self, service_id: str) -> dict[str, Any] | None:
        return self._s.fetch_one(
            "SELECT * FROM broadband_service_config WHERE service_id=%s", (service_id,))

    def config_for_apartment(self, apartment_id: str) -> dict[str, Any] | None:
        """The plan of the flat's live broadband, if it has one."""
        return self._s.fetch_one(
            """SELECT c.* FROM broadband_service_config c
                 JOIN household_service s ON s.service_id = c.service_id
                WHERE c.apartment_id=%s AND s.status <> 'retired'
                ORDER BY s.row_seq LIMIT 1""", (apartment_id,))

    def insert_config(self, *, service_id: str, apartment_id: str, created_at: str,
                      **values: Any) -> None:
        columns = [column for column in CONFIG_COLUMNS if column in values]
        if set(values) - set(columns):
            raise ValueError(f"Not plan columns: {sorted(set(values) - set(columns))}")
        names = ", ".join(["service_id", "apartment_id", "created_at", "updated_at", *columns])
        params = (service_id, apartment_id, created_at, created_at, *(values[c] for c in columns))
        self._s.execute(
            f"INSERT INTO broadband_service_config ({names}) "
            f"VALUES ({', '.join(['%s'] * len(params))})", params)

    def update_config(self, service_id: str, *, updated_at: str, **values: Any) -> None:
        columns = [column for column in CONFIG_COLUMNS if column in values]
        if set(values) - set(columns):
            raise ValueError(f"Not plan columns: {sorted(set(values) - set(columns))}")
        sets = "".join(f", {column}=%s" for column in columns)
        self._s.execute(
            f"UPDATE broadband_service_config SET updated_at=%s{sets} WHERE service_id=%s",
            (updated_at, *(values[c] for c in columns), service_id))

    def find_definition_id(self, apartment_id: str) -> str | None:
        """The active definition called, or with the ID, 'Broadband' (or 'Internet', 'WiFi')."""
        return self._s.scalar(
            """SELECT expense_definition_id FROM expense_definition
                WHERE apartment_id=%s AND is_active=1
                  AND (lower(trim(name)) IN ('broadband', 'broadband & wifi',
                                             'broadband and wifi', 'internet', 'wifi',
                                             'wi-fi')
                       OR lower(expense_definition_id) IN ('broadband', 'broadband-wifi',
                                                           'broadband_wifi', 'internet',
                                                           'wifi'))
                ORDER BY sort_order, expense_definition_id LIMIT 1""", (apartment_id,))

    def definition_id_taken(self, definition_id: str) -> bool:
        return self._s.fetch_one(
            "SELECT 1 AS present FROM expense_definition WHERE expense_definition_id=%s",
            (definition_id,)) is not None

"""The water supply: a household service on a fixed monthly contract.

The service row and its contract live here. A month's bill, its charges and
its payouts to the supplier are the ones every household service shares, read
through :class:`~.furniture_rental.FurnitureRentalRepository` (``uow.furniture``).
"""
from __future__ import annotations

from typing import Any

from .base import Repository

#: The household service type a water supply is (migration 0022).
SERVICE_TYPE = "water_tanker"

#: Contract columns a caller may change. Anything else is refused, so no
#: caller-supplied name ever reaches the SQL.
CONFIG_COLUMNS = ("provider_name", "monthly_rate_paise", "consumer_id", "billing_day_of_month",
                  "expense_definition_id", "pipeline_notes", "notes")


class WaterRepository(Repository):
    # ----------------------------------------------------------- the service --

    def live_service(self, apartment_id: str) -> dict[str, Any] | None:
        """The flat's water supply, if it has one that has not been retired."""
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

    # ---------------------------------------------------------- the contract --

    def config(self, service_id: str) -> dict[str, Any] | None:
        return self._s.fetch_one(
            "SELECT * FROM water_service_config WHERE service_id=%s", (service_id,))

    def insert_config(self, *, service_id: str, apartment_id: str, created_at: str,
                      **values: Any) -> None:
        columns = [column for column in CONFIG_COLUMNS if column in values]
        if set(values) - set(columns):
            raise ValueError(f"Not contract columns: {sorted(set(values) - set(columns))}")
        names = ", ".join(["service_id", "apartment_id", "created_at", "updated_at", *columns])
        params = (service_id, apartment_id, created_at, created_at, *(values[c] for c in columns))
        self._s.execute(
            f"INSERT INTO water_service_config ({names}) "
            f"VALUES ({', '.join(['%s'] * len(params))})", params)

    def update_config(self, service_id: str, *, updated_at: str, **values: Any) -> None:
        columns = [column for column in CONFIG_COLUMNS if column in values]
        if set(values) - set(columns):
            raise ValueError(f"Not contract columns: {sorted(set(values) - set(columns))}")
        sets = "".join(f", {column}=%s" for column in columns)
        self._s.execute(
            f"UPDATE water_service_config SET updated_at=%s{sets} WHERE service_id=%s",
            (updated_at, *(values[c] for c in columns), service_id))

    def find_definition_id(self, apartment_id: str) -> str | None:
        """The active definition called, or with the ID, 'Water' (or 'Water supply')."""
        return self._s.scalar(
            """SELECT expense_definition_id FROM expense_definition
                WHERE apartment_id=%s AND is_active=1
                  AND (lower(trim(name)) IN ('water', 'water supply')
                       OR lower(expense_definition_id) IN ('water', 'water-supply',
                                                           'water_supply'))
                ORDER BY sort_order, expense_definition_id LIMIT 1""", (apartment_id,))

    def definition_id_taken(self, definition_id: str) -> bool:
        return self._s.fetch_one(
            "SELECT 1 AS present FROM expense_definition WHERE expense_definition_id=%s",
            (definition_id,)) is not None

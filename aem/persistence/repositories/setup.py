"""Guided setup."""
from __future__ import annotations

from typing import Any

from .base import Repository


class SetupRepository(Repository):
    # The identifier columns the guided setup derives new IDs for. Fixed SQL per
    # pair: no identifier is ever interpolated into a statement.
    _TAKEN_IDS_SQL = {
        ("apartment", "apartment_id"): "SELECT apartment_id AS id FROM apartment",
        ("resident", "resident_id"): "SELECT resident_id AS id FROM resident",
        ("expense_definition", "expense_definition_id"):
            "SELECT expense_definition_id AS id FROM expense_definition",
    }

    def taken_ids(self, table: str, column: str) -> set[str]:
        """Every identifier already in use in one of the supported ID columns."""
        try:
            sql = self._TAKEN_IDS_SQL[(table, column)]
        except KeyError:
            raise ValueError(f"Unsupported identifier column: {table}.{column}") from None
        return {row["id"] for row in self._s.fetch_all(sql)}

    def apartment_count(self) -> int:
        return int(self._s.scalar("SELECT COUNT(*) FROM apartment", default=0))

    def residents_overview(self, apartment_id: str) -> list[dict[str, Any]]:
        return self._s.fetch_all(
            "SELECT resident_id, display_name, is_active FROM resident "
            "WHERE apartment_id=%s ORDER BY display_name", (apartment_id,))

    def active_definitions_preview(self, apartment_id: str) -> list[dict[str, Any]]:
        return self._s.fetch_all(
            "SELECT expense_definition_id, name, category, period_rule "
            "FROM expense_definition WHERE apartment_id=%s AND is_active=1 "
            "ORDER BY sort_order, name", (apartment_id,))

    def statement_count(self, apartment_id: str) -> int:
        return int(self._s.scalar(
            "SELECT COUNT(*) FROM monthly_statement WHERE apartment_id=%s",
            (apartment_id,), default=0))

    def setting_value(self, key: str) -> str | None:
        return self._s.scalar(
            "SELECT setting_value FROM app_setting WHERE setting_key=%s", (key,))

    def delete_setting(self, key: str) -> None:
        self._s.execute("DELETE FROM app_setting WHERE setting_key=%s", (key,))

    def resident_names(self, apartment_id: str) -> list[dict[str, Any]]:
        """``resident_id`` and ``display_name`` of every resident, in no particular order."""
        return self._s.fetch_all(
            "SELECT resident_id, display_name FROM resident "
            "WHERE apartment_id=%s", (apartment_id,))

    def definition_names(self, apartment_id: str) -> list[str]:
        return [row["name"] for row in self._s.fetch_all(
            "SELECT name FROM expense_definition WHERE apartment_id=%s",
            (apartment_id,))]

    def insert_resident(self, *, resident_id: str, apartment_id: str, display_name: str,
                        created_at: str, updated_at: str) -> None:
        self._s.execute(
            """INSERT INTO resident
               (resident_id, apartment_id, display_name, is_active, created_at, updated_at)
               VALUES (%s,%s,%s,1,%s,%s)""",
            (resident_id, apartment_id, display_name, created_at, updated_at))

    def insert_definition(self, *, expense_definition_id: str, apartment_id: str, name: str,
                          category: str, period_rule: str, default_allocation_method: str,
                          is_recurring: int, recurrence: str,
                          default_amount_paise: int | None, sort_order: int,
                          created_at: str, updated_at: str) -> None:
        self._s.execute(
            """INSERT INTO expense_definition
               (expense_definition_id, apartment_id, name, category, period_rule,
                default_allocation_method, is_recurring, is_active, recurrence,
                default_amount_paise, sort_order, created_at, updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,1,%s,%s,%s,%s,%s)""",
            (expense_definition_id, apartment_id, name, category, period_rule,
             default_allocation_method, is_recurring, recurrence, default_amount_paise,
             sort_order, created_at, updated_at))

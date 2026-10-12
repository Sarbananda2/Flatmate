"""Expense definitions (categories)."""
from __future__ import annotations

from typing import Any

from .base import Repository


class DefinitionsRepository(Repository):
    def get(self, expense_definition_id: str) -> dict[str, Any] | None:
        return self._s.fetch_one(
            "SELECT * FROM expense_definition WHERE expense_definition_id=%s",
            (expense_definition_id,))

    def exists(self, expense_definition_id: str) -> bool:
        return self._s.fetch_one(
            "SELECT 1 AS found FROM expense_definition WHERE expense_definition_id=%s",
            (expense_definition_id,)) is not None

    def list_for_apartment(self, apartment_id: str, *,
                           include_inactive: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM expense_definition WHERE apartment_id=%s"
        if not include_inactive:
            sql += " AND is_active=1"
        sql += " ORDER BY sort_order, name"
        return self._s.fetch_all(sql, (apartment_id,))

    def insert_definition(self, *, expense_definition_id: str, apartment_id: str, name: str,
                          category: str, period_rule: str, default_allocation_method: str,
                          is_recurring: int, recurrence: str,
                          default_amount_paise: int | None,
                          default_provider_name: str | None,
                          default_participants_json: str | None,
                          default_weights_json: str | None, day_of_month: int | None,
                          notes: str | None, sort_order: int, created_at: str,
                          updated_at: str) -> None:
        """Insert a definition. It starts active."""
        self._s.execute(
            """INSERT INTO expense_definition
               (expense_definition_id, apartment_id, name, category, period_rule,
                default_allocation_method, is_recurring, is_active, recurrence,
                default_amount_paise, default_provider_name, default_participants_json,
                default_weights_json, day_of_month, notes, sort_order,
                created_at, updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,1,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (expense_definition_id, apartment_id, name, category, period_rule,
             default_allocation_method, int(is_recurring), recurrence, default_amount_paise,
             default_provider_name, default_participants_json, default_weights_json,
             day_of_month, notes, sort_order, created_at, updated_at))

    def update_definition(self, expense_definition_id: str, *, name: str, category: str,
                          period_rule: str, default_allocation_method: str,
                          is_recurring: int, is_active: int, recurrence: str,
                          default_amount_paise: int | None,
                          default_provider_name: str | None,
                          default_participants_json: str | None,
                          default_weights_json: str | None, day_of_month: int | None,
                          notes: str | None, sort_order: int, updated_at: str) -> int:
        return self._s.execute(
            """UPDATE expense_definition SET name=%s, category=%s, period_rule=%s,
                   default_allocation_method=%s, is_recurring=%s, is_active=%s, recurrence=%s,
                   default_amount_paise=%s, default_provider_name=%s,
                   default_participants_json=%s, default_weights_json=%s, day_of_month=%s,
                   notes=%s, sort_order=%s, updated_at=%s
               WHERE expense_definition_id=%s""",
            (name, category, period_rule, default_allocation_method, is_recurring, is_active,
             recurrence, default_amount_paise, default_provider_name,
             default_participants_json, default_weights_json, day_of_month, notes,
             sort_order, updated_at, expense_definition_id))

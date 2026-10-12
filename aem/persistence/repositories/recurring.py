"""Recurring expense generation."""
from __future__ import annotations

from typing import Any

from .base import Repository


class RecurringRepository(Repository):
    def active_definitions(self, apartment_id: str) -> list[dict[str, Any]]:
        return self._s.fetch_all(
            "SELECT * FROM expense_definition WHERE apartment_id=%s AND is_active=1 "
            "ORDER BY sort_order, name", (apartment_id,))

    def expenses_against_definitions(self, apartment_id: str,
                                     settlement_period: str) -> list[dict[str, Any]]:
        """Every expense settling in the month that names a definition, in
        insertion order."""
        return self._s.fetch_all(
            """SELECT expense_definition_id, expense_id, description, amount_paise,
                      generated_from_recurrence
                 FROM expense_instance
                WHERE apartment_id=%s AND settlement_period=%s
                  AND expense_definition_id IS NOT NULL
                ORDER BY row_seq""",
            (apartment_id, settlement_period))

    def statement_for_month(self, apartment_id: str,
                            settlement_month: str) -> dict[str, Any] | None:
        return self._s.fetch_one(
            "SELECT statement_id, status FROM monthly_statement "
            "WHERE apartment_id=%s AND settlement_month=%s",
            (apartment_id, settlement_month))

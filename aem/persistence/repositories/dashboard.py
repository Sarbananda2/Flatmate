"""Read model for the dashboard."""
from __future__ import annotations

from typing import Any

from .base import Repository


class DashboardRepository(Repository):
    def expenses_in_period(self, apartment_id: str, period: str) -> list[dict[str, Any]]:
        """Newest first."""
        return self._s.fetch_all(
            """SELECT e.expense_id, e.description, e.amount_paise, e.expense_date,
                      e.service_period, e.settlement_period, e.expense_kind,
                      e.payer_resident_id, e.provider_name, e.statement_id,
                      d.category, r.display_name AS payer_display_name
                 FROM expense_instance e
                 LEFT JOIN expense_definition d
                        ON d.expense_definition_id = e.expense_definition_id
                 LEFT JOIN resident r ON r.resident_id = e.payer_resident_id
                WHERE e.apartment_id = %s AND e.settlement_period = %s
                ORDER BY e.expense_date DESC, e.row_seq DESC""", (apartment_id, period))

    def active_payments_in_month(self, apartment_id: str, period: str) -> list[dict[str, Any]]:
        """Newest first."""
        return self._s.fetch_all(
            """SELECT p.payment_id, p.payer_resident_id, p.amount_paise, p.payment_date,
                      p.payment_kind, p.payee_or_provider, p.status, p.expense_id,
                      r.display_name AS payer_display_name
                 FROM payment p JOIN resident r ON r.resident_id = p.payer_resident_id
                WHERE p.apartment_id = %s AND substr(p.payment_date, 1, 7) = %s
                  AND p.status = 'active'
                ORDER BY p.payment_date DESC, p.row_seq DESC""", (apartment_id, period))

    def statement_for_month(self, apartment_id: str, period: str) -> dict[str, Any] | None:
        return self._s.fetch_one(
            """SELECT statement_id, settlement_month, status, finalized_at,
                      correction_deadline, closed_at, finalized_by
                 FROM monthly_statement WHERE apartment_id=%s AND settlement_month=%s""",
            (apartment_id, period))

    def statement_summary_for_month(self, apartment_id: str,
                                    period: str) -> dict[str, Any] | None:
        return self._s.fetch_one(
            "SELECT statement_id, settlement_month, status FROM monthly_statement "
            "WHERE apartment_id=%s AND settlement_month=%s", (apartment_id, period))

    def charges_by_resident_in_period(self, apartment_id: str,
                                      period: str) -> list[dict[str, Any]]:
        return self._s.fetch_all(
            """SELECT a.resident_id, r.display_name,
                      SUM(a.amount_paise)::BIGINT AS charged_paise
                 FROM allocation a
                 JOIN expense_instance e ON e.expense_id = a.expense_id
                 JOIN resident r ON r.resident_id = a.resident_id
                WHERE e.apartment_id = %s AND e.settlement_period = %s
                GROUP BY a.resident_id, r.display_name ORDER BY r.display_name""",
            (apartment_id, period))

    def count_active_residents(self, apartment_id: str) -> int:
        return int(self._s.scalar(
            "SELECT COUNT(*) FROM resident WHERE apartment_id=%s AND is_active=1",
            (apartment_id,), default=0))

    def count_active_definitions(self, apartment_id: str) -> int:
        return int(self._s.scalar(
            "SELECT COUNT(*) FROM expense_definition WHERE apartment_id=%s AND is_active=1",
            (apartment_id,), default=0))

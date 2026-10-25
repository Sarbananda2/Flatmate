"""Read model for the accounting validation checks."""
from __future__ import annotations

from typing import Any

from .base import Repository


class ValidationRepository(Repository):
    def expenses_with_allocated_total(self, apartment_id: str) -> list[dict[str, Any]]:
        return self._s.fetch_all(
            """SELECT e.expense_id, e.description, e.amount_paise, e.settlement_period,
                      COALESCE((SELECT SUM(a.amount_paise) FROM allocation a
                                 WHERE a.expense_id = e.expense_id), 0)::BIGINT
                          AS allocated_paise
                 FROM expense_instance e
                WHERE e.apartment_id = %s""", (apartment_id,))

    def allocations_for_apartment(self, apartment_id: str) -> list[dict[str, Any]]:
        return self._s.fetch_all(
            """SELECT a.allocation_id, a.resident_id, a.expense_id
                 FROM allocation a
                 JOIN expense_instance e ON e.expense_id = a.expense_id
                WHERE e.apartment_id = %s""", (apartment_id,))

    def expenses_without_allocations(self, apartment_id: str) -> list[dict[str, Any]]:
        return self._s.fetch_all(
            """SELECT e.expense_id, e.description, e.amount_paise FROM expense_instance e
                WHERE e.apartment_id = %s AND e.amount_paise > 0
                  AND NOT EXISTS (SELECT 1 FROM allocation a
                                   WHERE a.expense_id = e.expense_id)""",
            (apartment_id,))

    def unlinked_expenses_allocated_to(self, apartment_id: str) -> list[dict[str, Any]]:
        """Expenses with no apartment that are charged to this apartment's residents."""
        return self._s.fetch_all(
            """SELECT e.expense_id, e.description FROM expense_instance e
                WHERE e.apartment_id IS NULL
                  AND EXISTS (SELECT 1 FROM allocation a JOIN resident r
                                ON r.resident_id = a.resident_id
                               WHERE a.expense_id = e.expense_id AND r.apartment_id = %s)""",
            (apartment_id,))

    def payments_with_applied_total(self, apartment_id: str) -> list[dict[str, Any]]:
        return self._s.fetch_all(
            """SELECT p.payment_id, p.amount_paise,
                      COALESCE((SELECT SUM(pa.amount_paise) FROM payment_application pa
                                 WHERE pa.payment_id = p.payment_id), 0)::BIGINT
                          AS applied_paise
                 FROM payment p WHERE p.apartment_id = %s""", (apartment_id,))

    def allocations_with_settled_total(self, apartment_id: str) -> list[dict[str, Any]]:
        return self._s.fetch_all(
            """SELECT a.allocation_id, a.amount_paise, a.resident_id,
                      COALESCE((SELECT SUM(pa.amount_paise) FROM payment_application pa
                                 WHERE pa.allocation_id = a.allocation_id), 0)::BIGINT
                          AS settled_paise
                 FROM allocation a
                 JOIN expense_instance e ON e.expense_id = a.expense_id
                WHERE e.apartment_id = %s""", (apartment_id,))

    def expenses_with_linked_advances(self, apartment_id: str) -> list[dict[str, Any]]:
        """Expenses funded by at least one active linked advance, with the total, and
        how much of it is a resident's tranche of a payout: a month's rent paid to
        the landlord, or a household service's bill paid to its provider."""
        return self._s.fetch_all(
            """SELECT e.expense_id, e.description, e.amount_paise, e.payer_resident_id,
                      COALESCE(SUM(p.amount_paise), 0)::BIGINT AS linked_paise,
                      COALESCE(SUM(p.amount_paise) FILTER (WHERE EXISTS (
                          SELECT 1 FROM rent_disbursement d
                           WHERE d.funding_payment_id = p.payment_id)
                          OR EXISTS (
                          SELECT 1 FROM service_disbursement d
                           WHERE d.funding_payment_id = p.payment_id)), 0)::BIGINT
                          AS tranche_paise
                 FROM expense_instance e
                 JOIN payment p ON p.expense_id = e.expense_id
                WHERE e.apartment_id = %s AND p.payment_kind = 'provider_advance'
                  AND p.status = 'active'
                GROUP BY e.expense_id, e.description, e.amount_paise, e.payer_resident_id""",
            (apartment_id,))

    def unlinked_advances_matching_payer_expenses(
            self, apartment_id: str) -> list[dict[str, Any]]:
        """Unlinked active advances that match an expense's payer and amount,
        where that expense has no linked advance of its own.

        The caller applies the "within seven days" test. It needs both dates to
        be real dates, and a row whose date is not one must be skipped rather
        than fail the query, which a cast in SQL would do.
        """
        return self._s.fetch_all(
            """SELECT p.payment_id, p.payer_resident_id, p.amount_paise, p.payment_date,
                      e.expense_id, e.description, e.expense_date
                 FROM payment p
                 JOIN expense_instance e
                   ON e.apartment_id = p.apartment_id
                  AND e.payer_resident_id = p.payer_resident_id
                  AND e.amount_paise = p.amount_paise
                WHERE p.apartment_id = %s AND p.payment_kind = 'provider_advance'
                  AND p.status = 'active' AND p.expense_id IS NULL
                  AND NOT EXISTS (SELECT 1 FROM payment lp WHERE lp.expense_id = e.expense_id
                                    AND lp.payment_kind = 'provider_advance'
                                    AND lp.status = 'active')""",
            (apartment_id,))

    def inactive_payments_with_application_count(
            self, apartment_id: str) -> list[dict[str, Any]]:
        return self._s.fetch_all(
            """SELECT p.payment_id, p.status,
                      (SELECT COUNT(*) FROM payment_application pa
                        WHERE pa.payment_id = p.payment_id) AS application_count
                 FROM payment p WHERE p.apartment_id = %s AND p.status <> 'active'""",
            (apartment_id,))

    def expenses_on_wrong_period_statement(self, apartment_id: str) -> list[dict[str, Any]]:
        return self._s.fetch_all(
            """SELECT e.expense_id, e.description, e.settlement_period, s.settlement_month,
                      s.statement_id
                 FROM expense_instance e
                 JOIN monthly_statement s ON s.statement_id = e.statement_id
                WHERE e.apartment_id = %s AND e.settlement_period <> s.settlement_month""",
            (apartment_id,))

    def non_draft_statements(self, apartment_id: str) -> list[dict[str, Any]]:
        return self._s.fetch_all(
            """SELECT statement_id, settlement_month, status, snapshot_json
                 FROM monthly_statement WHERE apartment_id = %s AND status <> 'draft'""",
            (apartment_id,))

    def inactive_residents_with_outstanding(self, apartment_id: str) -> list[dict[str, Any]]:
        return self._s.fetch_all(
            """SELECT r.resident_id, r.display_name,
                      (COALESCE((SELECT SUM(a.amount_paise) FROM allocation a
                                  WHERE a.resident_id = r.resident_id), 0)
                       - COALESCE((SELECT SUM(pa.amount_paise) FROM payment_application pa
                                    JOIN allocation a2 ON a2.allocation_id = pa.allocation_id
                                   WHERE a2.resident_id = r.resident_id), 0)
                      )::BIGINT AS outstanding_paise
                 FROM resident r WHERE r.apartment_id = %s AND r.is_active = 0""",
            (apartment_id,))

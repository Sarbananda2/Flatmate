"""Read model for the cross-month reconciliation."""
from __future__ import annotations

from typing import Any

from .base import Repository


class ReconciliationRepository(Repository):
    # -- the ledger: every query is ordered by date, then insertion order ------

    def expenses_for_ledger(self, apartment_id: str,
                            as_of_date: str | None) -> list[dict[str, Any]]:
        """The apartment's expenses, including unlinked ones allocated to its
        residents, oldest first."""
        return self._s.fetch_all(
            """SELECT e.* FROM expense_instance e
                WHERE (e.apartment_id = %s
                       OR (e.apartment_id IS NULL AND EXISTS (
                             SELECT 1 FROM allocation ax JOIN resident rx
                                    ON rx.resident_id = ax.resident_id
                              WHERE ax.expense_id = e.expense_id AND rx.apartment_id = %s)))
                  AND (%s::text IS NULL OR e.expense_date <= %s)
                ORDER BY e.expense_date, e.row_seq""",
            (apartment_id, apartment_id, as_of_date, as_of_date))

    def linked_advance_totals(self, apartment_id: str,
                              as_of_date: str | None) -> list[dict[str, Any]]:
        """Active provider advances linked to an expense, summed per expense."""
        return self._s.fetch_all(
            """SELECT p.expense_id, SUM(p.amount_paise)::BIGINT AS linked_paise
                 FROM payment p
                WHERE p.payment_kind = 'provider_advance' AND p.status = 'active'
                  AND p.expense_id IS NOT NULL
                  AND p.payer_resident_id IN (SELECT resident_id FROM resident
                                               WHERE apartment_id = %s)
                  AND (%s::text IS NULL OR p.payment_date <= %s)
                GROUP BY p.expense_id""",
            (apartment_id, as_of_date, as_of_date))

    def allocations_for_expense(self, expense_id: str,
                                apartment_id: str) -> list[dict[str, Any]]:
        """One expense's shares charged to this apartment's residents, in
        insertion order."""
        return self._s.fetch_all(
            """SELECT a.allocation_id, a.resident_id, a.amount_paise
                 FROM allocation a JOIN resident r ON r.resident_id = a.resident_id
                WHERE a.expense_id = %s AND r.apartment_id = %s
                ORDER BY a.row_seq""", (expense_id, apartment_id))

    def active_payments_for_ledger(self, apartment_id: str,
                                   as_of_date: str | None) -> list[dict[str, Any]]:
        return self._s.fetch_all(
            """SELECT p.* FROM payment p
                WHERE p.payer_resident_id IN (SELECT resident_id FROM resident
                                               WHERE apartment_id = %s)
                  AND p.status = 'active'
                  AND (%s::text IS NULL OR p.payment_date <= %s)
                ORDER BY p.payment_date, p.row_seq""",
            (apartment_id, as_of_date, as_of_date))

    def adjustments_for_ledger(self, apartment_id: str,
                               as_of_date: str | None) -> list[dict[str, Any]]:
        return self._s.fetch_all(
            """SELECT la.* FROM ledger_adjustment la
                WHERE (la.statement_id IN (SELECT statement_id FROM monthly_statement
                                            WHERE apartment_id = %s)
                       OR (la.statement_id IS NULL AND la.resident_id IN
                           (SELECT resident_id FROM resident WHERE apartment_id = %s)))
                  AND (%s::text IS NULL
                       OR COALESCE(la.effective_date, substr(la.created_at, 1, 10)) <= %s)
                ORDER BY COALESCE(la.effective_date, substr(la.created_at, 1, 10)),
                         la.row_seq""",
            (apartment_id, apartment_id, as_of_date, as_of_date))

    def opening_balances_for_ledger(self, apartment_id: str,
                                    as_of_date: str | None) -> list[dict[str, Any]]:
        return self._s.fetch_all(
            """SELECT ob.* FROM opening_balance ob
                WHERE ob.apartment_id = %s AND (%s::text IS NULL OR ob.as_of_date <= %s)
                ORDER BY ob.as_of_date, ob.row_seq""",
            (apartment_id, as_of_date, as_of_date))

    # -- the charge-level view ------------------------------------------------

    def charges_with_settled(self, apartment_id: str,
                             as_of_date: str | None) -> list[dict[str, Any]]:
        """Each resident charge with what active payments have settled of it."""
        return self._s.fetch_all(
            """SELECT a.resident_id, a.amount_paise,
                      COALESCE((SELECT SUM(pa.amount_paise) FROM payment_application pa
                                 JOIN payment p ON p.payment_id = pa.payment_id
                                WHERE pa.allocation_id = a.allocation_id AND p.status = 'active'
                                  AND (%s::text IS NULL OR p.payment_date <= %s)), 0)::BIGINT
                          AS settled
                 FROM allocation a
                 JOIN expense_instance e ON e.expense_id = a.expense_id
                 JOIN resident r ON r.resident_id = a.resident_id
                WHERE r.apartment_id = %s AND (%s::text IS NULL OR e.expense_date <= %s)""",
            (as_of_date, as_of_date, apartment_id, as_of_date, as_of_date))

    def opening_balances_with_settled(self, apartment_id: str,
                                      as_of_date: str | None) -> list[dict[str, Any]]:
        """Each opening balance with what active payments have settled of it."""
        return self._s.fetch_all(
            """SELECT ob.resident_id, ob.amount_paise,
                      COALESCE((SELECT SUM(pa.amount_paise) FROM payment_application pa
                                 JOIN payment p ON p.payment_id = pa.payment_id
                                WHERE pa.opening_balance_id = ob.opening_balance_id
                                  AND p.status = 'active'
                                  AND (%s::text IS NULL OR p.payment_date <= %s)), 0)::BIGINT
                          AS settled
                 FROM opening_balance ob
                WHERE ob.apartment_id = %s AND (%s::text IS NULL OR ob.as_of_date <= %s)""",
            (as_of_date, as_of_date, apartment_id, as_of_date, as_of_date))

    def unapplied_pool_payments(self, apartment_id: str,
                                as_of_date: str | None) -> list[dict[str, Any]]:
        """Each active pool payment with the part of it not yet applied."""
        return self._s.fetch_all(
            """SELECT p.payer_resident_id AS rid,
                      (p.amount_paise - COALESCE((SELECT SUM(pa.amount_paise)
                                                    FROM payment_application pa
                                                   WHERE pa.payment_id = p.payment_id), 0)
                      )::BIGINT AS unapplied
                 FROM payment p
                WHERE p.apartment_id = %s AND p.payment_kind = 'to_pool' AND p.status = 'active'
                  AND (%s::text IS NULL OR p.payment_date <= %s)""",
            (apartment_id, as_of_date, as_of_date))

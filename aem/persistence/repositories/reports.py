"""Read model for reports and exports."""
from __future__ import annotations

from typing import Any

from .base import Repository


class ReportsRepository(Repository):
    def expense_lines_for_period(self, apartment_id: str,
                                 settlement_period: str) -> list[dict[str, Any]]:
        """The household expenses settling in one month, with how each was funded."""
        return self._s.fetch_all(
            """SELECT e.settlement_period, e.service_period, e.expense_date,
                      e.expense_id, e.description, COALESCE(d.category, '') AS category,
                      e.expense_kind, e.amount_paise, e.provider_name,
                      COALESCE(r.display_name, 'Household pool') AS funded_by,
                      CASE WHEN EXISTS (SELECT 1 FROM payment p
                                         WHERE p.expense_id = e.expense_id
                                           AND p.payment_kind='provider_advance'
                                           AND p.status='active')
                           THEN 'linked_provider_advance'
                           WHEN e.payer_resident_id IS NOT NULL THEN 'payer_resident'
                           ELSE 'household_pool' END AS funding_source,
                      COALESCE(s.status, 'unattached') AS statement_status
                 FROM expense_instance e
                 LEFT JOIN expense_definition d
                        ON d.expense_definition_id = e.expense_definition_id
                 LEFT JOIN resident r ON r.resident_id = e.payer_resident_id
                 LEFT JOIN monthly_statement s ON s.statement_id = e.statement_id
                WHERE e.apartment_id = %s AND e.settlement_period = %s
                ORDER BY e.expense_date, e.row_seq""", (apartment_id, settlement_period))

    def expense_totals_by_period(self, apartment_id: str) -> list[dict[str, Any]]:
        """Household expense totals per settlement month, newest first."""
        return self._s.fetch_all(
            """SELECT e.settlement_period, COUNT(*) AS expense_count,
                      SUM(e.amount_paise)::BIGINT AS total_paise,
                      SUM(CASE WHEN e.expense_kind='electricity' THEN e.amount_paise ELSE 0 END)
                          ::BIGINT AS electricity_paise,
                      SUM(CASE WHEN e.payer_resident_id IS NOT NULL THEN e.amount_paise ELSE 0 END)
                          ::BIGINT AS resident_funded_paise
                 FROM expense_instance e WHERE e.apartment_id = %s
                GROUP BY e.settlement_period
                ORDER BY e.settlement_period DESC NULLS LAST""",
            (apartment_id,))

    def resident_charges_by_category(self, apartment_id: str, *,
                                     settlement_period: str | None = None
                                     ) -> list[dict[str, Any]]:
        """What each resident was charged and has settled, per month and category."""
        params: list[Any] = [apartment_id]
        clause = ""
        if settlement_period:
            clause = " AND e.settlement_period = %s"
            params.append(settlement_period)
        # Grouped by the definition's own category, as before; r.display_name is
        # determined by a.resident_id and is grouped only because PostgreSQL
        # requires every selected column to be.
        return self._s.fetch_all(
            f"""SELECT e.settlement_period, a.resident_id, r.display_name,
                       COALESCE(d.category, 'Uncategorised') AS category,
                       COUNT(DISTINCT e.expense_id) AS expense_count,
                       SUM(a.amount_paise)::BIGINT AS charged_paise,
                       COALESCE(SUM((SELECT COALESCE(SUM(pa.amount_paise), 0)
                                       FROM payment_application pa
                                       JOIN payment p ON p.payment_id = pa.payment_id
                                      WHERE pa.allocation_id = a.allocation_id
                                        AND p.status = 'active')), 0)::BIGINT AS settled_paise
                  FROM allocation a
                  JOIN expense_instance e ON e.expense_id = a.expense_id
                  JOIN resident r ON r.resident_id = a.resident_id
                  LEFT JOIN expense_definition d
                         ON d.expense_definition_id = e.expense_definition_id
                 WHERE e.apartment_id = %s{clause}
                 GROUP BY e.settlement_period, a.resident_id, r.display_name, d.category
                 ORDER BY e.settlement_period DESC NULLS LAST, r.display_name, category""",
            params)

    def provider_advances(self, apartment_id: str) -> list[dict[str, Any]]:
        """Every provider advance and the expense it funds, newest first."""
        return self._s.fetch_all(
            """SELECT p.payment_id, p.payment_date, p.payer_resident_id,
                      r.display_name, p.amount_paise, p.payee_or_provider, p.reference,
                      p.status, p.expense_id, e.description AS funded_expense_description,
                      e.amount_paise AS funded_expense_paise,
                      e.settlement_period AS funded_settlement_period,
                      e.payer_resident_id AS expense_payer_resident_id
                 FROM payment p
                 JOIN resident r ON r.resident_id = p.payer_resident_id
                 LEFT JOIN expense_instance e ON e.expense_id = p.expense_id
                WHERE p.apartment_id = %s AND p.payment_kind = 'provider_advance'
                ORDER BY p.payment_date DESC, p.row_seq DESC""", (apartment_id,))

    def unapplied_credits(self, apartment_id: str) -> list[dict[str, Any]]:
        """Active pool payments not fully applied to charges. Unordered."""
        return self._s.fetch_all(
            """SELECT 'unapplied_credit' AS entry_type, p.payment_id AS entry_id,
                      p.payment_date AS entry_date, p.payer_resident_id AS resident_id,
                      r.display_name,
                      (p.amount_paise - COALESCE((SELECT SUM(pa.amount_paise)
                                                    FROM payment_application pa
                                                   WHERE pa.payment_id = p.payment_id), 0))
                          ::BIGINT AS amount_paise,
                      'Overpayment held as a credit' AS reason, NULL::text AS statement_id,
                      NULL::text AS reversed_by
                 FROM payment p JOIN resident r ON r.resident_id = p.payer_resident_id
                WHERE p.apartment_id = %s AND p.payment_kind='to_pool' AND p.status='active'
                  AND p.amount_paise > COALESCE((SELECT SUM(pa.amount_paise)
                                                   FROM payment_application pa
                                                  WHERE pa.payment_id = p.payment_id), 0)""",
            (apartment_id,))

    def ledger_adjustments(self, apartment_id: str) -> list[dict[str, Any]]:
        """Ledger adjustments for the apartment's residents. Unordered."""
        return self._s.fetch_all(
            """SELECT 'ledger_adjustment' AS entry_type, la.adjustment_id AS entry_id,
                      COALESCE(la.effective_date, substr(la.created_at,1,10)) AS entry_date,
                      la.resident_id, r.display_name, la.amount_paise, la.reason,
                      la.statement_id,
                      (SELECT rv.adjustment_id FROM ledger_adjustment rv
                        WHERE rv.reverses_adjustment_id = la.adjustment_id
                        ORDER BY rv.row_seq LIMIT 1) AS reversed_by
                 FROM ledger_adjustment la JOIN resident r ON r.resident_id = la.resident_id
                WHERE r.apartment_id = %s""", (apartment_id,))

    def statement_summaries(self, apartment_id: str) -> list[dict[str, Any]]:
        return self._s.fetch_all(
            """SELECT s.statement_id, s.settlement_month, s.status, s.created_at,
                      s.finalized_at, s.finalized_by, s.correction_deadline, s.closed_at,
                      (SELECT COUNT(*) FROM expense_instance e
                        WHERE e.statement_id = s.statement_id) AS expense_count,
                      (SELECT COALESCE(SUM(e.amount_paise), 0)::BIGINT
                         FROM expense_instance e
                        WHERE e.statement_id = s.statement_id) AS statement_total_paise,
                      (SELECT COUNT(*) FROM statement_revision sr
                        WHERE sr.statement_id = s.statement_id) AS revision_count
                 FROM monthly_statement s WHERE s.apartment_id = %s
                ORDER BY s.settlement_month DESC""", (apartment_id,))

    def audit_trail(self, apartment_id: str, *, limit: int) -> list[dict[str, Any]]:
        """The apartment's audit rows (and apartment-less ones), newest first."""
        return self._s.fetch_all(
            """SELECT created_at, actor, entity_type, entity_id, action, field_name,
                      old_value, new_value, reason
                 FROM audit_log
                WHERE apartment_id = %s OR apartment_id IS NULL
                ORDER BY created_at DESC, row_seq DESC LIMIT %s""",
            (apartment_id, limit))

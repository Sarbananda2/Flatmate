"""Read model for the searchable transaction ledger."""
from __future__ import annotations

from typing import Any

from .base import Repository

# Sort keys the service accepts, and the ORDER BY each one stands for.
_SORTS = {
    # created_at is NULL on rows that predate the column. SQLite sorted NULL as
    # the smallest value; PostgreSQL needs telling.
    "date_desc": ("transaction_date DESC, created_at DESC NULLS LAST, transaction_id DESC"),
    "date_asc": ("transaction_date ASC, created_at ASC NULLS FIRST, transaction_id ASC"),
    "amount_desc": ("ABS(amount_paise) DESC, transaction_date DESC"),
    "amount_asc": ("ABS(amount_paise) ASC, transaction_date ASC"),
}

# One shape for all three sources so they can be filtered and sorted together.
_UNION_SQL = """
SELECT 'expense'::text AS transaction_type,
       e.expense_id AS transaction_id,
       e.expense_date AS transaction_date,
       e.settlement_period AS settlement_period,
       e.service_period AS service_period,
       e.description AS description,
       e.amount_paise AS amount_paise,
       e.expense_kind AS subtype,
       COALESCE(d.category, '') AS category,
       e.payer_resident_id AS resident_id,
       COALESCE(pr.display_name, '') AS resident_display_name,
       COALESCE(e.provider_name, '') AS counterparty,
       e.statement_id AS statement_id,
       COALESCE(s.status, 'unattached') AS status,
       e.reference AS reference,
       e.created_at AS created_at,
       'Household expense incurred'::text AS effect
  FROM expense_instance e
  LEFT JOIN expense_definition d ON d.expense_definition_id = e.expense_definition_id
  LEFT JOIN resident pr ON pr.resident_id = e.payer_resident_id
  LEFT JOIN monthly_statement s ON s.statement_id = e.statement_id
 WHERE e.apartment_id = %(apartment_id)s

UNION ALL

SELECT 'payment'::text AS transaction_type,
       p.payment_id,
       p.payment_date,
       substr(p.payment_date, 1, 7),
       substr(p.payment_date, 1, 7),
       COALESCE(p.note, p.payee_or_provider, p.payment_kind),
       p.amount_paise,
       p.payment_kind,
       ''::text AS category,
       p.payer_resident_id,
       r.display_name,
       COALESCE(p.payee_or_provider, 'Household pool'),
       NULL::text AS statement_id,
       p.status,
       p.reference,
       COALESCE(p.created_at, p.payment_date),
       CASE p.payment_kind
            WHEN 'to_pool' THEN 'Resident transfer into the pool'
            WHEN 'provider_advance' THEN 'Payment to an external provider'
            WHEN 'refund' THEN 'Refund paid out of the pool'
            ELSE 'Legacy payment adjustment' END
  FROM payment p
  JOIN resident r ON r.resident_id = p.payer_resident_id
 WHERE p.apartment_id = %(apartment_id)s

UNION ALL

SELECT 'adjustment'::text AS transaction_type,
       la.adjustment_id,
       COALESCE(la.effective_date, substr(la.created_at, 1, 10)),
       substr(COALESCE(la.effective_date, la.created_at), 1, 7),
       substr(COALESCE(la.effective_date, la.created_at), 1, 7),
       la.reason,
       la.amount_paise,
       CASE WHEN la.amount_paise >= 0 THEN 'credit' ELSE 'debit' END,
       ''::text AS category,
       la.resident_id,
       r.display_name,
       'Household pool'::text,
       la.statement_id,
       'posted'::text,
       NULL::text AS reference,
       la.created_at,
       'Signed ledger adjustment'::text AS effect
  FROM ledger_adjustment la
  JOIN resident r ON r.resident_id = la.resident_id
 WHERE r.apartment_id = %(apartment_id)s
"""


class LedgerViewRepository(Repository):
    @staticmethod
    def _filtered(*, apartment_id: str, settlement_period: str | None,
                  resident_id: str | None, category: str | None,
                  transaction_type: str | None, status: str | None,
                  date_from: str | None, date_to: str | None,
                  search: str | None) -> tuple[str, dict[str, Any]]:
        """The unified ledger narrowed by whichever filters are not ``None``."""
        params: dict[str, Any] = {"apartment_id": apartment_id}
        clauses: list[str] = []
        if settlement_period is not None:
            clauses.append("settlement_period = %(settlement_period)s")
            params["settlement_period"] = settlement_period
        if resident_id is not None:
            # An expense's resident_id is its payer; a resident's *share* of an expense
            # is matched through the allocation table so filtering by resident finds
            # every transaction that touches them.
            clauses.append("(resident_id = %(resident_id)s OR (transaction_type = 'expense' AND "
                           "EXISTS (SELECT 1 FROM allocation a WHERE a.expense_id = transaction_id "
                           "AND a.resident_id = %(resident_id)s)))")
            params["resident_id"] = resident_id
        if category is not None:
            clauses.append("category = %(category)s")
            params["category"] = category
        if transaction_type is not None:
            clauses.append("transaction_type = %(transaction_type)s")
            params["transaction_type"] = transaction_type
        if status is not None:
            clauses.append("status = %(status)s")
            params["status"] = status
        if date_from is not None:
            clauses.append("transaction_date >= %(date_from)s")
            params["date_from"] = date_from
        if date_to is not None:
            clauses.append("transaction_date <= %(date_to)s")
            params["date_to"] = date_to
        if search is not None:
            # ESCAPE '' turns off the backslash escape, so the pattern's only
            # wildcards are % and _.
            clauses.append("(description ILIKE %(search)s ESCAPE '' "
                           "OR counterparty ILIKE %(search)s ESCAPE '' "
                           "OR transaction_id ILIKE %(search)s ESCAPE '' "
                           "OR COALESCE(reference,'') ILIKE %(search)s ESCAPE '' "
                           "OR resident_display_name ILIKE %(search)s ESCAPE '')")
            params["search"] = f"%{search}%"
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return f"SELECT * FROM ({_UNION_SQL}) AS t {where}", params

    def count_transactions(self, **filters: Any) -> int:
        inner, params = self._filtered(**filters)
        return int(self._s.scalar(f"SELECT COUNT(*) FROM ({inner}) AS f", params, 0))

    def list_transactions(self, *, sort: str, limit: int, offset: int,
                          **filters: Any) -> list[dict[str, Any]]:
        """One page, in the order named by ``sort`` (a key of the sort whitelist)."""
        inner, params = self._filtered(**filters)
        return self._s.fetch_all(
            f"{inner} ORDER BY {_SORTS[sort]} LIMIT %(limit)s OFFSET %(offset)s",
            {**params, "limit": limit, "offset": offset})

    def totals_by_type(self, **filters: Any) -> list[dict[str, Any]]:
        inner, params = self._filtered(**filters)
        return self._s.fetch_all(
            f"SELECT transaction_type, COUNT(*) AS n, "
            f"COALESCE(SUM(amount_paise),0)::BIGINT AS total FROM ({inner}) AS f "
            f"GROUP BY transaction_type", params)

    def residents_for_filter(self, apartment_id: str) -> list[dict[str, Any]]:
        return self._s.fetch_all(
            "SELECT resident_id, display_name, is_active FROM resident "
            "WHERE apartment_id=%s ORDER BY display_name", (apartment_id,))

    def categories_in_use(self, apartment_id: str) -> list[str]:
        return [row["category"] for row in self._s.fetch_all(
            "SELECT DISTINCT category FROM expense_definition WHERE apartment_id=%s "
            "ORDER BY category", (apartment_id,))]

    def settlement_periods_in_use(self, apartment_id: str) -> list[str]:
        """Newest first."""
        return [row["settlement_period"] for row in self._s.fetch_all(
            "SELECT DISTINCT settlement_period FROM expense_instance "
            "WHERE apartment_id=%s AND settlement_period IS NOT NULL "
            "ORDER BY settlement_period DESC", (apartment_id,))]

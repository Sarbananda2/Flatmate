"""Ledger adjustments."""
from __future__ import annotations

from typing import Any

from .base import Repository


class AdjustmentsRepository(Repository):
    def get(self, adjustment_id: str) -> dict[str, Any] | None:
        return self._s.fetch_one(
            "SELECT * FROM ledger_adjustment WHERE adjustment_id=%s", (adjustment_id,))

    def count_reversals_of(self, adjustment_id: str) -> int:
        return int(self._s.scalar(
            "SELECT COUNT(*) FROM ledger_adjustment WHERE reverses_adjustment_id=%s",
            (adjustment_id,), default=0))

    def insert_adjustment(self, *, adjustment_id: str, statement_id: str | None,
                          resident_id: str, amount_paise: int, reason: str, created_at: str,
                          created_by: str, reverses_adjustment_id: str | None,
                          effective_date: str) -> None:
        self._s.execute(
            """INSERT INTO ledger_adjustment
               (adjustment_id, statement_id, resident_id, amount_paise, reason, created_at,
                created_by, reverses_adjustment_id, effective_date)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (adjustment_id, statement_id, resident_id, amount_paise, reason, created_at,
             created_by, reverses_adjustment_id, effective_date))

    @staticmethod
    def _list_base(apartment_id: str, resident_id: str | None,
                   statement_id: str | None) -> tuple[str, list[Any]]:
        clauses = ["r.apartment_id = %s"]
        params: list[Any] = [apartment_id]
        if resident_id:
            clauses.append("la.resident_id = %s")
            params.append(resident_id)
        if statement_id:
            clauses.append("la.statement_id = %s")
            params.append(statement_id)
        where = " AND ".join(clauses)
        base = f"""
            FROM ledger_adjustment la
            JOIN resident r ON r.resident_id = la.resident_id
            LEFT JOIN monthly_statement s ON s.statement_id = la.statement_id
            WHERE {where}
        """
        return base, params

    def count_for_apartment(self, *, apartment_id: str, resident_id: str | None = None,
                            statement_id: str | None = None) -> int:
        base, params = self._list_base(apartment_id, resident_id, statement_id)
        return int(self._s.scalar(f"SELECT COUNT(*) {base}", params, 0))

    def list_for_apartment(self, *, apartment_id: str, resident_id: str | None = None,
                           statement_id: str | None = None, limit: int,
                           offset: int) -> list[dict[str, Any]]:
        """Newest effective date first, with the adjustment that reversed each one."""
        base, params = self._list_base(apartment_id, resident_id, statement_id)
        return self._s.fetch_all(
            f"""SELECT la.*, r.display_name, s.settlement_month, s.status AS statement_status,
                       (SELECT rv.adjustment_id FROM ledger_adjustment rv
                         WHERE rv.reverses_adjustment_id = la.adjustment_id
                         ORDER BY rv.row_seq LIMIT 1) AS reversed_by
                  {base}
                ORDER BY la.effective_date DESC NULLS LAST, la.created_at DESC, la.row_seq DESC
                LIMIT %s OFFSET %s""", params + [limit, offset])

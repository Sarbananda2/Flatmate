"""Monthly statements and their revisions."""
from __future__ import annotations

from typing import Any

from .base import Repository


class StatementsRepository(Repository):
    def get(self, statement_id: str) -> dict[str, Any] | None:
        return self._s.fetch_one("SELECT * FROM monthly_statement WHERE statement_id = %s",
                                 (statement_id,))

    # -- the statement record ------------------------------------------------

    def find_for_month(self, apartment_id: str,
                       settlement_month: str) -> dict[str, Any] | None:
        """``statement_id`` and ``status`` of the apartment's statement for a month."""
        return self._s.fetch_one(
            "SELECT statement_id, status FROM monthly_statement "
            "WHERE apartment_id=%s AND settlement_month=%s",
            (apartment_id, settlement_month))

    def insert_draft(self, *, statement_id: str, apartment_id: str, settlement_month: str,
                     created_at: str, correction_window_hours: int, notes: str | None,
                     updated_at: str) -> None:
        self._s.execute(
            """INSERT INTO monthly_statement
               (statement_id, apartment_id, settlement_month, status, created_at,
                correction_window_hours, notes, updated_at)
               VALUES (%s,%s,%s,'draft',%s,%s,%s,%s)""",
            (statement_id, apartment_id, settlement_month, created_at,
             correction_window_hours, notes, updated_at))

    def status_and_apartment(self, statement_id: str) -> dict[str, Any] | None:
        return self._s.fetch_one(
            "SELECT status, apartment_id FROM monthly_statement "
            "WHERE statement_id=%s", (statement_id,))

    def store_snapshot(self, *, statement_id: str, snapshot_json: str,
                       updated_at: str) -> None:
        self._s.execute(
            "UPDATE monthly_statement SET snapshot_json=%s, updated_at=%s "
            "WHERE statement_id=%s", (snapshot_json, updated_at, statement_id))

    def mark_finalized(self, *, statement_id: str, finalized_at: str,
                       correction_deadline: str, snapshot_json: str, finalized_by: str,
                       correction_window_hours: int, updated_at: str) -> None:
        self._s.execute(
            """UPDATE monthly_statement SET status='finalized_correction_window',
                   finalized_at=%s, correction_deadline=%s, snapshot_json=%s, finalized_by=%s,
                   correction_window_hours=%s, updated_at=%s
               WHERE statement_id=%s""",
            (finalized_at, correction_deadline, snapshot_json, finalized_by,
             correction_window_hours, updated_at, statement_id))

    def in_correction_window(self) -> list[dict[str, Any]]:
        """Finalized statements that have a correction deadline, oldest record first."""
        return self._s.fetch_all(
            """SELECT statement_id, apartment_id, correction_deadline
                 FROM monthly_statement
                WHERE status='finalized_correction_window'
                  AND correction_deadline IS NOT NULL
                ORDER BY row_seq""")

    def mark_closed(self, *, statement_id: str, closed_at: str, closed_by: str,
                    updated_at: str) -> None:
        self._s.execute(
            "UPDATE monthly_statement SET status='closed', closed_at=%s, closed_by=%s, "
            "updated_at=%s WHERE statement_id=%s",
            (closed_at, closed_by, updated_at, statement_id))

    @staticmethod
    def _list_filter(apartment_id: str, status: str | None) -> tuple[str, list[Any]]:
        clauses = ["s.apartment_id = %s"]
        params: list[Any] = [apartment_id]
        if status:
            clauses.append("s.status = %s")
            params.append(status)
        return " AND ".join(clauses), params

    def count_for_apartment(self, apartment_id: str, *, status: str | None = None) -> int:
        where, params = self._list_filter(apartment_id, status)
        return int(self._s.scalar(
            f"SELECT COUNT(*) FROM monthly_statement s WHERE {where}", params, 0))

    def list_for_apartment(self, apartment_id: str, *, status: str | None = None,
                           limit: int, offset: int) -> list[dict[str, Any]]:
        """Statements with their counts and totals, newest month first."""
        where, params = self._list_filter(apartment_id, status)
        return self._s.fetch_all(
            f"""SELECT s.statement_id, s.apartment_id, s.settlement_month, s.status,
                       s.created_at, s.finalized_at, s.correction_deadline, s.closed_at,
                       s.finalized_by, s.correction_window_hours, s.notes,
                       s.snapshot_json IS NOT NULL AS has_snapshot,
                       (SELECT COUNT(*) FROM expense_instance e
                         WHERE e.statement_id = s.statement_id) AS expense_count,
                       (SELECT COALESCE(SUM(e.amount_paise), 0)::BIGINT
                          FROM expense_instance e
                         WHERE e.statement_id = s.statement_id) AS statement_total_paise,
                       (SELECT COUNT(*) FROM statement_revision sr
                         WHERE sr.statement_id = s.statement_id) AS revision_count
                  FROM monthly_statement s WHERE {where}
                 ORDER BY s.settlement_month DESC LIMIT %s OFFSET %s""",
            params + [limit, offset])

    def statement_months(self, apartment_id: str) -> list[str]:
        rows = self._s.fetch_all(
            "SELECT DISTINCT settlement_month FROM monthly_statement WHERE apartment_id=%s",
            (apartment_id,))
        return [row["settlement_month"] for row in rows]

    # -- expenses on, or eligible for, a statement ---------------------------

    def unattached_expenses(self, apartment_id: str, settlement_period: str, *,
                            expense_ids: list[str] | None = None) -> list[dict[str, Any]]:
        """Unattached expenses settling in a period, optionally limited to given IDs.

        ``expense_ids``, when given, must not be empty.
        """
        params: list[Any] = [apartment_id, settlement_period]
        sql = ("SELECT expense_id, description, amount_paise FROM expense_instance "
               "WHERE apartment_id=%s AND settlement_period=%s AND statement_id IS NULL")
        if expense_ids is not None:
            sql += f" AND expense_id IN ({self._placeholders(expense_ids)})"
            params.extend(expense_ids)
        return self._s.fetch_all(sql + " ORDER BY expense_date, row_seq", params)

    def candidate_expenses(self, apartment_id: str,
                           settlement_period: str) -> list[dict[str, Any]]:
        return self._s.fetch_all(
            """SELECT e.expense_id, e.description, e.amount_paise, e.expense_date,
                      e.settlement_period, e.service_period, e.expense_kind,
                      e.payer_resident_id
                 FROM expense_instance e
                WHERE e.apartment_id = %s AND e.settlement_period = %s
                  AND e.statement_id IS NULL
                ORDER BY e.expense_date, e.row_seq""",
            (apartment_id, settlement_period))

    def attach_expense(self, *, expense_id: str, statement_id: str, updated_at: str) -> None:
        self._s.execute(
            "UPDATE expense_instance SET statement_id=%s, updated_at=%s "
            "WHERE expense_id=%s", (statement_id, updated_at, expense_id))

    def expense_attachment(self, expense_id: str) -> dict[str, Any] | None:
        """``{"statement_id": ...}`` for an expense, or ``None`` if there is no such expense."""
        return self._s.fetch_one(
            "SELECT statement_id FROM expense_instance WHERE expense_id=%s", (expense_id,))

    def detach_expense(self, *, expense_id: str, updated_at: str) -> None:
        self._s.execute(
            "UPDATE expense_instance SET statement_id=NULL, updated_at=%s "
            "WHERE expense_id=%s", (updated_at, expense_id))

    def expenses_for(self, statement_id: str) -> list[dict[str, Any]]:
        """A statement's expenses with their definition and payer names."""
        return self._s.fetch_all(
            """SELECT e.*, d.name AS definition_name, d.category AS category,
                      r.display_name AS payer_display_name
                 FROM expense_instance e
                 LEFT JOIN expense_definition d
                        ON d.expense_definition_id = e.expense_definition_id
                 LEFT JOIN resident r ON r.resident_id = e.payer_resident_id
                WHERE e.statement_id = %s
                ORDER BY e.expense_date, e.expense_id""", (statement_id,))

    def allocations_with_settled(self, expense_id: str) -> list[dict[str, Any]]:
        """An expense's allocations and how much active payments have settled of each."""
        return self._s.fetch_all(
            """SELECT a.resident_id, a.amount_paise, a.allocation_id,
                      COALESCE((SELECT SUM(pa.amount_paise) FROM payment_application pa
                                 JOIN payment p ON p.payment_id = pa.payment_id
                                WHERE pa.allocation_id = a.allocation_id
                                  AND p.status = 'active'), 0)::BIGINT AS settled_paise
                 FROM allocation a WHERE a.expense_id = %s
                ORDER BY a.resident_id""", (expense_id,))

    def linked_provider_advances(self, expense_id: str) -> list[dict[str, Any]]:
        return self._s.fetch_all(
            """SELECT payment_id, payer_resident_id, amount_paise, payment_date
                 FROM payment WHERE expense_id = %s AND payment_kind = 'provider_advance'
                   AND status = 'active' ORDER BY payment_date, row_seq""",
            (expense_id,))

    def expense_count(self, statement_id: str) -> int:
        return int(self._s.scalar(
            "SELECT COUNT(*) FROM expense_instance WHERE statement_id=%s",
            (statement_id,), 0))

    def expense_total_paise(self, statement_id: str) -> int:
        return int(self._s.scalar(
            "SELECT COALESCE(SUM(amount_paise), 0)::BIGINT FROM expense_instance "
            "WHERE statement_id=%s", (statement_id,), 0))

    def expense_settlement_periods(self, apartment_id: str) -> list[str]:
        rows = self._s.fetch_all(
            "SELECT DISTINCT settlement_period FROM expense_instance WHERE apartment_id=%s "
            "AND settlement_period IS NOT NULL", (apartment_id,))
        return [row["settlement_period"] for row in rows]

    # -- what else a snapshot records ----------------------------------------

    def resident_names(self, apartment_id: str) -> list[dict[str, Any]]:
        return self._s.fetch_all(
            "SELECT resident_id, display_name, is_active FROM resident "
            "WHERE apartment_id=%s ORDER BY display_name", (apartment_id,))

    def adjustments_for(self, statement_id: str) -> list[dict[str, Any]]:
        return self._s.fetch_all(
            "SELECT * FROM ledger_adjustment WHERE statement_id=%s "
            "ORDER BY COALESCE(effective_date, created_at), row_seq", (statement_id,))

    def resident_charges_for_month(self, apartment_id: str,
                                   settlement_month: str) -> list[dict[str, Any]]:
        """Every share charged to this apartment's residents that settles in the
        month, with the expense and component it came from, oldest first."""
        return self._s.fetch_all(
            """SELECT a.resident_id, a.amount_paise,
                      c.name AS component_name, c.allocation_method AS component_method,
                      c.amount_paise AS component_amount_paise,
                      e.expense_id, e.description, e.amount_paise AS expense_amount_paise,
                      e.expense_date, e.service_period, e.allocation_method, e.expense_kind,
                      e.notes, COALESCE(e.settlement_period, e.expense_period)
                          AS settlement_period,
                      d.name AS definition_name
                 FROM allocation a
                 JOIN expense_instance e ON e.expense_id = a.expense_id
                 JOIN resident r ON r.resident_id = a.resident_id
                 LEFT JOIN expense_component c ON c.component_id = a.component_id
                 LEFT JOIN expense_definition d
                        ON d.expense_definition_id = e.expense_definition_id
                WHERE r.apartment_id = %s
                  AND COALESCE(e.settlement_period, e.expense_period) = %s
                ORDER BY e.expense_date, e.row_seq, c.row_seq NULLS FIRST, a.row_seq""",
            (apartment_id, settlement_month))

    def active_payments_in_month(self, apartment_id: str, month: str) -> list[dict[str, Any]]:
        return self._s.fetch_all(
            """SELECT p.*, r.display_name AS payer_display_name
                 FROM payment p JOIN resident r ON r.resident_id = p.payer_resident_id
                WHERE p.apartment_id = %s AND substr(p.payment_date, 1, 7) = %s
                  AND p.status = 'active'
                ORDER BY p.payment_date, p.row_seq""",
            (apartment_id, month))

    def payment_months(self, apartment_id: str) -> list[str]:
        rows = self._s.fetch_all(
            "SELECT DISTINCT substr(payment_date, 1, 7) AS month FROM payment "
            "WHERE apartment_id=%s", (apartment_id,))
        return [row["month"] for row in rows]

    # -- revisions -----------------------------------------------------------

    def revision_summaries(self, statement_id: str) -> list[dict[str, Any]]:
        """Revisions without their snapshots, in order."""
        return self._s.fetch_all(
            "SELECT revision_id, revision_number, reason, created_at, created_by "
            "FROM statement_revision WHERE statement_id=%s ORDER BY revision_number",
            (statement_id,))

    def revisions(self, statement_id: str) -> list[dict[str, Any]]:
        return self._s.fetch_all(
            "SELECT * FROM statement_revision WHERE statement_id=%s "
            "ORDER BY revision_number", (statement_id,))

    def latest_revision(self, statement_id: str) -> dict[str, Any] | None:
        return self._s.fetch_one(
            "SELECT revision_id FROM statement_revision WHERE statement_id=%s "
            "ORDER BY revision_number DESC LIMIT 1", (statement_id,))

    def next_revision_number(self, statement_id: str) -> int:
        return int(self._s.scalar(
            "SELECT COALESCE(MAX(revision_number), 0) + 1 FROM statement_revision "
            "WHERE statement_id=%s", (statement_id,), 1))

    def append_revision(self, *, revision_id: str, statement_id: str, revision_number: int,
                        snapshot_json: str, reason: str, created_at: str,
                        created_by: str) -> None:
        self._s.execute(
            """INSERT INTO statement_revision
               (revision_id, statement_id, revision_number, snapshot_json, reason,
                created_at, created_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (revision_id, statement_id, revision_number, snapshot_json, reason,
             created_at, created_by))

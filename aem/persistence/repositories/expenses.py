"""Expenses, their components, and allocations."""
from __future__ import annotations

from typing import Any

from .base import Repository


class ExpensesRepository(Repository):
    #: Sort key -> ORDER BY text for :meth:`list_page`. The service validates
    #: the key; ``row_seq`` (insertion order) breaks ties.
    _ORDERS = {
        "expense_date_desc": "e.expense_date DESC, e.row_seq DESC",
        "expense_date_asc": "e.expense_date ASC, e.row_seq ASC",
        "amount_desc": "e.amount_paise DESC, e.row_seq DESC",
        "amount_asc": "e.amount_paise ASC, e.row_seq ASC",
        "description_asc": "e.description ASC, e.row_seq ASC",
    }

    def get(self, expense_id: str) -> dict[str, Any] | None:
        return self._s.fetch_one("SELECT * FROM expense_instance WHERE expense_id = %s",
                                 (expense_id,))

    # -- lookups on neighbouring tables -------------------------------------

    def definition_period_rule(self, expense_definition_id: str) -> dict[str, Any] | None:
        """``{"period_rule": ...}`` for the definition, or ``None`` if it does not exist."""
        return self._s.fetch_one(
            "SELECT period_rule FROM expense_definition WHERE expense_definition_id = %s",
            (expense_definition_id,))

    def definition_owner(self, expense_definition_id: str) -> dict[str, Any] | None:
        """The definition's ``apartment_id`` and ``is_active``, or ``None``."""
        return self._s.fetch_one(
            "SELECT apartment_id, is_active FROM expense_definition "
            "WHERE expense_definition_id = %s",
            (expense_definition_id,))

    def resident_display_names(self, apartment_id: str) -> list[dict[str, Any]]:
        return self._s.fetch_all(
            "SELECT resident_id, display_name FROM resident WHERE apartment_id = %s",
            (apartment_id,))

    def statement_settlement_month(self, statement_id: str) -> str | None:
        return self._s.scalar(
            "SELECT settlement_month FROM monthly_statement WHERE statement_id = %s",
            (statement_id,))

    def statement_status(self, statement_id: str) -> str | None:
        return self._s.scalar(
            "SELECT status FROM monthly_statement WHERE statement_id = %s",
            (statement_id,))

    def statement_summary(self, statement_id: str) -> dict[str, Any] | None:
        return self._s.fetch_one(
            "SELECT statement_id, settlement_month, status FROM monthly_statement "
            "WHERE statement_id = %s", (statement_id,))

    # -- writing an expense --------------------------------------------------

    def insert_expense(self, *, expense_id: str, expense_definition_id: str | None,
                       statement_id: str | None, description: str, amount_paise: int,
                       expense_date: str, expense_period: str,
                       payer_resident_id: str | None, provider_name: str | None,
                       created_at: str, service_period: str, settlement_period: str,
                       period_rule: str, due_date: str, allocation_method: str,
                       expense_kind: str, apartment_id: str, reference: str | None,
                       notes: str | None, generated_from_recurrence: int,
                       updated_at: str) -> None:
        self._s.execute(
            """INSERT INTO expense_instance
               (expense_id, expense_definition_id, statement_id, description, amount_paise,
                expense_date, expense_period, payer_resident_id, provider_name, is_settled,
                created_at, service_period, settlement_period, period_rule, due_date,
                allocation_method, expense_kind, apartment_id, reference, notes,
                generated_from_recurrence, updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,0,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (expense_id, expense_definition_id, statement_id, description, amount_paise,
             expense_date, expense_period, payer_resident_id, provider_name,
             created_at, service_period, settlement_period, period_rule, due_date,
             allocation_method, expense_kind, apartment_id, reference, notes,
             int(generated_from_recurrence), updated_at))

    def insert_component(self, *, component_id: str, expense_id: str, name: str,
                         amount_paise: int, allocation_method: str) -> None:
        self._s.execute(
            """INSERT INTO expense_component
               (component_id, expense_id, name, amount_paise, allocation_method, host_resident_id)
               VALUES (%s,%s,%s,%s,%s,NULL)""",
            (component_id, expense_id, name, amount_paise, allocation_method))

    def insert_allocation(self, *, allocation_id: str, expense_id: str, component_id: str,
                          resident_id: str, amount_paise: int,
                          allocation_basis_json: str | None) -> None:
        self._s.execute(
            """INSERT INTO allocation
               (allocation_id, expense_id, component_id, resident_id, amount_paise,
                allocation_basis_json)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            (allocation_id, expense_id, component_id, resident_id, amount_paise,
             allocation_basis_json))

    def update_expense(self, expense_id: str, *, description: str, amount_paise: int,
                       expense_date: str, service_period: str | None,
                       settlement_period: str | None, due_date: str | None,
                       payer_resident_id: str | None, provider_name: str | None,
                       reference: str | None, notes: str | None, statement_id: str | None,
                       expense_definition_id: str | None, updated_at: str) -> int:
        return self._s.execute(
            """UPDATE expense_instance SET description=%s, amount_paise=%s, expense_date=%s,
                   service_period=%s, settlement_period=%s, due_date=%s, payer_resident_id=%s,
                   provider_name=%s, reference=%s, notes=%s, statement_id=%s,
                   expense_definition_id=%s, updated_at=%s
               WHERE expense_id=%s""",
            (description, amount_paise, expense_date, service_period, settlement_period,
             due_date, payer_resident_id, provider_name, reference, notes, statement_id,
             expense_definition_id, updated_at, expense_id))

    def set_allocation_method(self, expense_id: str, allocation_method: str) -> int:
        return self._s.execute(
            "UPDATE expense_instance SET allocation_method=%s WHERE expense_id=%s",
            (allocation_method, expense_id))

    def update_component(self, component_id: str, *, amount_paise: int,
                         allocation_method: str) -> int:
        return self._s.execute(
            "UPDATE expense_component SET amount_paise=%s, allocation_method=%s "
            "WHERE component_id=%s",
            (amount_paise, allocation_method, component_id))

    # -- components, allocations, and what hangs off an expense --------------

    def component_ids(self, expense_id: str) -> list[str]:
        """Component IDs in insertion order."""
        return [row["component_id"] for row in self._s.fetch_all(
            "SELECT component_id FROM expense_component WHERE expense_id=%s ORDER BY row_seq",
            (expense_id,))]

    def components(self, expense_id: str) -> list[dict[str, Any]]:
        return self._s.fetch_all(
            "SELECT * FROM expense_component WHERE expense_id=%s ORDER BY row_seq",
            (expense_id,))

    def applied_paise(self, expense_id: str) -> int:
        """Total of the payments applied to this expense's allocations."""
        return int(self._s.scalar(
            """SELECT COALESCE(SUM(pa.amount_paise), 0)::BIGINT FROM payment_application pa
               JOIN allocation a ON a.allocation_id = pa.allocation_id
               WHERE a.expense_id = %s""", (expense_id,), 0))

    def allocated_resident_ids(self, expense_id: str) -> list[str]:
        """Residents holding an allocation on this expense, in no particular order."""
        return [row["resident_id"] for row in self._s.fetch_all(
            "SELECT DISTINCT resident_id FROM allocation WHERE expense_id=%s",
            (expense_id,))]

    def allocations_with_settlement(self, expense_id: str) -> list[dict[str, Any]]:
        return self._s.fetch_all(
            """SELECT a.*, r.display_name,
                      (SELECT COALESCE(SUM(pa.amount_paise), 0)::BIGINT
                         FROM payment_application pa
                        WHERE pa.allocation_id = a.allocation_id) AS settled_paise
                 FROM allocation a JOIN resident r ON r.resident_id = a.resident_id
                WHERE a.expense_id = %s ORDER BY r.display_name, a.row_seq""",
            (expense_id,))

    def linked_payment_count(self, expense_id: str) -> int:
        return int(self._s.scalar(
            "SELECT COUNT(*) FROM payment WHERE expense_id=%s", (expense_id,), 0))

    def linked_payments(self, expense_id: str) -> list[dict[str, Any]]:
        return self._s.fetch_all(
            "SELECT * FROM payment WHERE expense_id=%s ORDER BY payment_date, payment_id",
            (expense_id,))

    def readings(self, expense_id: str) -> list[dict[str, Any]]:
        return self._s.fetch_all(
            "SELECT * FROM smart_plug_reading WHERE expense_id=%s ORDER BY row_seq",
            (expense_id,))

    def electricity_bill(self, expense_id: str) -> dict[str, Any] | None:
        return self._s.fetch_one(
            "SELECT * FROM electricity_bill WHERE expense_id=%s", (expense_id,))

    def kitty_paid_expenses(self, apartment_id: str) -> list[dict[str, Any]]:
        """Expenses the house kitty paid for itself, oldest first, with the order
        recorded: no resident is their payer, no active advance funds them, and
        they are not a month's rent, which is paid out by its own rent cycle."""
        return self._s.fetch_all(
            """SELECT e.*, e.row_seq AS recorded_order
                 FROM expense_instance e
                WHERE e.apartment_id = %s AND e.payer_resident_id IS NULL
                  AND e.amount_paise > 0
                  AND NOT EXISTS (SELECT 1 FROM payment p
                                   WHERE p.expense_id = e.expense_id
                                     AND p.payment_kind = 'provider_advance'
                                     AND p.status = 'active')
                  AND NOT EXISTS (SELECT 1 FROM rent_cycle rc
                                   WHERE rc.expense_id = e.expense_id)
                ORDER BY e.expense_date, e.row_seq""",
            (apartment_id,))

    def rent_cycle_for_expense(self, expense_id: str) -> dict[str, Any] | None:
        return self._s.fetch_one(
            "SELECT cycle_id, settlement_month, disbursement_status FROM rent_cycle "
            "WHERE expense_id=%s", (expense_id,))

    # -- deleting -------------------------------------------------------------

    def delete_rent_cycle(self, cycle_id: str) -> int:
        return self._s.execute("DELETE FROM rent_cycle WHERE cycle_id=%s", (cycle_id,))

    def delete_allocations(self, expense_id: str) -> int:
        return self._s.execute("DELETE FROM allocation WHERE expense_id=%s", (expense_id,))

    def delete_readings(self, expense_id: str) -> int:
        return self._s.execute("DELETE FROM smart_plug_reading WHERE expense_id=%s",
                               (expense_id,))

    def delete_electricity_bill(self, expense_id: str) -> int:
        return self._s.execute("DELETE FROM electricity_bill WHERE expense_id=%s",
                               (expense_id,))

    def delete_components(self, expense_id: str) -> int:
        return self._s.execute("DELETE FROM expense_component WHERE expense_id=%s",
                               (expense_id,))

    def delete_expense(self, expense_id: str) -> int:
        return self._s.execute("DELETE FROM expense_instance WHERE expense_id=%s",
                               (expense_id,))

    # -- listing ----------------------------------------------------------------

    @staticmethod
    def _list_base(*, apartment_id: str, settlement_period: str | None,
                   service_period: str | None, statement_id: str | None,
                   unattached_only: bool, expense_definition_id: str | None,
                   expense_kind: str | None, category: str | None,
                   resident_id: str | None, date_from: str | None, date_to: str | None,
                   search: str | None) -> tuple[str, list[Any]]:
        """The shared FROM/WHERE of the expense list, from already-validated filters."""
        clauses = ["e.apartment_id = %s"]
        params: list[Any] = [apartment_id]
        if settlement_period:
            clauses.append("e.settlement_period = %s")
            params.append(settlement_period)
        if service_period:
            clauses.append("e.service_period = %s")
            params.append(service_period)
        if statement_id:
            clauses.append("e.statement_id = %s")
            params.append(statement_id)
        if unattached_only:
            clauses.append("e.statement_id IS NULL")
        if expense_definition_id:
            clauses.append("e.expense_definition_id = %s")
            params.append(expense_definition_id)
        if expense_kind:
            clauses.append("e.expense_kind = %s")
            params.append(expense_kind)
        if category:
            clauses.append("d.category = %s")
            params.append(category)
        if resident_id:
            clauses.append("EXISTS (SELECT 1 FROM allocation a WHERE a.expense_id = e.expense_id "
                           "AND a.resident_id = %s)")
            params.append(resident_id)
        if date_from:
            clauses.append("e.expense_date >= %s")
            params.append(date_from)
        if date_to:
            clauses.append("e.expense_date <= %s")
            params.append(date_to)
        if search is not None:
            # ILIKE with no escape character: a case-insensitive substring match
            # in which a backslash in the search text is an ordinary character.
            clauses.append("(e.description ILIKE %s ESCAPE '' "
                           "OR COALESCE(e.provider_name,'') ILIKE %s ESCAPE '' "
                           "OR COALESCE(e.reference,'') ILIKE %s ESCAPE '' "
                           "OR COALESCE(e.notes,'') ILIKE %s ESCAPE '')")
            params.extend([f"%{search}%"] * 4)
        where = " AND ".join(clauses)
        base = f"""
            FROM expense_instance e
            LEFT JOIN expense_definition d ON d.expense_definition_id = e.expense_definition_id
            LEFT JOIN monthly_statement s ON s.statement_id = e.statement_id
            LEFT JOIN resident r ON r.resident_id = e.payer_resident_id
            WHERE {where}
        """
        return base, params

    def count_matching(self, **filters: Any) -> int:
        """How many expenses match the filters (see :meth:`list_page`)."""
        base, params = self._list_base(**filters)
        return int(self._s.scalar(f"SELECT COUNT(*) {base}", params, 0))

    def total_amount_matching(self, **filters: Any) -> int:
        """The summed amount of the expenses matching the filters."""
        base, params = self._list_base(**filters)
        return int(self._s.scalar(
            f"SELECT COALESCE(SUM(e.amount_paise), 0)::BIGINT {base}", params, 0))

    def list_page(self, *, sort: str, limit: int, offset: int,
                  **filters: Any) -> list[dict[str, Any]]:
        """One page of expenses with their definition, statement, and payer details.

        ``filters`` are the keyword arguments of :meth:`_list_base`; ``sort`` is
        one of the keys of ``_ORDERS``.
        """
        base, params = self._list_base(**filters)
        return self._s.fetch_all(
            f"""SELECT e.*, d.name AS definition_name, d.category AS category,
                       s.settlement_month AS statement_month, s.status AS statement_status,
                       r.display_name AS payer_display_name,
                       (SELECT COUNT(*) FROM allocation a WHERE a.expense_id = e.expense_id)
                           AS allocation_count,
                       (SELECT COALESCE(SUM(a.amount_paise), 0)::BIGINT FROM allocation a
                         WHERE a.expense_id = e.expense_id) AS allocated_paise,
                       (SELECT COALESCE(SUM(p.amount_paise), 0)::BIGINT FROM payment p
                         WHERE p.expense_id = e.expense_id
                           AND p.payment_kind = 'provider_advance'
                           AND p.status = 'active') AS linked_advance_paise
                  {base}
                ORDER BY {self._ORDERS[sort]} LIMIT %s OFFSET %s""",
            params + [int(limit), int(offset)])

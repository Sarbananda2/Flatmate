"""Electricity bills and smart-plug readings."""
from __future__ import annotations

from typing import Any

from .base import Repository


class ElectricityRepository(Repository):
    # -- lookups made while validating a bill ------------------------------- #

    def resident_names(self, apartment_id: str) -> list[dict[str, Any]]:
        """``resident_id`` and ``display_name`` of every resident, in no particular order."""
        return self._s.fetch_all(
            "SELECT resident_id, display_name FROM resident WHERE apartment_id=%s",
            (apartment_id,))

    def definition_apartment(self, expense_definition_id: str) -> dict[str, Any] | None:
        return self._s.fetch_one(
            "SELECT apartment_id FROM expense_definition WHERE expense_definition_id = %s",
            (expense_definition_id,))

    def definition_period_rule(self, expense_definition_id: str) -> str | None:
        row = self._s.fetch_one(
            "SELECT period_rule FROM expense_definition WHERE expense_definition_id=%s",
            (expense_definition_id,))
        return row["period_rule"] if row else None

    def statement_scope(self, statement_id: str) -> dict[str, Any] | None:
        return self._s.fetch_one(
            "SELECT apartment_id, status FROM monthly_statement WHERE statement_id = %s",
            (statement_id,))

    def statement_settlement_month(self, statement_id: str) -> str | None:
        row = self._s.fetch_one(
            "SELECT settlement_month FROM monthly_statement WHERE statement_id=%s",
            (statement_id,))
        return row["settlement_month"] if row else None

    # -- writing a bill ------------------------------------------------------ #

    def insert_expense(self, *, expense_id: str, expense_definition_id: str | None,
                       statement_id: str | None, description: str, amount_paise: int,
                       expense_date: str, expense_period: str,
                       payer_resident_id: str | None, provider_name: str | None,
                       created_at: str, service_period: str | None,
                       settlement_period: str | None, period_rule: str | None,
                       due_date: str | None, apartment_id: str, reference: str | None,
                       notes: str | None, updated_at: str) -> None:
        self._s.execute(
            """INSERT INTO expense_instance
               (expense_id, expense_definition_id, statement_id, description,
                amount_paise, expense_date, expense_period, payer_resident_id,
                provider_name, is_settled, created_at, service_period,
                settlement_period, period_rule, due_date, allocation_method,
                expense_kind, apartment_id, reference, notes,
                generated_from_recurrence, updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,0,%s,%s,%s,%s,%s,'measured','electricity',%s,%s,%s,0,%s)""",
            (expense_id, expense_definition_id, statement_id, description, amount_paise,
             expense_date, expense_period, payer_resident_id, provider_name, created_at,
             service_period, settlement_period, period_rule, due_date, apartment_id,
             reference, notes, updated_at))

    def insert_component(self, *, component_id: str, expense_id: str, name: str,
                         amount_paise: int, allocation_method: str) -> None:
        self._s.execute(
            """INSERT INTO expense_component
               (component_id, expense_id, name, amount_paise, allocation_method,
                host_resident_id)
               VALUES (%s,%s,%s,%s,%s,NULL)""",
            (component_id, expense_id, name, amount_paise, allocation_method))

    def insert_allocation(self, *, allocation_id: str, expense_id: str, component_id: str,
                          resident_id: str, amount_paise: int,
                          allocation_basis_json: str) -> None:
        self._s.execute(
            """INSERT INTO allocation
               (allocation_id, expense_id, component_id, resident_id,
                amount_paise, allocation_basis_json)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            (allocation_id, expense_id, component_id, resident_id, amount_paise,
             allocation_basis_json))

    def insert_reading(self, *, reading_id: str, resident_id: str, expense_id: str,
                       device_label: str, period_start: str, period_end: str,
                       energy_wh: int, source_note: str | None) -> None:
        self._s.execute(
            """INSERT INTO smart_plug_reading
               (reading_id, resident_id, expense_id, device_label, period_start,
                period_end, energy_wh, source_note)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (reading_id, resident_id, expense_id, device_label, period_start, period_end,
             energy_wh, source_note))

    def insert_bill(self, *, electricity_bill_id: str, expense_id: str,
                    bill_period_start: str, bill_period_end: str, settlement_period: str,
                    bill_number: str | None, provider_name: str | None,
                    total_amount_paise: int, calculation_json: str, notes: str | None,
                    created_at: str) -> None:
        self._s.execute(
            """INSERT INTO electricity_bill
               (electricity_bill_id, expense_id, bill_period_start, bill_period_end,
                settlement_period, bill_number, provider_name, total_amount_paise,
                calculation_json, notes, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (electricity_bill_id, expense_id, bill_period_start, bill_period_end,
             settlement_period, bill_number, provider_name, total_amount_paise,
             calculation_json, notes, created_at))

    # -- reading a saved bill ------------------------------------------------ #

    def bill_for_expense(self, expense_id: str) -> dict[str, Any] | None:
        return self._s.fetch_one("SELECT * FROM electricity_bill WHERE expense_id=%s",
                                 (expense_id,))

    def readings_for_expense(self, expense_id: str) -> list[dict[str, Any]]:
        """In the order they were saved."""
        return self._s.fetch_all(
            "SELECT * FROM smart_plug_reading WHERE expense_id=%s ORDER BY row_seq",
            (expense_id,))

    def components_for_expense(self, expense_id: str) -> list[dict[str, Any]]:
        """In the order they were saved."""
        return self._s.fetch_all(
            "SELECT * FROM expense_component WHERE expense_id = %s ORDER BY row_seq",
            (expense_id,))

    def allocations_for_expense(self, expense_id: str) -> list[dict[str, Any]]:
        """Allocations with component and resident names, by component then resident."""
        # NULLS FIRST keeps an allocation with no component where SQLite put it.
        return self._s.fetch_all(
            """SELECT a.*, c.name AS component_name, r.display_name
                 FROM allocation a
                 LEFT JOIN expense_component c ON c.component_id = a.component_id
                 LEFT JOIN resident r ON r.resident_id = a.resident_id
                WHERE a.expense_id = %s ORDER BY c.row_seq NULLS FIRST, a.resident_id""",
            (expense_id,))

    # -- replacing a saved calculation --------------------------------------- #

    def applied_paise_for_expense(self, expense_id: str) -> int:
        """Paise of payments already applied to this expense's allocations."""
        return int(self._s.scalar(
            """SELECT COALESCE(SUM(pa.amount_paise), 0)::BIGINT FROM payment_application pa
                 JOIN allocation a ON a.allocation_id = pa.allocation_id
                WHERE a.expense_id = %s""", (expense_id,), default=0))

    def charges_for_expense(self, expense_id: str) -> list[dict[str, Any]]:
        """Each allocation on the bill with its resident's name and what has been
        paid against it."""
        return self._s.fetch_all(
            """SELECT a.allocation_id, a.component_id, a.resident_id, a.amount_paise,
                      r.display_name,
                      COALESCE((SELECT SUM(pa.amount_paise) FROM payment_application pa
                                 WHERE pa.allocation_id = a.allocation_id), 0)::BIGINT
                          AS settled_paise
                 FROM allocation a LEFT JOIN resident r ON r.resident_id = a.resident_id
                WHERE a.expense_id = %s ORDER BY a.row_seq""", (expense_id,))

    def linked_advance_total_paise(self, expense_id: str) -> int:
        """Active provider advances linked to the bill."""
        return int(self._s.scalar(
            """SELECT COALESCE(SUM(amount_paise), 0)::BIGINT FROM payment
                WHERE expense_id=%s AND payment_kind='provider_advance'
                  AND status='active'""", (expense_id,), 0))

    def update_expense(self, expense_id: str, *, description: str, amount_paise: int,
                       expense_date: str, expense_period: str,
                       payer_resident_id: str | None, provider_name: str | None,
                       service_period: str | None, settlement_period: str | None,
                       period_rule: str | None, due_date: str | None,
                       reference: str | None, notes: str | None, updated_at: str) -> int:
        """A recalculated bill, in place: the row its payments are linked to stays."""
        return self._s.execute(
            """UPDATE expense_instance SET description=%s, amount_paise=%s, expense_date=%s,
                      expense_period=%s, payer_resident_id=%s, provider_name=%s,
                      service_period=%s, settlement_period=%s, period_rule=%s, due_date=%s,
                      reference=%s, notes=%s, updated_at=%s
                WHERE expense_id=%s""",
            (description, amount_paise, expense_date, expense_period, payer_resident_id,
             provider_name, service_period, settlement_period, period_rule, due_date,
             reference, notes, updated_at, expense_id))

    def update_component(self, component_id: str, *, name: str, amount_paise: int,
                         allocation_method: str) -> int:
        return self._s.execute(
            "UPDATE expense_component SET name=%s, amount_paise=%s, allocation_method=%s "
            "WHERE component_id=%s", (name, amount_paise, allocation_method, component_id))

    def update_allocation(self, allocation_id: str, *, amount_paise: int,
                          allocation_basis_json: str) -> int:
        """Change a charge in place, so the payments applied to it stay with it."""
        return self._s.execute(
            "UPDATE allocation SET amount_paise=%s, allocation_basis_json=%s "
            "WHERE allocation_id=%s", (amount_paise, allocation_basis_json, allocation_id))

    def delete_allocation(self, allocation_id: str) -> None:
        self._s.execute("DELETE FROM allocation WHERE allocation_id=%s", (allocation_id,))

    def delete_component(self, component_id: str) -> None:
        self._s.execute("DELETE FROM expense_component WHERE component_id=%s", (component_id,))

    def linked_payment_ids(self, expense_id: str) -> list[str]:
        """Payments that fund this expense, in the order they were recorded."""
        return [row["payment_id"] for row in self._s.fetch_all(
            "SELECT payment_id FROM payment WHERE expense_id=%s ORDER BY row_seq",
            (expense_id,))]

    def detach_payments(self, expense_id: str) -> None:
        self._s.execute("UPDATE payment SET expense_id=NULL WHERE expense_id=%s",
                        (expense_id,))

    def attach_payment(self, payment_id: str, expense_id: str) -> None:
        self._s.execute("UPDATE payment SET expense_id=%s WHERE payment_id=%s",
                        (expense_id, payment_id))

    def delete_allocations(self, expense_id: str) -> None:
        self._s.execute("DELETE FROM allocation WHERE expense_id=%s", (expense_id,))

    def delete_readings(self, expense_id: str) -> None:
        self._s.execute("DELETE FROM smart_plug_reading WHERE expense_id=%s", (expense_id,))

    def delete_bill(self, expense_id: str) -> None:
        self._s.execute("DELETE FROM electricity_bill WHERE expense_id=%s", (expense_id,))

    def delete_components(self, expense_id: str) -> None:
        self._s.execute("DELETE FROM expense_component WHERE expense_id=%s", (expense_id,))

    def delete_expense(self, expense_id: str) -> None:
        self._s.execute("DELETE FROM expense_instance WHERE expense_id=%s", (expense_id,))

    # -- lists and reports --------------------------------------------------- #

    def count_bills(self, apartment_id: str) -> int:
        return int(self._s.scalar(
            "SELECT COUNT(*) FROM expense_instance WHERE apartment_id=%s "
            "AND expense_kind='electricity'", (apartment_id,), default=0))

    def list_bills(self, apartment_id: str, *, limit: int, offset: int) -> list[dict[str, Any]]:
        """Newest bill first."""
        return self._s.fetch_all(
            """SELECT e.expense_id, e.description, e.amount_paise, e.expense_date,
                      e.service_period, e.settlement_period, e.payer_resident_id,
                      e.statement_id, e.provider_name,
                      r.display_name AS payer_display_name,
                      s.status AS statement_status, s.settlement_month,
                      b.bill_period_start, b.bill_period_end, b.bill_number,
                      (SELECT COALESCE(SUM(sp.energy_wh), 0)::BIGINT FROM smart_plug_reading sp
                        WHERE sp.expense_id = e.expense_id) AS total_energy_wh,
                      (SELECT COUNT(*) FROM expense_component c
                        WHERE c.expense_id = e.expense_id) AS component_count
                 FROM expense_instance e
                 LEFT JOIN electricity_bill b ON b.expense_id = e.expense_id
                 LEFT JOIN resident r ON r.resident_id = e.payer_resident_id
                 LEFT JOIN monthly_statement s ON s.statement_id = e.statement_id
                WHERE e.apartment_id = %s AND e.expense_kind = 'electricity'
                ORDER BY e.expense_date DESC, e.row_seq DESC LIMIT %s OFFSET %s""",
            (apartment_id, int(limit), int(offset)))

    def consumption_by_period(self, apartment_id: str) -> list[dict[str, Any]]:
        """Each resident's watt-hours and paise per service period, newest period first.

        Energy and money are first totalled per bill and resident, then summed
        over the bills of a service period, so that a period with more than one
        bill reports the paise of all of them beside the energy of all of them.
        """
        return self._s.fetch_all(
            """SELECT per.service_period, per.resident_id, r.display_name,
                      SUM(per.energy_wh)::BIGINT AS energy_wh,
                      SUM(per.amount_paise)::BIGINT AS amount_paise
                 FROM (SELECT e.expense_id, e.service_period, sp.resident_id,
                              SUM(sp.energy_wh) AS energy_wh,
                              (SELECT COALESCE(SUM(a.amount_paise), 0) FROM allocation a
                                WHERE a.expense_id = e.expense_id
                                  AND a.resident_id = sp.resident_id) AS amount_paise
                         FROM smart_plug_reading sp
                         JOIN expense_instance e ON e.expense_id = sp.expense_id
                        WHERE e.apartment_id = %s AND e.expense_kind = 'electricity'
                        GROUP BY e.expense_id, e.service_period, sp.resident_id) per
                 JOIN resident r ON r.resident_id = per.resident_id
                GROUP BY per.service_period, per.resident_id, r.display_name
                ORDER BY per.service_period DESC NULLS LAST, r.display_name""",
            (apartment_id,))

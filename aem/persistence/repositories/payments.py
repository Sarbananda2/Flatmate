"""Payments and their applications to charges."""
from __future__ import annotations

from typing import Any

from .base import Repository

#: What has been applied out of payment ``p``. SUM over BIGINT is NUMERIC in
#: PostgreSQL, so it is cast back to keep the value an ``int``.
_APPLIED_TO_PAYMENT = """COALESCE((SELECT SUM(pa.amount_paise) FROM payment_application pa
                                 WHERE pa.payment_id = p.payment_id), 0)::BIGINT"""


class PaymentsRepository(Repository):
    # -- charges a payment can be applied to ---------------------------------

    def charges_for_resident(self, resident_id: str) -> list[dict[str, Any]]:
        """One resident's allocations, oldest first.

        Ordered by (due date, insertion sequence, allocation ID); ``seq`` is the
        allocation row's insertion sequence.
        """
        return self._s.fetch_all(
            """SELECT a.allocation_id, a.amount_paise, a.row_seq AS seq,
                      e.expense_date, e.due_date, e.settlement_period, e.description,
                      e.statement_id
                 FROM allocation a
                 JOIN expense_instance e ON e.expense_id = a.expense_id
                WHERE a.resident_id = %s
                ORDER BY COALESCE(e.due_date, e.expense_date), a.row_seq, a.allocation_id""",
            (resident_id,))

    def applied_by_allocation(self) -> list[dict[str, Any]]:
        """Total applied to each allocation that has any application."""
        return self._s.fetch_all(
            """SELECT pa.allocation_id, SUM(pa.amount_paise)::BIGINT AS applied
                 FROM payment_application pa
                WHERE pa.allocation_id IS NOT NULL
                GROUP BY pa.allocation_id""")

    def opening_debt_for_resident(self, resident_id: str) -> dict[str, Any] | None:
        """The resident's opening balance if it is a debt, with what is applied to it."""
        return self._s.fetch_one(
            """SELECT ob.opening_balance_id, ob.amount_paise, ob.as_of_date,
                      COALESCE((SELECT SUM(pa.amount_paise) FROM payment_application pa
                                 WHERE pa.opening_balance_id = ob.opening_balance_id), 0)::BIGINT
                          AS applied
                 FROM opening_balance ob
                WHERE ob.resident_id = %s AND ob.amount_paise < 0""", (resident_id,))

    def opening_balance_as_of_date(self, opening_balance_id: str) -> str | None:
        return self._s.scalar(
            "SELECT as_of_date FROM opening_balance WHERE opening_balance_id=%s",
            (opening_balance_id,))

    def allocation_due_date(self, allocation_id: str | None) -> str | None:
        return self._s.scalar(
            """SELECT COALESCE(e.due_date, e.expense_date)
                 FROM allocation a
                 JOIN expense_instance e ON e.expense_id = a.expense_id
                WHERE a.allocation_id = %s""", (allocation_id,))

    # -- applications --------------------------------------------------------

    def application_id_for_allocation(self, payment_id: str,
                                      allocation_id: str | None) -> str | None:
        """The application of this payment to this allocation, if there is one."""
        return self._s.scalar(
            "SELECT payment_application_id FROM payment_application "
            "WHERE payment_id=%s AND allocation_id=%s", (payment_id, allocation_id))

    def application_id_for_opening_balance(self, payment_id: str,
                                           opening_balance_id: str) -> str | None:
        """The application of this payment to this opening balance, if there is one."""
        return self._s.scalar(
            "SELECT payment_application_id FROM payment_application "
            "WHERE payment_id=%s AND opening_balance_id=%s", (payment_id, opening_balance_id))

    def add_to_application(self, payment_application_id: str, amount_paise: int) -> int:
        return self._s.execute(
            "UPDATE payment_application SET amount_paise = amount_paise + %s "
            "WHERE payment_application_id=%s", (amount_paise, payment_application_id))

    def insert_application(self, *, payment_application_id: str, payment_id: str,
                           allocation_id: str | None, opening_balance_id: str | None,
                           amount_paise: int, charge_due_date: str | None,
                           created_at: str) -> None:
        self._s.execute(
            """INSERT INTO payment_application
               (payment_application_id, payment_id, allocation_id, opening_balance_id,
                amount_paise, charge_due_date, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (payment_application_id, payment_id, allocation_id, opening_balance_id,
             amount_paise, charge_due_date, created_at))

    def released_applications(self, payment_id: str) -> list[dict[str, Any]]:
        """What a payment is currently applied to, as recorded when it is reversed."""
        return self._s.fetch_all(
            "SELECT allocation_id, opening_balance_id, amount_paise "
            "FROM payment_application WHERE payment_id=%s", (payment_id,))

    def delete_applications(self, payment_id: str) -> int:
        return self._s.execute(
            "DELETE FROM payment_application WHERE payment_id=%s", (payment_id,))

    def applications_for_payment(self, payment_id: str) -> list[dict[str, Any]]:
        """A payment's applications with the charge each one settles, oldest charge first."""
        return self._s.fetch_all(
            """SELECT pa.*, COALESCE(a.resident_id, ob.resident_id) AS resident_id,
                      COALESCE(a.amount_paise, -ob.amount_paise) AS charge_amount_paise,
                      e.expense_id,
                      COALESCE(e.description, 'Opening balance') AS expense_description,
                      e.settlement_period, COALESCE(e.expense_date, ob.as_of_date) AS expense_date
                 FROM payment_application pa
                 LEFT JOIN allocation a ON a.allocation_id = pa.allocation_id
                 LEFT JOIN expense_instance e ON e.expense_id = a.expense_id
                 LEFT JOIN opening_balance ob ON ob.opening_balance_id = pa.opening_balance_id
                WHERE pa.payment_id = %s
                ORDER BY COALESCE(e.due_date, e.expense_date, ob.as_of_date) NULLS FIRST,
                         pa.row_seq""",
            (payment_id,))

    # -- payments ------------------------------------------------------------

    def get(self, payment_id: str) -> dict[str, Any] | None:
        return self._s.fetch_one("SELECT * FROM payment WHERE payment_id=%s", (payment_id,))

    def exists(self, payment_id: str) -> bool:
        return self._s.fetch_one(
            "SELECT 1 AS found FROM payment WHERE payment_id=%s", (payment_id,)) is not None

    def insert_payment(self, *, payment_id: str, payer_resident_id: str, amount_paise: int,
                       payment_date: str, payment_kind: str, expense_id: str | None,
                       payee_or_provider: str | None, reference: str | None,
                       note: str | None, apartment_id: str | None, created_at: str,
                       created_by: str, payment_mode: str | None) -> None:
        """A new, active payment."""
        self._s.execute(
            """INSERT INTO payment
               (payment_id, payer_resident_id, amount_paise, payment_date, payment_kind,
                expense_id, payee_or_provider, reference, note, apartment_id, status,
                reverses_payment_id, created_at, created_by, payment_mode)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'active',NULL,%s,%s,%s)""",
            (payment_id, payer_resident_id, amount_paise, payment_date, payment_kind,
             expense_id, payee_or_provider, reference, note, apartment_id, created_at,
             created_by, payment_mode))

    def insert_reversal(self, *, payment_id: str, payer_resident_id: str, amount_paise: int,
                        payment_date: str, payment_kind: str, expense_id: str | None,
                        payee_or_provider: str | None, reference: str | None,
                        note: str | None, apartment_id: str | None,
                        reverses_payment_id: str, created_at: str, created_by: str) -> None:
        """The mirror row that records a reversal and points at the original."""
        self._s.execute(
            """INSERT INTO payment
               (payment_id, payer_resident_id, amount_paise, payment_date, payment_kind,
                expense_id, payee_or_provider, reference, note, apartment_id, status,
                reverses_payment_id, created_at, created_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'reversal',%s,%s,%s)""",
            (payment_id, payer_resident_id, amount_paise, payment_date, payment_kind,
             expense_id, payee_or_provider, reference, note, apartment_id,
             reverses_payment_id, created_at, created_by))

    def mark_reversed(self, payment_id: str) -> int:
        return self._s.execute(
            "UPDATE payment SET status='reversed' WHERE payment_id=%s", (payment_id,))

    def reversal_of(self, payment_id: str) -> dict[str, Any] | None:
        return self._s.fetch_one(
            "SELECT payment_id, payment_date, note FROM payment "
            "WHERE reverses_payment_id=%s", (payment_id,))

    def pool_payments_with_applied(self, resident_id: str) -> list[dict[str, Any]]:
        """A resident's active pool payments, oldest first, with what each has applied."""
        return self._s.fetch_all(
            f"""SELECT p.payment_id, p.amount_paise, p.payment_date,
                      {_APPLIED_TO_PAYMENT} AS applied
                 FROM payment p
                WHERE p.payer_resident_id = %s AND p.payment_kind = 'to_pool'
                  AND p.status = 'active'
                ORDER BY p.payment_date, p.row_seq""", (resident_id,))

    # -- what a provider advance is linked to --------------------------------

    def expense_for_link(self, expense_id: str) -> dict[str, Any] | None:
        return self._s.fetch_one(
            "SELECT expense_id, apartment_id, statement_id, amount_paise "
            "FROM expense_instance WHERE expense_id=%s", (expense_id,))

    def statement_apartment_id(self, statement_id: str) -> str | None:
        return self._s.scalar(
            "SELECT apartment_id FROM monthly_statement WHERE statement_id=%s",
            (statement_id,))

    def expense_apartment_id_from_allocations(self, expense_id: str) -> str | None:
        """The apartment of any one resident the expense is allocated to."""
        return self._s.scalar(
            """SELECT r.apartment_id FROM allocation a
                 JOIN resident r ON r.resident_id = a.resident_id
                WHERE a.expense_id = %s LIMIT 1""", (expense_id,))

    def linked_advances_paise(self, expense_id: str) -> int:
        """Total of the active provider advances already linked to an expense."""
        return int(self._s.scalar(
            """SELECT COALESCE(SUM(amount_paise), 0)::BIGINT FROM payment
                WHERE expense_id=%s AND payment_kind='provider_advance' AND status='active'""",
            (expense_id,), default=0))

    def linked_expense_summary(self, expense_id: str) -> dict[str, Any] | None:
        return self._s.fetch_one(
            "SELECT expense_id, description, amount_paise, settlement_period "
            "FROM expense_instance WHERE expense_id=%s", (expense_id,))

    def rent_cycle_funded_by(self, payment_id: str) -> dict[str, Any] | None:
        """The rent cycle one of whose landlord payouts this payment is, if any."""
        return self._s.fetch_one(
            "SELECT cycle_id, settlement_month FROM rent_disbursement WHERE funding_payment_id=%s",
            (payment_id,))

    def service_payout_funded_by(self, payment_id: str) -> dict[str, Any] | None:
        """The household service payout this payment is, if any."""
        return self._s.fetch_one(
            "SELECT disbursement_id, settlement_month FROM service_disbursement "
            "WHERE funding_payment_id=%s", (payment_id,))

    def rent_payout_of(self, payment_id: str) -> dict[str, Any] | None:
        """The landlord payout this payment is part of, if any: the advance behind a
        resident's payout, or their own share that the kitty paid out."""
        return self._s.fetch_one(
            """SELECT d.*, d.row_seq AS seq FROM rent_disbursement d
                WHERE d.funding_payment_id = %s OR d.disbursement_id = 'rentdisb_' || %s
                LIMIT 1""", (payment_id, payment_id))

    def service_payout_of(self, payment_id: str) -> dict[str, Any] | None:
        """The same for a payout to a household service's provider."""
        return self._s.fetch_one(
            """SELECT d.* FROM service_disbursement d
                WHERE d.funding_payment_id = %s OR d.disbursement_id = 'svcdisb_' || %s
                LIMIT 1""", (payment_id, payment_id))

    def applied_by_expense(self, payment_id: str) -> list[dict[str, Any]]:
        """What a payment has settled of each expense's charges."""
        return self._s.fetch_all(
            """SELECT a.expense_id, SUM(pa.amount_paise)::BIGINT AS amount_paise
                 FROM payment_application pa
                 JOIN allocation a ON a.allocation_id = pa.allocation_id
                WHERE pa.payment_id = %s
                GROUP BY a.expense_id ORDER BY a.expense_id""", (payment_id,))

    def kitty_for_expense(self, expense_id: str) -> dict[str, Any] | None:
        """A month's bill as the kitty sees it: what residents have paid in towards
        its charges, what the kitty has paid out for it -- to the landlord or a
        provider, and back to whoever paid them -- and which kind of bill it is."""
        return self._s.fetch_one(
            """SELECT
                  e.expense_id, e.apartment_id, e.settlement_period,
                  COALESCE((SELECT SUM(pa.amount_paise) FROM payment_application pa
                              JOIN allocation a ON a.allocation_id = pa.allocation_id
                             WHERE a.expense_id = e.expense_id), 0)::BIGINT AS collected_paise,
                  (COALESCE((SELECT SUM(d.amount_paise) FROM rent_disbursement d
                               JOIN rent_cycle c ON c.cycle_id = d.cycle_id
                              WHERE c.expense_id = e.expense_id AND d.disbursed_by = 'pool'), 0)
                   + COALESCE((SELECT SUM(d.amount_paise) FROM service_disbursement d
                                WHERE d.expense_id = e.expense_id
                                  AND d.disbursed_by = 'pool'), 0))::BIGINT AS paid_out_paise,
                  COALESCE((SELECT SUM(p.amount_paise) FROM payment p
                             WHERE p.payment_kind = 'refund' AND p.status = 'active'
                               AND starts_with(p.payment_id, e.expense_id || '-reimbursement_')),
                           0)::BIGINT AS reimbursed_paise,
                  EXISTS (SELECT 1 FROM rent_cycle c WHERE c.expense_id = e.expense_id)
                      AS is_rent,
                  EXISTS (SELECT 1 FROM furniture_rental_config f
                           WHERE f.expense_definition_id = e.expense_definition_id)
                      AS is_service
                 FROM expense_instance e WHERE e.expense_id = %s""", (expense_id,))

    def statement_status_for_month(self, apartment_id: str,
                                   settlement_month: str) -> str | None:
        return self._s.scalar(
            "SELECT status FROM monthly_statement WHERE apartment_id=%s AND settlement_month=%s",
            (apartment_id, settlement_month))

    def delete_payment(self, payment_id: str) -> int:
        """A payment recorded by mistake, while its month is still open. Its
        applications go first."""
        self._s.execute("DELETE FROM payment_application WHERE payment_id=%s", (payment_id,))
        return self._s.execute("DELETE FROM payment WHERE payment_id=%s", (payment_id,))

    def payer_display_name(self, resident_id: str) -> str | None:
        return self._s.scalar(
            "SELECT display_name FROM resident WHERE resident_id=%s", (resident_id,))

    # -- listing -------------------------------------------------------------

    _ORDERS = {
        "payment_date_desc": "p.payment_date DESC, p.row_seq DESC",
        "payment_date_asc": "p.payment_date ASC, p.row_seq ASC",
        "amount_desc": "p.amount_paise DESC, p.row_seq DESC",
        "amount_asc": "p.amount_paise ASC, p.row_seq ASC",
    }

    @staticmethod
    def _list_base(*, apartment_id: str, resident_id: str | None, payment_kind: str | None,
                   status: str | None, settlement_period: str | None,
                   date_from: str | None, date_to: str | None, expense_id: str | None,
                   unapplied_only: bool, search: str | None) -> tuple[str, list[Any]]:
        """The FROM/WHERE shared by the count, the total and the page.

        Every value is already validated and normalised by the service. A
        filter that is empty or ``None`` is not applied, except ``search``,
        which is applied whenever it is not ``None``.
        """
        clauses = ["p.apartment_id = %s"]
        params: list[Any] = [apartment_id]
        if resident_id:
            clauses.append("p.payer_resident_id = %s")
            params.append(resident_id)
        if payment_kind:
            clauses.append("p.payment_kind = %s")
            params.append(payment_kind)
        if status:
            clauses.append("p.status = %s")
            params.append(status)
        if settlement_period:
            clauses.append("substr(p.payment_date, 1, 7) = %s")
            params.append(settlement_period)
        if date_from:
            clauses.append("p.payment_date >= %s")
            params.append(date_from)
        if date_to:
            clauses.append("p.payment_date <= %s")
            params.append(date_to)
        if expense_id:
            clauses.append("p.expense_id = %s")
            params.append(expense_id)
        if search is not None:
            # ILIKE keeps SQLite's case-insensitive LIKE; ESCAPE '' keeps a
            # backslash in the search text literal, as it was there.
            clauses.append("(COALESCE(p.payee_or_provider,'') ILIKE %s ESCAPE '' "
                           "OR COALESCE(p.reference,'') ILIKE %s ESCAPE '' "
                           "OR COALESCE(p.note,'') ILIKE %s ESCAPE '' "
                           "OR p.payment_id ILIKE %s ESCAPE '')")
            params.extend([f"%{search}%"] * 4)
        if unapplied_only:
            clauses.append("p.payment_kind = 'to_pool' AND p.status = 'active' AND p.amount_paise > "
                           "COALESCE((SELECT SUM(pa.amount_paise) FROM payment_application pa "
                           "WHERE pa.payment_id = p.payment_id), 0)")
        where = " AND ".join(clauses)
        base = f"""
            FROM payment p
            LEFT JOIN resident r ON r.resident_id = p.payer_resident_id
            WHERE {where}
        """
        return base, params

    def count_payments(self, **filters: Any) -> int:
        base, params = self._list_base(**filters)
        return int(self._s.scalar(f"SELECT COUNT(*) {base}", params, 0))

    def total_amount_paise(self, **filters: Any) -> int:
        base, params = self._list_base(**filters)
        return int(self._s.scalar(
            f"SELECT COALESCE(SUM(p.amount_paise), 0)::BIGINT {base}", params, 0))

    def list_payments(self, *, sort: str, limit: int, offset: int,
                      **filters: Any) -> list[dict[str, Any]]:
        """One page of payments. ``sort`` is a key the service has validated."""
        base, params = self._list_base(**filters)
        return self._s.fetch_all(
            f"""SELECT p.*, r.display_name AS payer_display_name,
                       {_APPLIED_TO_PAYMENT} AS applied_paise,
                       (SELECT COUNT(*) FROM payment_application pa
                         WHERE pa.payment_id = p.payment_id) AS application_count,
                       (SELECT e.description FROM expense_instance e
                         WHERE e.expense_id = p.expense_id) AS linked_expense_description
                  {base}
                ORDER BY {self._ORDERS[sort]} LIMIT %s OFFSET %s""",
            params + [limit, offset])

    def statement_payments(self, apartment_id: str) -> list[dict[str, Any]]:
        """Every active pool payment, refund and provider advance, oldest first,
        with the payer's name, the expense an advance funds, whether a pool
        payment settled any share of a month's rent, and the order recorded."""
        return self._s.fetch_all(
            """SELECT p.*, p.row_seq AS recorded_order, r.display_name AS payer_display_name,
                      (SELECT e.description FROM expense_instance e
                        WHERE e.expense_id = p.expense_id) AS linked_expense_description,
                      EXISTS (SELECT 1 FROM payment_application pa
                                JOIN allocation a ON a.allocation_id = pa.allocation_id
                                JOIN rent_cycle rc ON rc.expense_id = a.expense_id
                               WHERE pa.payment_id = p.payment_id) AS settles_rent
                 FROM payment p
                 LEFT JOIN resident r ON r.resident_id = p.payer_resident_id
                WHERE p.apartment_id = %s AND p.status = 'active'
                  AND p.payment_kind IN ('to_pool', 'refund', 'provider_advance')
                ORDER BY p.payment_date, p.row_seq""",
            (apartment_id,))

    def active_payments(self, apartment_id: str) -> list[dict[str, Any]]:
        """Every active payment, oldest first, with the payer's name, what has been
        applied out of it, and the expense it is linked to."""
        return self._s.fetch_all(
            f"""SELECT p.*, p.row_seq AS recorded_order, r.display_name AS payer_display_name,
                       {_APPLIED_TO_PAYMENT} AS applied_paise,
                       (SELECT COUNT(*) FROM payment_application pa
                         WHERE pa.payment_id = p.payment_id) AS application_count,
                       (SELECT e.description FROM expense_instance e
                         WHERE e.expense_id = p.expense_id) AS linked_expense_description
                  FROM payment p
                  LEFT JOIN resident r ON r.resident_id = p.payer_resident_id
                 WHERE p.apartment_id = %s AND p.status = 'active'
                 ORDER BY p.payment_date, p.row_seq""",
            (apartment_id,))

    def unapplied_credits(self, apartment_id: str) -> list[dict[str, Any]]:
        """Active pool payments with money left over, oldest first."""
        return self._s.fetch_all(
            f"""SELECT p.payment_id, p.payer_resident_id, r.display_name, p.payment_date,
                      p.amount_paise,
                      {_APPLIED_TO_PAYMENT} AS applied_paise,
                      p.amount_paise - {_APPLIED_TO_PAYMENT}
                          AS unapplied_paise
                 FROM payment p JOIN resident r ON r.resident_id = p.payer_resident_id
                WHERE p.apartment_id = %s AND p.payment_kind = 'to_pool' AND p.status = 'active'
                  AND p.amount_paise > {_APPLIED_TO_PAYMENT}
                ORDER BY p.payment_date, p.row_seq""", (apartment_id,))

    def provider_advances(self, apartment_id: str) -> list[dict[str, Any]]:
        """Provider advances with the expense each is linked to, newest first."""
        return self._s.fetch_all(
            """SELECT p.*, r.display_name AS payer_display_name,
                      e.description AS expense_description, e.amount_paise AS expense_amount_paise,
                      e.payer_resident_id AS expense_payer_resident_id,
                      e.settlement_period AS expense_settlement_period
                 FROM payment p
                 JOIN resident r ON r.resident_id = p.payer_resident_id
                 LEFT JOIN expense_instance e ON e.expense_id = p.expense_id
                WHERE p.apartment_id = %s AND p.payment_kind = 'provider_advance'
                ORDER BY p.payment_date DESC, p.row_seq DESC""", (apartment_id,))

    # -- one resident's charge ledger ----------------------------------------

    def charge_ledger_for_resident(self, resident_id: str) -> list[dict[str, Any]]:
        """One resident's allocations, oldest first, with what is settled on each."""
        return self._s.fetch_all(
            """SELECT a.allocation_id, a.amount_paise, e.expense_id, e.description,
                      e.expense_date, e.due_date, e.settlement_period, e.service_period,
                      e.statement_id, s.status AS statement_status,
                      COALESCE((SELECT SUM(pa.amount_paise) FROM payment_application pa
                                 WHERE pa.allocation_id = a.allocation_id), 0)::BIGINT
                          AS settled_paise
                 FROM allocation a
                 JOIN expense_instance e ON e.expense_id = a.expense_id
                 LEFT JOIN monthly_statement s ON s.statement_id = e.statement_id
                WHERE a.resident_id = %s
                ORDER BY COALESCE(e.due_date, e.expense_date), a.row_seq""", (resident_id,))

    def opening_debt_ledger_row(self, resident_id: str) -> dict[str, Any] | None:
        """The resident's opening debt as a (positive) charge, with what is settled."""
        return self._s.fetch_one(
            """SELECT ob.opening_balance_id, -ob.amount_paise AS amount_paise, ob.as_of_date,
                      ob.note,
                      COALESCE((SELECT SUM(pa.amount_paise) FROM payment_application pa
                                 WHERE pa.opening_balance_id = ob.opening_balance_id), 0)::BIGINT
                          AS settled_paise
                 FROM opening_balance ob
                WHERE ob.resident_id = %s AND ob.amount_paise < 0""", (resident_id,))

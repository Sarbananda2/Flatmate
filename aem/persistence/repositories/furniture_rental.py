"""Household services, and the furniture rental module."""
from __future__ import annotations

from typing import Any

from .base import Repository

#: Item columns a caller may change. Anything else is refused, so no
#: caller-supplied name ever reaches the SQL.
ITEM_COLUMNS = ("service_id", "name", "category", "monthly_rent_paise", "base_rent_paise",
                "is_gst_exclusive", "gst_rate_percent", "allocation_scope",
                "assigned_resident_id", "status", "delivery_date", "return_date")

#: An item with the resident it is for and the provider it is rented from.
ITEM_SELECT = """SELECT i.*, r.display_name AS assigned_resident_name,
                        COALESCE(c.provider_name, s.provider_name, s.display_name)
                            AS provider_name,
                        s.display_name AS service_display_name
                   FROM furniture_rental_item i
                   JOIN household_service s ON s.service_id = i.service_id
                   LEFT JOIN furniture_rental_config c ON c.service_id = i.service_id
                   LEFT JOIN resident r ON r.resident_id = i.assigned_resident_id"""

#: Subscription columns a caller may change.
CONFIG_COLUMNS = ("provider_name", "customer_id", "subscription_reference",
                  "deposit_paid_paise", "deposit_refunded_paise", "deposit_status",
                  "expense_definition_id", "notes")


class FurnitureRentalRepository(Repository):
    # ----------------------------------------------------------- the service --

    def live_service(self, apartment_id: str, service_type: str) -> dict[str, Any] | None:
        """The flat's first service of this type that has not been retired, if any."""
        return self._s.fetch_one(
            """SELECT * FROM household_service
                WHERE apartment_id=%s AND service_type=%s AND status <> 'retired'
                ORDER BY row_seq LIMIT 1""",
            (apartment_id, service_type))

    def live_services(self, apartment_id: str, service_type: str) -> list[dict[str, Any]]:
        """Every service of this type the flat has not retired, oldest first. A flat
        may rent furniture from several providers at once."""
        return self._s.fetch_all(
            """SELECT * FROM household_service
                WHERE apartment_id=%s AND service_type=%s AND status <> 'retired'
                ORDER BY row_seq""",
            (apartment_id, service_type))

    def service_in_apartment(self, service_id: str, apartment_id: str) -> dict[str, Any] | None:
        return self._s.fetch_one(
            "SELECT * FROM household_service WHERE service_id=%s AND apartment_id=%s",
            (service_id, apartment_id))

    def insert_service(self, *, service_id: str, apartment_id: str, service_type: str,
                       display_name: str, provider_name: str | None,
                       billing_day_of_month: int | None, created_at: str) -> None:
        self._s.execute(
            """INSERT INTO household_service (service_id, apartment_id, service_type,
                   display_name, provider_name, billing_day_of_month, created_at, updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (service_id, apartment_id, service_type, display_name, provider_name,
             billing_day_of_month, created_at, created_at))

    def update_service(self, service_id: str, *, display_name: str, provider_name: str | None,
                       billing_day_of_month: int | None, updated_at: str) -> None:
        self._s.execute(
            """UPDATE household_service SET display_name=%s, provider_name=%s,
                   billing_day_of_month=%s, updated_at=%s WHERE service_id=%s""",
            (display_name, provider_name, billing_day_of_month, updated_at, service_id))

    def retire_service(self, service_id: str, *, retired_at: str) -> None:
        self._s.execute(
            """UPDATE household_service SET status='retired', retired_at=%s, updated_at=%s
                WHERE service_id=%s""", (retired_at, retired_at, service_id))

    # ------------------------------------------------------ the subscription --

    def config(self, service_id: str) -> dict[str, Any] | None:
        return self._s.fetch_one(
            "SELECT * FROM furniture_rental_config WHERE service_id=%s", (service_id,))

    def configs_for_apartment(self, apartment_id: str) -> list[dict[str, Any]]:
        return self._s.fetch_all(
            "SELECT * FROM furniture_rental_config WHERE apartment_id=%s ORDER BY service_id",
            (apartment_id,))

    def insert_config(self, *, service_id: str, apartment_id: str, updated_at: str,
                      **values: Any) -> None:
        columns = [column for column in CONFIG_COLUMNS if column in values]
        if set(values) - set(columns):
            raise ValueError(f"Not subscription columns: {sorted(set(values) - set(columns))}")
        names = ", ".join(["service_id", "apartment_id", "updated_at", *columns])
        params = (service_id, apartment_id, updated_at, *(values[c] for c in columns))
        self._s.execute(
            f"INSERT INTO furniture_rental_config ({names}) "
            f"VALUES ({', '.join(['%s'] * len(params))})", params)

    def update_config(self, service_id: str, *, updated_at: str, **values: Any) -> None:
        columns = [column for column in CONFIG_COLUMNS if column in values]
        if set(values) - set(columns):
            raise ValueError(f"Not subscription columns: {sorted(set(values) - set(columns))}")
        sets = "".join(f", {column}=%s" for column in columns)
        self._s.execute(
            f"UPDATE furniture_rental_config SET updated_at=%s{sets} WHERE service_id=%s",
            (updated_at, *(values[c] for c in columns), service_id))

    def find_definition_id(self, apartment_id: str) -> str | None:
        """The active definition called, or with the ID, 'Furniture rental'."""
        return self._s.scalar(
            """SELECT expense_definition_id FROM expense_definition
                WHERE apartment_id=%s AND is_active=1
                  AND (lower(trim(name))='furniture rental'
                       OR lower(expense_definition_id) IN ('furniture-rental', 'furniture_rental'))
                ORDER BY sort_order, expense_definition_id LIMIT 1""", (apartment_id,))

    def definition_id_taken(self, definition_id: str) -> bool:
        return self._s.fetch_one(
            "SELECT 1 AS present FROM expense_definition WHERE expense_definition_id=%s",
            (definition_id,)) is not None

    # --------------------------------------------------------------- the items --

    def items(self, service_id: str) -> list[dict[str, Any]]:
        """Every item on the subscription, with the resident it is assigned to."""
        return self._s.fetch_all(
            f"{ITEM_SELECT} WHERE i.service_id=%s ORDER BY i.row_seq", (service_id,))

    def items_for_apartment(self, apartment_id: str, *,
                            include_retired: bool = False) -> list[dict[str, Any]]:
        """Every item on the flat's live furniture subscriptions, from whichever
        provider, in the order they were added. With ``include_retired``, the
        items of subscriptions since ended too, for the months they were billed."""
        live_only = "" if include_retired else "AND s.status <> 'retired'"
        return self._s.fetch_all(
            f"""{ITEM_SELECT}
                WHERE i.apartment_id=%s AND s.service_type='furniture_rental' {live_only}
                ORDER BY i.row_seq""", (apartment_id,))

    def item(self, item_id: str) -> dict[str, Any] | None:
        return self._s.fetch_one(f"{ITEM_SELECT} WHERE i.item_id=%s", (item_id,))

    def insert_item(self, *, item_id: str, service_id: str, apartment_id: str, name: str,
                    category: str, monthly_rent_paise: int, base_rent_paise: int,
                    is_gst_exclusive: bool, gst_rate_percent: float, allocation_scope: str,
                    assigned_resident_id: str | None, status: str,
                    delivery_date: str | None, return_date: str | None,
                    created_at: str) -> None:
        self._s.execute(
            """INSERT INTO furniture_rental_item (item_id, service_id, apartment_id, name,
                   category, monthly_rent_paise, base_rent_paise, is_gst_exclusive,
                   gst_rate_percent, allocation_scope, assigned_resident_id, status,
                   delivery_date, return_date, created_at, updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (item_id, service_id, apartment_id, name, category, monthly_rent_paise,
             base_rent_paise, is_gst_exclusive, gst_rate_percent, allocation_scope,
             assigned_resident_id, status, delivery_date, return_date, created_at,
             created_at))

    def update_item(self, item_id: str, *, updated_at: str, **values: Any) -> None:
        columns = [column for column in ITEM_COLUMNS if column in values]
        if set(values) - set(columns):
            raise ValueError(f"Not item columns: {sorted(set(values) - set(columns))}")
        sets = "".join(f", {column}=%s" for column in columns)
        self._s.execute(
            f"UPDATE furniture_rental_item SET updated_at=%s{sets} WHERE item_id=%s",
            (updated_at, *(values[c] for c in columns), item_id))

    def delete_item(self, item_id: str) -> int:
        return self._s.execute("DELETE FROM furniture_rental_item WHERE item_id=%s", (item_id,))

    # ------------------------------------------------------- the month's bill --

    def expenses_for_month(self, apartment_id: str, definition_id: str,
                           settlement_month: str) -> list[dict[str, Any]]:
        """Expenses recorded under the definition for a settlement month."""
        return self._s.fetch_all(
            """SELECT expense_id, description, amount_paise, statement_id, payer_resident_id,
                      notes, provider_name, settlement_period
                 FROM expense_instance
                WHERE apartment_id=%s AND expense_definition_id=%s AND settlement_period=%s
                ORDER BY row_seq""", (apartment_id, definition_id, settlement_month))

    def bill_months(self, apartment_id: str, definition_id: str) -> list[str]:
        """Every month a furniture bill is recorded for, oldest first."""
        return [row["settlement_period"] for row in self._s.fetch_all(
            """SELECT DISTINCT settlement_period FROM expense_instance
                WHERE apartment_id=%s AND expense_definition_id=%s
                  AND settlement_period IS NOT NULL
                ORDER BY settlement_period""", (apartment_id, definition_id))]

    def allocations_for_expense(self, expense_id: str) -> list[dict[str, Any]]:
        """Each resident's charge row on a bill, with what has been paid against it."""
        return self._s.fetch_all(
            """SELECT a.allocation_id, a.component_id, a.resident_id, a.amount_paise,
                      a.allocation_basis_json,
                      COALESCE((SELECT SUM(pa.amount_paise) FROM payment_application pa
                                 WHERE pa.allocation_id = a.allocation_id), 0)::BIGINT
                          AS settled_paise
                 FROM allocation a WHERE a.expense_id = %s ORDER BY a.row_seq""",
            (expense_id,))

    def update_allocation(self, allocation_id: str, *, amount_paise: int,
                          allocation_basis_json: str) -> int:
        """Change a charge in place, so the payments applied to it stay with it."""
        return self._s.execute(
            "UPDATE allocation SET amount_paise=%s, allocation_basis_json=%s "
            "WHERE allocation_id=%s", (amount_paise, allocation_basis_json, allocation_id))

    def delete_allocation(self, allocation_id: str) -> int:
        return self._s.execute("DELETE FROM allocation WHERE allocation_id=%s", (allocation_id,))

    def update_bill(self, expense_id: str, *, amount_paise: int, notes: str,
                    provider_name: str, updated_at: str) -> int:
        """A live bill following its items: the total, the items behind it, the providers."""
        return self._s.execute(
            """UPDATE expense_instance SET amount_paise=%s, notes=%s, provider_name=%s,
                   updated_at=%s WHERE expense_id=%s""",
            (amount_paise, notes, provider_name, updated_at, expense_id))

    def statement_for_month(self, apartment_id: str,
                            settlement_month: str) -> dict[str, Any] | None:
        return self._s.fetch_one(
            "SELECT statement_id, status FROM monthly_statement "
            "WHERE apartment_id=%s AND settlement_month=%s", (apartment_id, settlement_month))

    # ------------------------------------------------- paying the month's bill --

    def charges_for_expense(self, expense_id: str) -> list[dict[str, Any]]:
        """Each resident's charge on a month's bill, with what has been paid into
        the pool against it and the date of the latest such payment."""
        return self._s.fetch_all(
            """SELECT a.resident_id,
                      SUM(a.amount_paise)::BIGINT AS share_paise,
                      COALESCE(SUM((SELECT SUM(pa.amount_paise) FROM payment_application pa
                                     WHERE pa.allocation_id = a.allocation_id)), 0)::BIGINT
                          AS settled_paise,
                      (SELECT MAX(p.payment_date) FROM payment_application pa
                         JOIN payment p ON p.payment_id = pa.payment_id
                         JOIN allocation a2 ON a2.allocation_id = pa.allocation_id
                        WHERE a2.expense_id = a.expense_id
                          AND a2.resident_id = a.resident_id) AS last_payment_date
                 FROM allocation a
                WHERE a.expense_id = %s
                GROUP BY a.expense_id, a.resident_id
                ORDER BY a.resident_id""", (expense_id,))

    def resident_charges_for_expense(self, expense_id: str,
                                     resident_id: str) -> list[dict[str, Any]]:
        """One resident's allocations on a month's bill, oldest first, with what is
        still owed on each."""
        return self._s.fetch_all(
            """SELECT a.allocation_id,
                      (a.amount_paise - COALESCE((SELECT SUM(pa.amount_paise)
                                                    FROM payment_application pa
                                                   WHERE pa.allocation_id = a.allocation_id), 0)
                      )::BIGINT AS outstanding_paise
                 FROM allocation a
                WHERE a.expense_id = %s AND a.resident_id = %s ORDER BY a.row_seq""",
            (expense_id, resident_id))

    def payments_for_expense(self, expense_id: str) -> list[dict[str, Any]]:
        """Each payment that settled part of a resident's charge on a month's bill,
        oldest first, with what it put towards the charge -- not the whole
        payment, whose excess may have gone to other charges."""
        return self._s.fetch_all(
            """SELECT a.resident_id, p.payment_id, p.payment_date,
                      SUM(pa.amount_paise)::BIGINT AS amount_paise,
                      p.payment_mode, p.reference, p.note
                 FROM payment_application pa
                 JOIN payment p ON p.payment_id = pa.payment_id
                 JOIN allocation a ON a.allocation_id = pa.allocation_id
                WHERE a.expense_id = %s AND p.status = 'active'
                GROUP BY a.resident_id, p.payment_id, p.payment_date, p.payment_mode,
                         p.reference, p.note, p.created_at
                ORDER BY p.payment_date, p.created_at, p.payment_id""", (expense_id,))

    def linked_advance_total_paise(self, expense_id: str) -> int:
        """Active provider advances linked to a month's bill, however recorded."""
        return int(self._s.scalar(
            """SELECT COALESCE(SUM(amount_paise), 0)::BIGINT FROM payment
                WHERE expense_id=%s AND payment_kind='provider_advance'
                  AND status='active'""", (expense_id,), 0))

    # ------------------------------------------------- paying the provider --
    # Payouts to a provider are kept per service, for any service module.

    def service_disbursements(self, expense_id: str) -> list[dict[str, Any]]:
        """The payouts to the provider towards a bill, oldest first, with who
        funded each one that a resident paid."""
        return self._s.fetch_all(
            """SELECT d.*, d.row_seq AS seq, r.display_name AS funding_resident_name,
                      COALESCE(c.provider_name, s.provider_name, s.display_name)
                          AS provider_name
                 FROM service_disbursement d
                 JOIN household_service s ON s.service_id = d.service_id
                 LEFT JOIN furniture_rental_config c ON c.service_id = d.service_id
                 LEFT JOIN resident r ON r.resident_id = d.funding_resident_id
                WHERE d.expense_id = %s
                ORDER BY d.disbursed_at, d.row_seq""", (expense_id,))

    def service_disbursement(self, disbursement_id: str) -> dict[str, Any] | None:
        return self._s.fetch_one(
            "SELECT * FROM service_disbursement WHERE disbursement_id=%s", (disbursement_id,))

    def delete_service_disbursement(self, disbursement_id: str) -> int:
        """A payout recorded by mistake, while the month is still open."""
        return self._s.execute("DELETE FROM service_disbursement WHERE disbursement_id=%s",
                               (disbursement_id,))

    def update_service_disbursement_amount(self, disbursement_id: str,
                                           amount_paise: int) -> int:
        """A resident's payout split again between their share and their advance,
        while the month is open."""
        return self._s.execute(
            "UPDATE service_disbursement SET amount_paise=%s WHERE disbursement_id=%s",
            (amount_paise, disbursement_id))

    def service_disbursements_for_apartment(self, apartment_id: str) -> list[dict[str, Any]]:
        """Every payout to a household service's provider, from the kitty or by a
        resident, oldest first: the service, the provider's name, who advanced it,
        and the order recorded."""
        return self._s.fetch_all(
            """SELECT d.*, d.row_seq AS seq, s.display_name,
                      COALESCE(c.provider_name, s.provider_name, s.display_name)
                          AS provider_name,
                      r.display_name AS funding_resident_name
                 FROM service_disbursement d
                 JOIN household_service s ON s.service_id = d.service_id
                 LEFT JOIN furniture_rental_config c ON c.service_id = d.service_id
                 LEFT JOIN resident r ON r.resident_id = d.funding_resident_id
                WHERE d.apartment_id = %s
                ORDER BY d.disbursed_at, d.row_seq""", (apartment_id,))

    def insert_service_disbursement(self, *, disbursement_id: str, service_id: str,
                                    expense_id: str, apartment_id: str, settlement_month: str,
                                    amount_paise: int, disbursed_at: str,
                                    disbursement_reference: str, disbursed_by: str,
                                    funding_resident_id: str | None,
                                    funding_payment_id: str | None, note: str | None,
                                    created_at: str) -> None:
        self._s.execute(
            """INSERT INTO service_disbursement (disbursement_id, service_id, expense_id,
                   apartment_id, settlement_month, amount_paise, disbursed_at,
                   disbursement_reference, disbursed_by, funding_resident_id,
                   funding_payment_id, note, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (disbursement_id, service_id, expense_id, apartment_id, settlement_month,
             amount_paise, disbursed_at, disbursement_reference, disbursed_by,
             funding_resident_id, funding_payment_id, note, created_at))

    def reimbursements(self, payment_id_prefix: str) -> list[dict[str, Any]]:
        """Active refunds from the pool whose payment ID starts with the prefix,
        oldest first."""
        return self._s.fetch_all(
            """SELECT payment_id, payer_resident_id, amount_paise, payment_date, reference
                 FROM payment
                WHERE payment_kind = 'refund' AND status = 'active'
                  AND starts_with(payment_id, %s)
                ORDER BY payment_date, row_seq""", (payment_id_prefix,))

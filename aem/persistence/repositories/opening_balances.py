"""Opening balances."""
from __future__ import annotations

from typing import Any

from .base import Repository


class OpeningBalancesRepository(Repository):
    def get_for_resident(self, resident_id: str) -> dict[str, Any] | None:
        return self._s.fetch_one(
            "SELECT * FROM opening_balance WHERE resident_id=%s", (resident_id,))

    def get_with_resident_name(self, opening_balance_id: str) -> dict[str, Any] | None:
        return self._s.fetch_one(
            """SELECT ob.*, r.display_name FROM opening_balance ob
                 JOIN resident r ON r.resident_id = ob.resident_id
                WHERE ob.opening_balance_id=%s""", (opening_balance_id,))

    def get_for_resident_with_name(self, resident_id: str) -> dict[str, Any] | None:
        return self._s.fetch_one(
            """SELECT ob.*, r.display_name FROM opening_balance ob
                 JOIN resident r ON r.resident_id = ob.resident_id
                WHERE ob.resident_id=%s""", (resident_id,))

    def list_for_apartment(self, apartment_id: str) -> list[dict[str, Any]]:
        """Every opening balance in the apartment, by resident name."""
        return self._s.fetch_all(
            """SELECT ob.*, r.display_name FROM opening_balance ob
                 JOIN resident r ON r.resident_id = ob.resident_id
                WHERE ob.apartment_id = %s ORDER BY r.display_name""", (apartment_id,))

    def insert_opening_balance(self, *, opening_balance_id: str, apartment_id: str,
                               resident_id: str, amount_paise: int, as_of_date: str,
                               note: str | None, created_at: str, created_by: str,
                               updated_at: str) -> None:
        self._s.execute(
            """INSERT INTO opening_balance
               (opening_balance_id, apartment_id, resident_id, amount_paise, as_of_date,
                note, created_at, created_by, updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (opening_balance_id, apartment_id, resident_id, amount_paise, as_of_date,
             note, created_at, created_by, updated_at))

    def update_opening_balance(self, opening_balance_id: str, *, amount_paise: int,
                               as_of_date: str, note: str | None, updated_at: str) -> int:
        return self._s.execute(
            """UPDATE opening_balance SET amount_paise=%s, as_of_date=%s, note=%s,
                      updated_at=%s WHERE opening_balance_id=%s""",
            (amount_paise, as_of_date, note, updated_at, opening_balance_id))

    def delete_opening_balance(self, opening_balance_id: str) -> int:
        return self._s.execute(
            "DELETE FROM opening_balance WHERE opening_balance_id=%s", (opening_balance_id,))

    # -- payments applied to an opening debt ---------------------------------

    def applied_paise(self, opening_balance_id: str) -> int:
        return int(self._s.scalar(
            "SELECT COALESCE(SUM(amount_paise), 0)::BIGINT FROM payment_application "
            "WHERE opening_balance_id=%s", (opening_balance_id,), default=0))

    def applications_for(self, opening_balance_id: str) -> list[dict[str, Any]]:
        return self._s.fetch_all(
            "SELECT payment_id, amount_paise FROM payment_application "
            "WHERE opening_balance_id=%s", (opening_balance_id,))

    def delete_applications_for(self, opening_balance_id: str) -> int:
        return self._s.execute(
            "DELETE FROM payment_application WHERE opening_balance_id=%s",
            (opening_balance_id,))

    # -- statements that lock opening balances -------------------------------

    def non_draft_statements(self, apartment_id: str) -> list[dict[str, Any]]:
        """Statements past draft, earliest settlement month first."""
        return self._s.fetch_all(
            "SELECT statement_id, settlement_month, status FROM monthly_statement "
            "WHERE apartment_id=%s AND status <> 'draft' ORDER BY settlement_month",
            (apartment_id,))

    def first_non_draft_month(self, apartment_id: str) -> str | None:
        return self._s.scalar(
            "SELECT settlement_month FROM monthly_statement "
            "WHERE apartment_id=%s AND status <> 'draft' ORDER BY settlement_month LIMIT 1",
            (apartment_id,))

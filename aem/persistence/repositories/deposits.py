"""Household deposit slots, their transactions, and how the deposit was funded."""
from __future__ import annotations

from typing import Any

from .base import Repository

#: Columns of ``household_deposit_slot`` a service may set after insert.
#: Column names reach the SQL text, so they are checked against this.
_UPDATABLE = frozenset({
    "slot_label", "target_deposit_paise", "assigned_resident_id", "assigned_since",
    "status", "funded_amount_paise", "funded_date", "payment_method",
    "payment_reference", "paid_to_resident_id",
})


class DepositsRepository(Repository):
    def set_funding_mode(self, config_id: str, *, mode: str,
                         primary_signatory_resident_id: str | None,
                         updated_at: str) -> None:
        self._s.execute(
            """UPDATE rent_lease_config
                  SET deposit_funding_mode=%s, deposit_primary_signatory_resident_id=%s,
                      updated_at=%s
                WHERE config_id=%s""",
            (mode, primary_signatory_resident_id, updated_at, config_id))

    # ----------------------------------------------------------------- slots --

    def slot(self, slot_id: str) -> dict[str, Any] | None:
        return self._s.fetch_one(
            "SELECT * FROM household_deposit_slot WHERE slot_id=%s", (slot_id,))

    def slots_for_apartment(self, apartment_id: str) -> list[dict[str, Any]]:
        return self._s.fetch_all(
            "SELECT * FROM household_deposit_slot WHERE apartment_id=%s ORDER BY slot_number",
            (apartment_id,))

    def insert_slot(self, *, slot_id: str, apartment_id: str, config_id: str,
                    slot_number: int, slot_label: str, target_deposit_paise: int,
                    assigned_resident_id: str | None, assigned_since: str | None,
                    status: str, funded_amount_paise: int, payment_method: str | None,
                    created_at: str) -> None:
        self._s.execute(
            """INSERT INTO household_deposit_slot
               (slot_id, apartment_id, config_id, slot_number, slot_label,
                target_deposit_paise, assigned_resident_id, assigned_since, status,
                funded_amount_paise, payment_method, created_at, updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (slot_id, apartment_id, config_id, slot_number, slot_label,
             target_deposit_paise, assigned_resident_id, assigned_since, status,
             funded_amount_paise, payment_method, created_at, created_at))

    def update_slot(self, slot_id: str, *, updated_at: str, **fields: Any) -> int:
        """Set the given columns, in the order given."""
        unknown = sorted(set(fields) - _UPDATABLE)
        if unknown:
            raise ValueError(
                f"Not updatable household_deposit_slot columns: {', '.join(unknown)}")
        return self._s.execute(
            f"UPDATE household_deposit_slot SET {', '.join(f'{c}=%s' for c in fields)}, "
            "updated_at=%s WHERE slot_id=%s",
            (*fields.values(), updated_at, slot_id))

    # ---------------------------------------------------------- transactions --

    def insert_transaction(self, *, tx_id: str, slot_id: str, apartment_id: str,
                           resident_id: str, tx_type: str, amount_paise: int,
                           tx_date: str, created_at: str,
                           counterparty_resident_id: str | None = None,
                           payment_method: str | None = None,
                           reference: str | None = None,
                           notes: str | None = None) -> None:
        self._s.execute(
            """INSERT INTO household_deposit_transaction
               (tx_id, slot_id, apartment_id, resident_id, tx_type, amount_paise, tx_date,
                counterparty_resident_id, payment_method, reference, notes, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (tx_id, slot_id, apartment_id, resident_id, tx_type, amount_paise, tx_date,
             counterparty_resident_id, payment_method, reference, notes, created_at))

    def transactions_for_apartment(self, apartment_id: str) -> list[dict[str, Any]]:
        """Every deposit transaction, oldest first."""
        return self._s.fetch_all(
            "SELECT * FROM household_deposit_transaction WHERE apartment_id=%s "
            "ORDER BY row_seq", (apartment_id,))

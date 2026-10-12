"""Residents' exit clearance records."""
from __future__ import annotations

from typing import Any

from .base import Repository

_COLUMNS = (
    "clearance_id", "apartment_id", "resident_id", "move_out_date", "slot_id",
    "deposit_funded_paise", "deposit_deductions_paise", "deduction_reason",
    "ledger_outstanding_paise", "ledger_credit_paise", "net_ledger_offset_applied",
    "net_settlement_paise", "settlement_direction", "settlement_path", "settlement_date",
    "settlement_reference", "counterparty_resident_id", "clearing_payment_id", "notes",
    "created_at",
)


class ClearanceRepository(Repository):
    def insert(self, **fields: Any) -> None:
        """Add one clearance record. Every column must be given."""
        missing = sorted(set(_COLUMNS) - set(fields))
        unknown = sorted(set(fields) - set(_COLUMNS))
        if missing or unknown:
            raise ValueError(
                f"resident_exit_clearance columns: missing {missing}, unknown {unknown}")
        self._s.execute(
            f"INSERT INTO resident_exit_clearance ({', '.join(_COLUMNS)}) "
            f"VALUES ({', '.join('%s' for _ in _COLUMNS)})",
            tuple(fields[column] for column in _COLUMNS))

    def latest_for_resident(self, resident_id: str) -> dict[str, Any] | None:
        """The most recent clearance, for somebody who has left more than once."""
        return self._s.fetch_one(
            "SELECT * FROM resident_exit_clearance WHERE resident_id=%s "
            "ORDER BY row_seq DESC LIMIT 1", (resident_id,))

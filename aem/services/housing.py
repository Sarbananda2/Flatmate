"""Housing & Utilities: one summary of the household's recurring services.

Read-only. It composes what the rent and electricity services already report so
the hub screen makes a single call; it computes no money of its own.
"""
from __future__ import annotations

from typing import Any

from . import electricity, rent
from ..periods import normalize_month


def services_overview(db_path, apartment_id: str, settlement_month: str) -> dict[str, Any]:
    """The lease and the month's rent cycle, and the electricity bill settling
    in the month alongside the most recent bill on file."""
    settlement_month = normalize_month(settlement_month, "settlement_month")
    bills = electricity.list_electricity_bills(db_path, apartment_id, limit=500)["items"]
    for_month = [bill for bill in bills if bill["settlement_period"] == settlement_month]
    return {
        "settlement_month": settlement_month,
        "rent": {
            "config": rent.get_lease_config(db_path, apartment_id),
            "cycle": rent.get_rent_cycle(db_path, apartment_id, settlement_month),
        },
        "electricity": {
            "bill": for_month[0] if for_month else None,
            "bills_for_month": len(for_month),
            # Newest first, so this is the last bill entered for any month.
            "latest_bill": bills[0] if bills else None,
        },
    }

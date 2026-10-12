#!/usr/bin/env python3
"""Give three settlement months a different story on the Housing & Utilities page.

    python scripts/seed_settlement_periods.py                seed the default database
    python scripts/seed_settlement_periods.py --db other.sqlite

Run it after ``seed_lease_data.py``, which sets up the lease and October's
rent. Stepping the Settlement Period picker then shows:

    September 2026   rent fully collected and paid to the landlord;
                     the August electricity bill settles here
    October 2026     rent two-thirds collected, payout pending;
                     the September electricity bill settles here
    November 2026    rent not generated yet; no electricity bill, so the card
                     falls back to the most recent one

Everything goes through the services, so it is validated and audited as if it
had been entered in the app. September's rent already exists in the sample
household at its own amount, with payments against it, so the rent cycle adopts
that expense rather than charging the lease rent a second time.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aem.config import get_settings  # noqa: E402
from aem.db import connect  # noqa: E402
from aem.errors import AemError  # noqa: E402
from aem.migrations import migrate  # noqa: E402
from aem.money import format_inr  # noqa: E402
from aem.services import electricity, payments, rent  # noqa: E402

SEPTEMBER, OCTOBER = "2026-09", "2026-10"
OCTOBER_BILL_ID = "electricity-2026-10"

# Everyone had settled September's rent before it went to the landlord on the 5th.
SEPTEMBER_PAYMENTS = (("alice", "2026-09-02"), ("bob", "2026-09-03"), ("chris", "2026-09-04"))


def _one(path: Path, sql: str, params: tuple) -> dict | None:
    with connect(path, read_only=True) as conn:
        row = conn.execute(sql, params).fetchone()
    return dict(row) if row else None


def seed(path: Path, apartment_id: str) -> int:
    migrate(path)
    config = rent.get_lease_config(path, apartment_id)
    if config is None:
        print("There is no lease yet. Run scripts/seed_lease_data.py first.")
        return 1
    if rent.get_rent_cycle(path, apartment_id, SEPTEMBER) is not None:
        print(f"{SEPTEMBER} already has its rent generated; leaving the database as it is.")
        return 1
    if _one(path, "SELECT 1 FROM expense_instance WHERE expense_id=?", (OCTOBER_BILL_ID,)):
        print(f"{OCTOBER_BILL_ID} already exists; leaving the database as it is.")
        return 1

    # September: the sample household already has a rent expense for the month,
    # with payments against it, so the cycle adopts that expense as it stands
    # rather than charging the lease rent again. Whatever is still owed on it
    # is then paid, and the landlord is paid from the pool.
    cycle = rent.generate_monthly_rent_cycle(path, apartment_id, SEPTEMBER, actor="seed")
    owed = {row["resident_id"]: row["outstanding_paise"] for row in cycle["participants"]}
    for resident_id, paid_on in SEPTEMBER_PAYMENTS:
        if owed.get(resident_id, 0) > 0:
            rent.record_rent_payment(
                path, apartment_id=apartment_id, settlement_month=SEPTEMBER,
                resident_id=resident_id, amount_paise=owed[resident_id], payment_date=paid_on,
                payment_mode="upi", note=f"Rent for {rent.month_label(SEPTEMBER)}",
                actor="seed")
    cycle = rent.record_landlord_disbursement(
        path, apartment_id=apartment_id, settlement_month=SEPTEMBER, disbursement_mode="pool",
        reference="UTR428919028471", disbursed_date="2026-09-05", actor="seed")
    print(f"{SEPTEMBER}: rent {format_inr(cycle['total_rent_paise'])}, collected "
          f"{format_inr(cycle['collected_paise'])}, landlord {cycle['disbursement_status']}")

    # October: the September electricity bill, paid by Alice on the household's behalf.
    statement = _one(
        path, "SELECT statement_id FROM monthly_statement WHERE apartment_id=? "
              "AND settlement_month=?", (apartment_id, OCTOBER))
    electricity.create_electricity_expense(
        path, apartment_id=apartment_id, expense_id=OCTOBER_BILL_ID,
        description="September electricity bill", expense_date="2026-10-08",
        expense_definition_id="electricity", total_amount_paise=682_000,
        payer_resident_id="alice", statement_id=statement["statement_id"] if statement else None,
        bill_number="SEB/26/10/1304",
        smart_plug_readings=[
            {"resident_id": "alice", "kwh": "38.250", "device_label": "AC"},
            {"resident_id": "bob", "kwh": "29.500", "device_label": "AC"},
            {"resident_id": "chris", "kwh": "16.750", "device_label": "AC"}],
        components=[
            {"id": "energy", "name": "Energy charges", "amount_paise": 597_000,
             "allocation_method": "usage"},
            {"id": "fixed", "name": "Fixed and common charges", "amount_paise": 85_000,
             "allocation_method": "equal"}])
    payments.record_payment(
        path, apartment_id=apartment_id, payment_id="advance-electricity-2026-10",
        payer_resident_id="alice", amount_paise=682_000, payment_date="2026-10-08",
        payment_kind="provider_advance", payee_or_provider="State Electricity Board",
        expense_id=OCTOBER_BILL_ID)
    print(f"{OCTOBER}: electricity bill {format_inr(682_000)} added "
          "(the rent stays two-thirds collected)")
    print("2026-11: left empty on purpose")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", help="database path (default: the configured one)")
    parser.add_argument("--apartment", default="home", help="apartment ID (default: home)")
    args = parser.parse_args()
    path = Path(args.db or get_settings().database_path)
    print(f"Database: {path}")
    try:
        return seed(path, args.apartment)
    except AemError as exc:
        print(f"Stopped: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

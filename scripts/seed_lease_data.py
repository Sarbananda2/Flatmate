#!/usr/bin/env python3
"""Seed a master lease in its second term, with October 2026 rent part-collected.

    python scripts/seed_lease_data.py                           seed the default database
    python scripts/seed_lease_data.py --db other.sqlite         seed another one
    python scripts/seed_lease_data.py --scenario final-month    the term's last month
    python scripts/seed_lease_data.py --replace-existing-rent   see below

It gives the lease screens something real to show: a lease that has already
been renewed once, a contract term seven months in, and a month's rent that two
of three residents have paid.

Everything goes through ``aem.services.rent``, so the data is validated and
audited exactly as if it had been entered in the app. The one exception is the
archive timestamp of the completed term, which is backdated to the day that
term was actually renewed.

The script refuses to run against a database that already has a lease, so it
cannot be applied twice. If the month already has a rent expense for a
different amount, generating rent would adopt that expense rather than charge
the lease rent; ``--replace-existing-rent`` deletes it first, which is only
possible while it is unpaid and in a draft statement.
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
from aem.services import expenses, rent  # noqa: E402

MONTH = "2026-10"

LANDLORD = {
    "landlord_name": "Kumar Yadav",
    "landlord_phone": "+91 98765 43210",
    "landlord_pan": "ABCPS1234K",
    "landlord_bank_account": "50100012348912",
    "landlord_bank_ifsc": "HDFC0000128",
    "landlord_upi_id": "kumar.yadav@okhdfcbank",
    "due_day_of_month": 5,
    "requires_landlord_receipt": False,
    "notice_period_days": 30,
    "lock_in_months": 6,
    "security_deposit_paise": 15_000_000,
    "escalation_type": "percent",
    "escalation_percent": 5.0,
}

# Each scenario is a completed first term and the term it was renewed into.
# The lease's place in its term is worked out from today's date, so which
# scenario shows what depends on when it is seeded relative to October 2026.
SCENARIOS = {
    # May 2025 to March 2026, renewed into April 2026 to February 2027: October
    # is month 7 of 11, well before the notice window.
    "mid-term": {
        "previous": {"lease_start_date": "2025-05-01", "duration_months": 11,
                     "monthly_rent_paise": 2_700_000},
        "current": {"new_start_date": "2026-04-01", "duration_months": 11,
                    "new_monthly_rent_paise": 2_850_000},
    },
    # January to November 2025, renewed into December 2025 to October 2026:
    # October is month 11 of 11, so the notice window is open and the Rent
    # screen asks whether to renew or vacate.
    "final-month": {
        "previous": {"lease_start_date": "2025-01-01", "duration_months": 11,
                     "monthly_rent_paise": 2_700_000},
        "current": {"new_start_date": "2025-12-01", "duration_months": 11,
                    "new_monthly_rent_paise": 2_850_000},
    },
}

# Who has paid their October share so far. Chris is left owing.
PAYMENTS = (("alice", "2026-10-02"), ("bob", "2026-10-03"))


def _existing_rent_expense(path: Path, apartment_id: str) -> dict | None:
    """The month's rent expense under the household's Rent definition, if any."""
    with connect(path, read_only=True) as conn:
        definition_id = rent._find_rent_definition(conn, apartment_id)
        row = conn.execute(
            """SELECT expense_id, amount_paise FROM expense_instance
                WHERE apartment_id=? AND expense_definition_id=? AND settlement_period=?""",
            (apartment_id, definition_id, MONTH)).fetchone()
    return dict(row) if row else None


def seed(path: Path, apartment_id: str, *, scenario: str = "mid-term",
         replace_existing_rent: bool) -> int:
    previous, current = SCENARIOS[scenario]["previous"], SCENARIOS[scenario]["current"]
    migrate(path)
    if rent.get_lease_config(path, apartment_id) is not None:
        print(f"Apartment {apartment_id!r} already has a lease; leaving it as it is.")
        return 1
    if rent.list_lease_terms(path, apartment_id):
        print(f"Apartment {apartment_id!r} already has lease history; leaving it as it is.")
        return 1

    # Checked before anything is written, so a refusal leaves the database alone.
    # An expense already there for another amount would be adopted as it is
    # rather than charged at the lease rent.
    existing = _existing_rent_expense(path, apartment_id)
    replace = bool(existing
                   and existing["amount_paise"] != current["new_monthly_rent_paise"])
    if replace and not replace_existing_rent:
        print(f"{MONTH} already has a rent expense of {format_inr(existing['amount_paise'])} "
              f"({existing['expense_id']}).")
        print("Nothing was changed. Pass --replace-existing-rent to delete it and charge "
              "the lease rent instead.")
        return 1

    # 1. The lease as it stood in its first term, then the renewal that archived it.
    rent.save_lease_config(path, apartment_id, {**LANDLORD, **previous}, actor="seed")
    config = rent.renew_lease_contract(path, apartment_id, current, actor="seed")
    with connect(path) as conn:
        conn.execute(
            "UPDATE rent_lease_term SET archived_at=?, created_at=? WHERE apartment_id=?",
            (current["new_start_date"] + "T09:00:00+05:30",
             previous["lease_start_date"] + "T09:00:00+05:30",
             apartment_id))
    term = rent.list_lease_terms(path, apartment_id)[0]
    print(f"Lease: {config['landlord_name']}, {format_inr(config['monthly_rent_paise'])} a month, "
          f"term {config['term_number']} from {config['lease_start_date']} "
          f"to {config['lease_end_date']}")
    print(f"Archived term {term['term_number']}: {term['start_date']} to {term['end_date']} at "
          f"{format_inr(term['monthly_rent_paise'])} "
          f"(+{term['escalation_applied_percent']}% on renewal)")

    # 2. October's rent.
    if replace:
        expenses.delete_expense(path, existing["expense_id"], apartment_id=apartment_id,
                                reason="Replaced by rent generated under the master lease.",
                                actor="seed")
        print(f"Removed the existing {MONTH} rent expense of "
              f"{format_inr(existing['amount_paise'])} ({existing['expense_id']})")

    cycle = rent.generate_monthly_rent_cycle(path, apartment_id, MONTH, actor="seed")
    print(f"Rent for {MONTH}: {format_inr(cycle['total_rent_paise'])} between "
          f"{cycle['participant_count']} residents")

    # 3. Two of the three pay their share.
    shares = {row["resident_id"]: row["share_paise"] for row in cycle["participants"]}
    for resident_id, paid_on in PAYMENTS:
        cycle = rent.record_rent_payment(
            path, apartment_id=apartment_id, settlement_month=MONTH, resident_id=resident_id,
            amount_paise=shares[resident_id], payment_date=paid_on, payment_mode="upi",
            note=f"Rent for {rent.month_label(MONTH)}", actor="seed")
    for row in cycle["participants"]:
        print(f"  {row['display_name']:8} share {format_inr(row['share_paise'])}  "
              f"paid {format_inr(row['settled_paise'])}  ({row['collection_status']})")
    print(f"Collected {format_inr(cycle['collected_paise'])} of "
          f"{format_inr(cycle['total_rent_paise'])}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", help="database path (default: the configured one)")
    parser.add_argument("--apartment", default="home", help="apartment ID (default: home)")
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default="mid-term",
                        help="where the lease stands in October 2026 (default: mid-term)")
    parser.add_argument("--replace-existing-rent", action="store_true",
                        help=f"delete a {MONTH} rent expense for a different amount first")
    args = parser.parse_args()
    path = Path(args.db or get_settings().database_path)
    print(f"Database: {path}")
    try:
        return seed(path, args.apartment, scenario=args.scenario,
                    replace_existing_rent=args.replace_existing_rent)
    except AemError as exc:
        print(f"Stopped: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

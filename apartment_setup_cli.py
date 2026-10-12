"""First-run setup wizard for a local Apartment Expense Manager database.

Usage:
  python apartment_setup_cli.py --db apartment.sqlite
"""
from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime
from pathlib import Path

from electricity_workflow import initialize_database


def main() -> int:
    parser = argparse.ArgumentParser(description="Create an apartment and register residents.")
    parser.add_argument("--db", required=True, help="SQLite database path")
    args = parser.parse_args()
    db = Path(args.db)
    initialize_database(db)

    apartment_id = input("Apartment ID (short unique key, e.g. home): ").strip()
    apartment_name = input("Apartment name: ").strip()
    if not apartment_id or not apartment_name:
        print("Apartment ID and name are required.")
        return 2

    created_at = datetime.now().astimezone().isoformat(timespec="seconds")
    with sqlite3.connect(str(db)) as conn:
        if conn.execute("SELECT 1 FROM apartment WHERE apartment_id = ?", (apartment_id,)).fetchone():
            print(f"Apartment ID {apartment_id!r} already exists.")
            return 2
        conn.execute(
            "INSERT INTO apartment (apartment_id, name, created_at) VALUES (?, ?, ?)",
            (apartment_id, apartment_name, created_at),
        )
        resident_count_raw = input("How many residents should be registered? ").strip()
        try:
            resident_count = int(resident_count_raw)
            if resident_count < 1:
                raise ValueError
        except ValueError:
            conn.rollback()
            print("Enter a positive whole number of residents.")
            return 2
        for index in range(resident_count):
            print(f"\nResident {index + 1}")
            resident_id = input("  Resident ID (unique key, e.g. alex): ").strip()
            display_name = input("  Display name: ").strip()
            if not resident_id or not display_name:
                conn.rollback()
                print("Resident ID and display name are required. Setup rolled back.")
                return 2
            if conn.execute("SELECT 1 FROM resident WHERE resident_id = ?", (resident_id,)).fetchone():
                conn.rollback()
                print(f"Resident ID {resident_id!r} already exists. Setup rolled back.")
                return 2
            conn.execute(
                """INSERT INTO resident
                   (resident_id, apartment_id, display_name, active_from, is_active)
                   VALUES (?, ?, ?, ?, 1)""",
                (resident_id, apartment_id, display_name, datetime.now().date().isoformat()),
            )
        conn.commit()
    print(f"\nCreated apartment {apartment_name!r} ({apartment_id}) with {resident_count} resident(s).")
    print("Next: python electricity_entry_cli.py --db " + str(db) + " --apartment " + apartment_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

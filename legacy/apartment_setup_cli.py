"""First-run setup wizard for the Apartment Expense Manager database.

Usage:
  python apartment_setup_cli.py

Works against the configured PostgreSQL database (the ``AEM_DB_*`` settings in
the environment or ``.env.local``); there is no database file to name.
"""
from __future__ import annotations

import argparse
from datetime import datetime

from electricity_workflow import initialize_database

from aem.config import get_settings
from aem.errors import AemError, NotFoundError
from aem.persistence import Database, PersistenceError
from aem.persistence.postgres import PostgresDatabase
from aem.services import apartments, residents


def apartment_exists(db: Database, apartment_id: str) -> bool:
    try:
        apartments.read_apartment(db, apartment_id)
    except NotFoundError:
        return False
    return True


def resident_exists(db: Database, resident_id: str) -> bool:
    try:
        residents.read_resident(db, resident_id)
    except NotFoundError:
        return False
    return True


def run(db: Database) -> int:
    initialize_database(db)

    apartment_id = input("Apartment ID (short unique key, e.g. home): ").strip()
    apartment_name = input("Apartment name: ").strip()
    if not apartment_id or not apartment_name:
        print("Apartment ID and name are required.")
        return 2

    created_at = datetime.now().astimezone().isoformat(timespec="seconds")
    if apartment_exists(db, apartment_id):
        print(f"Apartment ID {apartment_id!r} already exists.")
        return 2
    # Nothing is written until every answer has been collected and checked, so
    # a refused answer below still leaves the database untouched ("rolled back").
    resident_count_raw = input("How many residents should be registered? ").strip()
    try:
        resident_count = int(resident_count_raw)
        if resident_count < 1:
            raise ValueError
    except ValueError:
        print("Enter a positive whole number of residents.")
        return 2
    people: list[tuple[str, str]] = []
    for index in range(resident_count):
        print(f"\nResident {index + 1}")
        resident_id = input("  Resident ID (unique key, e.g. alex): ").strip()
        display_name = input("  Display name: ").strip()
        if not resident_id or not display_name:
            print("Resident ID and display name are required. Setup rolled back.")
            return 2
        if resident_id in {rid for rid, _ in people} or resident_exists(db, resident_id):
            print(f"Resident ID {resident_id!r} already exists. Setup rolled back.")
            return 2
        people.append((resident_id, display_name))

    # Each service call is its own transaction: the apartment and the residents
    # are no longer saved as one all-or-nothing unit.
    saved: list[str] = []
    try:
        apartments.create_apartment(db, apartment_id=apartment_id, name=apartment_name,
                                    created_at=created_at)
        saved.append(f"apartment {apartment_id}")
        for resident_id, display_name in people:
            residents.create_resident(
                db, apartment_id=apartment_id, resident_id=resident_id,
                display_name=display_name,
                active_from=datetime.now().date().isoformat(), is_active=True)
            saved.append(f"resident {resident_id}")
    except (AemError, PersistenceError) as exc:
        print(f"\nSetup failed: {exc}")
        print("Already saved: " + (", ".join(saved) or "nothing"))
        return 1
    print(f"\nCreated apartment {apartment_name!r} ({apartment_id}) with {resident_count} resident(s).")
    print("Next: python electricity_entry_cli.py --apartment " + apartment_id)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create an apartment and register residents in the configured "
                    "PostgreSQL database (AEM_DB_* settings).")
    parser.parse_args()
    db = PostgresDatabase(get_settings().database)
    print(f"Database: {db.describe()}")
    try:
        return run(db)
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

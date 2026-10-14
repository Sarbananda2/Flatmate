#!/usr/bin/env python3
"""Put the flat's three rooms on file, with who lives in each.

    python scripts/seed_live_rooms.py              seed the configured database
    python scripts/seed_live_rooms.py --dry-run    say what it would do, change nothing

Room 1 is Rohan's, Room 2 Arnab's and Room 3 Sarbananda's. Residents are
matched by first name among the active residents. Each resident's deposit slot
is linked to their room, and if the lease's landlord is not yet on file as the
owner contact, they are added.

Safe to run again: anything already in place is left alone. It never moves a
resident out of a room or reassigns an occupied room; if the flat disagrees
with the plan, it stops and says so. Rooms are added with no attached washroom
or balcony -- set those on the Rooms tab.

Everything goes through the services, so it is validated and audited as if it
had been entered in the app.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aem.config import get_settings  # noqa: E402
from aem.errors import AemError  # noqa: E402
from aem.persistence import Database  # noqa: E402
from aem.persistence.postgres import PostgresDatabase  # noqa: E402
from aem.services import apartments, residents, rooms  # noqa: E402
from aem.services import property as household  # noqa: E402

PLAN = (("Room 1", "rohan"), ("Room 2", "arnab"), ("Room 3", "sarbananda"))
FALLBACK_SINCE = "2026-05-01"
ACTOR = "seed_live_rooms"


class PlanError(Exception):
    """The flat as it stands does not fit the plan."""


def _resident(active: list[dict], first_name: str) -> dict:
    matches = [row for row in active
               if row["display_name"].split()[0].lower() == first_name]
    if len(matches) != 1:
        found = ", ".join(row["display_name"] for row in matches) or "nobody"
        raise PlanError(f"Expected one active resident called {first_name.title()}; "
                        f"found {found}.")
    return matches[0]


def _since(resident: dict, lease: dict | None) -> str:
    """When they moved in: their own start date, else the lease's, else a default."""
    today = date.today().isoformat()
    for candidate in (resident.get("active_from"),
                      (lease or {}).get("lease_start_date"), FALLBACK_SINCE):
        if candidate and "2000-01-01" <= candidate <= today:
            return candidate
    return today


def seed(db: Database, apartment_id: str, *, dry_run: bool) -> list[str]:
    done: list[str] = []
    active = residents.read_residents(db, apartment_id, active_only=True)
    with db.read() as uow:
        lease = uow.rent.active_lease(apartment_id)
        slots = {row["assigned_resident_id"]: row
                 for row in uow.deposits.slots_for_apartment(apartment_id)
                 if row["assigned_resident_id"]}
    listing = rooms.list_rooms(db, apartment_id)["items"]
    by_number = {room["room_number"].strip().lower(): room for room in listing}

    # Check the whole plan before writing anything.
    plan = []
    for number, first_name in PLAN:
        resident = _resident(active, first_name)
        rid = resident["resident_id"]
        room = by_number.get(number.lower())
        elsewhere = next((r for r in listing if r["assigned_resident_id"] == rid
                          and r["room_number"].strip().lower() != number.lower()), None)
        if elsewhere:
            raise PlanError(f"{resident['display_name']} already occupies "
                            f"{elsewhere['room_number']}, not {number}.")
        if room and room["assigned_resident_id"] not in (None, rid):
            raise PlanError(f"{number} is occupied by {room['assigned_display_name']}.")
        plan.append((number, resident, room))

    for number, resident, room in plan:
        rid, name = resident["resident_id"], resident["display_name"]
        if room is None:
            if dry_run:
                done.append(f"would add {number} for {name}")
                continue
            room = rooms.create_room(db, apartment_id, {
                "room_number": number, "assigned_resident_id": rid,
                "effective_date": _since(resident, lease)}, actor=ACTOR)
            done.append(f"added {number} for {name}, since {room['assigned_since']}")
        elif room["assigned_resident_id"] is None:
            if dry_run:
                done.append(f"would move {name} into {number}")
                continue
            room = rooms.assign_resident(db, apartment_id, room["room_id"], rid,
                                         effective_date=_since(resident, lease), actor=ACTOR)
            done.append(f"moved {name} into {number}, since {room['assigned_since']}")
        else:
            done.append(f"{number} already has {name}")

        slot = slots.get(rid)
        if slot is None:
            done.append(f"  {name} holds no deposit slot, so none is linked")
        elif dry_run:
            if slot.get("room_id") != (room or {}).get("room_id"):
                done.append(f"  would link deposit slot {slot['slot_number']} "
                            f"({slot['slot_label']}) to {number}")
        elif rooms.link_slot_to_room(db, apartment_id, slot["slot_id"], room["room_id"],
                                     actor=ACTOR):
            done.append(f"  linked deposit slot {slot['slot_number']} "
                        f"({slot['slot_label']}) to {number}")
        else:
            done.append(f"  deposit slot {slot['slot_number']} already linked to {number}")

    with db.read() as uow:
        owner = uow.property.owner_contact(apartment_id)
    if lease is None:
        done.append("no lease on file, so no owner contact is added")
    elif owner is not None:
        done.append(f"owner contact already on file: {owner['name']}")
    elif dry_run:
        done.append(f"would add {lease['landlord_name']} as the owner contact")
    else:
        with db.transaction() as uow:
            contact = household.sync_owner_from_lease_in(uow, apartment_id, lease, actor=ACTOR)
        done.append(f"added {contact['name']} as the owner contact, from the lease")
    return done


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apartment", help="apartment_id; defaults to the only one")
    parser.add_argument("--dry-run", action="store_true", help="change nothing")
    args = parser.parse_args(argv)

    db = PostgresDatabase(get_settings().database)
    try:
        apartment_id = args.apartment or apartments.default_apartment_id(db)
        if not apartment_id:
            print("No apartment to seed. Pass --apartment.", file=sys.stderr)
            return 2
        print(f"Apartment: {apartment_id}{'  (dry run)' if args.dry_run else ''}")
        for line in seed(db, apartment_id, dry_run=args.dry_run):
            print(f"  {line}")
    except (PlanError, AemError) as exc:
        print(f"Stopped: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

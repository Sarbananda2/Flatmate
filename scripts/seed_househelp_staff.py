#!/usr/bin/env python3
"""Put sample house help staff on file, to try the House Help pages with.

    python scripts/seed_househelp_staff.py              seed the configured database
    python scripts/seed_househelp_staff.py --dry-run    say what it would do, change nothing

Two helpers: Sunita Sharma, who has done the housekeeping for everyone since
April 2026, and Ramesh Kumar, who joined on 15 November 2026 -- so November pays
him for its last 16 days -- and cooks for Sarbananda and Arnab and does
everyone's dishes. Each may take two days off a month before pay is deducted.
Each role is named the way the app's role builder names one: its service, its
area and its scope, as in "Cooking | Kitchen | Lunch & Dinner".

Sample data, so it is written only to a database on this machine: run it the
way scripts/dev.sh runs the app, so .env.local's production settings, if any,
are not used. Safe to run again: a helper already on file by name, or a role
they already do, is left alone. Everything goes through the services, so it is
validated and audited as if it had been entered in the app.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aem.config import get_settings  # noqa: E402
from aem.errors import AemError  # noqa: E402
from aem.persistence.postgres import PostgresDatabase  # noqa: E402
from aem.services import househelp, residents  # noqa: E402

APARTMENT = "supertech-capetown-1805"
ACTOR = "seed_househelp_staff"

STAFF = (
    {"name": "Sunita Sharma", "phone": "9876543210", "upi_id": "sunita.sharma@okaxis",
     "allowed_leaves_per_month": 2, "joined_date": "2026-04-01",
     "roles": ({"role_name": "Housekeeping | Whole House | General Maintenance",
                "monthly_salary_paise": 350_000, "split_method": "equal"},)},
    {"name": "Ramesh Kumar", "phone": "9812345678", "upi_id": "ramesh.cook@upi",
     "allowed_leaves_per_month": 2, "joined_date": "2026-11-15",
     "roles": ({"role_name": "Cooking | Kitchen | Lunch & Dinner", "monthly_salary_paise": 500_000,
                "split_method": "opt_in", "participant_ids": ["sarbananda-taw", "arnab-deka"]},
               {"role_name": "Utensils | Kitchen | Twice Daily", "monthly_salary_paise": 150_000,
                "split_method": "equal"})},
)


class PlanError(Exception):
    """The flat as it stands does not fit the plan."""


def _rupees(paise: int) -> str:
    return f"₹{paise / 100:,.0f}"


def seed(db: PostgresDatabase, apartment_id: str, *, dry_run: bool) -> list[str]:
    living = {row["resident_id"] for row in residents.read_residents(
        db, apartment_id, active_only=True)}
    for helper in STAFF:
        for role in helper["roles"]:
            missing = sorted(set(role.get("participant_ids", ())) - living)
            if missing:
                raise PlanError(f"{role['role_name']} is shared by {', '.join(missing)}, "
                                "who do not live in the flat.")

    done: list[str] = []
    on_file = {worker["name"].strip().lower(): worker
               for worker in househelp.list_workers(db, apartment_id)}
    for helper in STAFF:
        details = {key: value for key, value in helper.items() if key != "roles"}
        worker = on_file.get(helper["name"].lower())
        if worker is not None:
            done.append(f"{helper['name']} already on file")
        elif dry_run:
            done.append(f"would add {helper['name']}, joined {helper['joined_date']}, "
                        f"{helper['allowed_leaves_per_month']} days off a month")
        else:
            worker = househelp.create_worker(db, apartment_id, details, actor=ACTOR)
            done.append(f"added {worker['name']}, joined {worker['joined_date']}, "
                        f"{worker['allowed_leaves_per_month']} days off a month")
        theirs = {role["role_name"].strip().lower()
                  for role in (worker or {}).get("roles", [])}
        for role in helper["roles"]:
            what = (f"{role['role_name']} at {_rupees(role['monthly_salary_paise'])} a month, "
                    + ("shared by " + " and ".join(role["participant_ids"])
                       if role["split_method"] == "opt_in" else "shared by everyone"))
            if role["role_name"].lower() in theirs:
                done.append(f"  {role['role_name']} already theirs")
            elif dry_run:
                done.append(f"  would add {what}")
            else:
                househelp.create_role(db, apartment_id,
                                      {**role, "worker_id": worker["worker_id"]}, actor=ACTOR)
                done.append(f"  added {what}")
    return done


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apartment", default=APARTMENT,
                        help=f"apartment_id (default: {APARTMENT})")
    parser.add_argument("--dry-run", action="store_true", help="change nothing")
    args = parser.parse_args(argv)

    config = get_settings().database
    if not config.is_local:
        print(f"Stopped: {config.describe()} is not on this machine, and sample staff are "
              "for a local database only.", file=sys.stderr)
        return 2
    db = PostgresDatabase(config)
    try:
        print(f"Apartment: {args.apartment} on {config.describe()}"
              f"{'  (dry run)' if args.dry_run else ''}")
        for line in seed(db, args.apartment, dry_run=args.dry_run):
            print(f"  {line}")
    except (PlanError, AemError) as exc:
        print(f"Stopped: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

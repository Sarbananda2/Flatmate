#!/usr/bin/env python3
"""Database and server management for the Apartment Expense Manager.

    python scripts/manage.py init                 create or upgrade the database
    python scripts/manage.py status               schema version and record counts
    python scripts/manage.py serve                run the API (and the built UI)
    python scripts/manage.py backup               copy the database to ./backups
    python scripts/manage.py restore <file>       restore a backup
    python scripts/manage.py check                run the accounting validation
    python scripts/manage.py close-expired        close statements past their window
    python scripts/manage.py demo                 load a small sample household

Every command honours ``AEM_DB_PATH``; the default is ``./data/apartment.sqlite``.
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aem import __version__  # noqa: E402
from aem.config import get_settings  # noqa: E402
from aem.db import connect  # noqa: E402
from aem.errors import AemError  # noqa: E402
from aem.migrations import current_version, discover, migrate  # noqa: E402

COUNTED = ("apartment", "resident", "expense_definition", "expense_instance", "allocation",
           "payment", "payment_application", "ledger_adjustment", "monthly_statement",
           "statement_revision", "smart_plug_reading", "electricity_bill", "audit_log")


def cmd_init(args: argparse.Namespace) -> int:
    settings = get_settings()
    path = Path(args.db or settings.database_path)
    print(f"Database: {path}")
    for line in migrate(path):
        print(f"  {line}")
    print(f"Schema version: {current_version(path)}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    settings = get_settings()
    path = Path(args.db or settings.database_path)
    print(f"Apartment Expense Manager {__version__}")
    print(f"Database: {path}")
    if not path.exists():
        print("  The database does not exist yet. Run: python scripts/manage.py init")
        return 1
    latest = max((migration.version for migration in discover()), default=0)
    version = current_version(path)
    print(f"  Schema version: {version} (latest {latest})"
          f"{'' if version == latest else '  -- run init to upgrade'}")
    size_kb = path.stat().st_size / 1024
    print(f"  File size: {size_kb:,.1f} KiB")
    with connect(path, read_only=True) as conn:
        print(f"  Integrity: {conn.execute('PRAGMA quick_check(1)').fetchone()[0]}")
        for table in COUNTED:
            try:
                count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except sqlite3.Error:
                continue
            print(f"  {table:24} {count:>8}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    settings = get_settings()
    host = args.host or settings.host
    port = args.port or settings.port
    if host not in {"127.0.0.1", "localhost", "::1"}:
        print(f"WARNING: binding {host} exposes the household's accounts to the network.")
        print("         This application has no authentication. Use 127.0.0.1 instead.")
    print(f"Database: {args.db or settings.database_path}")
    print(f"Serving on http://{host}:{port}   (API docs at /docs)")
    if not (ROOT / "frontend" / "dist").is_dir():
        print("The frontend is not built. Run: cd frontend && npm install && npm run build")
        print("Or run the Vite dev server: cd frontend && npm run dev")
    uvicorn.run("aem.main:app", host=host, port=port, reload=args.reload,
                log_level=settings.log_level.lower())
    return 0


def cmd_backup(args: argparse.Namespace) -> int:
    """Back up with SQLite's own backup API, which is safe while the app runs."""
    settings = get_settings()
    source = Path(args.db or settings.database_path)
    if not source.exists():
        print(f"No database at {source}")
        return 1
    directory = Path(args.out or ROOT / "backups")
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S")
    target = directory / f"{source.stem}-{stamp}.sqlite"

    with sqlite3.connect(str(source)) as origin, sqlite3.connect(str(target)) as copy:
        origin.backup(copy)
    with sqlite3.connect(str(target)) as verify:
        check = verify.execute("PRAGMA integrity_check").fetchone()[0]
    if check != "ok":
        print(f"Backup wrote {target} but its integrity check said: {check}")
        return 1
    print(f"Backup: {target}  ({target.stat().st_size / 1024:,.1f} KiB, integrity ok)")
    print("Keep it somewhere other than this directory as well.")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    settings = get_settings()
    target = Path(args.db or settings.database_path)
    source = Path(args.backup)
    if not source.exists():
        print(f"No backup at {source}")
        return 1
    with sqlite3.connect(str(source)) as verify:
        check = verify.execute("PRAGMA integrity_check").fetchone()[0]
    if check != "ok":
        print(f"That backup fails its integrity check ({check}); refusing to restore it.")
        return 1
    if target.exists() and not args.force:
        print(f"{target} already exists. Re-run with --force to replace it.")
        print("The existing file will be set aside first, not deleted.")
        return 1
    if target.exists():
        stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S")
        aside = target.with_name(f"{target.stem}-replaced-{stamp}{target.suffix}")
        shutil.move(str(target), str(aside))
        print(f"Existing database moved aside to {aside}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    print(f"Restored {source} to {target}")
    for line in migrate(target):
        print(f"  {line}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    from aem.services import apartments, reconciliation, validation

    settings = get_settings()
    path = Path(args.db or settings.database_path)
    apartment_id = args.apartment or apartments.default_apartment_id(path)
    if not apartment_id:
        print("No apartment has been set up yet.")
        return 1
    report = validation.validate(path, apartment_id)
    print(f"Apartment: {apartment_id}")
    print(f"  Errors:   {report['error_count']}")
    print(f"  Warnings: {report['warning_count']}")
    for issue in report["issues"]:
        print(f"  [{issue['severity']:>7}] {issue['code']}: {issue['message']}")
        if issue["suggested_action"]:
            print(f"            -> {issue['suggested_action']}")
    try:
        settlement = reconciliation.reconcile(path, apartment_id=apartment_id)
    except AemError as error:
        print(f"  Reconciliation refused: {error}")
        return 1
    print(f"  Accounts sum to: {settlement['net_positions_sum_paise']} paise")
    for account in settlement["accounts"]:
        print(f"    {account['display_name']:<22} {account['net_position_paise']:>14,} paise")
    return 0 if report["error_count"] == 0 else 1


def cmd_close_expired(args: argparse.Namespace) -> int:
    from aem.services import statements

    settings = get_settings()
    path = Path(args.db or settings.database_path)
    closed = statements.close_expired_statements(path, actor="manage.py")
    print(f"Closed {len(closed)} statement(s): {', '.join(closed) or 'none'}")
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    """Load a small sample household so the UI has something to show."""
    from aem.services import (apartments, definitions, electricity, expenses, payments,
                              recurring, residents, statements)

    settings = get_settings()
    path = Path(args.db or settings.database_path)
    migrate(path)
    apartment_id = args.apartment or "home"
    try:
        apartments.create_apartment(path, apartment_id=apartment_id, name="Flat 402, Lakeview")
    except AemError:
        print(f"Apartment {apartment_id!r} already exists; leaving it as it is.")
        return 1
    for resident_id, name in (("alice", "Alice"), ("bob", "Bob"), ("chris", "Chris")):
        residents.create_resident(path, apartment_id=apartment_id, resident_id=resident_id,
                                  display_name=name, active_from="2026-08-01")
    definitions.seed_starter_definitions(path, apartment_id)
    definitions.update_expense_definition(path, "rent", default_amount_paise=4_500_000,
                                          day_of_month=1, default_provider_name="Landlord")
    definitions.update_expense_definition(path, "broadband", default_amount_paise=120_000,
                                          day_of_month=5)
    definitions.update_expense_definition(path, "maintenance", default_amount_paise=250_000,
                                          day_of_month=7)

    september = statements.create_monthly_statement(
        path, apartment_id=apartment_id, settlement_month="2026-09")
    recurring.generate(path, apartment_id=apartment_id, settlement_period="2026-09",
                       statement_id=september,
                       lines=[{"expense_definition_id": "rent"},
                              {"expense_definition_id": "broadband"},
                              {"expense_definition_id": "maintenance"}])
    expenses.create_expense(
        path, apartment_id=apartment_id, expense_id="groceries-2026-09",
        description="Groceries", amount_paise=333_401, expense_date="2026-09-03",
        expense_definition_id="groceries", allocation_method="equal",
        payer_resident_id="alice", statement_id=september)
    electricity.create_electricity_expense(
        path, apartment_id=apartment_id, expense_id="electricity-2026-09",
        description="August electricity bill", expense_date="2026-09-08",
        expense_definition_id="electricity", total_amount_paise=735_000,
        payer_resident_id="bob", statement_id=september, bill_number="SEB/26/09/1188",
        smart_plug_readings=[
            {"resident_id": "alice", "kwh": "42.500", "device_label": "AC"},
            {"resident_id": "bob", "kwh": "31.000", "device_label": "AC"},
            {"resident_id": "chris", "kwh": "18.250", "device_label": "AC"}],
        components=[
            {"id": "energy", "name": "Energy charges", "amount_paise": 650_000,
             "allocation_method": "usage"},
            {"id": "fixed", "name": "Fixed and common charges", "amount_paise": 85_000,
             "allocation_method": "equal"}])
    payments.record_payment(
        path, apartment_id=apartment_id, payment_id="advance-electricity-2026-09",
        payer_resident_id="bob", amount_paise=735_000, payment_date="2026-09-08",
        payment_kind="provider_advance", payee_or_provider="State Electricity Board",
        expense_id="electricity-2026-09")
    payments.record_payment(
        path, apartment_id=apartment_id, payment_id="pay-chris-2026-09",
        payer_resident_id="chris", amount_paise=2_500_000, payment_date="2026-09-20")
    payments.record_payment(
        path, apartment_id=apartment_id, payment_id="pay-bob-2026-09",
        payer_resident_id="bob", amount_paise=50_000, payment_date="2026-09-22")

    october = statements.create_monthly_statement(
        path, apartment_id=apartment_id, settlement_month="2026-10")
    recurring.generate(path, apartment_id=apartment_id, settlement_period="2026-10",
                       statement_id=october,
                       lines=[{"expense_definition_id": "rent"},
                              {"expense_definition_id": "broadband"}])
    print(f"Loaded a sample household into {path}.")
    print("The September statement is a draft you can review and finalize;")
    print("October has its recurring expenses generated.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="manage.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", help="SQLite database path (default: AEM_DB_PATH or "
                                     "./data/apartment.sqlite)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create or upgrade the database").set_defaults(func=cmd_init)
    sub.add_parser("status", help="schema version and record counts").set_defaults(func=cmd_status)

    serve = sub.add_parser("serve", help="run the API and the built UI")
    serve.add_argument("--host", help="default 127.0.0.1; anything else is a network exposure")
    serve.add_argument("--port", type=int)
    serve.add_argument("--reload", action="store_true", help="reload on code changes")
    serve.set_defaults(func=cmd_serve)

    backup = sub.add_parser("backup", help="copy the database safely")
    backup.add_argument("--out", help="destination directory (default: ./backups)")
    backup.set_defaults(func=cmd_backup)

    restore = sub.add_parser("restore", help="restore a backup")
    restore.add_argument("backup", help="the backup file to restore")
    restore.add_argument("--force", action="store_true",
                         help="replace an existing database (it is moved aside, not deleted)")
    restore.set_defaults(func=cmd_restore)

    check = sub.add_parser("check", help="run the accounting validation and reconciliation")
    check.add_argument("--apartment", help="apartment ID (default: the configured one)")
    check.set_defaults(func=cmd_check)

    sub.add_parser("close-expired", help="close statements past their correction window"
                   ).set_defaults(func=cmd_close_expired)

    demo = sub.add_parser("demo", help="load a small sample household")
    demo.add_argument("--apartment", help="apartment ID to create (default: home)")
    demo.set_defaults(func=cmd_demo)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except AemError as error:
        print(f"Error [{error.code}]: {error.message}", file=sys.stderr)
        if error.details:
            print(f"  {error.details}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

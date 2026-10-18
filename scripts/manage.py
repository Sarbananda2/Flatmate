#!/usr/bin/env python3
"""Database and server management for the Apartment Expense Manager.

    python scripts/manage.py init                 create or upgrade the schema  [migration role]
    python scripts/manage.py status               connection, schema version, record counts
    python scripts/manage.py serve                run the API (and the built UI)
    python scripts/manage.py backup               dump the database to ./backups
    python scripts/manage.py restore <file>       load a dump into an empty database  [migration role]
    python scripts/manage.py import-sqlite        copy an old SQLite database in, once  [migration role]
    python scripts/manage.py check                run the accounting validation
    python scripts/manage.py close-expired        close statements past their window
    python scripts/manage.py demo                 load a small sample household

Every command works against the PostgreSQL database named by the ``AEM_DB_*``
settings (or ``AEM_DATABASE_URL``); see ``.env.example``. They connect as the
runtime role, which cannot change the schema -- except the three marked
"migration role", which need ``AEM_DB_MIGRATION_PASSWORD`` as well. None of them starts
or stops a database server: locally, ``./scripts/dev.sh`` or
``./scripts/local_postgres.sh start`` does that.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aem import __version__  # noqa: E402
from aem.config import get_settings  # noqa: E402
from aem.errors import AemError  # noqa: E402
from aem.persistence import PersistenceError  # noqa: E402

DEFAULT_SQLITE_SOURCE = ROOT / "data" / "apartment.sqlite"


def _database():
    """The database as the runtime role: what every ordinary command uses."""
    from aem.persistence.postgres import PostgresDatabase

    return PostgresDatabase(get_settings().database)


def _migration_settings():
    """The schema owner's connection settings, or exit with how to provide them."""
    config = get_settings().migration_database
    if config is None:
        raise PersistenceError(
            "This command changes the schema, so it runs as the migration role, and that "
            "role's password is not configured. Set AEM_DB_MIGRATION_PASSWORD (and "
            "AEM_DB_MIGRATION_USER if it is not 'aem_owner') in the environment or "
            ".env.local.", code="migration_role_not_configured")
    return config


def _migrating_database():
    """The database with the migration role attached, for init and restore only."""
    from aem.persistence.postgres import PostgresDatabase

    settings = get_settings()
    return PostgresDatabase(settings.database, migration_config=_migration_settings())


def cmd_init(args: argparse.Namespace) -> int:
    db = _migrating_database()
    print(f"Database: {db.describe()}  (migrating as {_migration_settings().user})")
    for line in db.migrate():
        print(f"  {line}")
    print(f"Schema version: {db.schema_version()}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    db = _database()
    report = db.status()
    print(f"Apartment Expense Manager {__version__}")
    print(f"Database: {report['target']}")
    if not report["reachable"]:
        print(f"  Not reachable: {report['error']}")
        print("  Locally, start it with ./scripts/dev.sh or ./scripts/local_postgres.sh start")
        return 1
    version, latest = report["schema_version"], report["latest_schema_version"]
    print(f"  Server: PostgreSQL {report['server_version']} (collation {report['collation']})")
    if report["collation"] not in {"C", "POSIX"}:
        print("  NOTE: this database does not use the C collation, so lists sorted by name")
        print("        may order differently from how the application was written to expect.")
    print(f"  Schema version: {version} (latest {latest})"
          f"{'' if version == latest else '  -- run init to upgrade'}")
    print("  Integrity: " + ("ok" if not report["invalid_constraints"]
                             else f"{report['invalid_constraints']} constraint(s) not validated"))
    print(f"  Connected as: {report['role']}"
          + ("  -- WARNING: a privileged role; the app should use an ordinary one"
             if report["role_is_privileged"] else "  (ordinary role)"))
    print("  Trigger guard: " + ("installed" if report["trigger_guard_installed"] else
                                 "not installed (see scripts/sql/trigger_guard.sql)"))
    if report["disabled_triggers"]:
        print(f"  WARNING: {report['disabled_triggers']} integrity trigger(s) are disabled.")
    if report["role_owns_tables"] or report["role_can_change_schema"]:
        print("  WARNING: this role owns tables or can change the schema. The app should "
              "serve as a role that cannot.")
    else:
        print("  Schema rights: none (owns no tables; cannot create, alter, or truncate)")
    for table, count in db.table_counts().items():
        print(f"  {table:24} {count:>8}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    settings = get_settings()
    host = args.host or settings.host
    port = args.port or settings.port
    if host not in {"127.0.0.1", "localhost", "::1"}:
        print(f"WARNING: binding {host} makes the app reachable from the network. Every")
        print("         route needs a signed-in user, but put HTTPS in front of it so")
        print("         passwords and session cookies are not sent in the clear.")
    print(f"Database: {settings.database.describe()}")
    print(f"Serving on http://{host}:{port}   (API docs at /docs)")
    if not (ROOT / "frontend" / "dist").is_dir():
        print("The frontend is not built. Run: cd frontend && npm install && npm run build")
        print("Or run the Vite dev server: cd frontend && npm run dev")
    uvicorn.run("aem.main:app", host=host, port=port, reload=args.reload,
                log_level=settings.log_level.lower())
    return 0


def _client_tool(name: str) -> str | None:
    """A PostgreSQL client program: from ``AEM_PG_BIN_DIR`` if set, else ``PATH``."""
    # Loading the settings is what reads .env.local, where AEM_PG_BIN_DIR
    # usually lives. Without it this fell back to whatever PATH offered, which
    # may be another PostgreSQL version's pg_dump.
    get_settings()
    bin_dir = os.environ.get("AEM_PG_BIN_DIR")
    if bin_dir:
        candidate = Path(bin_dir).expanduser() / name
        return str(candidate) if candidate.is_file() else None
    return shutil.which(name)


def _client_environment(config=None) -> dict[str, str]:
    """Connection settings for pg_dump / pg_restore, passed through the
    environment so the password never appears on a command line. The runtime
    role's unless another role's settings are given."""
    from psycopg.conninfo import conninfo_to_dict

    config = config or get_settings().database
    params = conninfo_to_dict(config.url or "", **{
        key: value for key, value in config.connection_kwargs().items()
        if key not in {"application_name", "conninfo"}})
    names = {"host": "PGHOST", "port": "PGPORT", "dbname": "PGDATABASE", "user": "PGUSER",
             "password": "PGPASSWORD", "sslmode": "PGSSLMODE", "sslrootcert": "PGSSLROOTCERT",
             "options": "PGOPTIONS", "connect_timeout": "PGCONNECT_TIMEOUT"}
    env = dict(os.environ)
    for key, variable in names.items():
        if params.get(key) is not None:
            env[variable] = str(params[key])
    return env


def _missing_tool(name: str) -> int:
    print(f"{name} was not found. Set AEM_PG_BIN_DIR to the PostgreSQL 18 bin directory, "
          "or put it on PATH.")
    return 1


def cmd_backup(args: argparse.Namespace) -> int:
    """Dump with pg_dump, which is safe while the app runs."""
    pg_dump = _client_tool("pg_dump")
    if not pg_dump:
        return _missing_tool("pg_dump")
    db = _database()
    if not db.status()["reachable"]:
        print(f"Cannot reach {db.describe()}; nothing was backed up.")
        return 1
    directory = Path(args.out or ROOT / "backups")
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S")
    target = directory / f"apartment-{stamp}.dump"
    result = subprocess.run(
        [pg_dump, "--format=custom", "--no-owner", "--no-privileges", f"--file={target}"],
        env=_client_environment())
    if result.returncode != 0:
        target.unlink(missing_ok=True)
        print("pg_dump failed; no backup was written.")
        return 1
    pg_restore = _client_tool("pg_restore")
    if pg_restore and subprocess.run([pg_restore, "--list", str(target)],
                                     stdout=subprocess.DEVNULL).returncode != 0:
        print(f"Backup wrote {target} but pg_restore could not read it back.")
        return 1
    print(f"Backup: {target}  ({target.stat().st_size / 1024:,.1f} KiB)")
    print("Keep it somewhere other than this directory as well.")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    """Load a dump into an empty database, as the migration role, which becomes
    the owner of the restored tables. Never replaces existing data."""
    pg_restore = _client_tool("pg_restore")
    if not pg_restore:
        return _missing_tool("pg_restore")
    source = Path(args.backup)
    if not source.exists():
        print(f"No backup at {source}")
        return 1
    db = _migrating_database()
    report = db.status()
    if not report["reachable"]:
        print(f"Cannot reach {db.describe()}: {report['error']}")
        return 1
    if report["schema_version"] or db.table_counts():
        print(f"{db.describe()} already has the application's tables in it.")
        print("Restore never overwrites a database. Create a new, empty database, point")
        print("AEM_DB_NAME (or AEM_DATABASE_URL) at it, and run restore again.")
        return 1
    env = _client_environment(_migration_settings())
    if not env.get("PGDATABASE"):
        print("The connection settings do not name a database to restore into.")
        return 1
    # Restore the application's own objects only. The trigger guard (an event
    # trigger and its schema) belongs to the database's administrator, who
    # installs it; an ordinary role can neither create nor needs to restore it.
    listing = subprocess.run([pg_restore, "--list", str(source)],
                             capture_output=True, text=True)
    if listing.returncode != 0:
        print("pg_restore could not read that file.")
        return 1
    wanted = [line for line in listing.stdout.splitlines()
              if " EVENT TRIGGER " not in line and " aem_guard" not in line]
    with tempfile.NamedTemporaryFile("w", suffix=".list", delete=False) as handle:
        handle.write("\n".join(wanted) + "\n")
    try:
        result = subprocess.run(
            [pg_restore, "--no-owner", "--no-privileges", "--single-transaction",
             "--exit-on-error", f"--use-list={handle.name}",
             f"--dbname={env['PGDATABASE']}", str(source)],
            env=env)
    finally:
        Path(handle.name).unlink(missing_ok=True)
    if result.returncode != 0:
        print("pg_restore failed and was rolled back; the database is still empty.")
        return 1
    print(f"Restored {source} into {db.describe()}")
    # Brings the schema up to date and grants the runtime role its access.
    for line in db.migrate():
        print(f"  {line}")
    return 0


def cmd_import_sqlite(args: argparse.Namespace) -> int:
    """Copy an existing SQLite database into the (empty) PostgreSQL database."""
    from aem.persistence.sqlite_import.importer import import_sqlite

    owner = _migration_settings()
    source = Path(args.sqlite or os.environ.get("AEM_DB_PATH") or DEFAULT_SQLITE_SOURCE)
    print(f"From (read-only): {source}")
    print(f"Into:             {owner.describe()}  (as the migration role, {owner.user})")
    report = import_sqlite(source, owner, dry_run=args.dry_run)
    for line in report.log:
        print(f"  {line}")
    print(f"  SQLite schema version {report.source_schema_version}; "
          f"file SHA-256 {report.source_sha256[:16]}... unchanged")
    print(f"  {'table':24} {'sqlite':>8} {'postgres':>9}  columns compared")
    for result in report.tables:
        print(f"  {result.table:24} {result.source_rows:>8} {result.target_rows:>9}  "
              f"{result.columns_compared}")
    if report.committed:
        print(f"Imported {report.total_rows} rows. Every row was read back and compared with "
              "SQLite column by column, and every foreign key was validated, before committing.")
        print(f"The SQLite file was not modified. Keep it until you are satisfied; then run:")
        print("  python scripts/manage.py check")
    else:
        print(f"Dry run: {report.total_rows} rows would be imported and every check passed. "
              "The data was rolled back.")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    from aem.services import apartments, reconciliation, validation

    db = _database()
    apartment_id = args.apartment or apartments.default_apartment_id(db)
    if not apartment_id:
        print("No apartment has been set up yet.")
        return 1
    report = validation.validate(db, apartment_id)
    print(f"Apartment: {apartment_id}")
    print(f"  Errors:   {report['error_count']}")
    print(f"  Warnings: {report['warning_count']}")
    for issue in report["issues"]:
        print(f"  [{issue['severity']:>7}] {issue['code']}: {issue['message']}")
        if issue["suggested_action"]:
            print(f"            -> {issue['suggested_action']}")
    try:
        settlement = reconciliation.reconcile(db, apartment_id=apartment_id)
    except AemError as error:
        print(f"  Reconciliation refused: {error}")
        return 1
    print(f"  Accounts sum to: {settlement['net_positions_sum_paise']} paise")
    for account in settlement["accounts"]:
        print(f"    {account['display_name']:<22} {account['net_position_paise']:>14,} paise")
    return 0 if report["error_count"] == 0 else 1


def cmd_close_expired(args: argparse.Namespace) -> int:
    from aem.services import statements

    db = _database()
    closed = statements.close_expired_statements(db, actor="manage.py")
    print(f"Closed {len(closed)} statement(s): {', '.join(closed) or 'none'}")
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    """Load a small sample household so the UI has something to show."""
    from aem.services import (apartments, definitions, electricity, expenses, payments,
                              recurring, residents, statements)

    db = _database()
    db.require_current_schema()
    apartment_id = args.apartment or "home"
    try:
        apartments.create_apartment(db, apartment_id=apartment_id, name="Flat 402, Lakeview")
    except AemError:
        print(f"Apartment {apartment_id!r} already exists; leaving it as it is.")
        return 1
    for resident_id, name in (("alice", "Alice"), ("bob", "Bob"), ("chris", "Chris")):
        residents.create_resident(db, apartment_id=apartment_id, resident_id=resident_id,
                                  display_name=name, active_from="2026-08-01")
    definitions.seed_starter_definitions(db, apartment_id)
    definitions.update_expense_definition(db, "rent", default_amount_paise=4_500_000,
                                          day_of_month=1, default_provider_name="Landlord")
    definitions.update_expense_definition(db, "broadband", default_amount_paise=120_000,
                                          day_of_month=5)
    definitions.update_expense_definition(db, "maintenance", default_amount_paise=250_000,
                                          day_of_month=7)

    september = statements.create_monthly_statement(
        db, apartment_id=apartment_id, settlement_month="2026-09")
    recurring.generate(db, apartment_id=apartment_id, settlement_period="2026-09",
                       statement_id=september,
                       lines=[{"expense_definition_id": "rent"},
                              {"expense_definition_id": "broadband"},
                              {"expense_definition_id": "maintenance"}])
    expenses.create_expense(
        db, apartment_id=apartment_id, expense_id="groceries-2026-09",
        description="Groceries", amount_paise=333_401, expense_date="2026-09-03",
        expense_definition_id="groceries", allocation_method="equal",
        payer_resident_id="alice", statement_id=september)
    electricity.create_electricity_expense(
        db, apartment_id=apartment_id, expense_id="electricity-2026-09",
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
        db, apartment_id=apartment_id, payment_id="advance-electricity-2026-09",
        payer_resident_id="bob", amount_paise=735_000, payment_date="2026-09-08",
        payment_kind="provider_advance", payee_or_provider="State Electricity Board",
        expense_id="electricity-2026-09")
    payments.record_payment(
        db, apartment_id=apartment_id, payment_id="pay-chris-2026-09",
        payer_resident_id="chris", amount_paise=2_500_000, payment_date="2026-09-20")
    payments.record_payment(
        db, apartment_id=apartment_id, payment_id="pay-bob-2026-09",
        payer_resident_id="bob", amount_paise=50_000, payment_date="2026-09-22")

    october = statements.create_monthly_statement(
        db, apartment_id=apartment_id, settlement_month="2026-10")
    recurring.generate(db, apartment_id=apartment_id, settlement_period="2026-10",
                       statement_id=october,
                       lines=[{"expense_definition_id": "rent"},
                              {"expense_definition_id": "broadband"}])
    print(f"Loaded a sample household into {db.describe()}.")
    print("The September statement is a draft you can review and finalize;")
    print("October has its recurring expenses generated.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="manage.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create or upgrade the schema").set_defaults(func=cmd_init)
    sub.add_parser("status", help="schema version and record counts").set_defaults(func=cmd_status)

    serve = sub.add_parser("serve", help="run the API and the built UI")
    serve.add_argument("--host", help="default 127.0.0.1; anything else is a network exposure")
    serve.add_argument("--port", type=int)
    serve.add_argument("--reload", action="store_true", help="reload on code changes")
    serve.set_defaults(func=cmd_serve)

    backup = sub.add_parser("backup", help="dump the database with pg_dump")
    backup.add_argument("--out", help="destination directory (default: ./backups)")
    backup.set_defaults(func=cmd_backup)

    restore = sub.add_parser("restore", help="load a dump into an empty database")
    restore.add_argument("backup", help="the dump file to restore")
    restore.set_defaults(func=cmd_restore)

    importer = sub.add_parser(
        "import-sqlite", help="copy an old SQLite database into an empty PostgreSQL database")
    importer.add_argument("--sqlite", help="the SQLite file (default: AEM_DB_PATH or "
                                           "./data/apartment.sqlite); it is only read")
    importer.add_argument("--dry-run", action="store_true",
                          help="do the whole import and its checks, then roll it back "
                               "(creates the empty schema if the database has none)")
    importer.set_defaults(func=cmd_import_sqlite)

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
    except (AemError, PersistenceError) as error:
        print(f"Error [{error.code}]: {error}", file=sys.stderr)
        if error.details:
            print(f"  {error.details}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

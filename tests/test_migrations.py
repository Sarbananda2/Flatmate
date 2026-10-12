"""Migration safety.

The point of these tests is that an existing prototype database is *upgraded*,
not replaced: every resident, expense, allocation, payment, and statement
survives, the important totals are unchanged, and the new columns are
backfilled to mean what they meant before.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from aem.db import connect
from aem.migrations import MigrationError, current_version, discover, migrate, split_statements
from aem.services import expenses, payments, reconciliation, statements
from tests.conftest import APARTMENT

PROTOTYPE_SCHEMA = Path(__file__).resolve().parent.parent / "schema.sql"

COUNTED_TABLES = ("apartment", "resident", "expense_definition", "monthly_statement",
                  "expense_instance", "expense_component", "allocation",
                  "smart_plug_reading", "payment", "payment_application",
                  "ledger_adjustment", "statement_revision")


def build_prototype_database(path: Path) -> dict[str, int]:
    """Create a database exactly as the original prototype did, with data in it."""
    conn = sqlite3.connect(path)
    conn.executescript(PROTOTYPE_SCHEMA.read_text(encoding="utf-8"))
    conn.execute("INSERT INTO apartment VALUES (?,?,?,?,?)",
                 (APARTMENT, "Flat 402", "INR", "Asia/Kolkata", "2026-08-01T10:00:00+05:30"))
    conn.executemany(
        "INSERT INTO resident (resident_id, apartment_id, display_name, is_active) "
        "VALUES (?,?,?,1)",
        [("alice", APARTMENT, "Alice"), ("bob", APARTMENT, "Bob"),
         ("chris", APARTMENT, "Chris")])
    conn.execute(
        """INSERT INTO expense_definition
           (expense_definition_id, apartment_id, name, category, period_rule,
            default_allocation_method, is_recurring, is_active)
           VALUES ('rent',?,'Rent','Housing','current_month','equal',1,1)""", (APARTMENT,))
    conn.execute(
        """INSERT INTO monthly_statement
           (statement_id, apartment_id, settlement_month, status, created_at)
           VALUES ('stmt-aug',?,'2026-08','draft','2026-08-01T10:00:00+05:30')""",
        (APARTMENT,))
    conn.execute(
        """INSERT INTO expense_instance
           (expense_id, expense_definition_id, statement_id, description, amount_paise,
            expense_date, expense_period, payer_resident_id, provider_name, is_settled, created_at)
           VALUES ('rent-aug','rent','stmt-aug','Rent',4500000,'2026-08-01','2026-08',
                   'alice','Landlord',0,'2026-08-01T10:05:00+05:30')""")
    conn.execute(
        "INSERT INTO expense_component VALUES ('c-rent','rent-aug','Rent',4500000,'equal',NULL)")
    conn.executemany(
        """INSERT INTO allocation
           (allocation_id, expense_id, component_id, resident_id, amount_paise)
           VALUES (?,?,?,?,?)""",
        [("a-alice", "rent-aug", "c-rent", "alice", 1_500_000),
         ("a-bob", "rent-aug", "c-rent", "bob", 1_500_000),
         ("a-chris", "rent-aug", "c-rent", "chris", 1_500_000)])
    # An electricity expense with readings, the way the prototype stored them.
    conn.execute(
        """INSERT INTO expense_instance
           (expense_id, statement_id, description, amount_paise, expense_date,
            expense_period, payer_resident_id, provider_name, is_settled, created_at)
           VALUES ('elec-jul','stmt-aug','July electricity',600000,'2026-08-06','2026-08',
                   'bob','SEB',0,'2026-08-06T09:00:00+05:30')""")
    conn.execute(
        "INSERT INTO expense_component VALUES ('c-elec','elec-jul','July electricity',"
        "600000,'measured',NULL)")
    conn.executemany(
        """INSERT INTO allocation
           (allocation_id, expense_id, component_id, resident_id, amount_paise)
           VALUES (?,?,?,?,?)""",
        [("e-alice", "elec-jul", "c-elec", "alice", 300_000),
         ("e-bob", "elec-jul", "c-elec", "bob", 200_000),
         ("e-chris", "elec-jul", "c-elec", "chris", 100_000)])
    conn.executemany(
        """INSERT INTO smart_plug_reading
           (reading_id, resident_id, expense_id, device_label, period_start, period_end,
            energy_wh)
           VALUES (?,?,?,?,?,?,?)""",
        [("r-alice", "alice", "elec-jul", "AC", "2026-07-01", "2026-07-31", 60_000),
         ("r-bob", "bob", "elec-jul", "AC", "2026-07-01", "2026-07-31", 40_000),
         ("r-chris", "chris", "elec-jul", "AC", "2026-07-01", "2026-07-31", 20_000)])
    conn.execute(
        """INSERT INTO payment
           (payment_id, payer_resident_id, amount_paise, payment_date, payment_kind,
            payee_or_provider)
           VALUES ('pay-chris','chris',1000000,'2026-08-20','to_pool','collector')""")
    conn.execute(
        """INSERT INTO payment_application
           (payment_application_id, payment_id, allocation_id, amount_paise, charge_due_date)
           VALUES ('app-1','pay-chris','a-chris',1000000,'2026-08-01')""")
    conn.execute(
        """INSERT INTO ledger_adjustment
           (adjustment_id, statement_id, resident_id, amount_paise, reason, created_at, created_by)
           VALUES ('adj-old','stmt-aug','bob',5000,'Historical correction',
                   '2026-08-25T10:00:00+05:30','operator')""")
    conn.commit()
    counts = {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
              for table in COUNTED_TABLES}
    conn.close()
    return counts


@pytest.fixture()
def prototype_db(tmp_path: Path) -> tuple[Path, dict[str, int]]:
    path = tmp_path / "prototype.sqlite"
    return path, build_prototype_database(path)


def test_an_existing_prototype_database_is_adopted_not_rebuilt(prototype_db):
    path, _ = prototype_db
    assert current_version(path) == 1, "a prototype database is recognised as the baseline"
    log = migrate(path)
    assert any("adopted the existing prototype database" in line for line in log)
    assert any("0002_" in line for line in log)
    assert current_version(path) == max(m.version for m in discover())


def test_every_record_survives_the_migration(prototype_db):
    path, before = prototype_db
    migrate(path)
    with connect(path, read_only=True) as conn:
        after = {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                 for table in COUNTED_TABLES}
    assert after == before


def test_the_important_totals_are_unchanged(prototype_db):
    path, _ = prototype_db
    def totals(connection):
        return {
            "expenses": connection.execute(
                "SELECT COALESCE(SUM(amount_paise),0) FROM expense_instance").fetchone()[0],
            "allocations": connection.execute(
                "SELECT COALESCE(SUM(amount_paise),0) FROM allocation").fetchone()[0],
            "payments": connection.execute(
                "SELECT COALESCE(SUM(amount_paise),0) FROM payment").fetchone()[0],
            "applications": connection.execute(
                "SELECT COALESCE(SUM(amount_paise),0) FROM payment_application").fetchone()[0],
            "adjustments": connection.execute(
                "SELECT COALESCE(SUM(amount_paise),0) FROM ledger_adjustment").fetchone()[0],
            "energy_wh": connection.execute(
                "SELECT COALESCE(SUM(energy_wh),0) FROM smart_plug_reading").fetchone()[0],
        }

    with sqlite3.connect(path) as conn:
        before = totals(conn)
    migrate(path)
    with connect(path, read_only=True) as conn:
        after = totals(conn)
    assert after == before
    assert before["expenses"] == 5_100_000


def test_the_new_period_columns_are_backfilled_to_their_old_meaning(prototype_db):
    path, _ = prototype_db
    migrate(path)
    with connect(path, read_only=True) as conn:
        rows = {row["expense_id"]: dict(row) for row in conn.execute(
            "SELECT * FROM expense_instance").fetchall()}
    rent = rows["rent-aug"]
    # The prototype used expense_period as the settlement month; that meaning is kept.
    assert rent["expense_period"] == "2026-08"
    assert rent["settlement_period"] == "2026-08"
    assert rent["service_period"] == "2026-08"
    assert rent["due_date"] == "2026-08-31"
    assert rent["period_rule"] == "custom", (
        "an existing row keeps an explicit period rather than inheriting a rule retroactively")
    assert rent["apartment_id"] == APARTMENT
    assert rent["generated_from_recurrence"] == 0
    # An expense with readings is recognised as electricity.
    assert rows["elec-jul"]["expense_kind"] == "electricity"
    assert rent["expense_kind"] == "general"


def test_payments_are_linked_to_their_apartment_and_marked_active(prototype_db):
    path, _ = prototype_db
    migrate(path)
    with connect(path, read_only=True) as conn:
        row = dict(conn.execute("SELECT * FROM payment WHERE payment_id='pay-chris'").fetchone())
    assert row["apartment_id"] == APARTMENT
    assert row["status"] == "active"
    assert row["amount_paise"] == 1_000_000


def test_an_adjustment_gets_an_effective_date_from_its_creation_time(prototype_db):
    path, _ = prototype_db
    migrate(path)
    with connect(path, read_only=True) as conn:
        row = dict(conn.execute(
            "SELECT * FROM ledger_adjustment WHERE adjustment_id='adj-old'").fetchone())
    assert row["effective_date"] == "2026-08-25"


def test_a_migrated_database_reconciles_immediately(prototype_db):
    path, _ = prototype_db
    migrate(path)
    settlement = reconciliation.reconcile(path, apartment_id=APARTMENT)
    assert settlement["net_positions_sum_paise"] == 0
    assert settlement["validation"]["error_count"] == 0
    assert settlement["totals"]["household_expenses_paise"] == 5_100_000
    # Alice funded the rent, Bob the electricity, and both are credited once each.
    assert settlement["expense_funding_paise_by_resident"]["alice"] == 4_500_000
    assert settlement["expense_funding_paise_by_resident"]["bob"] == 600_000
    # Chris's one million is recorded as applied to his rent charge.
    chris = next(a for a in settlement["accounts"] if a["account_id"] == "chris")
    assert chris["settled_paise"] == 1_000_000
    assert chris["pool_payments_paise"] == 1_000_000


def test_the_migrated_database_keeps_working_for_new_records(prototype_db):
    path, _ = prototype_db
    migrate(path)
    expenses.create_expense(
        path, apartment_id=APARTMENT, expense_id="new-after-migration",
        description="Post-migration expense", amount_paise=90_000,
        expense_date="2026-09-02", period_rule="current_month", allocation_method="equal")
    payments.record_payment(
        path, apartment_id=APARTMENT, payment_id="new-payment", payer_resident_id="bob",
        amount_paise=100_000, payment_date="2026-09-10")
    settlement = reconciliation.reconcile(path, apartment_id=APARTMENT)
    assert settlement["net_positions_sum_paise"] == 0
    assert settlement["totals"]["household_expenses_paise"] == 5_190_000


def test_the_old_draft_statement_can_still_be_finalized(prototype_db):
    path, _ = prototype_db
    migrate(path)
    snapshot = statements.finalize_statement(path, statement_id="stmt-aug",
                                              finalized_by="operator", require_clean=True)
    assert snapshot["status"] == "finalized_correction_window"
    assert snapshot["statement_total_paise"] == 5_100_000
    stored = statements.read_finalized_snapshot(path, "stmt-aug")
    assert stored["hash_matches"] is True


def test_migrating_twice_is_a_no_op(prototype_db):
    path, before = prototype_db
    migrate(path)
    log = migrate(path)
    assert log == [f"database already at version {current_version(path)}"]
    with connect(path, read_only=True) as conn:
        after = {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                 for table in COUNTED_TABLES}
    assert after == before


def test_initialize_database_is_the_same_as_migrating(tmp_path):
    from aem.migrations import initialize_database

    path = tmp_path / "fresh.sqlite"
    initialize_database(path)
    assert current_version(path) == max(m.version for m in discover())
    initialize_database(path)  # repeat is safe
    with connect(path, read_only=True) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='apartment'"
        ).fetchone()[0] == 1


def test_a_migration_edited_after_being_applied_is_reported(tmp_path, monkeypatch):
    """Editing an applied migration is a mistake, and it is named rather than ignored."""
    path = tmp_path / "checksum.sqlite"
    migrate(path)
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE schema_migration SET checksum='tampered' WHERE version=2")
        conn.execute("COMMIT")
    with pytest.raises(MigrationError, match="changed after it"):
        migrate(path)


def test_no_migration_drops_data(tmp_path):
    """A forward-only policy, checked mechanically against the migration text."""
    forbidden = ("drop table", "drop column", "truncate", "delete from")
    for migration in discover():
        lowered = migration.sql.lower()
        for phrase in forbidden:
            assert phrase not in lowered, f"{migration.name} contains {phrase!r}"


def test_the_statement_splitter_handles_trigger_bodies():
    """A trigger body contains semicolons; splitting on them would corrupt it."""
    sql = """
    CREATE TABLE t (a INTEGER);
    CREATE TRIGGER trg BEFORE UPDATE ON t FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'no');
    END;
    CREATE INDEX idx ON t(a);
    """
    statements_found = split_statements(sql)
    assert len(statements_found) == 3
    assert statements_found[1].startswith("CREATE TRIGGER")
    assert statements_found[1].rstrip().endswith("END;")


def test_foreign_keys_are_enforced_on_every_connection(tmp_path):
    path = tmp_path / "fk.sqlite"
    migrate(path)
    with connect(path) as conn:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        conn.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO resident (resident_id, apartment_id, display_name) "
                "VALUES ('ghost','no-such-apartment','Ghost')")
        conn.execute("ROLLBACK")

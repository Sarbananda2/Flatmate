import unittest
from datetime import datetime, timezone, timedelta

import _root  # noqa: F401  (makes ``tests`` importable)

from electricity_workflow import initialize_database
from ledger_workflow import (
    LedgerWorkflowError,
    create_expense,
    create_expense_definition,
    create_monthly_statement,
    finalize_statement,
    list_expense_definitions,
    record_payment,
    statement_snapshot,
    close_expired_statements,
    reconcile_pooled_settlement,
    record_adjustment,
)
from tests.support import connect, drop_database, new_database


class LedgerWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.db = new_database()
        initialize_database(self.db)
        with connect(self.db) as conn:
            conn.execute(
                "INSERT INTO apartment (apartment_id, name, created_at) VALUES (%s, %s, %s)",
                ("home", "Test Apartment", "2026-10-10T10:00:00+05:30"),
            )
            conn.cursor().executemany(
                "INSERT INTO resident (resident_id, apartment_id, display_name) VALUES (%s, %s, %s)",
                [("alice", "home", "Alice"), ("bob", "home", "Bob")],
            )
        self.statement_id = create_monthly_statement(
            self.db, apartment_id="home", settlement_month="2026-10",
            statement_id="stmt-oct", created_at="2026-10-01T09:00:00+05:30"
        )

    def tearDown(self):
        drop_database(self.db)

    def test_create_expense_persists_allocations(self):
        result = create_expense(
            self.db, apartment_id="home", expense_id="groceries-1",
            description="Shared groceries", amount_paise=10001,
            expense_date="2026-10-05", expense_period="2026-10",
            allocations_paise={"alice": 5000, "bob": 5001},
            payer_resident_id="alice", statement_id=self.statement_id,
        )
        self.assertEqual(result["amount_paise"], 10001)
        with connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM expense_instance WHERE expense_id='groceries-1'").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT SUM(amount_paise) FROM allocation WHERE expense_id='groceries-1'").fetchone()[0], 10001)

    def test_rejects_allocation_sum_mismatch(self):
        with self.assertRaisesRegex(LedgerWorkflowError, "sum exactly"):
            create_expense(
                self.db, apartment_id="home", expense_id="bad",
                description="Bad expense", amount_paise=100,
                expense_date="2026-10-05", expense_period="2026-10",
                allocations_paise={"alice": 40, "bob": 40},
            )
        with connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM expense_instance WHERE expense_id='bad'").fetchone()[0], 0)

    def test_requires_all_apartment_residents_in_allocations(self):
        with self.assertRaisesRegex(LedgerWorkflowError, "exactly one amount"):
            create_expense(
                self.db, apartment_id="home", expense_id="bad",
                description="Bad expense", amount_paise=100,
                expense_date="2026-10-05", expense_period="2026-10",
                allocations_paise={"alice": 100},
            )

    def test_expense_definition_is_dynamic_and_listable(self):
        create_expense_definition(
            self.db, apartment_id="home", definition_id="maid",
            name="Maid", category="Household", period_rule="current_month",
            default_allocation_method="equal", is_recurring=True,
        )
        self.assertEqual(list_expense_definitions(self.db, "home")[0]["name"], "Maid")

    def test_record_payment_applies_oldest_allocation_first(self):
        create_expense(
            self.db, apartment_id="home", expense_id="old",
            description="Old expense", amount_paise=10000,
            expense_date="2026-10-02", expense_period="2026-10",
            allocations_paise={"alice": 6000, "bob": 4000},
        )
        create_expense(
            self.db, apartment_id="home", expense_id="new",
            description="New expense", amount_paise=10000,
            expense_date="2026-10-08", expense_period="2026-10",
            allocations_paise={"alice": 8000, "bob": 2000},
        )
        result = record_payment(
            self.db, apartment_id="home", payment_id="pay-alice",
            payer_resident_id="alice", amount_paise=7000,
            payment_date="2026-10-10",
        )
        self.assertEqual(sum(x["amount_paise"] for x in result["applications"]), 7000)
        self.assertEqual(result["unapplied_credit_paise"], 0)
        with connect(self.db) as conn:
            rows = conn.execute(
                """SELECT a.expense_id, SUM(pa.amount_paise) FROM payment_application pa
                   JOIN allocation a ON a.allocation_id=pa.allocation_id
                   WHERE pa.payment_id='pay-alice' GROUP BY a.expense_id"""
            ).fetchall()
        self.assertEqual(dict(rows), {"new": 1000, "old": 6000})

    def test_excess_payment_remains_unapplied_credit(self):
        create_expense(
            self.db, apartment_id="home", expense_id="charge",
            description="Charge", amount_paise=1000,
            expense_date="2026-10-02", expense_period="2026-10",
            allocations_paise={"alice": 1000, "bob": 0},
        )
        result = record_payment(
            self.db, apartment_id="home", payment_id="overpay",
            payer_resident_id="alice", amount_paise=1500,
            payment_date="2026-10-10",
        )
        self.assertEqual(result["unapplied_credit_paise"], 500)

    def test_provider_advance_is_recorded_separately(self):
        result = record_payment(
            self.db, apartment_id="home", payment_id="provider",
            payer_resident_id="alice", amount_paise=5000,
            payment_date="2026-10-10", payment_kind="provider_advance",
            payee_or_provider="Electricity provider",
        )
        self.assertEqual(result["applications"], [])
        with connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT payment_kind FROM payment WHERE payment_id='provider'").fetchone()[0], "provider_advance")

    def test_statement_snapshot_and_finalize(self):
        create_expense(
            self.db, apartment_id="home", expense_id="groceries",
            description="Groceries", amount_paise=10000,
            expense_date="2026-10-05", expense_period="2026-10",
            allocations_paise={"alice": 6000, "bob": 4000},
            payer_resident_id="alice", statement_id=self.statement_id,
        )
        snapshot = statement_snapshot(self.db, self.statement_id)
        self.assertEqual(snapshot["charges_paise_by_resident"], {"alice": 6000, "bob": 4000})
        now = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
        finalized = finalize_statement(
            self.db, statement_id=self.statement_id, finalized_by="operator", now=now,
        )
        self.assertEqual(finalized["status"], "finalized_correction_window")
        with connect(self.db) as conn:
            row = conn.execute("SELECT status, snapshot_json FROM monthly_statement WHERE statement_id=%s", (self.statement_id,)).fetchone()
        self.assertEqual(row[0], "finalized_correction_window")
        self.assertIsNotNone(row[1])
        with self.assertRaisesRegex(LedgerWorkflowError, "draft statement"):
            create_expense(
                self.db, apartment_id="home", expense_id="too-late",
                description="Late expense", amount_paise=100,
                expense_date="2026-10-10", expense_period="2026-10",
                allocations_paise={"alice": 50, "bob": 50}, statement_id=self.statement_id,
            )


    def test_full_reconciliation_is_zero_sum_and_includes_pool(self):
        create_expense(
            self.db, apartment_id="home", expense_id="settle-rent",
            description="Rent", amount_paise=20000,
            expense_date="2026-09-30", expense_period="2026-09",
            allocations_paise={"alice": 10000, "bob": 10000},
            payer_resident_id="alice",
        )
        result = reconcile_pooled_settlement(self.db, apartment_id="home")
        self.assertEqual(result["net_positions_sum_paise"], 0)
        self.assertEqual(result["net_positions_paise_by_account"]["alice"], 10000)
        self.assertEqual(result["net_positions_paise_by_account"]["bob"], -10000)
        self.assertEqual(result["net_positions_paise_by_account"]["__pool__"], 0)
        self.assertEqual(result["suggested_transfers"], [
            {"from_account": "bob", "to_account": "alice", "amount_paise": 10000}
        ])

    def test_pool_payment_and_unapplied_credit_are_reconciled(self):
        create_expense(
            self.db, apartment_id="home", expense_id="unfunded",
            description="Shared bill", amount_paise=10000,
            expense_date="2026-10-02", expense_period="2026-10",
            allocations_paise={"alice": 5000, "bob": 5000},
        )
        record_payment(
            self.db, apartment_id="home", payment_id="alice-pool",
            payer_resident_id="alice", amount_paise=7000,
            payment_date="2026-10-10", payment_kind="to_pool",
        )
        result = reconcile_pooled_settlement(self.db, apartment_id="home")
        positions = result["net_positions_paise_by_account"]
        self.assertEqual(sum(positions.values()), 0)
        self.assertEqual(positions["alice"], 2000)
        self.assertEqual(positions["bob"], -5000)
        self.assertEqual(positions["__pool__"], 3000)

    def test_linked_provider_advance_does_not_double_count_expense_payer(self):
        create_expense(
            self.db, apartment_id="home", expense_id="electricity",
            description="Electricity", amount_paise=10000,
            expense_date="2026-10-04", expense_period="2026-09",
            allocations_paise={"alice": 4000, "bob": 6000},
            payer_resident_id="alice",
        )
        record_payment(
            self.db, apartment_id="home", payment_id="electricity-advance",
            payer_resident_id="alice", amount_paise=10000,
            payment_date="2026-10-04", payment_kind="provider_advance",
            expense_id="electricity",
        )
        result = reconcile_pooled_settlement(self.db, apartment_id="home")
        self.assertEqual(result["net_positions_sum_paise"], 0)
        self.assertEqual(result["net_positions_paise_by_account"]["alice"], 6000)
        self.assertEqual(result["net_positions_paise_by_account"]["bob"], -6000)
        self.assertEqual(result["net_positions_paise_by_account"]["__pool__"], 0)

    def test_signed_adjustment_is_included_and_auditable(self):
        adjustment_id = record_adjustment(
            self.db, apartment_id="home", resident_id="bob", amount_paise=250,
            reason="Correction from prior statement", created_by="operator",
            statement_id=self.statement_id,
        )
        result = reconcile_pooled_settlement(self.db, apartment_id="home")
        self.assertEqual(result["adjustment_net_paise_by_resident"]["bob"], 250)
        self.assertEqual(result["net_positions_paise_by_account"]["bob"], 250)
        self.assertEqual(result["net_positions_paise_by_account"]["__pool__"], -250)
        with connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT reason FROM ledger_adjustment WHERE adjustment_id=%s", (adjustment_id,)).fetchone()[0], "Correction from prior statement")

    def test_reconciliation_can_cut_off_by_date(self):
        create_expense(
            self.db, apartment_id="home", expense_id="late",
            description="Later bill", amount_paise=2000,
            expense_date="2026-10-20", expense_period="2026-10",
            allocations_paise={"alice": 1000, "bob": 1000},
        )
        before = reconcile_pooled_settlement(self.db, apartment_id="home", as_of_date="2026-10-19")
        after = reconcile_pooled_settlement(self.db, apartment_id="home", as_of_date="2026-10-20")
        self.assertEqual(before["allocated_charges_paise_by_resident"], {"alice": 0, "bob": 0})
        self.assertEqual(after["allocated_charges_paise_by_resident"], {"alice": 1000, "bob": 1000})
        self.assertEqual(after["net_positions_sum_paise"], 0)

    def test_close_statement_after_72_hours(self):
        now = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
        finalize_statement(self.db, statement_id=self.statement_id, finalized_by="operator", now=now)
        before = close_expired_statements(self.db, now=now + timedelta(hours=71))
        after = close_expired_statements(self.db, now=now + timedelta(hours=73))
        self.assertEqual(before, [])
        self.assertEqual(after, [self.statement_id])


if __name__ == "__main__":
    unittest.main()

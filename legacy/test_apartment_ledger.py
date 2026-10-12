import unittest
from datetime import date, datetime, timezone

from apartment_ledger import (
    Charge,
    Statement,
    StatementStatus,
    apply_payment_oldest_first,
    close_expired_statement,
    custom_allocation,
    equal_split,
    finalize_statement,
    host_guest_electricity,
    net_settlement_transfers,
    pooled_net_positions,
    revise_statement,
    weighted_split,
)


class AllocationTests(unittest.TestCase):
    def test_equal_split_remainder_does_not_benefit_payer(self):
        shares = equal_split(10000, ["A", "B", "C"], payer_id="A")
        self.assertEqual(shares, {"A": 3333, "B": 3334, "C": 3333})
        self.assertEqual(sum(shares.values()), 10000)

    def test_equal_split_remainder_skips_payer_first(self):
        shares = equal_split(100, ["A", "B", "C", "D"], payer_id="A")
        self.assertEqual(shares, {"A": 25, "B": 25, "C": 25, "D": 25})
        self.assertEqual(sum(shares.values()), 100)

    def test_weighted_split_exact_total(self):
        shares = weighted_split(10000, {"A": 1, "B": 2, "C": 1})
        self.assertEqual(sum(shares.values()), 10000)
        self.assertEqual(shares, {"A": 2500, "B": 5000, "C": 2500})

    def test_custom_allocation_requires_exact_total(self):
        self.assertEqual(custom_allocation(1000, {"A": 300, "B": 700}), {"A": 300, "B": 700})
        with self.assertRaises(ValueError):
            custom_allocation(1000, {"A": 300, "B": 600})

    def test_guest_cost_goes_to_host(self):
        self.assertEqual(host_guest_electricity("A", 15000), {"A": 15000})


class PaymentTests(unittest.TestCase):
    def setUp(self):
        self.charges = [
            Charge("rent-oct", "B", 500000, date(2026, 10, 1), "October rent"),
            Charge("grocery-sep", "B", 25000, date(2026, 9, 2), "September groceries"),
            Charge("electric-aug", "B", 40000, date(2026, 8, 5), "August electricity"),
        ]

    def test_payment_applies_oldest_due_date_first(self):
        result = apply_payment_oldest_first(50000, self.charges)
        self.assertEqual(
            [(a.charge_id, a.amount_paise) for a in result.applications],
            [("electric-aug", 40000), ("grocery-sep", 10000)],
        )
        self.assertEqual(result.unapplied_credit_paise, 0)

    def test_excess_payment_becomes_credit(self):
        result = apply_payment_oldest_first(600000, self.charges)
        self.assertEqual(sum(a.amount_paise for a in result.applications), 565000)
        self.assertEqual(result.unapplied_credit_paise, 35000)

    def test_already_applied_amount_is_respected(self):
        result = apply_payment_oldest_first(
            20000, self.charges, already_applied={"electric-aug": 40000}
        )
        self.assertEqual(
            [(a.charge_id, a.amount_paise) for a in result.applications],
            [("grocery-sep", 20000)],
        )


class PooledSettlementTests(unittest.TestCase):
    def test_pooled_positions_for_provider_advance(self):
        # Expense total = ₹13,200, split equally: ₹4,400 each.
        positions = pooled_net_positions(
            allocated_charges={"You": 440000, "A": 440000, "B": 440000},
            provider_advances={"You": 1200000, "A": 120000},
        )
        self.assertEqual(positions, {"You": 760000, "A": -320000, "B": -440000})
        self.assertEqual(sum(positions.values()), 0)

    def test_transfer_plan_balances_pooled_positions(self):
        positions = {"You": 760000, "A": -320000, "B": -440000}
        transfers = net_settlement_transfers(positions)
        self.assertEqual(transfers, [("A", "You", 320000), ("B", "You", 440000)])
        self.assertEqual(sum(amount for _, _, amount in transfers), 760000)

    def test_transfer_plan_requires_zero_sum(self):
        with self.assertRaises(ValueError):
            net_settlement_transfers({"A": -100, "B": 50})


class StatementLifecycleTests(unittest.TestCase):
    def test_finalize_opens_72_hour_window(self):
        now = datetime(2026, 10, 10, 10, 0, tzinfo=timezone.utc)
        statement = Statement("s1", "2026-10")
        finalize_statement(statement, now, {"total_paise": 10000})
        self.assertEqual(statement.status, StatementStatus.FINALIZED_CORRECTION_WINDOW)
        self.assertEqual(
            (statement.correction_deadline - now).total_seconds(),
            72 * 60 * 60,
        )
        self.assertEqual(len(statement.revisions), 1)

    def test_revision_requires_reason_and_preserves_history(self):
        now = datetime(2026, 10, 10, 10, 0, tzinfo=timezone.utc)
        statement = Statement("s1", "2026-10")
        finalize_statement(statement, now, {"total_paise": 10000})
        revise_statement(
            statement,
            now.replace(hour=11),
            "Corrected electricity bill",
            {"total_paise": 12000},
        )
        self.assertEqual(len(statement.revisions), 2)
        self.assertEqual(statement.revisions[0]["snapshot"]["total_paise"], 10000)
        self.assertEqual(statement.snapshot["total_paise"], 12000)
        with self.assertRaises(ValueError):
            revise_statement(statement, now, " ", {"total_paise": 9000})

    def test_statement_closes_at_deadline(self):
        now = datetime(2026, 10, 10, 10, 0, tzinfo=timezone.utc)
        statement = Statement("s1", "2026-10")
        finalize_statement(statement, now, {"total_paise": 10000})
        close_expired_statement(statement, now.replace(day=13, hour=10))
        self.assertEqual(statement.status, StatementStatus.CLOSED)
        with self.assertRaises(ValueError):
            revise_statement(statement, now.replace(day=13, hour=10), "late correction", {"total_paise": 9000})


if __name__ == "__main__":
    unittest.main(verbosity=2)

import sqlite3
import tempfile
import unittest
from pathlib import Path

from electricity_workflow import (
    ElectricityWorkflowError,
    create_electricity_expense,
    get_electricity_expense,
    initialize_database,
)


class ElectricityWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "apartment.sqlite"
        initialize_database(self.db)
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                "INSERT INTO apartment (apartment_id, name, created_at) VALUES (?, ?, ?)",
                ("home", "Test Flat", "2026-10-10T10:00:00+05:30"),
            )
            conn.executemany(
                """INSERT INTO resident (resident_id, apartment_id, display_name, is_active)
                   VALUES (?, ?, ?, 1)""",
                [("alice", "home", "Alice"), ("bob", "home", "Bob")],
            )

    def tearDown(self):
        self.temp.cleanup()

    def make_expense(self, **overrides):
        args = {
            "db_path": self.db,
            "apartment_id": "home",
            "expense_id": "electricity-oct",
            "description": "October electricity bill",
            "expense_date": "2026-10-08",
            "expense_period": "2026-10",
            "total_amount_paise": 10000,
            "payer_resident_id": "alice",
            "provider_name": "Utility provider",
            "smart_plug_readings": [
                {"resident_id": "alice", "kwh": "60.000", "device_label": "AC plug"},
                {"resident_id": "bob", "kwh": "40", "device_label": "AC plug"},
            ],
            "components": [
                {"id": "energy", "name": "Energy charges", "amount_paise": 9000,
                 "allocation_method": "usage"},
                {"id": "fixed", "name": "Fixed charges", "amount_paise": 1000,
                 "allocation_method": "equal"},
            ],
        }
        args.update(overrides)
        return create_electricity_expense(**args)

    def test_initialize_database_is_safe_to_repeat(self):
        initialize_database(self.db)
        with sqlite3.connect(self.db) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='apartment'"
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_saves_expense_components_readings_and_allocations(self):
        result = self.make_expense()
        saved = get_electricity_expense(self.db, "electricity-oct")
        self.assertIsNotNone(saved)
        self.assertEqual(saved["expense"]["amount_paise"], 10000)
        self.assertEqual(len(saved["components"]), 2)
        self.assertEqual(len(saved["readings"]), 2)
        self.assertEqual(len(saved["allocations"]), 4)
        totals = {}
        for row in saved["allocations"]:
            totals[row["resident_id"]] = totals.get(row["resident_id"], 0) + row["amount_paise"]
        self.assertEqual(totals, {"alice": 5900, "bob": 4100})
        self.assertEqual(sum(totals.values()), result.total_amount_paise)
        self.assertEqual(saved["readings"][0]["energy_wh"], 60000)

    def test_saves_default_single_component_when_components_omitted(self):
        self.make_expense(components=None, total_amount_paise=10000)
        saved = get_electricity_expense(self.db, "electricity-oct")
        self.assertEqual(len(saved["components"]), 1)
        self.assertEqual(saved["components"][0]["allocation_method"], "measured")

    def test_rejects_unknown_resident_and_writes_nothing(self):
        with self.assertRaisesRegex(ElectricityWorkflowError, "does not belong"):
            self.make_expense(smart_plug_readings=[
                {"resident_id": "ghost", "kwh": 1}
            ])
        self.assertIsNone(get_electricity_expense(self.db, "electricity-oct"))

    def test_invalid_component_totals_write_nothing(self):
        with self.assertRaisesRegex(ElectricityWorkflowError, "components total"):
            self.make_expense(components=[
                {"id": "energy", "amount_paise": 9999, "allocation_method": "equal"}
            ])
        self.assertIsNone(get_electricity_expense(self.db, "electricity-oct"))

    def test_rejects_precision_beyond_wh(self):
        with self.assertRaisesRegex(ElectricityWorkflowError, "three decimal places"):
            self.make_expense(smart_plug_readings=[{"resident_id": "alice", "kwh": "1.0001"}])
        self.assertIsNone(get_electricity_expense(self.db, "electricity-oct"))

    def test_duplicate_expense_rolls_back(self):
        self.make_expense()
        with self.assertRaisesRegex(ElectricityWorkflowError, "Could not persist"):
            self.make_expense()
        saved = get_electricity_expense(self.db, "electricity-oct")
        self.assertEqual(len(saved["components"]), 2)

    def test_rejects_payer_from_another_apartment(self):
        with self.assertRaisesRegex(ElectricityWorkflowError, "payer_resident_id"):
            self.make_expense(payer_resident_id="ghost")
        self.assertIsNone(get_electricity_expense(self.db, "electricity-oct"))

    def test_cannot_attach_to_finalized_statement(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                """INSERT INTO monthly_statement
                   (statement_id, apartment_id, settlement_month, status, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                ("final", "home", "2026-10", "closed", "2026-10-10T10:00:00+05:30"),
            )
        with self.assertRaisesRegex(ElectricityWorkflowError, "draft statement"):
            self.make_expense(statement_id="final")
        self.assertIsNone(get_electricity_expense(self.db, "electricity-oct"))

    def test_retrieval_returns_none_for_missing_expense(self):
        self.assertIsNone(get_electricity_expense(self.db, "missing"))


if __name__ == "__main__":
    unittest.main()

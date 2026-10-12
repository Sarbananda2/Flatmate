import unittest

from electricity_engine import calculate_electricity, ElectricityCalculationError


class ElectricityEngineTests(unittest.TestCase):
    def test_actual_bill_allocated_by_smart_plug_usage(self):
        result = calculate_electricity({
            "residents": ["alice", "bob"],
            "smart_plug_readings": [
                {"resident_id": "alice", "kwh": "60"},
                {"resident_id": "bob", "kwh": "40"},
            ],
            "bill": {"total_amount_paise": 10000},
        })
        self.assertEqual(result.total_amount_paise, 10000)
        self.assertEqual({a.resident_id: a.amount_paise for a in result.allocations},
                         {"alice": 6000, "bob": 4000})

    def test_component_level_exception_equal_split(self):
        result = calculate_electricity({
            "residents": ["alice", "bob"],
            "smart_plug_readings": [
                {"resident_id": "alice", "kwh": 70},
                {"resident_id": "bob", "kwh": 30},
            ],
            "bill": {
                "total_amount_paise": 10001,
                "components": [
                    {"id": "usage", "name": "Usage-linked charges", "amount_paise": 9000,
                     "allocation_method": "usage"},
                    {"id": "fixed", "name": "Fixed/shared charges", "amount_paise": 1001,
                     "allocation_method": "equal"},
                ],
            },
        })
        amounts = {a.resident_id: a.amount_paise for a in result.allocations}
        self.assertEqual(amounts, {"alice": 6801, "bob": 3200})
        self.assertEqual(sum(amounts.values()), 10001)

    def test_custom_exact_amounts(self):
        result = calculate_electricity({
            "residents": ["a", "b"],
            "smart_plug_readings": [{"resident_id": "a", "kwh": 1}, {"resident_id": "b", "kwh": 1}],
            "bill": {
                "total_amount_paise": 101,
                "components": [{"id": "exception", "amount_paise": 101,
                                "allocation_method": "custom",
                                "allocations_paise": {"a": 40, "b": 61}}],
            },
        })
        self.assertEqual([a.amount_paise for a in result.allocations], [40, 61])

    def test_custom_weights(self):
        result = calculate_electricity({
            "residents": ["a", "b"],
            "smart_plug_readings": [{"resident_id": "a", "kwh": 1}, {"resident_id": "b", "kwh": 1}],
            "bill": {
                "total_amount_paise": 900,
                "components": [{"id": "custom", "amount_paise": 900, "allocation_method": "custom",
                                "weights": {"a": 1, "b": 2}}],
            },
        })
        self.assertEqual([a.amount_paise for a in result.allocations], [300, 600])

    def test_payer_gets_rounding_benefit_in_equal_split(self):
        result = calculate_electricity({
            "residents": ["alice", "bob", "cara"],
            "smart_plug_readings": [
                {"resident_id": "alice", "kwh": 1},
                {"resident_id": "bob", "kwh": 1},
                {"resident_id": "cara", "kwh": 1},
            ],
            "payer_resident_id": "alice",
            "bill": {"total_amount_paise": 100, "default_allocation_method": "equal"},
        })
        amounts = {a.resident_id: a.amount_paise for a in result.allocations}
        self.assertEqual(amounts, {"alice": 33, "bob": 34, "cara": 33})
        self.assertEqual(sum(amounts.values()), 100)

    def test_guest_usage_is_added_to_host_reading(self):
        # Guest usage is intentionally represented in the host's aggregate kWh.
        result = calculate_electricity({
            "residents": ["host", "other"],
            "smart_plug_readings": [
                {"resident_id": "host", "kwh": "25.5"},  # includes guest usage
                {"resident_id": "other", "kwh": "74.5"},
            ],
            "bill": {"total_amount_paise": 10000},
        })
        amounts = {a.resident_id: a.amount_paise for a in result.allocations}
        self.assertEqual(amounts, {"host": 2550, "other": 7450})

    def test_multiple_readings_for_same_resident_are_aggregated(self):
        result = calculate_electricity({
            "residents": ["a", "b"],
            "smart_plug_readings": [
                {"resident_id": "a", "kwh": 2},
                {"resident_id": "a", "kwh": 3},
                {"resident_id": "b", "kwh": 5},
            ],
            "bill": {"total_amount_paise": 1000},
        })
        self.assertEqual([a.amount_paise for a in result.allocations], [500, 500])

    def test_components_must_sum_to_bill_total(self):
        with self.assertRaisesRegex(ElectricityCalculationError, "components total"):
            calculate_electricity({
                "residents": ["a", "b"],
                "smart_plug_readings": [{"resident_id": "a", "kwh": 1}, {"resident_id": "b", "kwh": 1}],
                "bill": {"total_amount_paise": 1000,
                         "components": [{"id": "x", "amount_paise": 999, "allocation_method": "equal"}]},
            })

    def test_usage_split_rejects_zero_total_usage(self):
        with self.assertRaisesRegex(ElectricityCalculationError, "zero"):
            calculate_electricity({
                "residents": ["a", "b"],
                "smart_plug_readings": [],
                "bill": {"total_amount_paise": 1000},
            })

    def test_unknown_resident_in_reading_rejected(self):
        with self.assertRaisesRegex(ElectricityCalculationError, "unknown resident"):
            calculate_electricity({
                "residents": ["a"],
                "smart_plug_readings": [{"resident_id": "ghost", "kwh": 2}],
                "bill": {"total_amount_paise": 100},
            })

    def test_negative_kwh_rejected(self):
        with self.assertRaisesRegex(ElectricityCalculationError, "non-negative"):
            calculate_electricity({
                "residents": ["a"],
                "smart_plug_readings": [{"resident_id": "a", "kwh": -1}],
                "bill": {"total_amount_paise": 100},
            })

    def test_duplicate_component_id_rejected(self):
        with self.assertRaisesRegex(ElectricityCalculationError, "Duplicate"):
            calculate_electricity({
                "residents": ["a", "b"],
                "smart_plug_readings": [{"resident_id": "a", "kwh": 1}, {"resident_id": "b", "kwh": 1}],
                "bill": {"total_amount_paise": 100,
                         "components": [
                             {"id": "x", "amount_paise": 50, "allocation_method": "equal"},
                             {"id": "x", "amount_paise": 50, "allocation_method": "equal"},
                         ]},
            })

    def test_custom_amounts_must_match_component_amount(self):
        with self.assertRaisesRegex(ElectricityCalculationError, "must sum"):
            calculate_electricity({
                "residents": ["a", "b"],
                "smart_plug_readings": [{"resident_id": "a", "kwh": 1}, {"resident_id": "b", "kwh": 1}],
                "bill": {"total_amount_paise": 100,
                         "components": [{"id": "x", "amount_paise": 100, "allocation_method": "custom",
                                         "allocations_paise": {"a": 20, "b": 70}}]},
            })

    def test_json_serialization(self):
        result = calculate_electricity({
            "residents": ["a"],
            "smart_plug_readings": [{"resident_id": "a", "kwh": "1.25"}],
            "bill": {"total_amount_paise": 123},
        })
        self.assertIn('"total_amount_paise": 123', result.to_json())

    def test_zero_bill_can_have_zero_usage(self):
        result = calculate_electricity({
            "residents": ["a", "b"],
            "smart_plug_readings": [],
            "bill": {"total_amount_paise": 0, "default_allocation_method": "equal"},
        })
        self.assertEqual(sum(a.amount_paise for a in result.allocations), 0)


if __name__ == "__main__":
    unittest.main()

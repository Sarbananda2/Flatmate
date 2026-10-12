"""The pure engines and the period rules.

These have no database, so they are tested directly: the split functions, the
oldest-first allocation ordering, the pooled settlement arithmetic, the
electricity engine's validation, and the period-rule mapping.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from aem.engine.electricity import ElectricityCalculationError, calculate_electricity
from aem.engine.ledger import (
    Charge,
    apply_payment_oldest_first,
    close_expired_statement,
    custom_allocation,
    equal_split,
    finalize_statement,
    host_guest_electricity,
    net_settlement_transfers,
    pooled_net_positions,
    revise_statement,
    Statement,
    StatementStatus,
    weighted_split,
)
from aem.errors import ValidationError
from aem.money import ensure_paise, format_inr, kwh_to_wh, rupees_to_paise, wh_to_kwh_string
from aem.periods import (
    default_due_date,
    month_end,
    month_range,
    normalize_month,
    resolve_periods,
    shift_month,
)


# ------------------------------------------------------------------ splits ---

@pytest.mark.parametrize("total, people", [(1, 3), (2, 3), (100, 3), (101, 7), (999_999, 4)])
def test_an_equal_split_is_exact_for_any_total(total, people):
    participants = [f"r{index}" for index in range(people)]
    shares = equal_split(total, participants)
    assert sum(shares.values()) == total
    assert all(isinstance(value, int) for value in shares.values())
    assert max(shares.values()) - min(shares.values()) <= 1


def test_the_payer_is_last_in_line_for_remainder_paise():
    shares = equal_split(100, ["alice", "bob", "chris"], "alice")
    assert shares["alice"] == 33
    assert sum(shares.values()) == 100
    # Two spare paise: both go to non-payers before the payer gets one.
    shares = equal_split(101, ["alice", "bob", "chris"], "alice")
    assert shares["alice"] == 33
    assert sorted(shares.values()) == [33, 34, 34]


def test_the_payer_receives_the_remainder_only_when_alone():
    assert equal_split(100, ["alice"], "alice") == {"alice": 100}
    assert equal_split(5, ["alice", "bob"], "alice") == {"alice": 2, "bob": 3}


def test_a_duplicate_participant_is_refused():
    with pytest.raises(ValueError, match="unique"):
        equal_split(100, ["alice", "alice"])


def test_a_weighted_split_uses_largest_remainder_deterministically():
    assert weighted_split(1_000, {"alice": 2, "bob": 1, "chris": 1}) == {
        "alice": 500, "bob": 250, "chris": 250}
    # 100 by 1:1:1 leaves one spare paisa; the tie breaks on resident ID.
    assert weighted_split(100, {"c": 1, "b": 1, "a": 1}) == {"a": 34, "b": 33, "c": 33}
    assert sum(weighted_split(7, {"a": 3, "b": 2, "c": 2}).values()) == 7


def test_a_zero_weight_gets_nothing():
    assert weighted_split(100, {"a": 1, "b": 0}) == {"a": 100, "b": 0}


def test_weights_must_not_all_be_zero():
    with pytest.raises(ValueError, match="positive"):
        weighted_split(100, {"a": 0, "b": 0})


def test_custom_shares_must_total_exactly():
    assert custom_allocation(100, {"a": 60, "b": 40}) == {"a": 60, "b": 40}
    with pytest.raises(ValueError, match="sum exactly"):
        custom_allocation(100, {"a": 60, "b": 41})


def test_guest_electricity_is_assigned_to_the_host():
    assert host_guest_electricity("alice", 5_000) == {"alice": 5_000}
    with pytest.raises(ValueError):
        host_guest_electricity("alice", -1)


# ------------------------------------------------------ payment allocation ---

def charges(*specs):
    return [
        Charge(charge_id=charge_id, resident_id="alice", amount_paise=amount,
               due_date=date.fromisoformat(due), description=charge_id, sequence=sequence)
        for sequence, (charge_id, amount, due) in enumerate(specs)
    ]


def test_a_payment_settles_the_oldest_due_date_first():
    result = apply_payment_oldest_first(
        150, charges(("newer", 100, "2026-10-31"), ("older", 100, "2026-09-30")))
    assert [(a.charge_id, a.amount_paise) for a in result.applications] == [
        ("older", 100), ("newer", 50)]
    assert result.unapplied_credit_paise == 0


def test_a_tie_on_due_date_is_broken_by_creation_sequence():
    result = apply_payment_oldest_first(
        150, charges(("first", 100, "2026-10-31"), ("second", 100, "2026-10-31")))
    assert [(a.charge_id, a.amount_paise) for a in result.applications] == [
        ("first", 100), ("second", 50)]


def test_the_ordering_does_not_depend_on_the_charge_identifier():
    """Same due date, same sequence order, deliberately unhelpful IDs."""
    forward = apply_payment_oldest_first(
        150, charges(("zzz", 100, "2026-10-31"), ("aaa", 100, "2026-10-31")))
    assert [a.charge_id for a in forward.applications] == ["zzz", "aaa"], (
        "the insertion sequence decides, not the alphabet")


def test_an_excess_payment_becomes_an_unapplied_credit():
    result = apply_payment_oldest_first(500, charges(("only", 100, "2026-10-31")))
    assert [(a.charge_id, a.amount_paise) for a in result.applications] == [("only", 100)]
    assert result.unapplied_credit_paise == 400


def test_already_applied_amounts_are_respected():
    existing = charges(("one", 100, "2026-10-31"))
    result = apply_payment_oldest_first(100, existing, {"one": 60})
    assert [(a.charge_id, a.amount_paise) for a in result.applications] == [("one", 40)]
    assert result.unapplied_credit_paise == 60


def test_applying_more_than_a_charge_is_refused():
    with pytest.raises(ValueError, match="exceeds charge"):
        apply_payment_oldest_first(10, charges(("one", 100, "2026-10-31")), {"one": 200})


def test_a_zero_payment_applies_nothing():
    result = apply_payment_oldest_first(0, charges(("one", 100, "2026-10-31")))
    assert result.applications == []
    assert result.unapplied_credit_paise == 0


# -------------------------------------------------------- pooled settlement ---

def test_pooled_positions_credit_an_advance_and_debit_a_share():
    positions = pooled_net_positions(
        allocated_charges={"alice": 10_000, "bob": 10_000},
        provider_advances={"alice": 20_000})
    assert positions == {"alice": 10_000, "bob": -10_000}


def test_a_transfer_plan_clears_every_balance_exactly():
    positions = {"alice": 15_000, "bob": -10_000, "chris": -5_000}
    transfers = net_settlement_transfers(positions)
    assert sum(amount for _, _, amount in transfers) == 15_000
    settled = dict(positions)
    for debtor, creditor, amount in transfers:
        settled[debtor] += amount
        settled[creditor] -= amount
    assert all(value == 0 for value in settled.values())


def test_a_transfer_plan_requires_a_zero_sum_input():
    with pytest.raises(ValueError, match="sum to zero"):
        net_settlement_transfers({"alice": 100, "bob": -50})


def test_a_transfer_plan_is_deterministic():
    positions = {"d": -7_000, "a": 3_000, "c": -2_000, "b": 6_000}
    assert net_settlement_transfers(positions) == net_settlement_transfers(positions)


# ------------------------------------------------- statement lifecycle (pure) ---

def test_finalizing_opens_a_72_hour_window():
    statement = Statement(statement_id="s1", settlement_month="2026-09")
    now = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    finalize_statement(statement, now, {"total": 1})
    assert statement.status is StatementStatus.FINALIZED_CORRECTION_WINDOW
    assert statement.correction_deadline == now + timedelta(hours=72)
    assert statement.revisions[0]["reason"] == "initial_finalization"


def test_finalization_requires_a_timezone_aware_moment():
    statement = Statement(statement_id="s1", settlement_month="2026-09")
    with pytest.raises(ValueError, match="timezone-aware"):
        finalize_statement(statement, datetime(2026, 10, 1, 12), {})


def test_a_revision_needs_a_reason_and_keeps_the_history():
    statement = Statement(statement_id="s1", settlement_month="2026-09")
    now = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    finalize_statement(statement, now, {"total": 1})
    with pytest.raises(ValueError, match="reason is required"):
        revise_statement(statement, now + timedelta(hours=1), "   ", {"total": 2})
    revise_statement(statement, now + timedelta(hours=1), "corrected", {"total": 2})
    assert [entry["reason"] for entry in statement.revisions] == [
        "initial_finalization", "corrected"]


def test_a_statement_closes_at_its_deadline():
    statement = Statement(statement_id="s1", settlement_month="2026-09")
    now = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    finalize_statement(statement, now, {})
    close_expired_statement(statement, now + timedelta(hours=71))
    assert statement.status is StatementStatus.FINALIZED_CORRECTION_WINDOW
    close_expired_statement(statement, now + timedelta(hours=72))
    assert statement.status is StatementStatus.CLOSED


# ------------------------------------------------------ electricity engine ---

BASE = {
    "residents": ["alice", "bob"],
    "smart_plug_readings": [{"resident_id": "alice", "kwh": "60"},
                            {"resident_id": "bob", "kwh": "40"}],
}


def test_the_actual_bill_total_is_authoritative():
    result = calculate_electricity({**BASE, "bill": {"total_amount_paise": 10_000}})
    assert result.total_amount_paise == 10_000
    assert {a.resident_id: a.amount_paise for a in result.allocations} == {
        "alice": 6_000, "bob": 4_000}
    assert result.breakdown["allocation_basis"].startswith("actual bill amounts")


def test_each_component_can_use_its_own_rule():
    result = calculate_electricity({
        **BASE, "payer_resident_id": "alice",
        "bill": {"total_amount_paise": 10_000, "components": [
            {"id": "energy", "name": "Energy", "amount_paise": 9_000,
             "allocation_method": "usage"},
            {"id": "fixed", "name": "Fixed", "amount_paise": 1_000,
             "allocation_method": "equal"},
        ]}})
    assert {a.resident_id: a.amount_paise for a in result.allocations} == {
        "alice": 5_900, "bob": 4_100}
    assert sum(c.amount_paise for c in result.components) == 10_000


def test_a_custom_component_can_encode_an_agreed_exception():
    result = calculate_electricity({
        **BASE, "bill": {"total_amount_paise": 10_000, "components": [
            {"id": "ac", "name": "Bob's AC alone", "amount_paise": 10_000,
             "allocation_method": "custom",
             "allocations_paise": {"alice": 0, "bob": 10_000}},
        ]}})
    assert {a.resident_id: a.amount_paise for a in result.allocations} == {
        "alice": 0, "bob": 10_000}


def test_components_must_total_the_bill():
    with pytest.raises(ElectricityCalculationError, match="components total"):
        calculate_electricity({
            **BASE, "bill": {"total_amount_paise": 10_000, "components": [
                {"id": "energy", "name": "Energy", "amount_paise": 9_000,
                 "allocation_method": "usage"}]}})


def test_custom_amounts_must_total_their_component():
    with pytest.raises(ElectricityCalculationError, match="must sum to its amount"):
        calculate_electricity({
            **BASE, "bill": {"total_amount_paise": 10_000, "components": [
                {"id": "x", "name": "X", "amount_paise": 10_000,
                 "allocation_method": "custom",
                 "allocations_paise": {"alice": 1, "bob": 2}}]}})


def test_a_usage_split_with_no_usage_is_refused_not_estimated():
    with pytest.raises(ElectricityCalculationError, match="usage"):
        calculate_electricity({
            "residents": ["alice", "bob"], "smart_plug_readings": [],
            "bill": {"total_amount_paise": 10_000}})


def test_a_zero_bill_with_no_usage_is_allowed():
    result = calculate_electricity({
        "residents": ["alice", "bob"], "smart_plug_readings": [],
        "bill": {"total_amount_paise": 0}})
    assert result.total_amount_paise == 0
    assert all(a.amount_paise == 0 for a in result.allocations)


def test_several_readings_for_one_resident_are_added_together():
    result = calculate_electricity({
        "residents": ["alice", "bob"],
        "smart_plug_readings": [{"resident_id": "alice", "kwh": "30"},
                                {"resident_id": "alice", "kwh": "30"},
                                {"resident_id": "bob", "kwh": "40"}],
        "bill": {"total_amount_paise": 10_000}})
    assert {a.resident_id: a.amount_paise for a in result.allocations} == {
        "alice": 6_000, "bob": 4_000}


def test_an_unknown_resident_in_a_reading_is_refused():
    with pytest.raises(ElectricityCalculationError, match="unknown resident"):
        calculate_electricity({
            "residents": ["alice"],
            "smart_plug_readings": [{"resident_id": "ghost", "kwh": "1"}],
            "bill": {"total_amount_paise": 100}})


def test_a_duplicate_component_id_is_refused():
    with pytest.raises(ElectricityCalculationError, match="Duplicate"):
        calculate_electricity({
            **BASE, "bill": {"total_amount_paise": 200, "components": [
                {"id": "x", "name": "A", "amount_paise": 100, "allocation_method": "equal"},
                {"id": "x", "name": "B", "amount_paise": 100, "allocation_method": "equal"}]}})


def test_a_float_bill_total_is_refused():
    with pytest.raises(ElectricityCalculationError, match="integer number of paise"):
        calculate_electricity({**BASE, "bill": {"total_amount_paise": 100.5}})


def test_the_result_serialises_to_json():
    result = calculate_electricity({**BASE, "bill": {"total_amount_paise": 10_000}})
    import json

    payload = json.loads(result.to_json())
    assert payload["total_amount_paise"] == 10_000
    assert len(payload["allocations"]) == 2


# ------------------------------------------------------------------- money ---

def test_rupees_convert_to_paise_without_floating_point():
    assert rupees_to_paise("1250.75") == 125_075
    assert rupees_to_paise("1,234.50") == 123_450
    assert rupees_to_paise("0.01") == 1
    with pytest.raises(ValidationError, match="two decimal places"):
        rupees_to_paise("1.005")


def test_paise_render_with_indian_grouping():
    assert format_inr(123_456_789) == "₹12,34,567.89"
    assert format_inr(-5_000) == "-₹50.00"
    assert format_inr(0) == "₹0.00"


def test_kwh_converts_to_whole_watt_hours():
    assert kwh_to_wh("42.125") == 42_125
    assert wh_to_kwh_string(42_125) == "42.125"
    with pytest.raises(ValidationError, match="three decimal places"):
        kwh_to_wh("1.0001")


def test_a_boolean_is_never_money():
    with pytest.raises(ValidationError, match="integer number of paise"):
        ensure_paise(True, "amount")


# ----------------------------------------------------------------- periods ---

@pytest.mark.parametrize("rule, txn, service, settlement", [
    ("current_month", "2026-10-15", "2026-10", "2026-10"),
    ("previous_month", "2026-10-01", "2026-09", "2026-10"),
    ("previous_month", "2026-01-01", "2025-12", "2026-01"),
    ("transaction_date", "2026-10-15", "2026-10", "2026-10"),
])
def test_period_rules_resolve_as_documented(rule, txn, service, settlement):
    resolved = resolve_periods(rule, txn)
    assert (resolved.service_period, resolved.settlement_period) == (service, settlement)


def test_an_explicit_period_overrides_the_rule():
    resolved = resolve_periods("current_month", "2026-10-15", service_period="2026-08",
                               settlement_period="2026-11")
    assert resolved.service_period == "2026-08"
    assert resolved.settlement_period == "2026-11"


def test_a_custom_rule_needs_a_settlement_period():
    with pytest.raises(ValidationError, match="explicit settlement_period"):
        resolve_periods("custom", "2026-10-15")
    resolved = resolve_periods("custom", "2026-10-15", settlement_period="2026-12")
    assert resolved.settlement_period == "2026-12"
    assert resolved.service_period == "2026-12"


def test_an_unknown_rule_is_refused():
    with pytest.raises(ValidationError, match="Unsupported period_rule"):
        resolve_periods("quarterly", "2026-10-15")


def test_month_arithmetic_crosses_year_boundaries():
    assert shift_month("2026-01", -1) == "2025-12"
    assert shift_month("2026-12", 1) == "2027-01"
    assert shift_month("2026-06", 12) == "2027-06"
    assert month_range("2026-11", "2027-02") == ["2026-11", "2026-12", "2027-01", "2027-02"]
    assert month_range("2026-11", "2026-10") == []


def test_month_end_handles_february_and_leap_years():
    assert month_end("2026-02").isoformat() == "2026-02-28"
    assert month_end("2028-02").isoformat() == "2028-02-29"
    assert month_end("2026-04").isoformat() == "2026-04-30"


def test_a_due_date_defaults_to_the_end_of_the_settlement_month():
    assert default_due_date("2026-02") == "2026-02-28"
    assert default_due_date("2026-12") == "2026-12-31"


@pytest.mark.parametrize("bad", ["2026-13", "26-01", "2026/01", "", "2026-1"])
def test_an_invalid_month_is_refused(bad):
    with pytest.raises(ValidationError):
        normalize_month(bad)

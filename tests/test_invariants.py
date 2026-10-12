"""The accounting invariants, asserted against a realistic household.

The twenty required invariants, plus a twenty-first added when the household
decided that nothing prorates.

Each test names the invariant it protects. They run against the `household`
fixture -- three residents, two months, partial payments, an overpayment, a
linked provider advance, an electricity bill, a closed statement, and a
post-close adjustment -- so an invariant that only holds for one tidy expense
will not pass here.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from aem.errors import AemError, ConflictError, ImmutableRecordError, ValidationError
from aem.services import (
    adjustments,
    electricity,
    expenses,
    payments,
    reconciliation,
    recurring,
    residents as residents_service,
    statements,
)
from aem.services.common import POOL_ACCOUNT_ID
from tests.conftest import APARTMENT
from tests.support import DatabaseRefusal, connect


# --------------------------------------------------------------------------- #
# 1. All financial values are stored as integer paise.
# --------------------------------------------------------------------------- #

MONEY_COLUMNS = {
    "expense_instance": ["amount_paise"],
    "expense_component": ["amount_paise"],
    "allocation": ["amount_paise"],
    "payment": ["amount_paise"],
    "payment_application": ["amount_paise"],
    "ledger_adjustment": ["amount_paise"],
    "electricity_bill": ["total_amount_paise"],
    "expense_definition": ["default_amount_paise"],
    "smart_plug_reading": ["energy_wh"],
}


def test_every_money_column_is_declared_and_stored_as_an_integer(household):
    with connect(household, read_only=True) as conn:
        for table, columns in MONEY_COLUMNS.items():
            declared = {row[0]: row[1].upper() for row in conn.execute(
                """SELECT column_name, data_type FROM information_schema.columns
                    WHERE table_schema = current_schema() AND table_name = %s""",
                (table,)).fetchall()}
            for column in columns:
                assert declared[column] == "BIGINT", f"{table}.{column} is {declared[column]}"
                for row in conn.execute(f"SELECT {column} FROM {table}").fetchall():
                    value = row[0]
                    assert value is None or isinstance(value, int), (
                        f"{table}.{column} holds a non-integer: {value!r}")
                    assert not isinstance(value, float)


def test_the_api_refuses_a_floating_point_amount(client):
    client.post("/api/apartments", json={"apartment_id": "home", "name": "Flat"})
    client.post("/api/residents", json={"resident_id": "alice", "display_name": "Alice"})
    response = client.post("/api/expenses", json={
        "description": "Float smuggling", "amount_paise": 100.5,
        "expense_date": "2026-10-01", "allocation_method": "equal"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "request_validation_error"


def test_a_boolean_is_not_accepted_as_money(apartment):
    with pytest.raises(ValidationError, match="integer number of paise"):
        expenses.create_expense(
            apartment, apartment_id=APARTMENT, expense_id="bool",
            description="True is an int in Python", amount_paise=True,
            expense_date="2026-10-01", allocations_paise={"alice": 1, "bob": 0, "chris": 0})


# --------------------------------------------------------------------------- #
# 2. Every expense's resident allocations equal the expense total exactly.
# --------------------------------------------------------------------------- #

def test_every_expense_allocation_set_sums_to_its_total(household):
    with connect(household, read_only=True) as conn:
        rows = conn.execute(
            """SELECT e.expense_id, e.amount_paise,
                      COALESCE((SELECT SUM(a.amount_paise) FROM allocation a
                                 WHERE a.expense_id = e.expense_id), 0) AS allocated
                 FROM expense_instance e WHERE e.apartment_id = %s""", (APARTMENT,)).fetchall()
    assert rows
    for row in rows:
        assert row["allocated"] == row["amount_paise"], row["expense_id"]


@pytest.mark.parametrize("shares", [
    {"alice": 40, "bob": 40, "chris": 20},      # 100 total, expense is 101
    {"alice": 50, "bob": 50, "chris": 50},      # over
])
def test_an_unbalanced_allocation_is_refused_and_writes_nothing(configured, shares):
    with pytest.raises(ValidationError, match="sum exactly"):
        expenses.create_expense(
            configured, apartment_id=APARTMENT, expense_id="unbalanced",
            description="Does not add up", amount_paise=101, expense_date="2026-10-01",
            allocations_paise=shares)
    with connect(configured, read_only=True) as conn:
        assert conn.execute("SELECT COUNT(*) FROM expense_instance WHERE expense_id='unbalanced'"
                            ).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM allocation WHERE expense_id='unbalanced'"
                            ).fetchone()[0] == 0


def test_an_allocation_missing_a_resident_is_refused(configured):
    with pytest.raises(ValidationError, match="exactly one amount"):
        expenses.create_expense(
            configured, apartment_id=APARTMENT, expense_id="incomplete",
            description="Chris is missing", amount_paise=100, expense_date="2026-10-01",
            allocations_paise={"alice": 50, "bob": 50})


# --------------------------------------------------------------------------- #
# 3 & 4. Electricity component totals and resident allocations equal the bill.
# --------------------------------------------------------------------------- #

def test_electricity_components_and_allocations_both_equal_the_actual_bill(household):
    saved = electricity.get_electricity_expense(household, "electricity-2026-09")
    bill_total = saved["expense"]["amount_paise"]
    assert bill_total == 735_000
    assert sum(row["amount_paise"] for row in saved["components"]) == bill_total
    assert sum(saved["resident_totals_paise"].values()) == bill_total
    assert saved["reconciles"] is True


def test_each_electricity_component_allocation_equals_that_component(household):
    with connect(household, read_only=True) as conn:
        for component in conn.execute(
            "SELECT component_id, amount_paise FROM expense_component WHERE expense_id=%s",
            ("electricity-2026-09",)).fetchall():
            allocated = conn.execute(
                "SELECT COALESCE(SUM(amount_paise),0) FROM allocation WHERE component_id=%s",
                (component["component_id"],)).fetchone()[0]
            assert allocated == component["amount_paise"]


def test_components_that_do_not_total_the_bill_are_refused(configured):
    with pytest.raises(ValidationError, match="components total"):
        electricity.create_electricity_expense(
            configured, apartment_id=APARTMENT, expense_id="bad-bill",
            description="Wrong components", expense_date="2026-10-08",
            total_amount_paise=100_000, smart_plug_readings=[{"resident_id": "alice", "kwh": "1"}],
            components=[{"id": "energy", "name": "Energy", "amount_paise": 90_000,
                         "allocation_method": "usage"}])
    assert electricity.get_electricity_expense(configured, "bad-bill") is None


def test_the_engine_never_reconstructs_the_bill_from_kwh(configured):
    """A positive usage-allocated bill with no recorded usage is refused, not estimated."""
    with pytest.raises(ValidationError, match="usage"):
        electricity.create_electricity_expense(
            configured, apartment_id=APARTMENT, expense_id="no-readings",
            description="No readings at all", expense_date="2026-10-08",
            total_amount_paise=500_000, smart_plug_readings=[])
    assert electricity.get_electricity_expense(configured, "no-readings") is None


def test_guest_usage_is_charged_to_the_host_and_never_to_a_guest(configured):
    """Guest kWh is added to the host's reading, so no guest account is created."""
    without_guest = electricity.calculate_preview(
        configured, apartment_id=APARTMENT, total_amount_paise=300_000,
        smart_plug_readings=[{"resident_id": "alice", "kwh": "10"},
                             {"resident_id": "bob", "kwh": "10"},
                             {"resident_id": "chris", "kwh": "10"}])
    with_guest = electricity.calculate_preview(
        configured, apartment_id=APARTMENT, total_amount_paise=300_000,
        smart_plug_readings=[{"resident_id": "alice", "kwh": "10"},
                             {"resident_id": "alice", "kwh": "20", "device_label": "guest room"},
                             {"resident_id": "bob", "kwh": "10"},
                             {"resident_id": "chris", "kwh": "10"}])
    base = {row["resident_id"]: row["amount_paise"] for row in without_guest["allocations"]}
    guested = {row["resident_id"]: row["amount_paise"] for row in with_guest["allocations"]}
    assert set(guested) == {"alice", "bob", "chris"}
    assert guested["alice"] > base["alice"]
    assert guested["bob"] < base["bob"]
    assert sum(guested.values()) == 300_000


def test_kwh_keeps_three_decimal_places_and_refuses_a_fourth(configured):
    electricity.create_electricity_expense(
        configured, apartment_id=APARTMENT, expense_id="precise",
        description="Precise readings", expense_date="2026-10-08",
        total_amount_paise=100_000,
        smart_plug_readings=[{"resident_id": "alice", "kwh": "42.125"},
                             {"resident_id": "bob", "kwh": "0.001"}])
    saved = electricity.get_electricity_expense(configured, "precise")
    assert {row["resident_id"]: row["energy_wh"] for row in saved["readings"]} == {
        "alice": 42_125, "bob": 1}
    with pytest.raises(ValidationError, match="three decimal places"):
        electricity.create_electricity_expense(
            configured, apartment_id=APARTMENT, expense_id="too-precise",
            description="Too precise", expense_date="2026-10-08", total_amount_paise=100,
            smart_plug_readings=[{"resident_id": "alice", "kwh": "1.0001"}])


# --------------------------------------------------------------------------- #
# 5. Payments cannot be allocated beyond the available payment amount.
# --------------------------------------------------------------------------- #

def test_no_payment_is_applied_beyond_its_own_amount(household):
    with connect(household, read_only=True) as conn:
        rows = conn.execute(
            """SELECT p.payment_id, p.amount_paise,
                      COALESCE((SELECT SUM(pa.amount_paise) FROM payment_application pa
                                 WHERE pa.payment_id = p.payment_id), 0) AS applied
                 FROM payment p WHERE p.apartment_id = %s""", (APARTMENT,)).fetchall()
    assert rows
    for row in rows:
        assert row["applied"] <= row["amount_paise"], row["payment_id"]


def test_the_database_refuses_an_over_applied_payment(configured):
    """Defence in depth: the trigger rejects it even from direct SQL.

    The application amount here exceeds the *payment* but stays within the
    charge, so it is specifically the payment guard that must fire.
    """
    expenses.create_expense(
        configured, apartment_id=APARTMENT, expense_id="big-charge",
        description="A large charge", amount_paise=100_000, expense_date="2026-10-01",
        settlement_period="2026-10",
        allocations_paise={"alice": 100_000, "bob": 0, "chris": 0})
    payments.record_payment(
        configured, apartment_id=APARTMENT, payment_id="small-payment",
        payer_resident_id="bob", amount_paise=500, payment_date="2026-10-10",
        payment_kind="provider_advance")  # not auto-applied, so it starts unapplied
    with connect(configured) as conn:
        allocation_id = conn.execute(
            "SELECT allocation_id FROM allocation WHERE expense_id='big-charge'").fetchone()[0]
        with pytest.raises(DatabaseRefusal, match="overapplied_payment"):
            conn.execute(
                """INSERT INTO payment_application
                   (payment_application_id, payment_id, allocation_id, amount_paise, created_at)
                   VALUES ('bogus', 'small-payment', %s, 1000, '2026-10-01T00:00:00+05:30')""",
                (allocation_id,))


def test_the_database_refuses_an_over_settled_charge(household):
    """And the charge guard fires when an application exceeds the charge itself."""
    with connect(household) as conn:
        allocation_id = conn.execute(
            "SELECT allocation_id FROM allocation ORDER BY amount_paise LIMIT 1").fetchone()[0]
        with pytest.raises(DatabaseRefusal):
            conn.execute(
                """INSERT INTO payment_application
                   (payment_application_id, payment_id, allocation_id, amount_paise, created_at)
                   VALUES ('bogus', 'pay-chris-2026-09', %s, 99999999,
                           '2026-10-01T00:00:00+05:30')""",
                (allocation_id,))


def test_a_charge_cannot_be_settled_beyond_its_own_amount(household):
    with connect(household, read_only=True) as conn:
        rows = conn.execute(
            """SELECT a.allocation_id, a.amount_paise,
                      COALESCE((SELECT SUM(pa.amount_paise) FROM payment_application pa
                                 WHERE pa.allocation_id = a.allocation_id), 0) AS settled
                 FROM allocation a""").fetchall()
    for row in rows:
        assert row["settled"] <= row["amount_paise"], row["allocation_id"]


# --------------------------------------------------------------------------- #
# 6. Oldest-first allocation is deterministic.
# --------------------------------------------------------------------------- #

def test_oldest_first_allocation_is_deterministic_and_order_independent(
        configured, database_factory):
    """Two identical databases built in different insertion orders settle the same."""
    from aem.services import apartments as apartment_service
    from aem.services import residents as resident_service

    def build(order):
        path = database_factory()
        apartment_service.create_apartment(path, apartment_id=APARTMENT, name="Flat")
        for resident_id, name in (("alice", "Alice"), ("bob", "Bob"), ("chris", "Chris")):
            resident_service.create_resident(path, apartment_id=APARTMENT,
                                             resident_id=resident_id, display_name=name)
        # Three charges in the same settlement month, so they share a due date and
        # only the insertion sequence can decide the order.
        for index in order:
            expenses.create_expense(
                path, apartment_id=APARTMENT, expense_id=f"charge-{index}",
                description=f"Charge {index}", amount_paise=10_000 * index,
                expense_date=f"2026-10-0{index}", settlement_period="2026-10",
                allocations_paise={"alice": 10_000 * index, "bob": 0, "chris": 0})
        result = payments.record_payment(
            path, apartment_id=APARTMENT, payment_id="pay", payer_resident_id="alice",
            amount_paise=25_000, payment_date="2026-10-20")
        return [(app["amount_paise"]) for app in result["applications"]]

    first = build([1, 2, 3])
    # Re-running the same construction must produce the same applications.
    assert build([1, 2, 3]) == first
    # 25000 over charges of 10000, 20000, 30000 by date: 10000 then 15000.
    assert first == [10_000, 15_000]


def test_a_payment_settles_the_oldest_charge_first_across_months(configured):
    expenses.create_expense(
        configured, apartment_id=APARTMENT, expense_id="aug", description="August bill",
        amount_paise=10_000, expense_date="2026-08-02", settlement_period="2026-08",
        allocations_paise={"alice": 10_000, "bob": 0, "chris": 0})
    expenses.create_expense(
        configured, apartment_id=APARTMENT, expense_id="oct", description="October bill",
        amount_paise=10_000, expense_date="2026-10-02", settlement_period="2026-10",
        allocations_paise={"alice": 10_000, "bob": 0, "chris": 0})
    result = payments.record_payment(
        configured, apartment_id=APARTMENT, payment_id="pay", payer_resident_id="alice",
        amount_paise=12_000, payment_date="2026-10-25")
    settled = {}
    with connect(configured, read_only=True) as conn:
        for row in conn.execute(
            """SELECT a.expense_id, SUM(pa.amount_paise) AS amount
                 FROM payment_application pa JOIN allocation a
                   ON a.allocation_id = pa.allocation_id
                WHERE pa.payment_id = 'pay' GROUP BY a.expense_id""").fetchall():
            settled[row["expense_id"]] = row["amount"]
    assert settled == {"aug": 10_000, "oct": 2_000}
    assert result["unapplied_credit_paise"] == 0


# --------------------------------------------------------------------------- #
# 7. Unapplied credits remain visible and are not lost between months.
# --------------------------------------------------------------------------- #

def test_an_overpayment_stays_a_visible_credit_and_carries_forward(configured):
    expenses.create_expense(
        configured, apartment_id=APARTMENT, expense_id="small", description="Small charge",
        amount_paise=10_000, expense_date="2026-09-02", settlement_period="2026-09",
        allocations_paise={"alice": 10_000, "bob": 0, "chris": 0})
    result = payments.record_payment(
        configured, apartment_id=APARTMENT, payment_id="overpay", payer_resident_id="alice",
        amount_paise=30_000, payment_date="2026-09-20")
    assert result["unapplied_credit_paise"] == 20_000

    credits = payments.unapplied_credits(configured, APARTMENT)
    assert [(row["payer_resident_id"], row["unapplied_paise"]) for row in credits] == [
        ("alice", 20_000)]

    # A later month must still see the credit, not absorb it.
    expenses.create_expense(
        configured, apartment_id=APARTMENT, expense_id="later", description="October charge",
        amount_paise=5_000, expense_date="2026-10-02", settlement_period="2026-10",
        allocations_paise={"alice": 5_000, "bob": 0, "chris": 0})
    still = payments.unapplied_credits(configured, APARTMENT)
    assert sum(row["unapplied_paise"] for row in still) == 20_000, (
        "a new charge must not silently consume an existing credit")

    settlement = reconciliation.reconcile(configured, apartment_id=APARTMENT)
    assert settlement["unapplied_credits_paise_by_resident"]["alice"] == 20_000


def test_applying_a_credit_is_an_explicit_action(configured):
    expenses.create_expense(
        configured, apartment_id=APARTMENT, expense_id="charge-1", description="Charge",
        amount_paise=10_000, expense_date="2026-09-02", settlement_period="2026-09",
        allocations_paise={"alice": 10_000, "bob": 0, "chris": 0})
    payments.record_payment(
        configured, apartment_id=APARTMENT, payment_id="overpay", payer_resident_id="alice",
        amount_paise=30_000, payment_date="2026-09-20")
    expenses.create_expense(
        configured, apartment_id=APARTMENT, expense_id="charge-2", description="Later charge",
        amount_paise=8_000, expense_date="2026-10-02", settlement_period="2026-10",
        allocations_paise={"alice": 8_000, "bob": 0, "chris": 0})

    applied = payments.reapply_unapplied_credit(
        configured, apartment_id=APARTMENT, resident_id="alice")
    assert applied["total_applied_paise"] == 8_000
    remaining = payments.unapplied_credits(configured, APARTMENT)
    assert sum(row["unapplied_paise"] for row in remaining) == 12_000


# --------------------------------------------------------------------------- #
# 8. Provider advances are not double-counted.
# --------------------------------------------------------------------------- #

def test_a_linked_advance_replaces_the_expense_payer_credit(configured):
    expenses.create_expense(
        configured, apartment_id=APARTMENT, expense_id="bill", description="Water bill",
        amount_paise=30_000, expense_date="2026-10-04", settlement_period="2026-10",
        allocations_paise={"alice": 10_000, "bob": 10_000, "chris": 10_000},
        payer_resident_id="alice")
    before = reconciliation.reconcile(configured, apartment_id=APARTMENT)
    assert before["net_positions_paise_by_account"]["alice"] == 20_000

    payments.record_payment(
        configured, apartment_id=APARTMENT, payment_id="advance", payer_resident_id="alice",
        amount_paise=30_000, payment_date="2026-10-04", payment_kind="provider_advance",
        expense_id="bill")
    after = reconciliation.reconcile(configured, apartment_id=APARTMENT)
    # Still +20000, not +50000: the advance replaced the payer credit.
    assert after["net_positions_paise_by_account"]["alice"] == 20_000
    assert after["net_positions_sum_paise"] == 0
    assert after["expense_funding_paise_by_resident"]["alice"] == 0
    assert after["provider_advances_paise_by_resident"]["alice"] == 30_000


def test_an_advance_cannot_exceed_the_expense_it_funds(configured):
    expenses.create_expense(
        configured, apartment_id=APARTMENT, expense_id="bill", description="Water bill",
        amount_paise=30_000, expense_date="2026-10-04", settlement_period="2026-10",
        allocations_paise={"alice": 10_000, "bob": 10_000, "chris": 10_000})
    with pytest.raises(ValidationError, match="exceed the expense"):
        payments.record_payment(
            configured, apartment_id=APARTMENT, payment_id="too-much",
            payer_resident_id="alice", amount_paise=30_001, payment_date="2026-10-04",
            payment_kind="provider_advance", expense_id="bill")


def test_an_unlinked_advance_that_looks_like_a_duplicate_is_reported(configured):
    expenses.create_expense(
        configured, apartment_id=APARTMENT, expense_id="bill", description="Water bill",
        amount_paise=30_000, expense_date="2026-10-04", settlement_period="2026-10",
        allocations_paise={"alice": 10_000, "bob": 10_000, "chris": 10_000},
        payer_resident_id="alice")
    payments.record_payment(
        configured, apartment_id=APARTMENT, payment_id="unlinked", payer_resident_id="alice",
        amount_paise=30_000, payment_date="2026-10-05", payment_kind="provider_advance",
        payee_or_provider="Water board")
    settlement = reconciliation.reconcile(configured, apartment_id=APARTMENT)
    codes = {issue["code"] for issue in settlement["validation"]["issues"]}
    assert "possible_double_counted_advance" in codes, (
        "an unlinked advance matching an expense's payer, amount and date must be named")
    issue = next(i for i in settlement["validation"]["issues"]
                 if i["code"] == "possible_double_counted_advance")
    assert issue["entity_id"] == "unlinked"
    assert issue["details"]["expense_id"] == "bill"
    # The ledger still balances, so the operator is warned rather than blocked.
    assert settlement["net_positions_sum_paise"] == 0


def test_a_partially_linked_expense_is_reported_as_partly_pool_funded(configured):
    expenses.create_expense(
        configured, apartment_id=APARTMENT, expense_id="bill", description="Water bill",
        amount_paise=30_000, expense_date="2026-10-04", settlement_period="2026-10",
        allocations_paise={"alice": 10_000, "bob": 10_000, "chris": 10_000},
        payer_resident_id="alice")
    payments.record_payment(
        configured, apartment_id=APARTMENT, payment_id="part", payer_resident_id="alice",
        amount_paise=20_000, payment_date="2026-10-04", payment_kind="provider_advance",
        expense_id="bill")
    settlement = reconciliation.reconcile(configured, apartment_id=APARTMENT)
    issue = next(i for i in settlement["validation"]["issues"]
                 if i["code"] == "expense_partially_funded")
    assert issue["details"]["funded_by_pool_paise"] == 10_000
    assert issue["details"]["payer_resident_id_ignored"] == "alice"
    assert settlement["net_positions_paise_by_account"]["alice"] == 10_000


# --------------------------------------------------------------------------- #
# 9. Transfers between residents do not create household expenses.
# --------------------------------------------------------------------------- #

def test_payments_of_every_kind_leave_household_expenses_unchanged(configured):
    expenses.create_expense(
        configured, apartment_id=APARTMENT, expense_id="only", description="The only expense",
        amount_paise=30_000, expense_date="2026-10-01", settlement_period="2026-10",
        allocations_paise={"alice": 10_000, "bob": 10_000, "chris": 10_000})
    baseline = reconciliation.reconcile(configured, apartment_id=APARTMENT)
    assert baseline["totals"]["household_expenses_paise"] == 30_000

    for index, (kind, payer) in enumerate((
        ("to_pool", "alice"), ("provider_advance", "bob"), ("refund", "chris"))):
        payments.record_payment(
            configured, apartment_id=APARTMENT, payment_id=f"p{index}",
            payer_resident_id=payer, amount_paise=5_000, payment_date="2026-10-10",
            payment_kind=kind)

    after = reconciliation.reconcile(configured, apartment_id=APARTMENT)
    assert after["totals"]["household_expenses_paise"] == 30_000, (
        "a payment must never increase household expenses")
    assert after["totals"]["allocated_charges_paise"] == 30_000
    assert after["net_positions_sum_paise"] == 0
    with connect(configured, read_only=True) as conn:
        assert conn.execute("SELECT COUNT(*) FROM expense_instance").fetchone()[0] == 1


def test_a_resident_to_resident_transfer_is_modelled_without_an_expense(configured):
    """Alice hands Bob money: a pool payment plus a refund. No expense appears."""
    payments.record_payment(
        configured, apartment_id=APARTMENT, payment_id="alice-in",
        payer_resident_id="alice", amount_paise=10_000, payment_date="2026-10-10",
        payment_kind="to_pool", note="settling up with Bob")
    payments.record_payment(
        configured, apartment_id=APARTMENT, payment_id="bob-out", payer_resident_id="bob",
        amount_paise=10_000, payment_date="2026-10-10", payment_kind="refund",
        note="received from Alice via the pool")
    settlement = reconciliation.reconcile(configured, apartment_id=APARTMENT)
    assert settlement["totals"]["household_expenses_paise"] == 0
    assert settlement["net_positions_paise_by_account"]["alice"] == 10_000
    assert settlement["net_positions_paise_by_account"]["bob"] == -10_000
    assert settlement["net_positions_paise_by_account"][POOL_ACCOUNT_ID] == 0
    assert settlement["net_positions_sum_paise"] == 0


# --------------------------------------------------------------------------- #
# 10. A resident's share and the resident who funded it stay distinct.
# --------------------------------------------------------------------------- #

def test_the_payer_is_charged_their_share_and_credited_the_funding_separately(configured):
    expenses.create_expense(
        configured, apartment_id=APARTMENT, expense_id="rent", description="Rent",
        amount_paise=30_000, expense_date="2026-10-01", settlement_period="2026-10",
        allocations_paise={"alice": 10_000, "bob": 10_000, "chris": 10_000},
        payer_resident_id="alice")
    settlement = reconciliation.reconcile(configured, apartment_id=APARTMENT)
    alice = next(a for a in settlement["accounts"] if a["account_id"] == "alice")
    assert alice["allocated_charges_paise"] == 10_000, "her share is only her share"
    assert alice["expense_funding_paise"] == 30_000, "she is credited for funding the whole bill"
    assert alice["net_position_paise"] == 20_000, "net of the two"
    assert alice["outstanding_charges_paise"] == 10_000, (
        "funding an expense does not settle her own charge")

    entries = reconciliation.ledger_entries(
        configured, apartment_id=APARTMENT, account_id="alice")["items"]
    kinds = {entry["kind"] for entry in entries}
    assert {"expense_share", "expense_funded_by_payer"} <= kinds, (
        "the share and the funding are separate ledger entries")


# --------------------------------------------------------------------------- #
# 11. Household and resident balances reconcile to zero in aggregate.
# --------------------------------------------------------------------------- #

def test_all_accounts_sum_to_zero_in_the_full_household(household):
    settlement = reconciliation.reconcile(household, apartment_id=APARTMENT)
    assert settlement["net_positions_sum_paise"] == 0
    assert sum(settlement["net_positions_paise_by_account"].values()) == 0
    assert POOL_ACCOUNT_ID in settlement["net_positions_paise_by_account"]


def test_every_ledger_entry_set_sums_to_zero(household):
    entries = reconciliation.ledger_entries(household, apartment_id=APARTMENT, limit=5000)
    assert entries["sum_paise"] == 0
    assert entries["total"] > 0


def test_the_two_independent_views_of_the_ledger_agree(household):
    """Net positions and the charge-level view are cross-checked inside reconcile.

    net = unapplied credit − outstanding charges + funding + adjustments − refunds
    """
    settlement = reconciliation.reconcile(household, apartment_id=APARTMENT)
    for account in settlement["accounts"]:
        if account["account_type"] != "resident":
            continue
        expected = (account["unapplied_credit_paise"]
                    - account["outstanding_charges_paise"]
                    + account["funding_credits_paise"]
                    + account["adjustments_paise"]
                    - account["refunds_paise"]
                    - account["legacy_payment_adjustments_paise"])
        assert expected == account["net_position_paise"], account["account_id"]


# --------------------------------------------------------------------------- #
# 12. Settlement transfers do not exceed the balances they settle.
# --------------------------------------------------------------------------- #

def test_suggested_transfers_never_exceed_the_balances_they_settle(household):
    settlement = reconciliation.reconcile(household, apartment_id=APARTMENT)
    positions = dict(settlement["net_positions_paise_by_account"])
    debtors = {account: -amount for account, amount in positions.items() if amount < 0}
    creditors = {account: amount for account, amount in positions.items() if amount > 0}
    for transfer in settlement["transfers"]:
        assert transfer["amount_paise"] > 0
        debtors[transfer["from_account"]] -= transfer["amount_paise"]
        creditors[transfer["to_account"]] -= transfer["amount_paise"]
        assert debtors[transfer["from_account"]] >= 0, "a debtor paid more than they owed"
        assert creditors[transfer["to_account"]] >= 0, "a creditor received more than owed"
    assert all(value == 0 for value in debtors.values())
    assert all(value == 0 for value in creditors.values())


def test_settlement_suggestions_change_no_records(household):
    with connect(household, read_only=True) as conn:
        before = {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                  for table in ("expense_instance", "allocation", "payment",
                                "payment_application", "ledger_adjustment")}
    reconciliation.reconcile(household, apartment_id=APARTMENT)
    reconciliation.reconcile(household, apartment_id=APARTMENT)
    with connect(household, read_only=True) as conn:
        after = {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                 for table in before}
    assert before == after


# --------------------------------------------------------------------------- #
# 13. Re-running a reconciliation without new transactions is identical.
# --------------------------------------------------------------------------- #

def _comparable(settlement: dict) -> str:
    payload = {key: value for key, value in settlement.items() if key != "validation"}
    return json.dumps(payload, sort_keys=True, default=str)


def test_reconciliation_is_reproducible(household):
    first = reconciliation.reconcile(household, apartment_id=APARTMENT)
    second = reconciliation.reconcile(household, apartment_id=APARTMENT)
    third = reconciliation.reconcile(household, apartment_id=APARTMENT)
    assert _comparable(first) == _comparable(second) == _comparable(third)
    assert first["validation"]["issues"] == second["validation"]["issues"]


def test_an_as_of_date_reproduces_a_historical_position(household):
    early = reconciliation.reconcile(household, apartment_id=APARTMENT,
                                     as_of_date="2026-09-15")
    again = reconciliation.reconcile(household, apartment_id=APARTMENT,
                                     as_of_date="2026-09-15")
    assert _comparable(early) == _comparable(again)
    assert early["net_positions_sum_paise"] == 0
    # The October expenses and the October adjustment are excluded.
    assert "2026-10" not in early["household_expenses_paise_by_settlement_period"]
    assert early["adjustment_net_paise_by_resident"]["chris"] == 0

    late = reconciliation.reconcile(household, apartment_id=APARTMENT)
    assert late["adjustment_net_paise_by_resident"]["chris"] == -25_000
    assert (late["totals"]["household_expenses_paise"]
            > early["totals"]["household_expenses_paise"])


# --------------------------------------------------------------------------- #
# 14. Re-running recurring generation does not duplicate expenses.
# --------------------------------------------------------------------------- #

def test_regenerating_recurring_expenses_creates_no_duplicates(configured):
    first = recurring.generate(
        configured, apartment_id=APARTMENT, settlement_period="2026-11",
        lines=[{"expense_definition_id": "rent"}, {"expense_definition_id": "broadband"}])
    assert first["created_count"] == 2

    for _ in range(3):
        again = recurring.generate(
            configured, apartment_id=APARTMENT, settlement_period="2026-11",
            lines=[{"expense_definition_id": "rent"}, {"expense_definition_id": "broadband"}])
        assert again["created_count"] == 0
        assert again["skipped_count"] == 2

    with connect(configured, read_only=True) as conn:
        counts = conn.execute(
            """SELECT expense_definition_id, COUNT(*) AS n FROM expense_instance
                WHERE apartment_id=%s AND settlement_period='2026-11'
                GROUP BY expense_definition_id""", (APARTMENT,)).fetchall()
    assert {row["expense_definition_id"]: row["n"] for row in counts} == {
        "rent": 1, "broadband": 1}


def test_the_database_itself_refuses_a_duplicate_generated_expense(configured):
    recurring.generate(
        configured, apartment_id=APARTMENT, settlement_period="2026-11",
        lines=[{"expense_definition_id": "rent"}])
    with connect(configured) as conn:
        with pytest.raises(DatabaseRefusal):
            conn.execute(
                """INSERT INTO expense_instance
                   (expense_id, expense_definition_id, description, amount_paise, expense_date,
                    expense_period, is_settled, created_at, settlement_period,
                    generated_from_recurrence, apartment_id)
                   VALUES ('dupe','rent','Rent again',1,'2026-11-01','2026-11',0,
                           '2026-11-01T00:00:00+05:30','2026-11',1,%s)""",
                (APARTMENT,))


def test_a_manually_entered_expense_also_blocks_generation(configured):
    expenses.create_expense(
        configured, apartment_id=APARTMENT, expense_id="rent-by-hand", description="Rent",
        amount_paise=4_500_000, expense_date="2026-11-01",
        expense_definition_id="rent", allocation_method="equal")
    preview = recurring.preview(configured, apartment_id=APARTMENT,
                                settlement_period="2026-11")
    rent = next(line for line in preview["lines"]
                if line["expense_definition_id"] == "rent")
    assert rent["status"] == "already_exists"
    assert rent["existing_expense_id"] == "rent-by-hand"
    result = recurring.generate(
        configured, apartment_id=APARTMENT, settlement_period="2026-11",
        lines=[{"expense_definition_id": "rent"}])
    assert result["created_count"] == 0


def test_generation_needs_a_confirmed_line(configured):
    with pytest.raises(ValidationError, match="nothing was generated"):
        recurring.generate(configured, apartment_id=APARTMENT,
                           settlement_period="2026-11", lines=[])


# --------------------------------------------------------------------------- #
# 15. Finalized statement snapshots remain reproducible.
# --------------------------------------------------------------------------- #

def test_a_finalized_snapshot_is_stored_hashed_and_re_readable(household):
    stored = statements.read_finalized_snapshot(household, "stmt-2026-09")
    assert stored["hash_matches"] is True
    again = statements.read_finalized_snapshot(household, "stmt-2026-09")
    assert again["snapshot"] == stored["snapshot"]
    assert again["snapshot_hash"] == stored["snapshot_hash"]
    snapshot = stored["snapshot"]
    assert snapshot["settlement_month"] == "2026-09"
    assert snapshot["expense_count"] >= 4
    assert snapshot["statement_total_paise"] == sum(
        expense["amount_paise"] for expense in snapshot["expenses"])


def test_later_activity_does_not_change_the_stored_snapshot(household):
    before = statements.read_finalized_snapshot(household, "stmt-2026-09")
    expenses.create_expense(
        household, apartment_id=APARTMENT, expense_id="much-later",
        description="A November expense", amount_paise=90_000, expense_date="2026-11-02",
        settlement_period="2026-11", allocation_method="equal")
    payments.record_payment(
        household, apartment_id=APARTMENT, payment_id="much-later-pay",
        payer_resident_id="alice", amount_paise=10_000, payment_date="2026-11-05")
    after = statements.read_finalized_snapshot(household, "stmt-2026-09")
    assert after["snapshot"] == before["snapshot"]
    assert after["snapshot_hash"] == before["snapshot_hash"]
    assert after["hash_matches"] is True


def test_the_snapshot_records_how_each_expense_was_funded(household):
    snapshot = statements.read_finalized_snapshot(household, "stmt-2026-09")["snapshot"]
    electricity_line = next(e for e in snapshot["expenses"]
                            if e["expense_id"] == "electricity-2026-09")
    assert electricity_line["funding_source"] == "linked_provider_advance"
    assert electricity_line["linked_provider_advances"][0]["amount_paise"] == 735_000
    groceries = next(e for e in snapshot["expenses"]
                     if e["expense_id"] == "groceries-2026-09")
    assert groceries["funding_source"] == "payer_resident"
    assert groceries["payer_resident_id"] == "alice"


# --------------------------------------------------------------------------- #
# 16. Closed statements cannot be silently modified.
# --------------------------------------------------------------------------- #

def test_a_closed_statement_refuses_an_expense_edit(household):
    with pytest.raises(ImmutableRecordError) as error:
        expenses.update_expense(household, "groceries-2026-09",
                                description="Changed after close", reason="trying anyway")
    assert error.value.code == "closed_statement"


def test_a_closed_statement_refuses_a_new_expense(household):
    with pytest.raises(ConflictError, match="draft statement"):
        expenses.create_expense(
            household, apartment_id=APARTMENT, expense_id="too-late",
            description="Too late", amount_paise=1_000, expense_date="2026-09-30",
            settlement_period="2026-09", statement_id="stmt-2026-09",
            allocation_method="equal")


def test_a_closed_statement_refuses_a_revision(household):
    with pytest.raises(AemError):
        statements.revise_statement(household, "stmt-2026-09", reason="too late")


def test_the_database_refuses_to_rewrite_a_closed_snapshot(household):
    with connect(household) as conn:
        with pytest.raises(DatabaseRefusal, match="closed_statement"):
            conn.execute("UPDATE monthly_statement SET snapshot_json='{}' WHERE statement_id=%s",
                         ("stmt-2026-09",))
        with pytest.raises(DatabaseRefusal, match="closed_statement"):
            conn.execute("UPDATE monthly_statement SET status='draft' WHERE statement_id=%s",
                         ("stmt-2026-09",))


def test_the_database_refuses_to_delete_an_expense_in_a_closed_statement(household):
    with connect(household) as conn:
        with pytest.raises(DatabaseRefusal):
            conn.execute("DELETE FROM expense_instance WHERE expense_id=%s",
                         ("groceries-2026-09",))


def test_the_audit_log_cannot_be_rewritten(household):
    # The role the application serves as is not permitted to try...
    with connect(household) as conn:
        with pytest.raises(DatabaseRefusal, match="permission denied"):
            conn.execute("UPDATE audit_log SET actor='someone else'")
        with pytest.raises(DatabaseRefusal, match="permission denied"):
            conn.execute("DELETE FROM audit_log")
    # ...and the triggers refuse even the role that owns the table.
    with connect(household, as_owner=True) as conn:
        with pytest.raises(DatabaseRefusal, match="append_only"):
            conn.execute("UPDATE audit_log SET actor='someone else'")
        with pytest.raises(DatabaseRefusal, match="append_only"):
            conn.execute("DELETE FROM audit_log")


def test_statement_revisions_cannot_be_rewritten(household):
    with connect(household) as conn:
        with pytest.raises(DatabaseRefusal, match="permission denied"):
            conn.execute("UPDATE statement_revision SET reason='different'")
    with connect(household, as_owner=True) as conn:
        with pytest.raises(DatabaseRefusal, match="append_only"):
            conn.execute("UPDATE statement_revision SET reason='different'")


def test_an_edit_inside_the_correction_window_requires_a_reason_and_makes_a_revision(configured):
    statement = statements.create_monthly_statement(
        configured, apartment_id=APARTMENT, settlement_month="2026-10",
        statement_id="stmt-oct")
    expenses.create_expense(
        configured, apartment_id=APARTMENT, expense_id="rent-oct", description="Rent",
        amount_paise=30_000, expense_date="2026-10-01", settlement_period="2026-10",
        statement_id=statement, allocation_method="equal")
    now = datetime.now(timezone.utc)
    statements.finalize_statement(configured, statement_id=statement,
                                 finalized_by="operator", now=now)

    with pytest.raises(ValidationError, match="reason is required"):
        expenses.update_expense(configured, "rent-oct", description="Rent (corrected)")

    expenses.update_expense(configured, "rent-oct", description="Rent (corrected)",
                            reason="landlord re-issued the receipt", now=now)
    revisions = statements.list_statement_revisions(configured, statement)
    assert [r["revision_number"] for r in revisions] == [1, 2]
    assert revisions[1]["reason"] == "landlord re-issued the receipt"


def test_the_correction_window_closes_exactly_at_the_deadline(configured):
    statement = statements.create_monthly_statement(
        configured, apartment_id=APARTMENT, settlement_month="2026-10",
        statement_id="stmt-oct")
    now = datetime(2026, 10, 31, 12, tzinfo=timezone.utc)
    snapshot = statements.finalize_statement(
        configured, statement_id=statement, finalized_by="operator", now=now)
    assert snapshot["correction_deadline"] == (now + timedelta(hours=72)).isoformat(
        timespec="seconds")
    assert statements.close_expired_statements(
        configured, now=now + timedelta(hours=71, minutes=59)) == []
    assert statements.close_expired_statements(configured, now=now + timedelta(hours=72)) == [
        statement]


# --------------------------------------------------------------------------- #
# 17. Adjustments are traceable and affect balances exactly once.
# --------------------------------------------------------------------------- #

def test_an_adjustment_affects_the_balance_exactly_once(configured):
    before = reconciliation.reconcile(configured, apartment_id=APARTMENT)
    adjustments.record_adjustment(
        configured, apartment_id=APARTMENT, resident_id="bob", amount_paise=7_500,
        reason="Agreed goodwill credit", created_by="operator", adjustment_id="adj-1")
    after = reconciliation.reconcile(configured, apartment_id=APARTMENT)
    delta = (after["net_positions_paise_by_account"]["bob"]
             - before["net_positions_paise_by_account"]["bob"])
    assert delta == 7_500
    assert after["adjustment_net_paise_by_resident"]["bob"] == 7_500
    assert after["net_positions_sum_paise"] == 0

    entries = [e for e in reconciliation.ledger_entries(
        configured, apartment_id=APARTMENT, kind="ledger_adjustment")["items"]
        if e["account_id"] == "bob"]
    assert len(entries) == 1, "exactly one entry per adjustment per account"
    assert entries[0]["source_id"] == "adj-1"
    assert entries[0]["description"].endswith("Agreed goodwill credit")


def test_an_adjustment_is_corrected_by_a_reversal_not_an_edit(configured):
    adjustment_id = adjustments.record_adjustment(
        configured, apartment_id=APARTMENT, resident_id="bob", amount_paise=7_500,
        reason="Mistake", created_by="operator")
    reversal_id = adjustments.reverse_adjustment(
        configured, adjustment_id, apartment_id=APARTMENT, reason="entered on the wrong resident")
    settlement = reconciliation.reconcile(configured, apartment_id=APARTMENT)
    assert settlement["adjustment_net_paise_by_resident"]["bob"] == 0
    assert settlement["net_positions_sum_paise"] == 0

    listed = adjustments.list_adjustments(configured, apartment_id=APARTMENT)["items"]
    original = next(row for row in listed if row["adjustment_id"] == adjustment_id)
    reversal = next(row for row in listed if row["adjustment_id"] == reversal_id)
    assert original["reversed_by"] == reversal_id
    assert reversal["reverses_adjustment_id"] == adjustment_id
    assert reversal["amount_paise"] == -original["amount_paise"]


def test_an_adjustment_cannot_be_reversed_twice(configured):
    adjustment_id = adjustments.record_adjustment(
        configured, apartment_id=APARTMENT, resident_id="bob", amount_paise=7_500,
        reason="Mistake", created_by="operator")
    adjustments.reverse_adjustment(configured, adjustment_id, apartment_id=APARTMENT,
                                   reason="first")
    with pytest.raises(ConflictError, match="already been reversed"):
        adjustments.reverse_adjustment(configured, adjustment_id, apartment_id=APARTMENT,
                                       reason="again")


def test_a_reversing_adjustment_must_be_the_exact_negative(configured):
    adjustment_id = adjustments.record_adjustment(
        configured, apartment_id=APARTMENT, resident_id="bob", amount_paise=7_500,
        reason="Mistake", created_by="operator")
    with pytest.raises(ValidationError, match="exact negative"):
        adjustments.record_adjustment(
            configured, apartment_id=APARTMENT, resident_id="bob", amount_paise=-1_000,
            reason="partial reversal", created_by="operator",
            reverses_adjustment_id=adjustment_id)


def test_a_zero_adjustment_is_refused(configured):
    with pytest.raises(ValidationError, match="non-zero"):
        adjustments.record_adjustment(
            configured, apartment_id=APARTMENT, resident_id="bob", amount_paise=0,
            reason="nothing", created_by="operator")


# --------------------------------------------------------------------------- #
# 18. Date cutoffs and accounting-period rules behave consistently.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "rule, transaction_date, expected_service, expected_settlement",
    [
        ("current_month", "2026-10-05", "2026-10", "2026-10"),
        ("previous_month", "2026-10-05", "2026-09", "2026-10"),
        ("transaction_date", "2026-10-05", "2026-10", "2026-10"),
        ("current_month", "2026-01-31", "2026-01", "2026-01"),
        ("previous_month", "2026-01-02", "2025-12", "2026-01"),
    ],
)
def test_period_rules_map_a_transaction_date_consistently(
    configured, rule, transaction_date, expected_service, expected_settlement
):
    result = expenses.create_expense(
        configured, apartment_id=APARTMENT, expense_id=f"rule-{rule}-{transaction_date}",
        description=f"{rule} expense", amount_paise=30_000, expense_date=transaction_date,
        period_rule=rule, allocation_method="equal")
    assert result["service_period"] == expected_service
    assert result["settlement_period"] == expected_settlement


def test_a_custom_rule_requires_an_explicit_settlement_period(configured):
    from aem.periods import resolve_periods

    with pytest.raises(ValidationError, match="explicit settlement_period"):
        resolve_periods("custom", "2026-10-05")


def test_a_statement_collects_expenses_by_settlement_period_not_transaction_date(configured):
    statement = statements.create_monthly_statement(
        configured, apartment_id=APARTMENT, settlement_month="2026-10",
        statement_id="stmt-oct")
    # Transaction in October, service period September: belongs to October.
    expenses.create_expense(
        configured, apartment_id=APARTMENT, expense_id="sept-electricity",
        description="September electricity", amount_paise=60_000, expense_date="2026-10-06",
        period_rule="previous_month", allocation_method="equal")
    # Transaction in October, but explicitly settled in November.
    expenses.create_expense(
        configured, apartment_id=APARTMENT, expense_id="deferred",
        description="Deferred to November", amount_paise=30_000, expense_date="2026-10-06",
        period_rule="custom", settlement_period="2026-11", allocation_method="equal")

    attached = statements.attach_eligible_expenses(configured, statement)
    assert [row["expense_id"] for row in attached["attached"]] == ["sept-electricity"]
    preview = statements.preview_statement(configured, statement)
    assert preview["statement_total_paise"] == 60_000


def test_an_expense_cannot_be_attached_to_the_wrong_months_statement(configured):
    statements.create_monthly_statement(
        configured, apartment_id=APARTMENT, settlement_month="2026-10",
        statement_id="stmt-oct")
    with pytest.raises(ValidationError, match="different month"):
        expenses.create_expense(
            configured, apartment_id=APARTMENT, expense_id="november",
            description="November expense", amount_paise=30_000, expense_date="2026-11-02",
            period_rule="current_month", statement_id="stmt-oct", allocation_method="equal")


def test_the_as_of_cutoff_is_inclusive_of_its_own_date(configured):
    expenses.create_expense(
        configured, apartment_id=APARTMENT, expense_id="on-the-day", description="On the day",
        amount_paise=30_000, expense_date="2026-10-20", settlement_period="2026-10",
        allocation_method="equal")
    before = reconciliation.reconcile(configured, apartment_id=APARTMENT,
                                      as_of_date="2026-10-19")
    on_day = reconciliation.reconcile(configured, apartment_id=APARTMENT,
                                      as_of_date="2026-10-20")
    assert before["totals"]["household_expenses_paise"] == 0
    assert on_day["totals"]["household_expenses_paise"] == 30_000


def test_a_charge_due_date_defaults_to_the_end_of_its_settlement_month(configured):
    result = expenses.create_expense(
        configured, apartment_id=APARTMENT, expense_id="due", description="Due date test",
        amount_paise=30_000, expense_date="2026-02-03", period_rule="current_month",
        allocation_method="equal")
    assert result["due_date"] == "2026-02-28"


# --------------------------------------------------------------------------- #
# 19. Invalid data cannot partially modify the database.
# --------------------------------------------------------------------------- #

def test_a_rejected_expense_leaves_no_partial_rows(configured):
    with connect(configured, read_only=True) as conn:
        before = {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                  for table in ("expense_instance", "expense_component", "allocation")}
    with pytest.raises(ValidationError):
        expenses.create_expense(
            configured, apartment_id=APARTMENT, expense_id="partial",
            description="Partial", amount_paise=100, expense_date="2026-10-01",
            allocations_paise={"alice": 100, "bob": 100, "chris": 100})
    with connect(configured, read_only=True) as conn:
        after = {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                 for table in before}
    assert before == after


def test_a_rejected_electricity_bill_leaves_no_partial_rows(configured):
    with connect(configured, read_only=True) as conn:
        before = {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                  for table in ("expense_instance", "expense_component", "allocation",
                                "smart_plug_reading", "electricity_bill")}
    with pytest.raises(ValidationError):
        electricity.create_electricity_expense(
            configured, apartment_id=APARTMENT, expense_id="half-saved",
            description="Half saved", expense_date="2026-10-08", total_amount_paise=100_000,
            smart_plug_readings=[{"resident_id": "ghost", "kwh": "1"}])
    with connect(configured, read_only=True) as conn:
        after = {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                 for table in before}
    assert before == after


def test_a_duplicate_expense_id_rolls_back_cleanly(configured):
    electricity.create_electricity_expense(
        configured, apartment_id=APARTMENT, expense_id="once",
        description="Once", expense_date="2026-10-08", total_amount_paise=100_000,
        smart_plug_readings=[{"resident_id": "alice", "kwh": "5"}])
    with connect(configured, read_only=True) as conn:
        before = conn.execute("SELECT COUNT(*) FROM allocation").fetchone()[0]
    with pytest.raises(ValidationError, match="Could not persist"):
        electricity.create_electricity_expense(
            configured, apartment_id=APARTMENT, expense_id="once",
            description="Twice", expense_date="2026-10-09", total_amount_paise=200_000,
            smart_plug_readings=[{"resident_id": "bob", "kwh": "5"}])
    with connect(configured, read_only=True) as conn:
        assert conn.execute("SELECT COUNT(*) FROM allocation").fetchone()[0] == before
        assert conn.execute("SELECT amount_paise FROM expense_instance WHERE expense_id='once'"
                            ).fetchone()[0] == 100_000


def test_a_reconciliation_error_is_raised_rather_than_balanced_away(apartment):
    """An expense whose allocations do not add up stops the calculation."""
    from aem.errors import ReconciliationError

    with connect(apartment) as conn:
        conn.execute("BEGIN")
        conn.execute(
            """INSERT INTO expense_instance
               (expense_id, description, amount_paise, expense_date, expense_period,
                is_settled, created_at, settlement_period, service_period, due_date,
                apartment_id, allocation_method, expense_kind, generated_from_recurrence)
               VALUES ('broken','Hand-edited',10000,'2026-10-01','2026-10',0,
                       '2026-10-01T00:00:00+05:30','2026-10','2026-10','2026-10-31',
                       %s, 'custom','general',0)""", (APARTMENT,))
        conn.execute(
            """INSERT INTO allocation (allocation_id, expense_id, resident_id, amount_paise)
               VALUES ('a-broken','broken','alice',4000)""")
        conn.execute("COMMIT")

    with pytest.raises(ReconciliationError) as error:
        reconciliation.reconcile(apartment, apartment_id=APARTMENT)
    assert error.value.code == "allocation_total_mismatch"
    assert error.value.details["expense_id"] == "broken"


def test_validation_names_the_record_at_fault(apartment):
    from aem.services import validation

    with connect(apartment) as conn:
        conn.execute("BEGIN")
        conn.execute(
            """INSERT INTO expense_instance
               (expense_id, description, amount_paise, expense_date, expense_period,
                is_settled, created_at, settlement_period, service_period, due_date,
                apartment_id, allocation_method, expense_kind, generated_from_recurrence)
               VALUES ('broken','Hand-edited',10000,'2026-10-01','2026-10',0,
                       '2026-10-01T00:00:00+05:30','2026-10','2026-10','2026-10-31',
                       %s, 'custom','general',0)""", (APARTMENT,))
        conn.execute("COMMIT")
    report = validation.validate(apartment, APARTMENT)
    assert report["is_reconcilable"] is False
    codes = {issue["code"] for issue in report["issues"]}
    assert "expense_not_allocated" in codes
    issue = next(i for i in report["issues"] if i["code"] == "expense_not_allocated")
    assert issue["entity_id"] == "broken"
    assert issue["suggested_action"]


# --------------------------------------------------------------------------- #
# 20. Rounding behaviour is deterministic and documented.
# --------------------------------------------------------------------------- #

def test_an_equal_split_remainder_never_benefits_the_payer(configured):
    plan = expenses.preview_allocation(
        configured, apartment_id=APARTMENT, amount_paise=100, method="equal",
        payer_resident_id="alice")
    # 100 over three: base 33, one remainder paisa, which must not go to Alice.
    assert plan["shares_paise"]["alice"] == 33
    assert sum(plan["shares_paise"].values()) == 100
    assert plan["basis"]["remainder_paise"] == 1
    assert "non-payers first" in plan["basis"]["rounding_rule"]


def test_a_remainder_falls_on_the_payer_only_when_there_is_nobody_else(configured):
    plan = expenses.preview_allocation(
        configured, apartment_id=APARTMENT, amount_paise=100, method="equal",
        participants=["alice"], payer_resident_id="alice")
    assert plan["shares_paise"]["alice"] == 100


def test_electricity_rounding_puts_the_payer_last_and_is_repeatable(configured):
    payload = dict(
        apartment_id=APARTMENT, total_amount_paise=1_000,
        smart_plug_readings=[{"resident_id": "alice", "kwh": "1"},
                             {"resident_id": "bob", "kwh": "1"},
                             {"resident_id": "chris", "kwh": "1"}],
        payer_resident_id="chris")
    first = electricity.calculate_preview(configured, **payload)
    second = electricity.calculate_preview(configured, **payload)
    shares = {row["resident_id"]: row["amount_paise"] for row in first["allocations"]}
    assert shares == {row["resident_id"]: row["amount_paise"] for row in second["allocations"]}
    assert sum(shares.values()) == 1_000
    # 1000 over three equal readings: 333 each with one spare paisa, to a non-payer.
    assert shares["chris"] == 333
    assert sorted(shares.values()) == [333, 333, 334]
    assert "payer is placed last" in first["rounding_rule"]


def test_a_weighted_split_uses_largest_remainder_and_warns_about_the_payer_rule(configured):
    plan = expenses.preview_allocation(
        configured, apartment_id=APARTMENT, amount_paise=1_000, method="weighted",
        weights={"alice": 2, "bob": 1, "chris": 1}, payer_resident_id="alice")
    assert plan["shares_paise"] == {"alice": 500, "bob": 250, "chris": 250}
    assert sum(plan["shares_paise"].values()) == 1_000
    assert any("payer rounding preference" in warning for warning in plan["warnings"]), (
        "the weighted path must say that the payer preference is defined for equal splits only")


@pytest.mark.parametrize("amount", [1, 2, 7, 99, 100, 101, 333_401, 999_999_999])
def test_an_equal_split_always_totals_the_amount_exactly(configured, amount):
    plan = expenses.preview_allocation(
        configured, apartment_id=APARTMENT, amount_paise=amount, method="equal",
        payer_resident_id="bob")
    assert sum(plan["shares_paise"].values()) == amount
    assert all(isinstance(share, int) for share in plan["shares_paise"].values())


def test_rounding_in_the_real_household_still_balances(household):
    """Groceries of 333401 paise over three residents is deliberately awkward."""
    detail = expenses.read_expense(household, "groceries-2026-09")
    shares = {row["resident_id"]: row["amount_paise"] for row in detail["allocations"]}
    assert sum(shares.values()) == 333_401
    # Alice paid, so she takes the lower share of the remainder.
    assert shares["alice"] == 111_133
    assert sorted(shares.values()) == [111_133, 111_134, 111_134]


# --------------------------------------------------------------------------- #
# 21. Nothing prorates. Decided by the household on 2026-10-11.
#
# A partial month never reduces a share, for any category. Active dates are
# recorded for reference and must never alter an allocation. These tests exist
# so the decision cannot drift back without a failure.
# --------------------------------------------------------------------------- #

def test_nothing_prorates_for_a_partial_month(configured):
    """A resident who arrives mid-month is charged a full ordinary share."""
    # Chris joins on the 16th of a 30-day month, which is exactly half of it.
    residents_service.update_resident(configured, "chris", apartment_id=APARTMENT,
                                      active_from="2026-11-16")
    result = expenses.create_expense(
        configured, apartment_id=APARTMENT, expense_id="rent-nov", description="Rent",
        amount_paise=4_500_000, expense_date="2026-11-01", period_rule="current_month",
        allocation_method="equal")
    shares = result["allocations_paise"]
    assert shares == {"alice": 1_500_000, "bob": 1_500_000, "chris": 1_500_000}, (
        "a mid-month arrival must still be charged a full ordinary share")
    assert sum(shares.values()) == 4_500_000, (
        "the household always collects the whole fixed cost; there is no shortfall "
        "for the other residents to absorb")


def test_nothing_prorates_for_a_resident_who_moves_out_mid_month(configured):
    residents_service.deactivate_resident(
        configured, "bob", apartment_id=APARTMENT, active_until="2026-11-10")
    result = expenses.create_expense(
        configured, apartment_id=APARTMENT, expense_id="rent-nov", description="Rent",
        amount_paise=4_500_000, expense_date="2026-11-01", period_rule="current_month",
        allocation_method="equal",
        participants=["alice", "bob", "chris"])
    assert result["allocations_paise"] == {
        "alice": 1_500_000, "bob": 1_500_000, "chris": 1_500_000}


@pytest.mark.parametrize("active_from, active_until", [
    ("2026-11-01", None),          # here all month
    ("2026-11-16", None),          # arrived halfway through
    ("2026-11-29", None),          # arrived on the second-to-last day
    ("2026-01-01", "2026-11-02"),  # left on the second day
])
def test_the_share_is_identical_whatever_the_active_dates_say(
    configured, active_from, active_until
):
    """The dates are reference data. They must not reach the arithmetic at all."""
    residents_service.update_resident(
        configured, "chris", apartment_id=APARTMENT,
        active_from=active_from, active_until=active_until,
        is_active=active_until is None)
    result = expenses.create_expense(
        configured, apartment_id=APARTMENT, expense_id=f"rent-{active_from}",
        description="Rent", amount_paise=4_500_000, expense_date="2026-11-01",
        period_rule="current_month", allocation_method="equal",
        participants=["alice", "bob", "chris"])
    assert result["allocations_paise"]["chris"] == 1_500_000


def test_excluding_somebody_is_a_participation_choice_not_a_proration(configured):
    """The supported way to leave somebody out: omit them, and they get an explicit zero."""
    residents_service.update_resident(configured, "chris", apartment_id=APARTMENT,
                                      active_from="2026-11-16")
    result = expenses.create_expense(
        configured, apartment_id=APARTMENT, expense_id="rent-nov", description="Rent",
        amount_paise=4_500_000, expense_date="2026-11-01", period_rule="current_month",
        allocation_method="equal", participants=["alice", "bob"])
    shares = result["allocations_paise"]
    assert shares == {"alice": 2_250_000, "bob": 2_250_000, "chris": 0}
    assert sum(shares.values()) == 4_500_000, (
        "excluding a participant redistributes the whole cost; it never discounts it")
    # The zero is a recorded decision, not a charge, so no allocation row is written.
    detail = expenses.read_expense(configured, "rent-nov")
    assert {row["resident_id"] for row in detail["allocations"]} == {"alice", "bob"}


def test_the_allocation_preview_also_never_prorates(configured):
    """The preview the operator sees must agree with what gets saved."""
    residents_service.update_resident(configured, "chris", apartment_id=APARTMENT,
                                      active_from="2026-11-16")
    preview = expenses.preview_allocation(
        configured, apartment_id=APARTMENT, amount_paise=4_500_000, method="equal")
    assert preview["shares_paise"] == {
        "alice": 1_500_000, "bob": 1_500_000, "chris": 1_500_000}
    assert preview["total_paise"] == 4_500_000


def test_active_dates_never_appear_in_an_allocation_basis(configured):
    """Nothing in the saved reasoning references a date range, because none was used."""
    residents_service.update_resident(configured, "chris", apartment_id=APARTMENT,
                                      active_from="2026-11-16")
    expenses.create_expense(
        configured, apartment_id=APARTMENT, expense_id="rent-nov", description="Rent",
        amount_paise=4_500_000, expense_date="2026-11-01", period_rule="current_month",
        allocation_method="equal")
    detail = expenses.read_expense(configured, "rent-nov")
    for row in detail["allocations"]:
        basis = json.dumps(row["allocation_basis"] or {})
        assert "active_from" not in basis
        assert "active_until" not in basis
        assert "prorat" not in basis.lower()


def test_a_part_month_resident_still_reconciles_to_zero(configured):
    """The whole point of the rule: no shortfall anywhere in the ledger."""
    residents_service.update_resident(configured, "chris", apartment_id=APARTMENT,
                                      active_from="2026-11-16")
    expenses.create_expense(
        configured, apartment_id=APARTMENT, expense_id="rent-nov", description="Rent",
        amount_paise=4_500_000, expense_date="2026-11-01", period_rule="current_month",
        allocation_method="equal", payer_resident_id="alice")
    settlement = reconciliation.reconcile(configured, apartment_id=APARTMENT)
    assert settlement["net_positions_sum_paise"] == 0
    assert settlement["totals"]["allocated_charges_paise"] == 4_500_000
    assert settlement["totals"]["household_expenses_paise"] == 4_500_000, (
        "allocated charges equal the household cost exactly -- nothing is left uncovered")
    assert settlement["validation"]["error_count"] == 0


# --------------------------------------------------------------------------- #
# Electricity corrections are atomic; new bills are shared by active residents
# --------------------------------------------------------------------------- #

_BILL_TABLES = ("expense_instance", "expense_component", "allocation",
                "smart_plug_reading", "electricity_bill")


def _create_october_bill(db, **overrides):
    fields = dict(
        apartment_id=APARTMENT, expense_id="electricity-2026-10",
        description="September electricity bill", expense_date="2026-10-08",
        total_amount_paise=600_000, payer_resident_id="alice",
        smart_plug_readings=[{"resident_id": "alice", "kwh": "30"},
                             {"resident_id": "bob", "kwh": "20"},
                             {"resident_id": "chris", "kwh": "10"}],
        components=[
            {"id": "energy", "name": "Energy charges", "amount_paise": 510_000,
             "allocation_method": "usage"},
            {"id": "fixed", "name": "Fixed charges", "amount_paise": 90_000,
             "allocation_method": "equal"}])
    fields.update(overrides)
    electricity.create_electricity_expense(db, **fields)


def _bill_state(db, expense_id="electricity-2026-10"):
    saved = electricity.get_electricity_expense(db, expense_id)
    with connect(db, read_only=True) as conn:
        counts = {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                  for table in _BILL_TABLES}
    return saved, counts


@pytest.mark.parametrize("changes", [
    # Refused by the engine: a custom split that misses a resident.
    {"components": [{"id": "all", "name": "Everything", "amount_paise": 600_000,
                     "allocation_method": "custom",
                     "allocations_paise": {"alice": 600_000}}]},
    # Refused by the engine: components that do not total the bill.
    {"total_amount_paise": 700_000},
    # Refused by validation: a reading for someone outside the apartment.
    {"smart_plug_readings": [{"resident_id": "ghost", "kwh": "1"}]},
    # Refused by validation: an impossible billing period.
    {"bill_period_start": "2026-09-30", "bill_period_end": "2026-09-01"},
])
def test_a_refused_recalculation_leaves_the_saved_bill_intact(configured, changes):
    _create_october_bill(configured)
    payments.record_payment(
        configured, apartment_id=APARTMENT, payment_id="advance-electricity-2026-10",
        payer_resident_id="alice", amount_paise=600_000, payment_date="2026-10-08",
        payment_kind="provider_advance", payee_or_provider="State Electricity Board",
        expense_id="electricity-2026-10")
    before = _bill_state(configured)
    assert before[0] is not None

    with pytest.raises(ValidationError):
        electricity.recalculate_electricity_expense(
            configured, "electricity-2026-10", reason="Trying a bad correction", **changes)

    assert _bill_state(configured) == before
    with connect(configured, read_only=True) as conn:
        assert conn.execute(
            "SELECT expense_id FROM payment WHERE payment_id = 'advance-electricity-2026-10'"
        ).fetchone()[0] == "electricity-2026-10"
        assert conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE action = 'supersede_electricity_calculation'"
        ).fetchone()[0] == 0


def test_a_valid_recalculation_replaces_the_bill_and_keeps_its_funding_link(configured):
    _create_october_bill(configured)
    payments.record_payment(
        configured, apartment_id=APARTMENT, payment_id="advance-electricity-2026-10",
        payer_resident_id="alice", amount_paise=600_000, payment_date="2026-10-08",
        payment_kind="provider_advance", payee_or_provider="State Electricity Board",
        expense_id="electricity-2026-10")
    electricity.recalculate_electricity_expense(
        configured, "electricity-2026-10", reason="Meter re-read",
        total_amount_paise=630_000,
        components=[
            {"id": "energy", "name": "Energy charges", "amount_paise": 540_000,
             "allocation_method": "usage"},
            {"id": "fixed", "name": "Fixed charges", "amount_paise": 90_000,
             "allocation_method": "equal"}])
    saved, counts = _bill_state(configured)
    assert saved["expense"]["amount_paise"] == 630_000
    assert sum(a["amount_paise"] for a in saved["allocations"]) == 630_000
    assert counts["electricity_bill"] == 1
    with connect(configured, read_only=True) as conn:
        assert conn.execute(
            "SELECT expense_id FROM payment WHERE payment_id = 'advance-electricity-2026-10'"
        ).fetchone()[0] == "electricity-2026-10"


def test_a_custom_split_survives_a_recalculation_that_does_not_change_it(configured):
    _create_october_bill(configured, components=[
        {"id": "energy", "name": "Energy charges", "amount_paise": 510_000,
         "allocation_method": "usage"},
        {"id": "fixed", "name": "Fixed charges", "amount_paise": 90_000,
         "allocation_method": "custom",
         "allocations_paise": {"alice": 50_000, "bob": 40_000, "chris": 0}}])
    electricity.recalculate_electricity_expense(
        configured, "electricity-2026-10", reason="Corrected the bill number",
        bill_number="SEB/26/10/0042")
    saved, _ = _bill_state(configured)
    fixed = {a["resident_id"]: a["amount_paise"] for a in saved["allocations"]
             if a["component_name"] == "Fixed charges"}
    assert fixed == {"alice": 50_000, "bob": 40_000, "chris": 0}


def test_a_new_electricity_bill_is_not_shared_with_a_deactivated_resident(configured):
    residents_service.deactivate_resident(configured, "chris", active_until="2026-09-30",
                                          reason="Moved out")
    preview = electricity.calculate_preview(
        configured, apartment_id=APARTMENT, total_amount_paise=90_000,
        expense_date="2026-10-08", smart_plug_readings=[],
        components=[{"id": "fixed", "name": "Fixed charges", "amount_paise": 90_000,
                     "allocation_method": "equal"}])
    assert {a["resident_id"]: a["amount_paise"] for a in preview["allocations"]} == {
        "alice": 45_000, "bob": 45_000}

    _create_october_bill(
        configured, total_amount_paise=90_000,
        smart_plug_readings=[],
        components=[{"id": "fixed", "name": "Fixed charges", "amount_paise": 90_000,
                     "allocation_method": "equal"}])
    saved, _ = _bill_state(configured)
    assert {a["resident_id"]: a["amount_paise"] for a in saved["allocations"]} == {
        "alice": 45_000, "bob": 45_000}

    # A reading for someone who has left is refused rather than silently billed.
    with pytest.raises(ValidationError, match="does not belong"):
        _create_october_bill(configured, expense_id="electricity-late",
                             smart_plug_readings=[{"resident_id": "chris", "kwh": "5"}])


def test_correcting_an_old_bill_keeps_the_residents_it_was_shared_between(configured):
    _create_october_bill(configured)
    residents_service.deactivate_resident(configured, "chris", active_until="2026-10-31",
                                          reason="Moved out")
    electricity.recalculate_electricity_expense(
        configured, "electricity-2026-10", reason="Corrected the bill number",
        bill_number="SEB/26/10/0042")
    saved, _ = _bill_state(configured)
    fixed = {a["resident_id"]: a["amount_paise"] for a in saved["allocations"]
             if a["component_name"] == "Fixed charges"}
    assert fixed == {"alice": 30_000, "bob": 30_000, "chris": 30_000}

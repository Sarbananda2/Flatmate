"""Opening balances and the carry-forward working on a statement.

An opening balance is history brought in from before the app, so the tests pin
what must stay true once it is in: the ledger still sums to zero, the two
readings of the accounts still agree, an opening debt is paid off before any
newer charge, and every statement shows how a balance was reached, with one
month's carried-forward figure being the next month's brought-forward one.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from aem.config import get_settings
from aem.errors import ConflictError, NotFoundError, ValidationError
from aem.services import (
    adjustments,
    expenses,
    opening_balances,
    payments,
    reconciliation,
    statements,
)

from .conftest import APARTMENT
from .support import connect


def _set(db, resident_id, amount_paise, as_of_date="2026-08-31", **extra):
    return opening_balances.set_opening_balance(
        db, apartment_id=APARTMENT, resident_id=resident_id, amount_paise=amount_paise,
        as_of_date=as_of_date, **extra)


def _positions(db, **kwargs):
    return reconciliation.reconcile(
        db, apartment_id=APARTMENT, **kwargs)["net_positions_paise_by_account"]


def _account(db, resident_id):
    result = reconciliation.reconcile(db, apartment_id=APARTMENT)
    return next(a for a in result["accounts"] if a["account_id"] == resident_id)


def _dinner(db, expense_date="2026-09-02", expense_id="dinner"):
    """₹3,000 from the pool split three ways: each resident owes ₹1,000."""
    expenses.create_expense(
        db, apartment_id=APARTMENT, expense_id=expense_id, description="Dinner",
        amount_paise=300_000, expense_date=expense_date, period_rule="transaction_date",
        allocation_method="equal")


def _movement(db, month):
    statement_id = statements.create_monthly_statement(
        db, apartment_id=APARTMENT, settlement_month=month)
    statements.attach_eligible_expenses(db, statement_id)
    snapshot = statements.statement_snapshot(db, statement_id)
    return {row["resident_id"]: row for row in snapshot["carry_forward_breakdown"]}


def _closes(row):
    return (row["brought_forward_paise"] - row["statement_charges_paise"]
            - row["unattached_charges_paise"] + row["payments_applied_paise"]
            + row["provider_advances_paise"] + row["adjustments_paise"]) == row["carried_forward_paise"]


# ------------------------------------------------------------------- the ledger --

def test_an_opening_debt_is_owed_to_the_pool(apartment):
    _set(apartment, "alice", -125_000, note="August groceries")
    assert _positions(apartment) == {"alice": -125_000, "bob": 0, "chris": 0,
                                     "__pool__": 125_000}
    account = _account(apartment, "alice")
    assert account["opening_balance_paise"] == -125_000
    assert account["outstanding_charges_paise"] == 125_000
    assert account["opening_debt_outstanding_paise"] == 125_000
    # It is not a household expense and not a share of one.
    result = reconciliation.reconcile(apartment, apartment_id=APARTMENT)
    assert result["totals"]["household_expenses_paise"] == 0
    assert account["allocated_charges_paise"] == 0


def test_an_opening_credit_is_owed_by_the_pool(apartment):
    _set(apartment, "bob", 80_000)
    assert _positions(apartment)["bob"] == 80_000
    assert _positions(apartment)["__pool__"] == -80_000
    account = _account(apartment, "bob")
    assert account["outstanding_charges_paise"] == 0
    assert account["opening_debt_outstanding_paise"] == 0


def test_opening_balances_that_net_to_zero_leave_the_pool_alone(apartment):
    _set(apartment, "alice", -50_000)
    _set(apartment, "bob", 50_000)
    result = reconciliation.reconcile(apartment, apartment_id=APARTMENT)
    assert result["net_positions_paise_by_account"]["__pool__"] == 0
    assert result["totals"]["opening_balances_paise"] == 0
    assert [(t["from_account"], t["to_account"], t["amount_paise"])
            for t in result["transfers"]] == [("alice", "bob", 50_000)]


def test_the_ledger_carries_one_entry_per_side(apartment):
    _set(apartment, "alice", -125_000)
    entries = reconciliation.ledger_entries(
        apartment, apartment_id=APARTMENT, kind="opening_balance")
    assert entries["total"] == 2 and entries["sum_paise"] == 0
    assert {(e["account_id"], e["amount_paise"], e["date"]) for e in entries["items"]} == {
        ("alice", -125_000, "2026-08-31"), ("__pool__", 125_000, "2026-08-31")}


def test_a_position_dated_before_the_opening_balance_does_not_include_it(apartment):
    _set(apartment, "alice", -125_000, as_of_date="2026-08-31")
    assert _positions(apartment, as_of_date="2026-08-30")["alice"] == 0
    assert _positions(apartment, as_of_date="2026-08-31")["alice"] == -125_000


# ------------------------------------------------------------------- settlement --

def test_a_payment_clears_the_opening_debt_before_any_newer_charge(apartment):
    _set(apartment, "alice", -125_000)
    _dinner(apartment)
    payments.record_payment(
        apartment, apartment_id=APARTMENT, payment_id="alice-1", payer_resident_id="alice",
        amount_paise=150_000, payment_date="2026-09-10")

    balance = opening_balances.read_opening_balance(
        apartment, apartment_id=APARTMENT, resident_id="alice")
    assert (balance["settled_paise"], balance["outstanding_paise"]) == (125_000, 0)
    account = _account(apartment, "alice")
    assert account["opening_debt_outstanding_paise"] == 0
    assert account["outstanding_charges_paise"] == 75_000  # ₹250 went on to the dinner
    assert account["net_position_paise"] == -75_000


def test_a_part_payment_leaves_the_rest_of_the_opening_debt_open(apartment):
    _set(apartment, "alice", -125_000)
    payments.record_payment(
        apartment, apartment_id=APARTMENT, payment_id="alice-1", payer_resident_id="alice",
        amount_paise=25_000, payment_date="2026-09-10")
    account = _account(apartment, "alice")
    assert account["opening_debt_outstanding_paise"] == 100_000
    assert account["settled_paise"] == 25_000
    assert account["unapplied_credit_paise"] == 0


def test_reversing_the_payment_reopens_the_opening_debt(apartment):
    _set(apartment, "alice", -125_000)
    payments.record_payment(
        apartment, apartment_id=APARTMENT, payment_id="alice-1", payer_resident_id="alice",
        amount_paise=125_000, payment_date="2026-09-10")
    assert _positions(apartment)["alice"] == 0

    payments.reverse_payment(apartment, "alice-1", reason="bounced")
    account = _account(apartment, "alice")
    assert account["net_position_paise"] == -125_000
    assert account["opening_debt_outstanding_paise"] == 125_000


def test_shrinking_a_debt_below_what_was_paid_releases_the_payment(apartment):
    _set(apartment, "alice", -125_000)
    payments.record_payment(
        apartment, apartment_id=APARTMENT, payment_id="alice-1", payer_resident_id="alice",
        amount_paise=100_000, payment_date="2026-09-10")

    result = _set(apartment, "alice", -40_000)
    assert result["released_paise"] == 100_000
    account = _account(apartment, "alice")
    assert account["unapplied_credit_paise"] == 100_000
    assert account["opening_debt_outstanding_paise"] == 40_000
    assert account["net_position_paise"] == 60_000

    payments.reapply_unapplied_credit(apartment, apartment_id=APARTMENT, resident_id="alice")
    account = _account(apartment, "alice")
    assert account["opening_debt_outstanding_paise"] == 0
    assert account["unapplied_credit_paise"] == 60_000


def test_clearing_an_opening_balance_removes_it_from_the_ledger(apartment):
    _set(apartment, "alice", -125_000)
    payments.record_payment(
        apartment, apartment_id=APARTMENT, payment_id="alice-1", payer_resident_id="alice",
        amount_paise=50_000, payment_date="2026-09-10")
    cleared = opening_balances.clear_opening_balance(
        apartment, apartment_id=APARTMENT, resident_id="alice")
    assert cleared["released_paise"] == 50_000
    assert _positions(apartment)["alice"] == 50_000
    with pytest.raises(NotFoundError):
        opening_balances.clear_opening_balance(
            apartment, apartment_id=APARTMENT, resident_id="alice")


def test_a_zero_opening_balance_is_refused(apartment):
    with pytest.raises(ValidationError):
        _set(apartment, "alice", 0)


def test_opening_balances_lock_once_a_statement_is_finalized(apartment):
    _set(apartment, "alice", -125_000)
    statement_id = statements.create_monthly_statement(
        apartment, apartment_id=APARTMENT, settlement_month="2026-09")
    statements.finalize_statement(apartment, statement_id=statement_id,
                                  finalized_by="operator")

    listing = opening_balances.list_opening_balances(apartment, APARTMENT)
    assert (listing["is_editable"], listing["locked_by_month"]) == (False, "2026-09")
    with pytest.raises(ConflictError):
        _set(apartment, "alice", -100_000)
    with pytest.raises(ConflictError):
        opening_balances.clear_opening_balance(
            apartment, apartment_id=APARTMENT, resident_id="alice")
    assert _positions(apartment)["alice"] == -125_000


def test_every_change_is_audited(apartment):
    _set(apartment, "alice", -125_000)
    _set(apartment, "alice", -100_000)
    opening_balances.clear_opening_balance(
        apartment, apartment_id=APARTMENT, resident_id="alice")
    with connect(apartment, read_only=True) as conn:
        actions = [row[0] for row in conn.execute(
            "SELECT action FROM audit_log WHERE entity_type='opening_balance' ORDER BY row_seq")]
    assert actions == ["create", "update", "delete"]


# ---------------------------------------------------------------- carry forward --

def test_the_working_on_a_statement_closes_for_every_resident(apartment):
    _set(apartment, "alice", -125_000)
    _set(apartment, "bob", 80_000)
    _dinner(apartment)
    payments.record_payment(
        apartment, apartment_id=APARTMENT, payment_id="alice-1", payer_resident_id="alice",
        amount_paise=150_000, payment_date="2026-09-10")
    adjustments.record_adjustment(
        apartment, apartment_id=APARTMENT, resident_id="chris", amount_paise=-5_000,
        reason="Broken glass", created_by="operator", effective_date="2026-09-15")

    rows = _movement(apartment, "2026-09")

    assert rows["alice"] == {
        "resident_id": "alice", "display_name": "Alice", "opening_balance_paise": -125_000,
        "brought_forward_paise": -125_000, "statement_charges_paise": 100_000,
        "unattached_charges_paise": 0, "payments_applied_paise": 150_000,
        "provider_advances_paise": 0, "adjustments_paise": 0,
        "carried_forward_paise": -75_000,
        "formula_explanation": "-₹1,250.00 (brought forward) - ₹1,000.00 (charges) "
                               "+ ₹1,500.00 (payments) = -₹750.00 (carried forward)"}
    assert rows["chris"]["formula_explanation"] == (
        "₹0.00 (brought forward) - ₹1,000.00 (charges) - ₹50.00 (adjustments) "
        "= -₹1,050.00 (carried forward)")
    assert (rows["bob"]["brought_forward_paise"], rows["bob"]["carried_forward_paise"]) == (
        80_000, -20_000)
    assert (rows["chris"]["adjustments_paise"], rows["chris"]["carried_forward_paise"]) == (
        -5_000, -105_000)
    assert all(_closes(row) for row in rows.values())
    positions = _positions(apartment)
    assert {rid: row["carried_forward_paise"] for rid, row in rows.items()} == {
        rid: positions[rid] for rid in rows}


def test_what_one_month_carries_forward_the_next_brings_forward(apartment):
    _set(apartment, "alice", -125_000)
    _dinner(apartment, "2026-09-02", "dinner-sep")
    _dinner(apartment, "2026-10-02", "dinner-oct")
    payments.record_payment(
        apartment, apartment_id=APARTMENT, payment_id="alice-oct", payer_resident_id="alice",
        amount_paise=200_000, payment_date="2026-10-05")

    september = _movement(apartment, "2026-09")
    october = _movement(apartment, "2026-10")

    assert september["alice"]["carried_forward_paise"] == -225_000
    for rid in ("alice", "bob", "chris"):
        assert october[rid]["brought_forward_paise"] == september[rid]["carried_forward_paise"]
        assert _closes(september[rid]) and _closes(october[rid])
    # October's payment is not part of September's working.
    assert september["alice"]["payments_applied_paise"] == 0
    assert october["alice"]["carried_forward_paise"] == -125_000


def test_an_opening_balance_dated_inside_the_month_is_brought_forward_into_it(apartment):
    _set(apartment, "alice", -125_000, as_of_date="2026-09-01")
    row = _movement(apartment, "2026-09")["alice"]
    assert row["brought_forward_paise"] == -125_000 and _closes(row)
    assert row["payments_applied_paise"] == 0 and row["adjustments_paise"] == 0


def test_an_opening_balance_dated_after_the_month_is_not_in_its_working(apartment):
    _set(apartment, "alice", -125_000, as_of_date="2026-10-01")
    row = _movement(apartment, "2026-09")["alice"]
    assert (row["brought_forward_paise"], row["carried_forward_paise"]) == (0, 0)


def test_a_charge_left_off_the_statement_is_shown_apart(household):
    """October's geyser repair settles in October but was never attached."""
    snapshot = statements.statement_snapshot(household, "stmt-2026-10")
    rows = snapshot["carry_forward_breakdown"]
    assert snapshot["carry_forward_totals"]["unattached_charges_paise"] == 250_000
    assert all(_closes(row) for row in rows)
    assert _closes(snapshot["carry_forward_totals"])
    assert "(charges not on this statement)" in rows[0]["formula_explanation"]
    positions = snapshot["net_positions_paise_by_account"]
    for row in rows:
        assert row["carried_forward_paise"] == positions[row["resident_id"]]


def test_money_paid_to_a_provider_is_an_advance_not_a_pool_payment(household):
    """Alice paid September's groceries herself; Bob's advance paid the electricity."""
    with household.read() as uow:
        movement = reconciliation.carry_forward(uow, APARTMENT, "2026-09")
    rows = {row["resident_id"]: row for row in movement["residents"]}
    assert (rows["alice"]["provider_advances_paise"],
            rows["alice"]["payments_applied_paise"]) == (333_401, 0)
    assert (rows["bob"]["provider_advances_paise"],
            rows["bob"]["payments_applied_paise"]) == (735_000, 50_000)
    assert (rows["chris"]["provider_advances_paise"],
            rows["chris"]["payments_applied_paise"]) == (0, 2_500_000)
    assert all(row["brought_forward_paise"] == 0 for row in rows.values())


def test_a_sealed_statement_keeps_the_working_it_was_sealed_with(apartment):
    _set(apartment, "alice", -125_000)
    _dinner(apartment)
    statement_id = statements.create_monthly_statement(
        apartment, apartment_id=APARTMENT, settlement_month="2026-09")
    statements.attach_eligible_expenses(apartment, statement_id)
    statements.finalize_statement(apartment, statement_id=statement_id,
                                  finalized_by="operator")
    payments.record_payment(
        apartment, apartment_id=APARTMENT, payment_id="alice-late", payer_resident_id="alice",
        amount_paise=225_000, payment_date="2026-09-28")

    sealed = statements.read_finalized_snapshot(apartment, statement_id)
    alice = next(r for r in sealed["snapshot"]["carry_forward_breakdown"]
                 if r["resident_id"] == "alice")
    assert alice["carried_forward_paise"] == -225_000
    assert sealed["hash_matches"] is True


# -------------------------------------------------------------------------- API --

@pytest.fixture()
def api(apartment):
    from fastapi.testclient import TestClient

    from aem.main import create_app

    settings = replace(get_settings(),
                       auto_close_expired_statements=False)
    with TestClient(create_app(settings, database=apartment)) as test_client:
        yield test_client


def test_the_api_sets_lists_and_clears_an_opening_balance(api):
    assert api.get("/api/opening-balances").json()["items"] == []

    created = api.post("/api/opening-balances", json={
        "resident_id": "alice", "amount_paise": -125_000, "as_of_date": "2026-08-31",
        "note": "August groceries"})
    assert created.status_code == 200
    assert created.json()["direction"] == "owed_by_resident"
    assert created.json()["outstanding_paise"] == 125_000

    replaced = api.post("/api/opening-balances", json={
        "resident_id": "alice", "amount_paise": 40_000, "as_of_date": "2026-08-31"})
    assert replaced.json()["direction"] == "owed_to_resident"

    listing = api.get("/api/opening-balances").json()
    assert listing["total"] == 1 and listing["is_editable"] is True
    assert listing["items"][0]["display_name"] == "Alice"
    assert listing["owed_to_residents_paise"] == 40_000

    assert api.delete("/api/opening-balances/alice").status_code == 200
    assert api.get("/api/opening-balances").json()["total"] == 0
    assert api.delete("/api/opening-balances/alice").status_code == 404


@pytest.mark.parametrize("payload", [
    {"resident_id": "alice", "amount_paise": 0, "as_of_date": "2026-08-31"},
    {"resident_id": "alice", "amount_paise": 12.5, "as_of_date": "2026-08-31"},
    {"resident_id": "alice", "amount_paise": -100, "as_of_date": "31-08-2026"},
    {"resident_id": "alice", "amount_paise": -100},
])
def test_the_api_refuses_a_malformed_opening_balance(api, payload):
    assert api.post("/api/opening-balances", json=payload).status_code == 422
    assert api.get("/api/opening-balances").json()["total"] == 0


def test_the_api_refuses_an_unknown_resident(api):
    response = api.post("/api/opening-balances", json={
        "resident_id": "nobody", "amount_paise": -100, "as_of_date": "2026-08-31"})
    assert response.status_code == 404


def test_the_api_reports_the_lock_as_a_conflict(api, apartment):
    statement_id = statements.create_monthly_statement(
        apartment, apartment_id=APARTMENT, settlement_month="2026-09")
    statements.finalize_statement(apartment, statement_id=statement_id,
                                  finalized_by="operator")
    response = api.post("/api/opening-balances", json={
        "resident_id": "alice", "amount_paise": -100, "as_of_date": "2026-08-31"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "opening_balance_locked"


def test_the_statement_and_resident_endpoints_expose_the_new_figures(api):
    api.post("/api/opening-balances", json={
        "resident_id": "alice", "amount_paise": -125_000, "as_of_date": "2026-08-31"})
    created = api.post("/api/statements", json={"settlement_month": "2026-09"})
    statement_id = created.json()["statement_id"]

    preview = api.get(f"/api/statements/{statement_id}/preview").json()
    alice = next(r for r in preview["carry_forward_breakdown"]
                 if r["resident_id"] == "alice")
    assert alice["brought_forward_paise"] == alice["carried_forward_paise"] == -125_000

    resident = api.get("/api/residents/alice").json()
    assert resident["account"]["opening_balance_paise"] == -125_000
    assert resident["charges"][0]["is_opening_balance"] is True

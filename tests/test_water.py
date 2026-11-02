"""The water supply: a fixed monthly contract, split equally and paid through the kitty.

These tests pin what reaches the ledger: the rate split equally to the paisa,
the two steps of paying it -- into the kitty, then out to the supplier -- the
kitty paying back whoever paid the supplier beyond their share, and a month
following the contract and the flat -- one gone by too -- until its statement
is finalized.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from aem.config import get_settings
from aem.errors import ConflictError, ValidationError
from aem.services import (
    definitions,
    expenses,
    reconciliation,
    residents,
    statements,
    validation,
    water,
)

from .conftest import APARTMENT
from .support import connect, sign_in

MONTH = "2026-11"


@pytest.fixture()
def clock(monkeypatch):
    """It is 10 November 2026, the day the water supply is set up. A test moves
    the clock on by changing ``now``."""
    now = {"now": "2026-11-10T09:00:00+05:30"}
    monkeypatch.setattr(water, "now_timestamp", lambda value=None: now["now"])
    monkeypatch.setattr(water, "_today", lambda: now["now"][:10])
    return now


def _rows(db, sql, params=()):
    with connect(db, read_only=True) as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _setup(db, **payload):
    return water.setup_water(db, APARTMENT, {"monthly_rate_paise": 60_000,
                                             "billing_day_of_month": 10, **payload})


def _month(db, month=MONTH):
    return water.get_overview(db, APARTMENT, month)["month"]


def _shares(month):
    return {row["resident_id"]: row["total_paise"] for row in month["shares"]}


def _standing(db, month=MONTH):
    return {row["resident_id"]: (row["total_paise"], row["paid_paise"],
                                 row["outstanding_paise"], row["status"])
            for row in _month(db, month)["shares"]}


def _pay_in(db, resident, paise, month=MONTH):
    return water.record_payment(db, APARTMENT, {
        "settlement_month": month, "resident_id": resident, "amount_paise": paise,
        "payment_date": f"{month}-12", "payment_mode": "upi", "reference": "UPI-1"})["month"]


def _payout(db, mode="pool", month=MONTH, **details):
    return water.disburse_payout(db, APARTMENT, {
        "settlement_month": month, "disbursement_mode": mode, "reference": "UTR-77",
        "disbursed_date": f"{month}-10", **details})["month"]


def _owed(db, month=MONTH):
    return [(row["recipient_resident_id"], row["net_reimbursement_owed_paise"],
             row["pending_reimbursement_paise"], row["can_reimburse"])
            for row in _month(db, month)["reimbursements"]]


def _net(db):
    return reconciliation.reconcile(db, apartment_id=APARTMENT)["net_positions_paise_by_account"]


# ----------------------------------------------------------------- setup --

def test_the_contract_is_set_up_with_its_fixed_rate(apartment, clock):
    overview = _setup(apartment, consumer_id="SOC-402", pipeline_notes="Filtration line")
    assert (overview["service"]["service_type"], overview["service"]["display_name"]) == (
        "water_tanker", "Water Supply")
    config = overview["config"]
    assert (config["provider_name"], config["monthly_rate_paise"], config["consumer_id"],
            config["billing_day_of_month"], config["pipeline_notes"]) == (
        "Society Water Supply", 60_000, "SOC-402", 10, "Filtration line")
    definition = _rows(apartment, "SELECT * FROM expense_definition WHERE expense_definition_id=%s",
                       (config["expense_definition_id"],))[0]
    assert (definition["name"], definition["category"], definition["is_recurring"]) == (
        "Water", "Utilities", 0)
    # Setting it up again changes only what is sent, and keeps the one supply.
    again = _setup(apartment, provider_name="Capetown Society")
    assert (again["service"]["service_id"], again["config"]["provider_name"],
            again["config"]["consumer_id"]) == (overview["service"]["service_id"],
                                                "Capetown Society", "SOC-402")
    assert len(_rows(apartment, "SELECT 1 FROM household_service")) == 1
    with pytest.raises(ValidationError):
        _setup(apartment, billing_day_of_month=32)


def test_the_households_own_water_definition_is_used(apartment, clock):
    definitions.create_expense_definition(
        apartment, apartment_id=APARTMENT, definition_id="water", name="Water",
        category="Utilities", period_rule="current_month", recurrence="monthly")
    assert _setup(apartment)["config"]["expense_definition_id"] == "water"
    assert len(_rows(apartment, "SELECT 1 FROM expense_definition "
                                "WHERE lower(name)='water'")) == 1


# --------------------------------------------------------- the month's bill --

def test_the_rate_is_split_equally_between_the_flatmates(apartment, clock):
    _setup(apartment)
    month = _month(apartment)
    assert (month["bill_state"], month["total_paise"], month["participant_count"],
            month["due_date"]) == ("live", 60_000, 3, "2026-11-10")
    assert _shares(month) == {"alice": 20_000, "bob": 20_000, "chris": 20_000}
    assert month["expense"]["amount_paise"] == 60_000
    assert _standing(apartment)["alice"] == (20_000, 0, 20_000, "unpaid")
    # A rate that does not divide: the paise over go one each, in resident order.
    _setup(apartment, monthly_rate_paise=60_001)
    assert _shares(_month(apartment)) == {"alice": 20_001, "bob": 20_000, "chris": 20_000}
    assert validation.validate(apartment, APARTMENT)["issues"] == []


def test_a_month_ahead_is_a_preview_and_nothing_is_billed_without_a_rate(apartment, clock):
    _setup(apartment, monthly_rate_paise=0)
    assert (_month(apartment)["bill_state"], _month(apartment)["expense"]) == ("not_billed", None)
    _setup(apartment, monthly_rate_paise=60_000)
    december = _month(apartment, "2026-12")
    assert (december["bill_state"], december["expense"], _shares(december)) == (
        "upcoming", None, {"alice": 20_000, "bob": 20_000, "chris": 20_000})


# ------------------------------------------------------- paying the bill --

def test_shares_are_paid_into_the_kitty_and_the_society_paid_from_it(apartment, clock):
    _setup(apartment)
    month = _pay_in(apartment, "alice", 20_000)
    assert (month["collected_paise"], month["collected_count"]) == (20_000, 1)
    assert _standing(apartment)["alice"] == (20_000, 20_000, 0, "paid")
    with pytest.raises(ConflictError) as paid:
        _pay_in(apartment, "alice", 20_000)
    assert paid.value.code == "water_share_already_paid"

    # The society is paid what the kitty holds now, and the rest later.
    month = _payout(apartment, amount_paise=20_000)
    assert (month["payout_status"], month["disbursed_paise"],
            month["remaining_disbursement_paise"]) == ("partially_disbursed", 20_000, 40_000)
    with pytest.raises(ValidationError) as too_much:
        _payout(apartment, amount_paise=40_001)
    assert too_much.value.code == "tranche_exceeds_remaining"
    _pay_in(apartment, "bob", 20_000)
    _pay_in(apartment, "chris", 20_000)
    month = _payout(apartment)
    assert (month["payout_status"], month["remaining_disbursement_paise"],
            [row["amount_paise"] for row in month["disbursements"]]) == (
        "disbursed", 0, [20_000, 40_000])
    with pytest.raises(ConflictError) as done:
        _payout(apartment)
    assert done.value.code == "water_already_disbursed"
    assert validation.validate(apartment, APARTMENT)["issues"] == []
    assert sum(_net(apartment).values()) == 0


def test_a_resident_who_pays_the_society_is_paid_back_the_rest_by_the_kitty(apartment, clock):
    _setup(apartment)
    # Bob pays the whole ₹600 himself: his ₹200 share through the kitty, and ₹400 beyond it.
    month = _payout(apartment, mode="resident_advance", funding_resident_id="bob")
    assert sorted((row["disbursed_by"], row["amount_paise"]) for row in month["disbursements"]) == [
        ("pool", 20_000), ("resident_advance", 40_000)]
    assert (month["payout_status"], _standing(apartment)["bob"]) == (
        "disbursed", (20_000, 20_000, 0, "paid"))
    assert _owed(apartment) == [("bob", 40_000, 40_000, False)]

    # The kitty pays him back what Alice and Chris pay in.
    _pay_in(apartment, "alice", 20_000)
    _pay_in(apartment, "chris", 20_000)
    assert _owed(apartment) == [("bob", 40_000, 40_000, True)]
    water.reimburse_from_kitty(apartment, APARTMENT, {
        "settlement_month": MONTH, "recipient_resident_id": "bob", "amount_paise": 40_000,
        "payment_date": "2026-11-15"})
    assert _owed(apartment) == [("bob", 40_000, 0, False)]
    with pytest.raises(ValidationError) as more:
        water.reimburse_from_kitty(apartment, APARTMENT, {
            "settlement_month": MONTH, "recipient_resident_id": "bob", "amount_paise": 1,
            "payment_date": "2026-11-15"})
    assert more.value.code == "exceeds_pending_reimbursement"
    assert validation.validate(apartment, APARTMENT)["issues"] == []
    assert sum(_net(apartment).values()) == 0


# ------------------------------------------------------- the open month --

def test_the_open_month_follows_a_new_flatmate_and_a_new_rate(apartment, clock):
    _setup(apartment)
    _pay_in(apartment, "alice", 20_000)
    # Dave moves in: four share the month, and Alice's ₹50 beyond her new share is owed back.
    residents.create_resident(apartment, apartment_id=APARTMENT, resident_id="dave",
                              display_name="Dave", active_from="2026-11-12")
    month = _month(apartment)
    assert _shares(month) == {"alice": 15_000, "bob": 15_000, "chris": 15_000, "dave": 15_000}
    assert _standing(apartment)["alice"] == (15_000, 15_000, 0, "paid")
    assert _owed(apartment) == [("alice", 5_000, 5_000, True)]

    # Chris moves out: nothing prorates, so the month he was in is still his in full.
    residents.deactivate_resident(apartment, "chris", active_until="2026-11-20",
                                  apartment_id=APARTMENT)
    assert _shares(_month(apartment)) == {"alice": 15_000, "bob": 15_000, "chris": 15_000,
                                          "dave": 15_000}

    # A new rate: ₹800, ₹200 each, and Alice's share is paid again.
    _setup(apartment, monthly_rate_paise=80_000)
    assert _shares(_month(apartment)) == {"alice": 20_000, "bob": 20_000, "chris": 20_000,
                                          "dave": 20_000}
    assert _standing(apartment)["alice"] == (20_000, 20_000, 0, "paid")
    assert _owed(apartment) == []
    # Chris has moved out still owing his share, which the checks say.
    assert [issue["code"] for issue in validation.validate(apartment, APARTMENT)["issues"]] == [
        "inactive_resident_open_balance"]
    assert sum(_net(apartment).values()) == 0


def test_a_month_gone_by_follows_the_contract_until_its_statement_is_finalized(apartment,
                                                                               clock):
    _setup(apartment)
    _pay_in(apartment, "alice", 20_000)

    # December: November is still open, so a new rate reaches it too, and
    # Alice's ₹200 now stands against a ₹250 share.
    clock["now"] = "2026-12-03T09:00:00+05:30"
    _setup(apartment, monthly_rate_paise=75_000)
    assert (_month(apartment)["bill_state"], _shares(_month(apartment))) == (
        "live", {"alice": 25_000, "bob": 25_000, "chris": 25_000})
    assert _standing(apartment)["alice"] == (25_000, 20_000, 5_000, "partial")
    # A payment for it is still taken.
    _pay_in(apartment, "alice", 5_000)

    # Its statement finalized, it is its snapshot.
    statement = statements.create_monthly_statement(apartment, apartment_id=APARTMENT,
                                                    settlement_month=MONTH)
    statements.finalize_statement(apartment, statement_id=statement, finalized_by="operator",
                                  require_clean=False)
    before = _month(apartment)
    assert (before["bill_state"], before["total_paise"]) == ("frozen", 75_000)
    _setup(apartment, monthly_rate_paise=90_000)
    residents.create_resident(apartment, apartment_id=APARTMENT, resident_id="dave",
                              display_name="Dave", active_from="2026-12-01")
    november = _month(apartment)
    assert (november["bill_state"], november["total_paise"], _shares(november)) == (
        "frozen", 75_000, {"alice": 25_000, "bob": 25_000, "chris": 25_000})
    assert november["shares"] == before["shares"]
    # December bills the new rate between the four.
    assert _shares(_month(apartment, "2026-12")) == {
        "alice": 22_500, "bob": 22_500, "chris": 22_500, "dave": 22_500}
    assert validation.validate(apartment, APARTMENT)["issues"] == []


def test_a_water_expense_entered_by_hand_is_kept_as_it_was(apartment, clock):
    definitions.create_expense_definition(
        apartment, apartment_id=APARTMENT, definition_id="water", name="Water",
        category="Utilities", period_rule="current_month", recurrence="none",
        is_recurring=False)
    # A tanker bill for November, already entered.
    expenses.create_expense(apartment, apartment_id=APARTMENT, expense_id="tanker-nov",
                            description="Water tanker", amount_paise=4_500,
                            expense_date="2026-11-05",
                            allocations_paise={"alice": 1_500, "bob": 1_500, "chris": 1_500},
                            expense_definition_id="water", settlement_period=MONTH)
    _setup(apartment)
    month = _month(apartment)
    assert (month["expense"]["expense_id"], month["total_paise"]) == ("tanker-nov", 4_500)
    assert _rows(apartment, "SELECT amount_paise FROM expense_instance "
                            "WHERE expense_id='tanker-nov'") == [{"amount_paise": 4_500}]


# --------------------------------------------------------------- the API --

@pytest.fixture()
def api(apartment):
    from fastapi.testclient import TestClient

    from aem.main import create_app

    settings = replace(get_settings(), auto_close_expired_statements=False)
    with TestClient(create_app(settings, database=apartment)) as client:
        sign_in(client)
        yield client


def test_the_water_supply_over_http(api, clock):
    base = "/api/services/water"
    assert api.get(base, params={"month": MONTH}).json()["service"] is None
    setup = api.post(f"{base}/setup", params={"month": MONTH},
                     json={"monthly_rate_paise": 60_000, "billing_day_of_month": 10})
    assert setup.status_code == 200, setup.text
    assert _shares(setup.json()["month"]) == {"alice": 20_000, "bob": 20_000, "chris": 20_000}
    paid = api.post(f"{base}/record-payment", json={
        "settlement_month": MONTH, "resident_id": "alice", "amount_paise": 20_000,
        "payment_date": "2026-11-12", "payment_mode": "upi", "reference": "UPI-9"})
    assert paid.status_code == 201, paid.text
    assert paid.json()["month"]["collected_paise"] == 20_000
    payout = api.post(f"{base}/disburse-payout", json={
        "settlement_month": MONTH, "disbursement_mode": "pool", "reference": "UTR-1",
        "disbursed_date": "2026-11-12", "amount_paise": 20_000})
    assert payout.status_code == 201, payout.text
    assert payout.json()["month"]["payout_status"] == "partially_disbursed"
    assert api.post(f"{base}/setup", json={"billing_day_of_month": 32}).status_code == 422
    assert api.post(f"{base}/disburse-payout", json={
        "settlement_month": MONTH, "disbursement_mode": "pool",
        "disbursed_date": "2026-11-12"}).status_code == 422

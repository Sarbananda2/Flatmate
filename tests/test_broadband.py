"""Broadband & WiFi: a fixed monthly plan, split equally and paid through the kitty.

These tests pin what reaches the ledger: the plan's rate split equally to the
paisa, the two steps of paying it -- into the kitty, then out to the ISP -- the
kitty paying back whoever paid the ISP beyond their share, a month following
the plan and the flat until its statement is finalized, and a payment or payout
recorded by mistake deleted while its month is open.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from aem.config import get_settings
from aem.errors import ConflictError, NotFoundError, ValidationError
from aem.services import (
    broadband,
    definitions,
    expenses,
    payments,
    reconciliation,
    residents,
    statements,
    validation,
)

from .conftest import APARTMENT
from .support import connect, sign_in

MONTH = "2026-11"
#: ₹1,179 a month: ₹393 each for Alice, Bob and Chris.
RATE = 117_900
SHARE = 39_300


@pytest.fixture()
def clock(monkeypatch):
    """It is 3 November 2026, the day broadband is set up. A test moves the
    clock on by changing ``now``."""
    now = {"now": "2026-11-03T09:00:00+05:30"}
    monkeypatch.setattr(broadband, "now_timestamp", lambda value=None: now["now"])
    monkeypatch.setattr(broadband, "_today", lambda: now["now"][:10])
    return now


def _rows(db, sql, params=()):
    with connect(db, read_only=True) as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _setup(db, **payload):
    return broadband.setup_broadband(db, APARTMENT, {"monthly_rate_paise": RATE,
                                                     "billing_day_of_month": 5, **payload})


def _month(db, month=MONTH):
    return broadband.get_overview(db, APARTMENT, month)["month"]


def _shares(month):
    return {row["resident_id"]: row["total_paise"] for row in month["shares"]}


def _share(month, resident):
    return next(row for row in month["shares"] if row["resident_id"] == resident)


def _standing(db, month=MONTH):
    return {row["resident_id"]: (row["total_paise"], row["paid_paise"],
                                 row["outstanding_paise"], row["status"])
            for row in _month(db, month)["shares"]}


def _pay_in(db, resident, paise, month=MONTH):
    return broadband.record_payment(db, APARTMENT, {
        "settlement_month": month, "resident_id": resident, "amount_paise": paise,
        "payment_date": f"{month}-04", "payment_mode": "upi", "reference": "UPI-1"})["month"]


def _payout(db, mode="pool", month=MONTH, **details):
    return broadband.disburse_payout(db, APARTMENT, {
        "settlement_month": month, "disbursement_mode": mode, "reference": "UTR-77",
        "disbursed_date": f"{month}-05", **details})["month"]


def _reimburse(db, resident, paise, month=MONTH):
    return broadband.reimburse_from_kitty(db, APARTMENT, {
        "settlement_month": month, "recipient_resident_id": resident, "amount_paise": paise,
        "payment_date": f"{month}-08"})["month"]


def _owed(db, month=MONTH):
    return [(row["recipient_resident_id"], row["net_reimbursement_owed_paise"],
             row["pending_reimbursement_paise"], row["can_reimburse"])
            for row in _month(db, month)["reimbursements"]]


def _advance(month):
    return next(row for row in month["disbursements"] if row["disbursed_by"] == "resident_advance")


def _net(db):
    return reconciliation.reconcile(db, apartment_id=APARTMENT)["net_positions_paise_by_account"]


# ----------------------------------------------------------------- setup --

def test_the_plan_is_set_up_with_its_connection_details(apartment, clock):
    overview = _setup(apartment, account_number="AX-2048-7731", speed_mbps=200,
                      wifi_ssid="Capetown-1805", notes="Router in the living room; Disney+ bundled")
    assert (overview["service"]["service_type"], overview["service"]["display_name"]) == (
        "broadband", "Broadband & WiFi")
    config = overview["config"]
    assert (config["provider_name"], config["account_number"], config["monthly_rate_paise"],
            config["speed_mbps"], config["billing_day_of_month"], config["wifi_ssid"],
            config["notes"]) == (
        "Airtel Xstream Fiber", "AX-2048-7731", RATE, 200, 5, "Capetown-1805",
        "Router in the living room; Disney+ bundled")
    assert overview["next_billing_date"] == "2026-11-05"
    definition = _rows(apartment, "SELECT * FROM expense_definition WHERE expense_definition_id=%s",
                       (config["expense_definition_id"],))[0]
    assert (definition["expense_definition_id"], definition["name"], definition["category"],
            definition["is_recurring"]) == ("broadband", "Broadband", "Utilities", 0)

    # Setting it up again changes only what is sent, and keeps the one connection;
    # a detail sent as null is cleared.
    again = _setup(apartment, provider_name="ACT Fibernet", speed_mbps=300, wifi_ssid=None)
    assert (again["service"]["service_id"], again["config"]["provider_name"],
            again["config"]["speed_mbps"], again["config"]["wifi_ssid"],
            again["config"]["account_number"]) == (
        overview["service"]["service_id"], "ACT Fibernet", 300, None, "AX-2048-7731")
    assert len(_rows(apartment, "SELECT 1 FROM household_service")) == 1
    # The month's bill names the ISP it is paid to.
    assert _month(apartment)["expense"]["description"] == "Broadband & WiFi for November 2026"
    assert _rows(apartment, "SELECT provider_name FROM expense_instance") == [
        {"provider_name": "ACT Fibernet"}]

    for bad in ({"billing_day_of_month": 32}, {"speed_mbps": 0}, {"wifi_ssid": "x" * 33},
                {"monthly_rate_paise": -1}):
        with pytest.raises(ValidationError):
            _setup(apartment, **bad)


def test_the_households_own_broadband_definition_is_used(apartment, clock):
    # The starter set's Broadband, recurring every month.
    definitions.create_expense_definition(
        apartment, apartment_id=APARTMENT, definition_id="broadband", name="Broadband",
        category="Utilities", period_rule="current_month", recurrence="monthly")
    assert _setup(apartment)["config"]["expense_definition_id"] == "broadband"
    assert len(_rows(apartment, "SELECT 1 FROM expense_definition "
                                "WHERE lower(name) LIKE 'broadband%%'")) == 1


# --------------------------------------------------------- the month's bill --

def test_the_rate_is_split_equally_between_the_flatmates(apartment, clock):
    _setup(apartment)
    month = _month(apartment)
    assert (month["bill_state"], month["total_paise"], month["participant_count"],
            month["base_share_paise"], month["due_date"]) == ("live", RATE, 3, SHARE,
                                                              "2026-11-05")
    assert _shares(month) == {"alice": SHARE, "bob": SHARE, "chris": SHARE}
    assert month["expense"]["amount_paise"] == RATE
    assert _standing(apartment)["alice"] == (SHARE, 0, SHARE, "unpaid")
    # A rate that does not divide: the paise over go one each, in resident order.
    _setup(apartment, monthly_rate_paise=RATE + 2)
    assert _shares(_month(apartment)) == {"alice": SHARE + 1, "bob": SHARE + 1, "chris": SHARE}
    assert validation.validate(apartment, APARTMENT)["issues"] == []


def test_a_month_ahead_is_a_preview_and_nothing_is_billed_without_a_rate(apartment, clock):
    _setup(apartment, monthly_rate_paise=0)
    month = _month(apartment)
    assert (month["bill_state"], month["expense"]) == ("not_billed", None)
    assert month["blocked_reason"] == (
        "The broadband plan has no monthly rate yet, so there is nothing to bill.")
    _setup(apartment, monthly_rate_paise=RATE)
    december = _month(apartment, "2026-12")
    assert (december["bill_state"], december["expense"], _shares(december)) == (
        "upcoming", None, {"alice": SHARE, "bob": SHARE, "chris": SHARE})
    assert _rows(apartment, "SELECT settlement_period FROM expense_instance") == [
        {"settlement_period": MONTH}]


# ------------------------------------------------------- paying the bill --

def test_shares_are_paid_into_the_kitty_and_the_isp_paid_from_it(apartment, clock):
    _setup(apartment)
    month = _pay_in(apartment, "alice", SHARE)
    assert (month["collected_paise"], month["collected_count"]) == (SHARE, 1)
    assert _standing(apartment)["alice"] == (SHARE, SHARE, 0, "paid")
    with pytest.raises(ConflictError) as paid:
        _pay_in(apartment, "alice", SHARE)
    assert paid.value.code == "broadband_share_already_paid"

    # The ISP is paid what the kitty holds on the due date, and the rest later.
    month = _payout(apartment, amount_paise=SHARE)
    assert (month["payout_status"], month["disbursed_paise"],
            month["remaining_disbursement_paise"]) == ("partially_disbursed", SHARE, 2 * SHARE)
    with pytest.raises(ValidationError) as too_much:
        _payout(apartment, amount_paise=2 * SHARE + 1)
    assert too_much.value.code == "tranche_exceeds_remaining"
    _pay_in(apartment, "bob", SHARE)
    _pay_in(apartment, "chris", SHARE)
    month = _payout(apartment)
    assert (month["payout_status"], month["remaining_disbursement_paise"],
            [row["amount_paise"] for row in month["disbursements"]]) == (
        "disbursed", 0, [SHARE, 2 * SHARE])
    with pytest.raises(ConflictError) as done:
        _payout(apartment)
    assert done.value.code == "broadband_already_disbursed"
    assert validation.validate(apartment, APARTMENT)["issues"] == []
    assert sum(_net(apartment).values()) == 0


def test_a_resident_who_pays_the_isp_is_paid_back_the_rest_by_the_kitty(apartment, clock):
    _setup(apartment)
    # Bob pays the whole ₹1,179 himself: his ₹393 share through the kitty, and ₹786 beyond it.
    month = _payout(apartment, mode="resident_advance", funding_resident_id="bob")
    assert sorted((row["disbursed_by"], row["amount_paise"]) for row in month["disbursements"]) == [
        ("pool", SHARE), ("resident_advance", 2 * SHARE)]
    assert (month["payout_status"], _standing(apartment)["bob"]) == (
        "disbursed", (SHARE, SHARE, 0, "paid"))
    assert _owed(apartment) == [("bob", 2 * SHARE, 2 * SHARE, False)]
    with pytest.raises(ValidationError) as needs_who:
        _payout(apartment, mode="resident_advance")
    assert needs_who.value.details["field"] == "funding_resident_id"

    # The kitty pays him back what Alice and Chris pay in.
    _pay_in(apartment, "alice", SHARE)
    _pay_in(apartment, "chris", SHARE)
    assert _owed(apartment) == [("bob", 2 * SHARE, 2 * SHARE, True)]
    _reimburse(apartment, "bob", 2 * SHARE)
    assert _owed(apartment) == [("bob", 2 * SHARE, 0, False)]
    with pytest.raises(ValidationError) as more:
        _reimburse(apartment, "bob", 1)
    assert more.value.code == "exceeds_pending_reimbursement"
    with pytest.raises(NotFoundError) as nobody:
        _reimburse(apartment, "alice", 1)
    assert nobody.value.code == "broadband_no_reimbursement"
    assert validation.validate(apartment, APARTMENT)["issues"] == []
    assert sum(_net(apartment).values()) == 0


# ------------------------------------------------------- the open month --

def test_the_open_month_follows_a_new_flatmate_and_a_new_rate(apartment, clock):
    _setup(apartment)
    _pay_in(apartment, "alice", SHARE)
    # Dave moves in: four share the month, and Alice's ₹98.25 beyond her new share is owed back.
    residents.create_resident(apartment, apartment_id=APARTMENT, resident_id="dave",
                              display_name="Dave", active_from="2026-11-12")
    assert _shares(_month(apartment)) == {"alice": 29_475, "bob": 29_475, "chris": 29_475,
                                          "dave": 29_475}
    assert _standing(apartment)["alice"] == (29_475, 29_475, 0, "paid")
    assert _owed(apartment) == [("alice", 9_825, 9_825, True)]

    # A new plan at ₹1,572 a month: ₹393 each, and Alice's share is paid again.
    _setup(apartment, monthly_rate_paise=4 * SHARE)
    assert _shares(_month(apartment)) == {"alice": SHARE, "bob": SHARE, "chris": SHARE,
                                          "dave": SHARE}
    assert (_standing(apartment)["alice"], _owed(apartment)) == ((SHARE, SHARE, 0, "paid"), [])
    assert sum(_net(apartment).values()) == 0


def test_a_month_gone_by_follows_the_plan_until_its_statement_is_finalized(apartment, clock):
    _setup(apartment)
    _pay_in(apartment, "alice", SHARE)

    # December: November is still open, so a new rate reaches it too.
    clock["now"] = "2026-12-02T09:00:00+05:30"
    _setup(apartment, monthly_rate_paise=150_000)
    assert (_month(apartment)["bill_state"], _shares(_month(apartment))) == (
        "live", {"alice": 50_000, "bob": 50_000, "chris": 50_000})
    assert _standing(apartment)["alice"] == (50_000, SHARE, 50_000 - SHARE, "partial")

    # Its statement finalized, it is its snapshot.
    statement = statements.create_monthly_statement(apartment, apartment_id=APARTMENT,
                                                    settlement_month=MONTH)
    statements.finalize_statement(apartment, statement_id=statement, finalized_by="operator",
                                  require_clean=False)
    before = _month(apartment)
    _setup(apartment, monthly_rate_paise=180_000)
    november = _month(apartment)
    assert (november["bill_state"], november["total_paise"], november["transactions_open"]) == (
        "frozen", 150_000, False)
    assert november["shares"] == before["shares"]
    # December bills the new rate.
    assert _shares(_month(apartment, "2026-12")) == {
        "alice": 60_000, "bob": 60_000, "chris": 60_000}
    assert validation.validate(apartment, APARTMENT)["issues"] == []


def test_a_broadband_expense_entered_by_hand_is_kept_as_it_was(apartment, clock):
    definitions.create_expense_definition(
        apartment, apartment_id=APARTMENT, definition_id="broadband", name="Broadband",
        category="Utilities", period_rule="current_month", recurrence="monthly")
    # November's bill, already entered as a recurring expense.
    expenses.create_expense(apartment, apartment_id=APARTMENT, expense_id="airtel-nov",
                            description="Airtel bill", amount_paise=105_000,
                            expense_date="2026-11-02",
                            allocations_paise={"alice": 35_000, "bob": 35_000, "chris": 35_000},
                            expense_definition_id="broadband", settlement_period=MONTH)
    _setup(apartment)
    month = _month(apartment)
    assert (month["expense"]["expense_id"], month["total_paise"]) == ("airtel-nov", 105_000)
    assert _rows(apartment, "SELECT amount_paise FROM expense_instance") == [
        {"amount_paise": 105_000}]
    # It is paid as the plan's own bills are.
    _pay_in(apartment, "bob", 35_000)
    assert _standing(apartment)["bob"] == (35_000, 35_000, 0, "paid")


# --------------------------------------------------- undoing a mistake --

def test_a_payment_or_payout_recorded_by_mistake_is_deleted_while_the_month_is_open(
        apartment, clock):
    _setup(apartment)
    month = _pay_in(apartment, "alice", SHARE)
    payment_id = _share(month, "alice")["payments"][0]["payment_id"]
    after = broadband.delete_payment(apartment, APARTMENT, payment_id)
    assert after["deleted"]["deleted_payment_ids"] == [payment_id]
    assert (after["month"]["collected_paise"], _standing(apartment)["alice"]) == (
        0, (SHARE, 0, SHARE, "unpaid"))
    with pytest.raises(NotFoundError):
        broadband.delete_payment(apartment, APARTMENT, payment_id)

    # Bob paid the ISP the whole bill: deleting his payout deletes all of it --
    # the kitty's payout of his share, his share paid into the kitty, his advance.
    advance = _advance(_payout(apartment, mode="resident_advance", funding_resident_id="bob"))
    gone = broadband.delete_disbursement(apartment, APARTMENT, advance["disbursement_id"])
    assert (len(gone["deleted"]["deleted_disbursement_ids"]),
            len(gone["deleted"]["deleted_payment_ids"])) == (2, 2)
    assert (gone["month"]["payout_status"], gone["month"]["disbursements"],
            _standing(apartment)["bob"]) == ("pending", [], (SHARE, 0, SHARE, "unpaid"))

    # Once the kitty has paid him back, the reimbursement must go first.
    advance = _advance(_payout(apartment, mode="resident_advance", funding_resident_id="bob"))
    _pay_in(apartment, "alice", SHARE)
    _pay_in(apartment, "chris", SHARE)
    refund_id = _reimburse(apartment, "bob", 2 * SHARE)["reimbursements"][0][
        "reimbursements"][0]["payment_id"]
    with pytest.raises(ConflictError) as reimbursed:
        broadband.delete_disbursement(apartment, APARTMENT, advance["disbursement_id"])
    assert reimbursed.value.code == "payout_reimbursed"
    broadband.delete_payment(apartment, APARTMENT, refund_id)
    assert _owed(apartment) == [("bob", 2 * SHARE, 2 * SHARE, True)]
    broadband.delete_disbursement(apartment, APARTMENT, advance["disbursement_id"])
    assert _month(apartment)["disbursements"] == []
    with pytest.raises(NotFoundError) as no_payout:
        broadband.delete_disbursement(apartment, APARTMENT, advance["disbursement_id"])
    assert no_payout.value.code == "payout_not_found"
    assert validation.validate(apartment, APARTMENT)["issues"] == []
    assert sum(_net(apartment).values()) == 0

    # Its statement finalized, the month's transactions are locked.
    paid = _share(_month(apartment), "alice")["payments"][0]["payment_id"]
    statement = statements.create_monthly_statement(apartment, apartment_id=APARTMENT,
                                                    settlement_month=MONTH)
    statements.finalize_statement(apartment, statement_id=statement, finalized_by="operator",
                                  require_clean=False)
    with pytest.raises(ConflictError) as locked:
        broadband.delete_payment(apartment, APARTMENT, paid)
    assert locked.value.code == "statement_not_draft"


def test_only_a_payment_towards_broadband_is_deleted_here(apartment, clock):
    # Alice's share of the groceries, paid into the kitty: her only charge, so it settles it.
    expenses.create_expense(apartment, apartment_id=APARTMENT, expense_id="groceries-oct",
                            description="Groceries", amount_paise=30_000,
                            expense_date="2026-10-20",
                            allocations_paise={"alice": 10_000, "bob": 10_000, "chris": 10_000},
                            settlement_period="2026-10")
    payments.record_payment(apartment, apartment_id=APARTMENT, payment_id="groceries-pay",
                            payer_resident_id="alice", amount_paise=10_000,
                            payment_date="2026-10-25")
    with pytest.raises(ConflictError) as not_set_up:
        broadband.delete_payment(apartment, APARTMENT, "groceries-pay")
    assert not_set_up.value.code == "broadband_not_configured"
    _setup(apartment)
    with pytest.raises(NotFoundError) as other:
        broadband.delete_payment(apartment, APARTMENT, "groceries-pay")
    assert other.value.code == "payment_not_found"
    assert len(_rows(apartment, "SELECT 1 FROM payment WHERE payment_id='groceries-pay'")) == 1


# --------------------------------------------------------------- the API --

@pytest.fixture()
def api(apartment):
    from fastapi.testclient import TestClient

    from aem.main import create_app

    settings = replace(get_settings(), auto_close_expired_statements=False)
    with TestClient(create_app(settings, database=apartment)) as client:
        sign_in(client)
        yield client


def test_broadband_over_http(api, clock):
    base = "/api/services/broadband"
    assert api.get(base, params={"month": MONTH}).json()["service"] is None
    setup = api.post(f"{base}/setup", params={"month": MONTH}, json={
        "monthly_rate_paise": RATE, "billing_day_of_month": 5, "speed_mbps": 200,
        "account_number": "AX-2048-7731", "wifi_ssid": "Capetown-1805"})
    assert setup.status_code == 200, setup.text
    assert setup.json()["config"]["speed_mbps"] == 200
    assert _shares(setup.json()["month"]) == {"alice": SHARE, "bob": SHARE, "chris": SHARE}

    paid = api.post(f"{base}/record-payment", json={
        "settlement_month": MONTH, "resident_id": "alice", "amount_paise": SHARE,
        "payment_date": "2026-11-04", "payment_mode": "upi", "reference": "UPI-9"})
    assert paid.status_code == 201, paid.text
    assert paid.json()["month"]["collected_paise"] == SHARE
    payment_id = _share(paid.json()["month"], "alice")["payments"][0]["payment_id"]

    payout = api.post(f"{base}/disburse-payout", json={
        "settlement_month": MONTH, "disbursement_mode": "resident_advance",
        "funding_resident_id": "bob", "reference": "UTR-1", "disbursed_date": "2026-11-05",
        "amount_paise": 2 * SHARE})
    assert payout.status_code == 201, payout.text
    assert payout.json()["month"]["payout_status"] == "partially_disbursed"
    owed = payout.json()["month"]["reimbursements"]
    assert [(row["recipient_resident_id"], row["pending_reimbursement_paise"])
            for row in owed] == [("bob", SHARE)]
    refund = api.post(f"{base}/reimburse-from-kitty", json={
        "settlement_month": MONTH, "recipient_resident_id": "bob", "amount_paise": SHARE,
        "payment_date": "2026-11-06"})
    assert refund.status_code == 201, refund.text
    refund_id = refund.json()["month"]["reimbursements"][0]["reimbursements"][0]["payment_id"]

    # Undone in turn: the reimbursement, then Bob's payout, then Alice's payment.
    assert api.delete(f"{base}/payments/{refund_id}").status_code == 200
    advance = _advance(api.get(base, params={"month": MONTH}).json()["month"])
    gone = api.delete(f"{base}/disbursements/{advance['disbursement_id']}")
    assert gone.status_code == 200, gone.text
    assert gone.json()["month"]["disbursements"] == []
    deleted = api.delete(f"{base}/payments/{payment_id}")
    assert (deleted.status_code, deleted.json()["deleted"]["deleted_payment_ids"]) == (
        200, [payment_id])
    assert deleted.json()["month"]["collected_paise"] == 0
    assert api.delete(f"{base}/payments/{payment_id}").status_code == 404
    assert api.delete(f"{base}/disbursements/svcdisb_nope").status_code == 404

    for bad in ({"billing_day_of_month": 32}, {"speed_mbps": 0}, {"wifi_ssid": "x" * 33}):
        assert api.post(f"{base}/setup", json=bad).status_code == 422
    assert api.post(f"{base}/disburse-payout", json={
        "settlement_month": MONTH, "disbursement_mode": "pool",
        "disbursed_date": "2026-11-05"}).status_code == 422

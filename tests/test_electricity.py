"""Electricity bills recalculated while their month is open.

A bill is corrected in place: its charges change rather than being replaced, so
what a resident has already paid towards their share stays applied to it. A
bill that goes up leaves them the difference still owed; one that would go
below what somebody has paid is refused.
"""
from __future__ import annotations

import pytest

from aem.errors import ConflictError
from aem.services import electricity, payments, reconciliation, validation

from .conftest import APARTMENT
from .support import connect

BILL = "electricity-2026-10"


def _components(energy_paise, fixed_paise=90_000):
    return [{"id": "energy", "name": "Energy charges", "amount_paise": energy_paise,
             "allocation_method": "usage"},
            {"id": "fixed", "name": "Fixed charges", "amount_paise": fixed_paise,
             "allocation_method": "equal"}]


def _rows(db, sql, params=()):
    with connect(db, read_only=True) as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _charges(db, resident):
    """A resident's charges on the bill, by part, with what has been paid against each."""
    return _rows(db, """SELECT a.allocation_id, c.name, a.amount_paise,
                               COALESCE((SELECT SUM(pa.amount_paise) FROM payment_application pa
                                          WHERE pa.allocation_id = a.allocation_id), 0)
                                   AS settled_paise
                          FROM allocation a JOIN expense_component c
                            ON c.component_id = a.component_id
                         WHERE a.expense_id = %s AND a.resident_id = %s
                         ORDER BY c.row_seq""", (BILL, resident))


def _recalculate(db, total_paise, energy_paise, **changes):
    return electricity.recalculate_electricity_expense(
        db, BILL, reason="Meter re-read", total_amount_paise=total_paise,
        components=_components(energy_paise), **changes)


def _bill(db):
    """October's ₹6,000 bill, by 30, 20 and 10 kWh."""
    electricity.create_electricity_expense(
        db, apartment_id=APARTMENT, expense_id=BILL,
        description="September electricity bill", expense_date="2026-10-08",
        total_amount_paise=600_000,
        smart_plug_readings=[{"resident_id": "alice", "kwh": "30"},
                             {"resident_id": "bob", "kwh": "20"},
                             {"resident_id": "chris", "kwh": "10"}],
        components=_components(510_000))


@pytest.fixture()
def billed(apartment):
    """October's bill, with Bob's ₹2,000 share of it paid into the kitty."""
    _bill(apartment)
    payments.record_payment(apartment, apartment_id=APARTMENT, payment_id="pay-bob",
                            payer_resident_id="bob", amount_paise=200_000,
                            payment_date="2026-10-10")
    assert [(row["name"], row["amount_paise"], row["settled_paise"])
            for row in _charges(apartment, "bob")] == [
        ("Energy charges", 170_000, 170_000), ("Fixed charges", 30_000, 30_000)]
    return apartment


def test_recalculating_an_open_bill_keeps_payments_and_tops_up_the_rest(billed):
    before = _charges(billed, "bob")
    saved = _recalculate(billed, 660_000, 570_000)
    assert saved["expense"]["amount_paise"] == 660_000
    assert saved["resident_totals_paise"] == {"alice": 315_000, "bob": 220_000,
                                              "chris": 125_000}
    # Bob's charges are the same rows, grown: his ₹2,000 stays on them, ₹200 is still owed.
    after = _charges(billed, "bob")
    assert [row["allocation_id"] for row in after] == [row["allocation_id"] for row in before]
    assert [(row["amount_paise"], row["settled_paise"]) for row in after] == [
        (190_000, 170_000), (30_000, 30_000)]
    assert _rows(billed, "SELECT payment_id, amount_paise FROM payment_application "
                         "ORDER BY amount_paise") == [
        {"payment_id": "pay-bob", "amount_paise": 30_000},
        {"payment_id": "pay-bob", "amount_paise": 170_000}]
    assert sum(reconciliation.reconcile(billed, apartment_id=APARTMENT)[
        "net_positions_paise_by_account"].values()) == 0
    assert validation.validate(billed, APARTMENT)["issues"] == []


def test_an_open_bill_is_never_recalculated_below_what_was_paid(billed):
    with pytest.raises(ConflictError) as below:
        _recalculate(billed, 540_000, 450_000)
    assert below.value.code == "electricity_share_below_paid"
    assert below.value.details == {"resident_id": "bob", "settled_paise": 200_000,
                                   "share_paise": 180_000}
    # Nothing changed.
    assert electricity.get_electricity_expense(billed, BILL)["expense"]["amount_paise"] == 600_000
    assert [row["amount_paise"] for row in _charges(billed, "bob")] == [170_000, 30_000]


def test_an_open_bill_is_never_recalculated_below_what_was_paid_to_the_provider(apartment):
    _bill(apartment)
    # Alice paid the provider the whole bill herself.
    payments.record_payment(
        apartment, apartment_id=APARTMENT, payment_id="advance-alice",
        payer_resident_id="alice", amount_paise=600_000, payment_date="2026-10-09",
        payment_kind="provider_advance", payee_or_provider="State Electricity Board",
        expense_id=BILL)
    with pytest.raises(ConflictError) as below:
        _recalculate(apartment, 590_000, 500_000)
    assert below.value.code == "electricity_below_disbursed"
    assert below.value.details == {"advanced_paise": 600_000, "total_amount_paise": 590_000}
    # Going up is fine, and the advance stays linked to the bill.
    assert _recalculate(apartment, 630_000, 540_000)["expense"]["amount_paise"] == 630_000
    assert _rows(apartment, "SELECT expense_id FROM payment WHERE payment_id='advance-alice'") == [
        {"expense_id": BILL}]
    # The ₹300 the advance no longer covers falls to the kitty, which is worth a warning.
    assert [issue["code"] for issue in validation.validate(apartment, APARTMENT)["issues"]] == [
        "expense_partially_funded"]

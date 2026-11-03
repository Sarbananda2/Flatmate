"""Electricity: the month's UPPCL and DG bills split by AC, and kWh bills corrected.

Each month's two bills -- the grid's and the generator's -- are split so that
every flatmate pays their own AC and an equal share of the rest, to the paisa;
they pay it into the House Kitty, which pays UPPCL and DG separately.

A bill split by smart-plug readings is corrected in place: its charges change
rather than being replaced, so what a resident has already paid towards their
share stays applied to it. A bill that goes up leaves them the difference still
owed; one that would go below what somebody has paid is refused.
"""
from __future__ import annotations

import json
from dataclasses import replace

import pytest

from aem.config import get_settings
from aem.engine import ElectricityCalculationError, calculate_consolidated_electricity
from aem.errors import ConflictError, NotFoundError, ValidationError
from aem.services import (
    apartments,
    electricity,
    payments,
    reconciliation,
    residents,
    statements,
    validation,
)

from .conftest import APARTMENT
from .support import connect, sign_in

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



# ======================================================== the month's bill ==
#
# The household's own figures: UPPCL and DG together ₹4,000.00; Sarbananda's
# AC ₹971.05, Arnab's ₹895.48, Anagarag's none. The engine splits them in the
# order it is given; the household's months split in resident order.

UPPCL, DG = 300_000, 100_000
BILLS = [{"id": "uppcl", "name": "UPPCL (Grid)", "amount_paise": UPPCL},
         {"id": "dg", "name": "Diesel Generator (DG)", "amount_paise": DG}]
MONTH = "2026-11"


def test_each_flatmate_pays_their_ac_and_an_equal_share_of_the_rest():
    split = calculate_consolidated_electricity(
        components=BILLS, ac_allocations={"sarbananda": 97_105, "arnab": 89_548, "anagarag": 0},
        residents=["sarbananda", "arnab", "anagarag"])
    assert (split["total_paise"], split["total_ac_paise"], split["common_remainder_paise"],
            split["common_share_paise"]) == (400_000, 186_653, 213_347, 71_115)
    # ₹2,133.47 three ways: the two paise over go one each, in the order given.
    assert [(row["resident_id"], row["common_share_paise"], row["ac_paise"],
             row["responsibility_paise"]) for row in split["residents"]] == [
        ("sarbananda", 71_116, 97_105, 168_221),
        ("arnab", 71_116, 89_548, 160_664),
        ("anagarag", 71_115, 0, 71_115)]
    assert sum(row["responsibility_paise"] for row in split["residents"]) == 400_000


def test_the_balances_settle_in_as_few_transfers_as_the_greedy_order_makes():
    split = calculate_consolidated_electricity(
        components=BILLS, ac_allocations={"sarbananda": 97_105, "arnab": 89_548},
        residents=["sarbananda", "arnab", "anagarag"],
        payments={"sarbananda": UPPCL, "arnab": DG})
    assert [(row["resident_id"], row["net_balance_paise"]) for row in split["residents"]] == [
        ("sarbananda", 131_779), ("arnab", -60_664), ("anagarag", -71_115)]
    assert split["transfers"] == [
        {"from_resident_id": "anagarag", "to_resident_id": "sarbananda", "amount_paise": 71_115},
        {"from_resident_id": "arnab", "to_resident_id": "sarbananda", "amount_paise": 60_664}]
    # Until the bills are paid in full there is nothing to settle between them.
    part = calculate_consolidated_electricity(
        components=BILLS, ac_allocations={}, residents=["a", "b"], payments={"a": UPPCL})
    assert (part["unpaid_paise"], part["transfers"]) == (DG, [])


@pytest.mark.parametrize(("ac", "message"), [
    ({"a": 300_000, "b": 100_001}, "more than"),
    ({"stranger": 1}, "not sharing"),
    ({"a": -1}, "negative"),
])
def test_ac_can_neither_exceed_the_bill_nor_belong_to_a_stranger(ac, message):
    with pytest.raises(ElectricityCalculationError, match=message):
        calculate_consolidated_electricity(components=BILLS, ac_allocations=ac,
                                           residents=["a", "b"])


# --------------------------------------------------- the prepaid meters --
#
# The meters are prepaid: flatmates and the kitty recharge them as the month
# goes, and the month's bills are what was recharged.

@pytest.fixture()
def flat(db):
    """The household's own flatmates, and no expense definitions yet."""
    apartments.create_apartment(db, apartment_id=APARTMENT, name="Supertech Cape Town 1805")
    for resident_id, name in (("sarbananda", "Sarbananda"), ("arnab", "Arnab"),
                              ("anagarag", "Anagarag")):
        residents.create_resident(db, apartment_id=APARTMENT, resident_id=resident_id,
                                  display_name=name, active_from="2026-08-01")
    return db


def _top_up(db, provider, paise, by=None, month=MONTH, day="05", reference=None):
    """A recharge of a meter: by a flatmate from their own account, or by the kitty."""
    return electricity.disburse_month_payout(db, APARTMENT, {
        "settlement_month": month, "provider": provider,
        "disbursement_mode": "resident_advance" if by else "pool",
        **({"funding_resident_id": by} if by else {}),
        "amount_paise": paise, "disbursed_date": f"{month}-{day}",
        "reference": reference or f"UTR-{provider}-{paise}"})


def _set_ac(db, month=MONTH, **ac):
    return electricity.record_month_bill(db, APARTMENT, {"settlement_month": month,
                                                         "ac_allocations": ac})


def _pay_in(db, resident, paise, month=MONTH):
    return electricity.record_month_payment(db, APARTMENT, {
        "settlement_month": month, "resident_id": resident, "amount_paise": paise,
        "payment_date": f"{month}-12", "payment_mode": "upi", "reference": "UPI-1"})


def _reimburse(db, resident, paise, month=MONTH):
    return electricity.reimburse_month_from_kitty(db, APARTMENT, {
        "settlement_month": month, "recipient_resident_id": resident, "amount_paise": paise,
        "payment_date": f"{month}-20"})


def _standing(month):
    return {row["resident_id"]: (row["total_paise"], row["paid_paise"],
                                 row["outstanding_paise"], row["status"])
            for row in month["shares"]}


def _nets(month):
    return {row["resident_id"]: row["net_balance_paise"] for row in month["shares"]}


def _recharges(month):
    return [(row["provider_key"], row["amount_paise"], row["paid_by"], row["resident_id"])
            for row in month["top_ups"]]


def test_three_recharges_and_the_ac_settle_to_the_paisa(flat):
    _top_up(flat, "uppcl", 200_000, by="sarbananda", day="03")
    _top_up(flat, "uppcl", 150_000, by="arnab", day="12")
    month = _top_up(flat, "dg", 50_000, by="sarbananda", day="20")
    # The bills are what was recharged.
    assert [(row["id"], row["amount_paise"]) for row in month["components"]] == [
        ("uppcl", 350_000), ("dg", 50_000)]
    assert month["total_paise"] == 400_000
    assert _recharges(month) == [("uppcl", 200_000, "flatmate", "sarbananda"),
                                 ("uppcl", 150_000, "flatmate", "arnab"),
                                 ("dg", 50_000, "flatmate", "sarbananda")]

    month = _set_ac(flat, sarbananda=97_105, arnab=89_548, anagarag=0)
    # ₹2,133.47 shared three ways, the spare paisa one each in resident order.
    assert (month["total_ac_paise"], month["common_remainder_paise"]) == (186_653, 213_347)
    assert [(row["resident_id"], row["common_share_paise"], row["ac_paise"], row["total_paise"])
            for row in month["shares"]] == [
        ("anagarag", 71_116, 0, 71_116), ("arnab", 71_116, 89_548, 160_664),
        ("sarbananda", 71_115, 97_105, 168_220)]
    assert sum(row["total_paise"] for row in month["shares"]) == 400_000
    # Sarbananda recharged ₹2,500.00 against ₹1,682.20: the kitty owes back the
    # rest. Arnab recharged ₹1,500.00 against ₹1,606.64; Anagarag nothing.
    assert _nets(month) == {"anagarag": -71_116, "arnab": -10_664, "sarbananda": 81_780}
    assert [(row["recipient_resident_id"], row["net_reimbursement_owed_paise"])
            for row in month["reimbursements"]] == [("sarbananda", 81_780)]
    assert validation.validate(flat, APARTMENT)["issues"] == []

    # As the others pay in, the kitty pays Sarbananda back, and everyone is square.
    _pay_in(flat, "arnab", 10_664)
    _pay_in(flat, "anagarag", 71_116)
    month = _reimburse(flat, "sarbananda", 81_780)
    assert _nets(month) == {"anagarag": 0, "arnab": 0, "sarbananda": 0}
    assert validation.validate(flat, APARTMENT)["issues"] == []
    assert sum(reconciliation.reconcile(
        flat, apartment_id=APARTMENT)["net_positions_paise_by_account"].values()) == 0


def test_the_first_recharge_records_the_month(flat):
    month = _top_up(flat, "uppcl", 120_000)
    assert (month["bill_state"], month["total_paise"], month["service_period"]) == (
        "live", 120_000, "2026-10")
    # The kitty recharged it, so it owes nobody, and everyone owes it their share.
    assert _recharges(month) == [("uppcl", 120_000, "kitty", None)]
    assert _nets(month) == {"anagarag": -40_000, "arnab": -40_000, "sarbananda": -40_000}
    [expense] = _rows(flat, "SELECT * FROM expense_instance")
    assert (expense["amount_paise"], expense["expense_kind"], expense["expense_definition_id"],
            expense["settlement_period"], expense["service_period"]) == (
        120_000, "electricity", "electricity", MONTH, "2026-10")
    assert [(row["name"], row["amount_paise"]) for row in _rows(
        flat, "SELECT name, amount_paise FROM expense_component ORDER BY row_seq")] == [
        ("UPPCL (Grid)", 120_000), ("Diesel Generator (DG)", 0)]
    # One charge per flatmate, for all they owe on the month.
    charges = _rows(flat, """SELECT resident_id, amount_paise, component_id, allocation_basis_json
                               FROM allocation ORDER BY resident_id""")
    assert [(row["resident_id"], row["amount_paise"], row["component_id"]) for row in charges] \
        == [("anagarag", 40_000, None), ("arnab", 40_000, None), ("sarbananda", 40_000, None)]
    basis = json.loads(charges[0]["allocation_basis_json"])
    assert {key: basis[key] for key in ("uppcl_paise", "dg_paise", "common_remainder_paise",
                                        "common_share_paise")} == {
        "uppcl_paise": 120_000, "dg_paise": 0, "common_remainder_paise": 120_000,
        "common_share_paise": 40_000}
    [listed] = electricity.list_electricity_bills(flat, APARTMENT)["items"]
    assert (listed["amount_paise"], listed["settlement_period"]) == (120_000, MONTH)


def test_removing_a_recharge_brings_its_meter_down_and_splits_the_month_again(flat):
    _top_up(flat, "uppcl", 300_000)
    month = _top_up(flat, "dg", 90_000, by="arnab")
    dg = next(row for row in month["top_ups"] if row["provider_key"] == "dg")
    month = electricity.delete_month_top_up(flat, APARTMENT, MONTH, dg["top_up_id"])
    assert (month["total_paise"], _recharges(month)) == (300_000, [("uppcl", 300_000, "kitty", None)])
    # What Arnab paid with it went too: Arnab's share is owed again.
    assert _standing(month)["arnab"] == (100_000, 0, 100_000, "unpaid")
    assert _rows(flat, "SELECT 1 FROM payment") == []

    # The last recharge takes the month with it -- unless somebody has paid towards it.
    _pay_in(flat, "arnab", 100_000)
    uppcl = month["top_ups"][0]["top_up_id"]
    with pytest.raises(ConflictError) as paid:
        electricity.delete_month_top_up(flat, APARTMENT, MONTH, uppcl)
    assert paid.value.code == "electricity_month_has_payments"
    month = _top_up(flat, "uppcl", 60_000, month="2026-12")
    month = electricity.delete_month_top_up(flat, APARTMENT, "2026-12",
                                            month["top_ups"][0]["top_up_id"])
    assert (month["bill_state"], month["expense"]) == ("not_billed", None)
    assert len(_rows(flat, "SELECT 1 FROM expense_instance")) == 1
    with pytest.raises(NotFoundError):
        electricity.delete_month_top_up(flat, APARTMENT, MONTH, "svcdisb-nope")
    assert validation.validate(flat, APARTMENT)["issues"] == []


def test_a_payment_into_the_kitty_recorded_by_mistake_is_deleted(flat):
    _top_up(flat, "uppcl", 200_000, by="sarbananda", day="03")
    _top_up(flat, "uppcl", 150_000, by="arnab", day="12")
    _top_up(flat, "dg", 50_000, by="sarbananda", day="20")
    _set_ac(flat, sarbananda=97_105, arnab=89_548, anagarag=0)
    month = _pay_in(flat, "anagarag", 71_116)
    [paid] = next(row for row in month["shares"] if row["resident_id"] == "anagarag")["payments"]
    assert paid["top_up_id"] is None

    month = electricity.delete_payment(flat, APARTMENT, paid["payment_id"])
    assert month["deleted"]["deleted_payment_ids"] == [paid["payment_id"]]
    # Anagarag owes their share again; the recharges and everyone else stay.
    assert _standing(month)["anagarag"] == (71_116, 0, 71_116, "unpaid")
    assert _nets(month) == {"anagarag": -71_116, "arnab": -10_664, "sarbananda": 81_780}
    assert month["total_paise"] == 400_000
    assert validation.validate(flat, APARTMENT)["issues"] == []
    with pytest.raises(NotFoundError):
        electricity.delete_payment(flat, APARTMENT, paid["payment_id"])


def test_a_share_paid_with_a_recharge_goes_only_with_the_recharge(flat):
    month = _top_up(flat, "uppcl", 200_000, by="sarbananda", day="03")
    [recharge] = month["top_ups"]
    [paid] = next(row for row in month["shares"] if row["resident_id"] == "sarbananda")["payments"]
    # It came with the recharge, and says so.
    assert paid["top_up_id"] == recharge["top_up_id"]
    with pytest.raises(ConflictError) as refused:
        electricity.delete_payment(flat, APARTMENT, paid["payment_id"])
    assert refused.value.code == "electricity_payment_is_a_recharge"
    assert "Remove the recharge instead" in str(refused.value)
    # Deleting it alone would leave the meter's bill up: nothing moved.
    after = electricity.get_month(flat, APARTMENT, MONTH)
    assert (after["total_paise"], _recharges(after)) == (
        200_000, [("uppcl", 200_000, "flatmate", "sarbananda")])
    assert _standing(after) == _standing(month)


def test_a_payment_stays_once_its_month_is_finalized(flat):
    _top_up(flat, "uppcl", 120_000)
    month = _pay_in(flat, "arnab", 40_000)
    [paid] = next(row for row in month["shares"] if row["resident_id"] == "arnab")["payments"]
    statement = statements.create_monthly_statement(flat, apartment_id=APARTMENT,
                                                    settlement_month=MONTH)
    statements.finalize_statement(flat, statement_id=statement, finalized_by="operator",
                                  require_clean=False)
    with pytest.raises(ConflictError) as refused:
        electricity.delete_payment(flat, APARTMENT, paid["payment_id"])
    assert refused.value.code == "statement_not_draft"
    assert _standing(electricity.get_month(flat, APARTMENT, MONTH))["arnab"] == (
        40_000, 40_000, 0, "paid")


def test_the_ac_comes_out_of_what_was_recharged(flat):
    with pytest.raises(ConflictError) as nothing:
        _set_ac(flat, sarbananda=10_000)
    assert nothing.value.code == "electricity_no_recharges"
    _top_up(flat, "uppcl", 100_000)
    with pytest.raises(ValidationError) as too_much:
        _set_ac(flat, sarbananda=60_000, arnab=40_001)
    assert too_much.value.code == "electricity_ac_exceeds_bill"
    with pytest.raises(ValidationError) as stranger:
        _set_ac(flat, dave=1_000)
    assert stranger.value.code == "electricity_ac_not_participant"
    # Set, the AC stays as each later recharge splits the month again.
    _set_ac(flat, sarbananda=30_000)
    month = _top_up(flat, "dg", 20_000, by="anagarag")
    assert (month["total_paise"], month["ac_allocations"]["sarbananda"]) == (120_000, 30_000)
    assert {row["resident_id"]: row["total_paise"] for row in month["shares"]} == {
        "anagarag": 30_000, "arnab": 30_000, "sarbananda": 60_000}
    # A recharge needs its amount.
    with pytest.raises(ValidationError) as unknown:
        electricity.disburse_month_payout(flat, APARTMENT, {
            "settlement_month": MONTH, "provider": "dg", "disbursement_mode": "pool",
            "reference": "UTR", "disbursed_date": f"{MONTH}-06"})
    assert unknown.value.code == "electricity_amount_required"


def test_a_preview_splits_the_ac_without_recording_it(flat):
    _top_up(flat, "uppcl", 400_000)
    preview = electricity.preview_month_bill(flat, APARTMENT, MONTH, {
        "ac_allocations": {"sarbananda": 97_105, "arnab": 89_548}})
    assert [(row["display_name"], row["responsibility_paise"]) for row in preview["residents"]] \
        == [("Anagarag", 71_116), ("Arnab", 160_664), ("Sarbananda", 168_220)]
    assert electricity.get_month(flat, APARTMENT, MONTH)["ac_allocations"] == {
        "anagarag": 0, "arnab": 0, "sarbananda": 0}


def test_a_finalized_month_takes_no_more_recharges_or_ac(flat):
    _top_up(flat, "uppcl", 100_000)
    statement = statements.create_monthly_statement(flat, apartment_id=APARTMENT,
                                                    settlement_month=MONTH)
    statements.finalize_statement(flat, statement_id=statement, finalized_by="operator",
                                  require_clean=False)
    month = electricity.get_month(flat, APARTMENT, MONTH)
    assert (month["bill_state"], month["can_record"]) == ("frozen", False)
    for change in (lambda: _top_up(flat, "dg", 10_000), lambda: _set_ac(flat, arnab=1)):
        with pytest.raises(ConflictError) as refused:
            change()
        assert refused.value.code == "electricity_month_finalized"
    with pytest.raises(ConflictError) as refused:
        electricity.recalculate_electricity_expense(flat, month["expense"]["expense_id"],
                                                    reason="typo", total_amount_paise=1)
    assert refused.value.code == "electricity_bill_consolidated"
    # A month never recharged is not recorded by a refused one either.
    with pytest.raises(NotFoundError):
        _top_up(flat, "dg", 10_000, by="nobody", month="2026-12")
    assert len(_rows(flat, "SELECT 1 FROM expense_instance")) == 1


@pytest.fixture()
def api(flat):
    from fastapi.testclient import TestClient

    from aem.main import create_app

    settings = replace(get_settings(), auto_close_expired_statements=False)
    with TestClient(create_app(settings, database=flat)) as client:
        sign_in(client)
        yield client


def test_the_prepaid_meters_over_http(api):
    base = "/api/electricity"
    recharge = {"settlement_month": MONTH, "provider": "uppcl",
                "disbursement_mode": "resident_advance", "funding_resident_id": "sarbananda",
                "amount_paise": 400_000, "disbursed_date": f"{MONTH}-03", "reference": "UTR-1"}
    topped = api.post(f"{base}/disburse-payout", json=recharge)
    assert topped.status_code == 200, topped.text
    assert topped.json()["total_paise"] == 400_000
    assert api.post(f"{base}/disburse-payout",
                    json={**recharge, "amount_paise": None}).status_code == 422

    preview = api.post(f"{base}/month/{MONTH}/preview",
                       json={"ac_allocations": {"sarbananda": 97_105, "arnab": 89_548}})
    assert (preview.status_code, preview.json()["common_remainder_paise"]) == (200, 213_347)
    too_much = api.post(f"{base}/month/{MONTH}/preview", json={"ac_allocations": {"arnab": 400_001}})
    assert (too_much.status_code, too_much.json()["error"]["code"]) == (
        422, "electricity_ac_exceeds_bill")

    saved = api.post(f"{base}/bills", json={
        "settlement_month": MONTH, "ac_allocations": {"sarbananda": 97_105, "arnab": 89_548}})
    assert saved.status_code == 201, saved.text
    assert {row["resident_id"]: row["net_balance_paise"] for row in saved.json()["shares"]} == {
        "anagarag": -71_116, "arnab": -160_664, "sarbananda": 231_780}
    assert api.get(f"{base}/bills").json()["items"][0]["amount_paise"] == 400_000
    month = api.get(f"{base}/month/{MONTH}").json()
    assert [(row["provider_key"], row["resident_name"]) for row in month["top_ups"]] == [
        ("uppcl", "Sarbananda")]

    paid = api.post(f"{base}/record-payment", json={
        "settlement_month": MONTH, "resident_id": "anagarag", "amount_paise": 71_116,
        "payment_date": f"{MONTH}-05"})
    assert paid.status_code == 200, paid.text
    assert api.delete(f"{base}/month/{MONTH}/top-ups/svcdisb-nope").status_code == 404
    nothing_owed = api.post(f"{base}/reimburse-from-kitty", json={
        "settlement_month": MONTH, "recipient_resident_id": "arnab", "amount_paise": 1,
        "payment_date": f"{MONTH}-07"})
    assert nothing_owed.status_code == 404


def test_a_payment_is_deleted_over_http(api):
    base = "/api/electricity"
    recharge = {"settlement_month": MONTH, "provider": "uppcl", "amount_paise": 120_000,
                "disbursed_date": f"{MONTH}-03", "reference": "UTR-1"}
    assert api.post(f"{base}/disburse-payout",
                    json={**recharge, "disbursement_mode": "pool"}).status_code == 200
    month = api.post(f"{base}/disburse-payout", json={
        **recharge, "amount_paise": 30_000, "disbursement_mode": "resident_advance",
        "funding_resident_id": "sarbananda", "reference": "UTR-2"}).json()
    paid = api.post(f"{base}/record-payment", json={
        "settlement_month": MONTH, "resident_id": "arnab", "amount_paise": 50_000,
        "payment_date": f"{MONTH}-05"})
    assert paid.status_code == 200, paid.text
    [payment] = next(row for row in paid.json()["shares"] if row["resident_id"] == "arnab")["payments"]

    deleted = api.delete(f"{base}/payments/{payment['payment_id']}")
    assert deleted.status_code == 200, deleted.text
    assert _standing(deleted.json())["arnab"] == (50_000, 0, 50_000, "unpaid")
    assert api.delete(f"{base}/payments/{payment['payment_id']}").status_code == 404
    # A share paid with a recharge goes with the recharge.
    [with_recharge] = next(row for row in month["shares"]
                           if row["resident_id"] == "sarbananda")["payments"]
    refused = api.delete(f"{base}/payments/{with_recharge['payment_id']}")
    assert (refused.status_code, refused.json()["error"]["code"]) == (
        409, "electricity_payment_is_a_recharge")

"""Rent under a single master lease, split equally.

A rent cycle wraps an ordinary expense, so these tests pin the things that must
stay true of it as accounting: the shares always sum to the rent to the paisa,
a partial month never reduces a share, paying the landlord from the pool and
having one resident pay both leave a ledger that sums to zero, and a resident
who pays the landlord is credited once, not twice. They also pin the one
workflow switch: whether the landlord issues receipts.
"""
from __future__ import annotations

import base64
import hashlib
import sqlite3
from dataclasses import replace

import pytest

from aem.config import get_settings
from aem.errors import ConflictError, NotFoundError, ValidationError
from aem.money import paise_in_words
from aem.services import (
    apartments,
    expenses,
    payments,
    pool_statements,
    reconciliation,
    recurring,
    rent,
    residents,
    statements,
    validation,
)

from .conftest import APARTMENT
from .support import DatabaseRefusal, connect, sign_in

MONTH = "2026-11"
LEASE = {
    "landlord_name": "Rajesh Kumar Sharma",
    "landlord_phone": "+91 98765 43210",
    "landlord_pan": "abcps1234k",
    "landlord_bank_account": "50100012348912",
    "landlord_bank_ifsc": "hdfc0000128",
    "landlord_upi_id": "rajeshsharma@okaxis",
    "monthly_rent_paise": 4_500_000,
    "due_day_of_month": 5,
}
PDF = {"filename": "receipt-nov.pdf", "content_type": "application/pdf",
       "content_base64": base64.b64encode(b"%PDF-1.4 landlord receipt").decode()}


def _lease(db, **overrides):
    return rent.save_lease_config(db, APARTMENT, {**LEASE, **overrides})


def _generate(db, month=MONTH, excluded=None):
    return rent.generate_monthly_rent_cycle(db, APARTMENT, month, excluded)


def _disburse(db, mode="pool", month=MONTH, **extra):
    return rent.record_landlord_disbursement(
        db, apartment_id=APARTMENT, settlement_month=month, disbursement_mode=mode,
        reference=extra.pop("reference", "UTR329188401928"),
        disbursed_date=extra.pop("disbursed_date", f"{month}-04"), **extra)


def _shares(cycle):
    return {p["resident_id"]: p["share_paise"] for p in cycle["participants"]}


def _positions(db):
    return reconciliation.reconcile(
        db, apartment_id=APARTMENT)["net_positions_paise_by_account"]


def _rows(db, sql, params=()):
    with connect(db, read_only=True) as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


@pytest.fixture()
def leased(apartment):
    """Three residents and a lease at ₹45,000 a month, no receipts from the landlord."""
    _lease(apartment)
    return apartment


# -------------------------------------------------------------------- the lease --

def test_the_lease_is_saved_normalised_and_off_for_receipts_by_default(apartment):
    assert rent.get_lease_config(apartment, APARTMENT) is None
    config = _lease(apartment)
    assert config["monthly_rent_paise"] == 4_500_000
    assert config["landlord_pan"] == "ABCPS1234K"
    assert config["landlord_bank_ifsc"] == "HDFC0000128"
    assert config["requires_landlord_receipt"] is False
    assert rent.get_lease_config(apartment, APARTMENT) == config


def test_saving_again_updates_the_one_lease_rather_than_adding_another(leased):
    first = rent.get_lease_config(leased, APARTMENT)
    second = _lease(leased, monthly_rent_paise=4_800_000, requires_landlord_receipt=True)
    assert second["config_id"] == first["config_id"]
    assert second["monthly_rent_paise"] == 4_800_000
    assert second["requires_landlord_receipt"] is True
    assert len(_rows(leased, "SELECT 1 FROM rent_lease_config")) == 1


def test_rent_is_recorded_under_a_housing_definition(apartment, configured):
    # `configured` already has the household's own Rent definition: it is reused.
    assert _lease(configured)["expense_definition_id"] == "rent"
    assert len(_rows(configured, "SELECT 1 FROM expense_definition WHERE category='Housing'")) == 1


def test_a_housing_definition_is_created_when_the_household_has_none(apartment):
    config = _lease(apartment)
    definition = _rows(apartment, "SELECT * FROM expense_definition WHERE expense_definition_id=%s",
                       (config["expense_definition_id"],))[0]
    assert (definition["category"], definition["recurrence"]) == ("Housing", "none")


@pytest.mark.parametrize("field, value", [
    ("monthly_rent_paise", 0),
    ("monthly_rent_paise", -100),
    ("monthly_rent_paise", 45000.5),
    ("monthly_rent_paise", True),
    ("due_day_of_month", 0),
    ("due_day_of_month", 29),
    ("landlord_name", "   "),
    ("landlord_pan", "NOTAPAN"),
    ("landlord_bank_ifsc", "HDFC1000128"),
    ("requires_landlord_receipt", "yes"),
])
def test_an_invalid_lease_is_refused_and_nothing_is_saved(apartment, field, value):
    with pytest.raises(ValidationError):
        _lease(apartment, **{field: value})
    assert rent.get_lease_config(apartment, APARTMENT) is None
    assert _rows(apartment, "SELECT 1 FROM expense_definition") == []


def test_the_database_itself_refuses_non_integer_or_non_positive_rent(leased):
    for bad in (0, -1, 4500000.5):
        with connect(leased) as conn, pytest.raises(DatabaseRefusal):
            conn.execute("UPDATE rent_lease_config SET monthly_rent_paise=%s", (bad,))


# ------------------------------------------------------------ the equal split --

def test_rent_is_split_equally_with_nothing_left_over(leased):
    cycle = _generate(leased)
    assert _shares(cycle) == {"alice": 1_500_000, "bob": 1_500_000, "chris": 1_500_000}
    assert (cycle["base_share_paise"], cycle["remainder_paise"]) == (1_500_000, 0)
    assert cycle["allocated_paise"] == cycle["total_rent_paise"] == 4_500_000
    assert cycle["disbursement_status"] == "pending"
    assert cycle["disbursed_by"] is None, "nobody has funded a payout that has not happened"


def test_the_rent_expense_is_an_ordinary_housing_expense_for_the_month(leased):
    cycle = _generate(leased)
    expense = expenses.read_expense(leased, cycle["expense_id"])
    assert expense["description"] == "Rent for November 2026"
    assert (expense["service_period"], expense["settlement_period"]) == (MONTH, MONTH)
    assert expense["due_date"] == "2026-11-05", "rent is due on the lease's due day"
    assert expense["payer_resident_id"] is None
    assert expense["allocation_method"] == "equal"
    category = _rows(leased, "SELECT category FROM expense_definition "
                             "WHERE expense_definition_id=%s", (expense["expense_definition_id"],))
    assert category == [{"category": "Housing"}]


@pytest.mark.parametrize("rent_paise", [4_500_000, 4_500_001, 4_500_002, 1, 2, 100, 9_999_999])
@pytest.mark.parametrize("excluded", [[], ["chris"], ["bob", "chris"]])
def test_shares_always_sum_to_the_rent_to_the_paisa(apartment, rent_paise, excluded):
    _lease(apartment, monthly_rent_paise=rent_paise)
    cycle = _generate(apartment, excluded=excluded)
    shares = _shares(cycle)
    assert sum(shares.values()) == rent_paise, "zero paisa drift"
    assert max(shares.values()) - min(shares.values()) <= 1
    stored = _rows(apartment, "SELECT SUM(amount_paise) AS total FROM allocation "
                              "WHERE expense_id=%s", (cycle["expense_id"],))
    assert stored[0]["total"] == rent_paise
    assert all(isinstance(v, int) and not isinstance(v, bool) for v in shares.values())


def test_remainder_paise_go_one_each_in_resident_id_order(apartment):
    _lease(apartment, monthly_rent_paise=4_500_002)
    cycle = _generate(apartment)
    assert _shares(cycle) == {"alice": 1_500_001, "bob": 1_500_001, "chris": 1_500_000}
    assert (cycle["base_share_paise"], cycle["remainder_paise"]) == (1_500_000, 2)


# ----------------------------------------------------------- nothing prorates --

@pytest.mark.parametrize("active_from", ["2026-11-01", "2026-11-16", "2026-11-29"])
def test_a_partial_month_never_reduces_a_share(leased, active_from):
    residents.update_resident(leased, "chris", apartment_id=APARTMENT, active_from=active_from)
    assert _shares(_generate(leased)) == {
        "alice": 1_500_000, "bob": 1_500_000, "chris": 1_500_000}


def test_leaving_someone_out_is_a_whole_share_decision(leased):
    cycle = _generate(leased, excluded=["chris"])
    assert _shares(cycle) == {"alice": 2_250_000, "bob": 2_250_000}
    assert cycle["participant_count"] == 2
    assert [r["resident_id"] for r in cycle["excluded_residents"]] == ["chris"]
    assert sum(_shares(cycle).values()) == 4_500_000, "the household still collects it all"


def test_someone_must_take_part_and_exclusions_must_be_residents(leased):
    with pytest.raises(ValidationError) as nobody:
        _generate(leased, excluded=["alice", "bob", "chris"])
    assert nobody.value.code == "no_participants"
    with pytest.raises(ValidationError):
        _generate(leased, excluded=["nobody"])
    assert rent.get_rent_cycle(leased, APARTMENT, MONTH) is None
    assert _rows(leased, "SELECT 1 FROM expense_instance") == []


def test_the_preview_states_the_shares_generating_would_charge(leased):
    overview = rent.rent_month_overview(leased, APARTMENT, MONTH,
                                        excluded_resident_ids=["bob"])
    preview = overview["preview"]
    assert overview["cycle"] is None and preview["mode"] == "create"
    assert {p["resident_id"]: p["share_paise"] for p in preview["participants"]} == {
        "alice": 2_250_000, "chris": 2_250_000}
    assert preview["can_generate"] is True
    assert _rows(leased, "SELECT 1 FROM expense_instance") == [], "a preview saves nothing"


# ------------------------------------------------- one rent expense per month --

def test_rent_cannot_be_generated_before_the_lease_or_twice(apartment):
    with pytest.raises(ConflictError) as unset:
        _generate(apartment)
    assert unset.value.code == "rent_not_configured"
    _lease(apartment)
    _generate(apartment)
    with pytest.raises(ConflictError) as twice:
        _generate(apartment)
    assert twice.value.code == "rent_cycle_exists"
    assert len(_rows(apartment, "SELECT 1 FROM expense_instance")) == 1


def test_rent_already_generated_by_the_recurring_generator_is_adopted(configured):
    _lease(configured)
    recurring.generate(configured, apartment_id=APARTMENT, settlement_period=MONTH,
                       lines=[{"expense_definition_id": "rent"}])
    existing = _rows(configured, "SELECT expense_id FROM expense_instance")[0]["expense_id"]

    preview = rent.rent_month_overview(configured, APARTMENT, MONTH)["preview"]
    assert (preview["mode"], preview["existing_expense_id"]) == ("adopt", existing)

    cycle = _generate(configured)
    assert cycle["expense_id"] == existing and cycle["adopted_existing_expense"] is True
    # The landlord is due on the lease's day, whatever the adopted charge's own due date.
    assert (preview["due_date"], cycle["due_date"]) == ("2026-11-05", "2026-11-05")
    assert cycle["charge_due_date"] == "2026-11-30"
    assert len(_rows(configured, "SELECT 1 FROM expense_instance")) == 1, "no second rent"
    assert _positions(configured)["__pool__"] == 4_500_000


def test_the_recurring_generator_does_not_duplicate_generated_rent(configured):
    _lease(configured)
    _generate(configured)
    lines = {line["expense_definition_id"]: line for line in recurring.preview(
        configured, apartment_id=APARTMENT, settlement_period=MONTH)["lines"]}
    assert lines["rent"]["status"] == "already_exists"
    result = recurring.generate(configured, apartment_id=APARTMENT, settlement_period=MONTH,
                                lines=[{"expense_definition_id": "rent"}])
    assert result["created_count"] == 0
    assert len(_rows(configured, "SELECT 1 FROM expense_instance")) == 1


def test_rent_joins_a_draft_statement_and_is_refused_by_a_finalized_one(leased):
    draft = statements.create_monthly_statement(leased, apartment_id=APARTMENT,
                                                settlement_month=MONTH)
    assert _generate(leased)["statement"]["statement_id"] == draft

    later = statements.create_monthly_statement(leased, apartment_id=APARTMENT,
                                                settlement_month="2026-12")
    statements.finalize_statement(leased, statement_id=later, finalized_by="operator",
                                  require_clean=False)
    with pytest.raises(ConflictError) as refused:
        _generate(leased, month="2026-12")
    assert refused.value.code == "statement_not_draft"
    assert rent.get_rent_cycle(leased, APARTMENT, "2026-12") is None


def test_removing_the_rent_expense_discards_its_pending_cycle(leased):
    cycle = _generate(leased)
    expenses.delete_expense(leased, cycle["expense_id"], apartment_id=APARTMENT)
    assert rent.get_rent_cycle(leased, APARTMENT, MONTH) is None
    assert _shares(_generate(leased, excluded=["chris"])) == {
        "alice": 2_250_000, "bob": 2_250_000}


# ------------------------------------------------------- paying from the pool --

def test_a_pool_payout_adds_nothing_to_the_ledger(leased):
    _generate(leased)
    before = _positions(leased)
    assert before == {"alice": -1_500_000, "bob": -1_500_000, "chris": -1_500_000,
                      "__pool__": 4_500_000}

    cycle = _disburse(leased, "pool")
    assert (cycle["disbursement_status"], cycle["disbursed_by"]) == ("disbursed", "pool")
    assert (cycle["disbursed_at"], cycle["disbursement_reference"]) == (
        "2026-11-04", "UTR329188401928")
    assert cycle["funding_resident_id"] is None
    assert _positions(leased) == before, "the expense was already a cost to the pool"
    assert sum(_positions(leased).values()) == 0
    assert _rows(leased, "SELECT 1 FROM payment") == []
    assert validation.validate(leased, APARTMENT)["issues"] == []


def test_residents_paying_the_pool_is_the_internal_collection(leased):
    _generate(leased)
    payments.record_payment(leased, apartment_id=APARTMENT, payment_id="pay-alice",
                            payer_resident_id="alice", amount_paise=1_500_000,
                            payment_date="2026-11-02")
    payments.record_payment(leased, apartment_id=APARTMENT, payment_id="pay-bob",
                            payer_resident_id="bob", amount_paise=500_000,
                            payment_date="2026-11-03")
    cycle = rent.get_rent_cycle(leased, APARTMENT, MONTH)
    status = {p["resident_id"]: p["collection_status"] for p in cycle["participants"]}
    assert status == {"alice": "paid", "bob": "partial", "chris": "unpaid"}
    assert (cycle["collected_paise"], cycle["collected_count"]) == (2_000_000, 1)
    assert _positions(leased) == {"alice": 0, "bob": -1_000_000, "chris": -1_500_000,
                                  "__pool__": 2_500_000}


# ------------------------------------------- paying for rent goes to the rent --

def _pay_rent(db, resident_id, amount_paise, month=MONTH, **extra):
    return rent.record_rent_payment(
        db, apartment_id=APARTMENT, settlement_month=month, resident_id=resident_id,
        amount_paise=amount_paise, payment_date=extra.pop("payment_date", f"{month}-03"),
        **extra)


def _status(cycle, resident_id):
    row = next(p for p in cycle["participants"] if p["resident_id"] == resident_id)
    return row["collection_status"], row["settled_paise"], row["outstanding_paise"]


def _outstanding(db, expense_id, resident_id):
    return _rows(db, """SELECT a.amount_paise - COALESCE(
                               (SELECT SUM(pa.amount_paise) FROM payment_application pa
                                 WHERE pa.allocation_id = a.allocation_id), 0) AS open
                          FROM allocation a WHERE a.expense_id=%s AND a.resident_id=%s""",
                 (expense_id, resident_id))[0]["open"]


@pytest.fixture()
def owing(leased):
    """November rent generated, with an older October charge still open:
    each resident owes ₹1,000 for a dinner that settled in October."""
    expenses.create_expense(
        leased, apartment_id=APARTMENT, expense_id="dinner-oct", description="Dinner",
        amount_paise=300_000, expense_date="2026-10-12", period_rule="transaction_date",
        allocation_method="equal")
    _generate(leased)
    return leased


def test_an_untargeted_payment_still_goes_to_the_oldest_charge(owing):
    """The behaviour the rent payment exists to get around, and which must not change."""
    payments.record_payment(owing, apartment_id=APARTMENT, payment_id="plain",
                            payer_resident_id="bob", amount_paise=1_500_000,
                            payment_date="2026-11-03")
    cycle = rent.get_rent_cycle(owing, APARTMENT, MONTH)
    assert _outstanding(owing, "dinner-oct", "bob") == 0, "October's dinner was paid first"
    assert _status(cycle, "bob") == ("partial", 1_400_000, 100_000)


def test_a_rent_payment_settles_the_rent_even_with_an_older_charge_open(owing):
    cycle = _pay_rent(owing, "bob", 1_500_000, payment_mode="upi", reference="UPI-778")

    assert _status(cycle, "bob") == ("paid", 1_500_000, 0)
    assert (cycle["collected_paise"], cycle["collected_count"]) == (1_500_000, 1)
    assert _outstanding(owing, "dinner-oct", "bob") == 100_000, "the older charge is untouched"
    assert cycle["payment"]["applied_to_rent_paise"] == 1_500_000
    assert cycle["payment"]["applied_to_other_charges_paise"] == 0
    assert cycle["payment"]["unapplied_credit_paise"] == 0

    stored = _rows(owing, "SELECT * FROM payment")
    assert len(stored) == 1
    assert (stored[0]["payment_kind"], stored[0]["payer_resident_id"], stored[0]["amount_paise"],
            stored[0]["payment_mode"], stored[0]["reference"], stored[0]["note"]) == (
        "to_pool", "bob", 1_500_000, "upi", "UPI-778", "Rent for November 2026")
    # Still an ordinary pool payment to the ledger: bob is ₹15,000 better off, no more.
    assert _positions(owing) == {"alice": -1_600_000, "bob": -100_000, "chris": -1_600_000,
                                 "__pool__": 3_300_000}
    assert validation.validate(owing, APARTMENT)["issues"] == []


def test_a_part_payment_of_rent_leaves_the_rest_of_that_rent_owed(owing):
    cycle = _pay_rent(owing, "bob", 600_000)
    assert _status(cycle, "bob") == ("partial", 600_000, 900_000)
    cycle = _pay_rent(owing, "bob", 900_000)
    assert _status(cycle, "bob") == ("paid", 1_500_000, 0)
    assert _outstanding(owing, "dinner-oct", "bob") == 100_000


def test_rent_paid_over_the_share_spills_oldest_first_then_becomes_credit(owing):
    cycle = _pay_rent(owing, "bob", 1_650_000)
    assert _status(cycle, "bob") == ("paid", 1_500_000, 0), "the rent takes only what is owed"
    assert _outstanding(owing, "dinner-oct", "bob") == 0, "the excess paid off the dinner"
    assert cycle["payment"]["applied_to_other_charges_paise"] == 100_000
    assert cycle["payment"]["unapplied_credit_paise"] == 50_000
    applied = _rows(owing, "SELECT SUM(amount_paise) AS total, COUNT(*) AS n "
                           "FROM payment_application")
    assert applied == [{"total": 1_600_000, "n": 2}], "never more than the payment itself"
    assert _positions(owing)["bob"] == 50_000
    assert sum(_positions(owing).values()) == 0


def test_rent_can_be_paid_after_the_landlord_has_been_paid(owing):
    _disburse(owing, "resident_advance", funding_resident_id="alice")
    cycle = _pay_rent(owing, "chris", 1_500_000)
    assert _status(cycle, "chris")[0] == "paid"
    assert (cycle["collected_paise"], cycle["collected_count"]) == (3_000_000, 2)


def test_reversing_a_rent_payment_reopens_the_rent(owing):
    cycle = _pay_rent(owing, "bob", 1_500_000)
    payments.reverse_payment(owing, cycle["payment"]["payment_id"], reason="bounced")
    assert _status(rent.get_rent_cycle(owing, APARTMENT, MONTH), "bob") == (
        "unpaid", 0, 1_500_000)


def test_a_rent_payment_is_refused_when_there_is_no_rent_share_to_pay(owing):
    with pytest.raises(NotFoundError):
        _pay_rent(owing, "bob", 1_500_000, month="2027-01")
    for bad in (0, -5, 1500.5, True):
        with pytest.raises(ValidationError):
            _pay_rent(owing, "bob", bad)
    with pytest.raises(ValidationError):
        _pay_rent(owing, "bob", 1_500_000, payment_mode="cheque")
    with pytest.raises(ValidationError):
        _pay_rent(owing, "bob", 1_500_000, payment_date="03/11/2026")
    assert _rows(owing, "SELECT 1 FROM payment") == []

    _pay_rent(owing, "bob", 1_500_000)
    with pytest.raises(ConflictError) as twice:
        _pay_rent(owing, "bob", 1_500_000)
    assert twice.value.code == "rent_share_already_paid"

    _disburse(owing, "resident_advance", funding_resident_id="alice")
    with pytest.raises(ConflictError) as funder:
        _pay_rent(owing, "alice", 1_500_000)
    assert funder.value.code == "rent_share_funded_by_payout"
    assert len(_rows(owing, "SELECT 1 FROM payment WHERE payment_kind='to_pool'")) == 1


def test_someone_left_out_of_the_rent_cannot_pay_it(leased):
    _generate(leased, excluded=["chris"])
    with pytest.raises(ValidationError) as refused:
        _pay_rent(leased, "chris", 1_500_000)
    assert refused.value.code == "not_a_rent_participant"
    assert _rows(leased, "SELECT 1 FROM payment") == []


def test_a_payment_can_only_target_the_payers_own_charge(owing):
    alices = _rows(owing, "SELECT allocation_id FROM allocation "
                          "WHERE expense_id='dinner-oct' AND resident_id='alice'")[0]
    shared = dict(apartment_id=APARTMENT, payer_resident_id="bob", amount_paise=100_000,
                  payment_date="2026-11-03", target_allocation_id=alices["allocation_id"])
    with pytest.raises(ValidationError) as foreign:
        payments.record_payment(owing, payment_id="p1", **shared)
    assert foreign.value.code == "target_charge_not_payers"
    with pytest.raises(ValidationError):
        payments.record_payment(owing, payment_id="p2", payment_kind="refund", **shared)
    assert _rows(owing, "SELECT 1 FROM payment") == []
    assert _rows(owing, "SELECT 1 FROM payment_application") == []


def test_targeting_a_settled_charge_falls_back_to_oldest_first(owing):
    dinner = _rows(owing, "SELECT allocation_id FROM allocation "
                          "WHERE expense_id='dinner-oct' AND resident_id='bob'")[0]["allocation_id"]
    payments.record_payment(owing, apartment_id=APARTMENT, payment_id="p1",
                            payer_resident_id="bob", amount_paise=100_000,
                            payment_date="2026-11-01")
    result = payments.record_payment(
        owing, apartment_id=APARTMENT, payment_id="p2", payer_resident_id="bob",
        amount_paise=200_000, payment_date="2026-11-02", target_allocation_id=dinner)
    assert [a["allocation_id"] for a in result["applications"]] != [dinner]
    assert sum(a["amount_paise"] for a in result["applications"]) == 200_000
    assert _status(rent.get_rent_cycle(owing, APARTMENT, MONTH), "bob")[1] == 200_000


def test_a_payout_is_recorded_once(leased):
    _generate(leased)
    _disburse(leased, "pool")
    with pytest.raises(ConflictError) as again:
        _disburse(leased, "resident_advance", funding_resident_id="alice")
    assert again.value.code == "rent_already_disbursed"
    assert _rows(leased, "SELECT 1 FROM payment") == []
    with connect(leased) as conn, pytest.raises(DatabaseRefusal):
        conn.execute("UPDATE rent_cycle SET disbursement_reference='edited'")


def test_a_payout_needs_generated_rent_a_reference_and_a_coherent_mode(leased):
    with pytest.raises(NotFoundError):
        _disburse(leased, "pool")
    _generate(leased)
    for bad in (dict(mode="cash"), dict(mode="pool", reference="  "),
                dict(mode="pool", disbursed_date="04/11/2026"),
                dict(mode="pool", funding_resident_id="alice"),
                dict(mode="resident_advance"),
                dict(mode="resident_advance", funding_resident_id="nobody")):
        with pytest.raises((ValidationError, NotFoundError)):
            _disburse(leased, **bad)
    cycle = rent.get_rent_cycle(leased, APARTMENT, MONTH)
    assert cycle["disbursement_status"] == "pending"
    assert _rows(leased, "SELECT 1 FROM payment") == []
    assert _rows(leased, "SELECT 1 FROM rent_receipt") == []


# ------------------------------------------------- one resident pays landlord --

def test_a_resident_advance_credits_the_whole_rent_and_keeps_their_share(leased):
    _generate(leased)
    cycle = _disburse(leased, "resident_advance", funding_resident_id="alice")

    assert (cycle["disbursed_by"], cycle["funding_resident_id"]) == ("resident_advance", "alice")
    # Alice paid ₹45,000 and owes ₹15,000 of it: the household owes her ₹30,000.
    assert _positions(leased) == {"alice": 3_000_000, "bob": -1_500_000,
                                  "chris": -1_500_000, "__pool__": 0}
    assert _shares(cycle)["alice"] == 1_500_000, "funding it does not waive her share"

    advance = _rows(leased, "SELECT * FROM payment")
    assert len(advance) == 1
    assert (advance[0]["payment_kind"], advance[0]["expense_id"], advance[0]["amount_paise"],
            advance[0]["payer_resident_id"], advance[0]["reference"]) == (
        "provider_advance", cycle["expense_id"], 4_500_000, "alice", "UTR329188401928")
    assert cycle["funding_payment_id"] == advance[0]["payment_id"]
    assert expenses.read_expense(leased, cycle["expense_id"])["payer_resident_id"] == "alice"


def test_a_resident_advance_is_not_counted_twice(leased):
    """The expense names Alice as payer *and* has her advance linked to it.
    She must be credited ₹45,000 once, not ₹90,000."""
    _generate(leased)
    _disburse(leased, "resident_advance", funding_resident_id="alice")
    result = reconciliation.reconcile(leased, apartment_id=APARTMENT)
    alice = next(a for a in result["accounts"] if a["account_id"] == "alice")
    assert alice["funding_credits_paise"] == 4_500_000
    assert result["totals"]["household_expenses_paise"] == 4_500_000
    assert sum(result["net_positions_paise_by_account"].values()) == 0
    report = validation.validate(leased, APARTMENT)
    assert report["issues"] == [], report["issues"]


def test_the_funder_shows_as_covered_without_paying_the_pool(leased):
    _generate(leased)
    cycle = _disburse(leased, "resident_advance", funding_resident_id="alice")
    status = {p["resident_id"]: p["collection_status"] for p in cycle["participants"]}
    assert status == {"alice": "funded_payout", "bob": "unpaid", "chris": "unpaid"}
    assert (cycle["collected_paise"], cycle["collected_count"]) == (1_500_000, 1)


def test_the_remainder_paisa_avoids_the_resident_who_paid_the_landlord(apartment):
    _lease(apartment, monthly_rent_paise=4_500_001)
    assert _shares(_generate(apartment)) == {
        "alice": 1_500_001, "bob": 1_500_000, "chris": 1_500_000}
    cycle = _disburse(apartment, "resident_advance", funding_resident_id="alice")
    assert _shares(cycle) == {"alice": 1_500_000, "bob": 1_500_001, "chris": 1_500_000}
    assert sum(_shares(cycle).values()) == 4_500_001
    assert sum(_positions(apartment).values()) == 0
    assert cycle["warnings"] == []


def test_settled_charges_are_not_re_split_for_a_remainder_paisa(apartment):
    _lease(apartment, monthly_rent_paise=4_500_001)
    _generate(apartment)
    payments.record_payment(apartment, apartment_id=APARTMENT, payment_id="pay-bob",
                            payer_resident_id="bob", amount_paise=1_500_000,
                            payment_date="2026-11-02")
    cycle = _disburse(apartment, "resident_advance", funding_resident_id="alice")
    assert _shares(cycle) == {"alice": 1_500_001, "bob": 1_500_000, "chris": 1_500_000}
    assert len(cycle["warnings"]) == 1
    assert sum(_positions(apartment).values()) == 0


def test_a_pool_payout_is_refused_when_the_expense_says_a_resident_paid(configured):
    _lease(configured)
    recurring.generate(configured, apartment_id=APARTMENT, settlement_period=MONTH,
                       lines=[{"expense_definition_id": "rent", "payer_resident_id": "bob"}])
    _generate(configured)
    with pytest.raises(ConflictError) as refused:
        _disburse(configured, "pool")
    assert refused.value.code == "rent_already_funded"
    # Recording it as Bob's advance replaces the payer credit rather than adding to it.
    _disburse(configured, "resident_advance", funding_resident_id="bob")
    assert _positions(configured) == {"alice": -1_500_000, "bob": 3_000_000,
                                      "chris": -1_500_000, "__pool__": 0}


def test_the_advance_behind_a_recorded_payout_cannot_be_reversed(leased):
    _generate(leased)
    cycle = _disburse(leased, "resident_advance", funding_resident_id="alice")
    with pytest.raises(ConflictError) as refused:
        payments.reverse_payment(leased, cycle["funding_payment_id"], reason="wrong resident")
    assert refused.value.code == "rent_funding_payment"
    assert _positions(leased)["alice"] == 3_000_000
    with pytest.raises(ConflictError) as kept:
        expenses.delete_expense(leased, cycle["expense_id"], apartment_id=APARTMENT)
    assert kept.value.code in ("rent_cycle_disbursed", "expense_has_linked_payment")


def test_a_failed_payout_changes_nothing(leased, monkeypatch):
    _generate(leased)
    before = _positions(leased)

    def explode(*args, **kwargs):
        raise ValidationError("boom")

    # Fails after the advance, the tranche and the cycle's new state are written.
    monkeypatch.setattr(rent, "_public_tranche", explode)
    with pytest.raises(ValidationError):
        _disburse(leased, "resident_advance", funding_resident_id="alice")
    monkeypatch.undo()
    assert _positions(leased) == before
    assert _rows(leased, "SELECT 1 FROM payment") == []
    assert _rows(leased, "SELECT 1 FROM rent_disbursement") == []
    assert rent.get_rent_cycle(leased, APARTMENT, MONTH)["disbursement_status"] == "pending"


# ------------------------------------------------------ the landlord's receipt --

def test_without_landlord_receipts_a_payout_needs_only_the_reference(leased):
    _generate(leased)
    cycle = _disburse(leased, "pool")
    assert cycle["disbursement_status"] == "disbursed"
    assert cycle["landlord_receipt"] is None


def test_without_landlord_receipts_an_attached_one_is_refused(leased):
    _generate(leased)
    with pytest.raises(ValidationError) as refused:
        _disburse(leased, "pool", landlord_receipt=PDF)
    assert refused.value.code == "landlord_receipt_not_expected"
    assert rent.get_rent_cycle(leased, APARTMENT, MONTH)["disbursement_status"] == "pending"


def _attach(db, receipt=PDF, month=MONTH):
    return rent.attach_landlord_receipt(db, apartment_id=APARTMENT, settlement_month=month,
                                        landlord_receipt=receipt)


@pytest.fixture()
def receipted(apartment):
    """A lease whose landlord issues receipts, with November's rent generated."""
    _lease(apartment, requires_landlord_receipt=True)
    _generate(apartment)
    return apartment


def test_with_landlord_receipts_a_payout_is_not_held_up_by_a_missing_one(receipted):
    """Rent is paid on the 5th; the receipt arrives whenever the landlord sends it."""
    cycle = _disburse(receipted, "resident_advance", funding_resident_id="alice")
    assert cycle["disbursement_status"] == "disbursed"
    assert cycle["landlord_receipt"] is None
    assert cycle["landlord_receipt_status"] == "pending"
    # The payout is complete in every other respect without the file. (It writes
    # no HRA receipts: they are switched off, DEBT-001.)
    assert _rows(receipted, "SELECT 1 FROM rent_receipt") == []
    assert _positions(receipted) == {"alice": 3_000_000, "bob": -1_500_000,
                                     "chris": -1_500_000, "__pool__": 0}
    with pytest.raises(NotFoundError):
        rent.get_landlord_receipt(receipted, apartment_id=APARTMENT, settlement_month=MONTH)


def test_a_receipt_can_still_be_attached_with_the_payout(receipted):
    cycle = _disburse(receipted, "pool", landlord_receipt=PDF)
    content = b"%PDF-1.4 landlord receipt"
    assert cycle["landlord_receipt_status"] == "attached"
    assert cycle["landlord_receipt"]["filename"] == "receipt-nov.pdf"
    assert cycle["landlord_receipt"]["sha256"] == hashlib.sha256(content).hexdigest()
    stored = rent.get_landlord_receipt(receipted, apartment_id=APARTMENT,
                                       settlement_month=MONTH)
    assert bytes(stored["content"]) == content


def test_a_receipt_can_be_attached_after_the_payout_and_changes_nothing_else(receipted):
    _disburse(receipted, "pool")
    before = _positions(receipted)
    frozen = _rows(receipted, "SELECT * FROM rent_cycle")
    hra = _rows(receipted, "SELECT * FROM rent_receipt ORDER BY resident_id")

    cycle = _attach(receipted)
    assert cycle["landlord_receipt_status"] == "attached"
    assert cycle["replaced_landlord_receipt"] is False
    assert cycle["landlord_receipt"]["filename"] == "receipt-nov.pdf"
    stored = rent.get_landlord_receipt(receipted, apartment_id=APARTMENT,
                                       settlement_month=MONTH)
    assert bytes(stored["content"]) == b"%PDF-1.4 landlord receipt"

    assert _positions(receipted) == before
    assert _rows(receipted, "SELECT * FROM rent_cycle") == frozen, "the cycle stays frozen"
    assert _rows(receipted, "SELECT * FROM rent_receipt ORDER BY resident_id") == hra


def test_an_attached_receipt_can_be_replaced_and_the_swap_is_audited(receipted):
    _disburse(receipted, "pool", landlord_receipt=PDF)
    photo = {"filename": "receipt-photo.png", "content_type": "image/png",
             "content_base64": base64.b64encode(b"\x89PNG the right one").decode()}
    cycle = _attach(receipted, photo)

    assert cycle["replaced_landlord_receipt"] is True
    assert cycle["landlord_receipt"]["filename"] == "receipt-photo.png"
    assert len(_rows(receipted, "SELECT 1 FROM rent_landlord_receipt")) == 1
    stored = rent.get_landlord_receipt(receipted, apartment_id=APARTMENT,
                                       settlement_month=MONTH)
    assert (bytes(stored["content"]), stored["content_type"]) == (
        b"\x89PNG the right one", "image/png")

    trail = _rows(receipted, "SELECT action, old_value, new_value FROM audit_log "
                             "WHERE action LIKE '%%landlord_receipt' ORDER BY row_seq")
    assert [row["action"] for row in trail] == ["replace_landlord_receipt"]
    assert hashlib.sha256(b"%PDF-1.4 landlord receipt").hexdigest() in trail[0]["old_value"]
    assert cycle["landlord_receipt"]["sha256"] in trail[0]["new_value"]


def test_a_receipt_is_attached_only_to_a_paid_month_that_expects_one(receipted):
    # Not paid yet: there is no payout for the receipt to belong to.
    with pytest.raises(ConflictError) as unpaid:
        _attach(receipted)
    assert unpaid.value.code == "rent_not_disbursed"
    with pytest.raises(NotFoundError):
        _attach(receipted, month="2027-01")
    assert _rows(receipted, "SELECT 1 FROM rent_landlord_receipt") == []


def test_no_receipt_is_chased_when_the_landlord_issues_none(leased):
    _generate(leased)
    assert rent.get_rent_cycle(leased, APARTMENT, MONTH)["landlord_receipt_status"] == \
        "not_required"
    cycle = _disburse(leased, "pool")
    assert cycle["landlord_receipt_status"] == "not_required", "nothing is pending"
    with pytest.raises(ValidationError) as refused:
        _attach(leased)
    assert refused.value.code == "landlord_receipt_not_expected"
    assert _rows(leased, "SELECT 1 FROM rent_landlord_receipt") == []


def test_the_flag_is_read_when_it_matters_not_when_rent_was_generated(leased):
    """Turning receipts on after a month is paid makes that month's receipt pending."""
    _generate(leased)
    _disburse(leased, "pool")
    _lease(leased, requires_landlord_receipt=True)
    assert rent.get_rent_cycle(leased, APARTMENT, MONTH)["landlord_receipt_status"] == "pending"
    assert _attach(leased)["landlord_receipt_status"] == "attached"
    # Turning it off again stops the chasing but keeps the file that was attached.
    _lease(leased, requires_landlord_receipt=False)
    assert rent.get_rent_cycle(leased, APARTMENT, MONTH)["landlord_receipt_status"] == "attached"


def test_a_pending_month_has_no_receipt_status_to_chase(receipted):
    assert rent.get_rent_cycle(receipted, APARTMENT, MONTH)["landlord_receipt_status"] == \
        "not_required"


@pytest.mark.parametrize("bad", [
    {"content_type": "text/html"},
    {"content_base64": "not base64!"},
    {"content_base64": ""},
    {"filename": " "},
    {"content_base64": base64.b64encode(b"x" * (rent.MAX_LANDLORD_RECEIPT_BYTES + 1)).decode()},
])
def test_an_unusable_landlord_receipt_is_refused(receipted, bad):
    with pytest.raises(ValidationError):
        _disburse(receipted, "pool", landlord_receipt={**PDF, **bad})
    assert rent.get_rent_cycle(receipted, APARTMENT, MONTH)["disbursement_status"] == "pending"
    # And equally when it is attached afterwards.
    _disburse(receipted, "pool")
    with pytest.raises(ValidationError):
        _attach(receipted, {**PDF, **bad})
    assert _rows(receipted, "SELECT 1 FROM rent_landlord_receipt") == []


# --------------------------------------------------------------- HRA receipts --
#
# Payouts no longer write HRA receipts (DEBT-001), but the receipts written
# before that are kept, immutable, and still served. These tests put such
# receipts on file the way a payout used to, then read them.

def _receipts_on_file(db, month=MONTH):
    """HRA receipts for a paid month, written as payouts wrote them before DEBT-001."""
    with db.transaction() as uow:
        cycle = uow.rent.cycle_for_month(APARTMENT, month)
        config = rent._config_row(uow, APARTMENT)
        timestamp = "2026-11-04T10:00:00+05:30"
        for charge in rent._charges(uow, cycle["expense_id"]):
            receipt = {
                "receipt_id": f"rentreceipt_{charge['resident_id']}", "cycle_id": cycle["cycle_id"],
                "resident_id": charge["resident_id"], "settlement_month": month,
                "amount_paise": charge["share_paise"], "landlord_name": config["landlord_name"],
                "landlord_pan": config["landlord_pan"] or "", "created_at": timestamp}
            uow.rent.insert_receipt(**receipt, verification_hash=rent.receipt_hash(receipt))


def test_a_payout_writes_no_hra_receipts(leased):
    _generate(leased)
    _disburse(leased, "pool")
    assert _rows(leased, "SELECT 1 FROM rent_receipt") == []
    cycle = rent.get_rent_cycle(leased, APARTMENT, MONTH)
    assert all(p["receipt_available"] is False for p in cycle["participants"])
    with pytest.raises(NotFoundError) as none:
        rent.get_resident_rent_receipt(leased, apartment_id=APARTMENT,
                                       settlement_month=MONTH, resident_id="alice")
    assert none.value.code == "rent_receipt_not_found"

def test_each_participant_gets_a_verifiable_receipt_for_their_share(leased):
    _generate(leased, excluded=["chris"])
    with pytest.raises(ConflictError) as early:
        rent.get_resident_rent_receipt(leased, apartment_id=APARTMENT,
                                       settlement_month=MONTH, resident_id="alice")
    assert early.value.code == "rent_not_disbursed"

    _disburse(leased, "pool")
    _receipts_on_file(leased)
    receipt = rent.get_resident_rent_receipt(leased, apartment_id=APARTMENT,
                                             settlement_month=MONTH, resident_id="alice")
    assert receipt["amount_paise"] == 2_250_000
    assert receipt["amount_in_words"] == "Twenty-Two Thousand Five Hundred Rupees Only"
    assert (receipt["landlord_name"], receipt["landlord_pan"]) == (
        "Rajesh Kumar Sharma", "ABCPS1234K")
    assert receipt["receipt_number"] == "RCPT-2026-11-01"
    assert receipt["disbursement_reference"] == "UTR329188401928"
    assert receipt["premises"] == "Flat 402"
    assert len(receipt["verification_hash"]) == 64 and receipt["hash_matches"] is True
    assert receipt["share_settled"] is False, "paying the pool is a separate fact"

    with pytest.raises(NotFoundError):
        rent.get_resident_rent_receipt(leased, apartment_id=APARTMENT,
                                       settlement_month=MONTH, resident_id="chris")
    amounts = _rows(leased, "SELECT SUM(amount_paise) AS total, COUNT(*) AS n FROM rent_receipt")
    assert amounts == [{"total": 4_500_000, "n": 2}], "the receipts account for all the rent"


def test_a_receipt_states_the_premises_by_its_postal_address(leased):
    _generate(leased)
    _disburse(leased, "pool")
    _receipts_on_file(leased)
    apartments.update_apartment(leased, APARTMENT, flat_number="402", tower_block="B",
                                society_name="Green Meadows", city="Pune",
                                state="Maharashtra", pincode="411045")
    receipt = rent.get_resident_rent_receipt(leased, apartment_id=APARTMENT,
                                             settlement_month=MONTH, resident_id="alice")
    assert receipt["premises"] == "402, B, Green Meadows, Pune, Maharashtra - 411045"
    assert receipt["hash_matches"] is True, "the address is not part of what was hashed"


def test_a_receipt_keeps_the_landlord_details_it_was_issued_with(leased):
    _generate(leased)
    _disburse(leased, "pool")
    _receipts_on_file(leased)
    _lease(leased, landlord_name="Someone Else", landlord_pan="ZZZZZ9999Z")
    receipt = rent.get_resident_rent_receipt(leased, apartment_id=APARTMENT,
                                             settlement_month=MONTH, resident_id="bob")
    assert (receipt["landlord_name"], receipt["landlord_pan"]) == (
        "Rajesh Kumar Sharma", "ABCPS1234K")
    assert receipt["hash_matches"] is True


def test_receipts_are_immutable_and_tampering_is_detectable(leased):
    _generate(leased)
    _disburse(leased, "pool")
    _receipts_on_file(leased)
    # As the table's owner: the runtime role may not update a receipt at all,
    # and could not remove the guard below.
    with connect(leased, as_owner=True) as conn:
        with pytest.raises(DatabaseRefusal):
            conn.execute("UPDATE rent_receipt SET amount_paise = amount_paise + 1")
        with pytest.raises(DatabaseRefusal):
            conn.execute("DELETE FROM rent_receipt")
        # Remove the guard, as somebody editing the database directly could.
        conn.execute("DROP TRIGGER trg_rent_receipt_no_update ON rent_receipt")
        conn.execute("UPDATE rent_receipt SET amount_paise = amount_paise + 1 "
                     "WHERE resident_id='alice'")
    receipt = rent.get_resident_rent_receipt(leased, apartment_id=APARTMENT,
                                             settlement_month=MONTH, resident_id="alice")
    assert receipt["hash_matches"] is False


def test_a_missing_pan_is_flagged_when_the_rent_is_large_enough_to_need_one(apartment):
    _lease(apartment, landlord_pan=None)
    _generate(apartment)
    _disburse(apartment, "pool")
    _receipts_on_file(apartment)
    receipt = rent.get_resident_rent_receipt(apartment, apartment_id=APARTMENT,
                                             settlement_month=MONTH, resident_id="alice")
    assert receipt["landlord_pan"] is None and receipt["landlord_pan_advised"] is True


@pytest.mark.parametrize("paise, words", [
    (1_500_000, "Fifteen Thousand Rupees Only"),
    (4_500_000, "Forty-Five Thousand Rupees Only"),
    (1_500_050, "Fifteen Thousand Rupees and Fifty Paise Only"),
    (12_345_678_901, "Twelve Crore Thirty-Four Lakh Fifty-Six Thousand Seven Hundred "
                     "Eighty-Nine Rupees and One Paisa Only"),
    (100, "One Rupee Only"),
    (0, "Zero Rupees Only"),
])
def test_amounts_are_written_out_in_indian_numbering(paise, words):
    assert paise_in_words(paise) == words


# ------------------------------------------------------------------- the API --

@pytest.fixture()
def api(apartment):
    from fastapi.testclient import TestClient

    from aem.main import create_app

    settings = get_settings()
    with TestClient(create_app(settings, database=apartment)) as test_client:
        sign_in(test_client)
        yield test_client


def test_the_rent_workflow_over_http(api):
    assert api.get("/api/rent/config").json() == {"config": None}
    assert api.get(f"/api/rent/cycle/{MONTH}").json()["preview"] is None

    saved = api.post("/api/rent/config", json={**LEASE, "requires_landlord_receipt": True})
    assert saved.status_code == 200 and saved.json()["requires_landlord_receipt"] is True

    preview = api.get(f"/api/rent/cycle/{MONTH}", params={"exclude": "chris"}).json()
    assert preview["cycle"] is None and preview["preview"]["participant_count"] == 2

    generated = api.post(f"/api/rent/cycle/{MONTH}/generate", json={})
    assert generated.status_code == 201
    assert generated.json()["total_rent_paise"] == 4_500_000

    body = {"disbursement_mode": "resident_advance", "funding_resident_id": "alice",
            "reference": "UTR1", "disbursed_date": "2026-11-05"}
    receipt_url = f"/api/rent/cycle/{MONTH}/landlord-receipt"
    early = api.post(receipt_url, json=PDF)
    assert (early.status_code, early.json()["error"]["code"]) == (409, "rent_not_disbursed")

    # Paid on the due date with no receipt in hand.
    paid = api.post(f"/api/rent/cycle/{MONTH}/disburse", json=body)
    assert paid.status_code == 200 and paid.json()["disbursement_status"] == "disbursed"
    assert paid.json()["landlord_receipt_status"] == "pending"
    assert api.get(receipt_url).status_code == 404

    overview = api.get(f"/api/rent/cycle/{MONTH}").json()
    assert overview["cycle"]["funding_resident_name"] == "Alice"
    assert overview["cycle"]["landlord_receipt_status"] == "pending"

    # HRA receipts are switched off (DEBT-001): the payout issued none.
    no_receipt = api.get(f"/api/rent/cycle/{MONTH}/receipt/bob")
    assert (no_receipt.status_code, no_receipt.json()["error"]["code"]) == (
        404, "rent_receipt_not_found")

    # The landlord sends it later.
    attached = api.post(receipt_url, json=PDF)
    assert attached.status_code == 200, attached.text
    assert attached.json()["landlord_receipt_status"] == "attached"
    assert "content" not in attached.json()["landlord_receipt"]
    assert api.get(f"/api/rent/cycle/{MONTH}").json()["cycle"]["landlord_receipt_status"] \
        == "attached"

    download = api.get(receipt_url)
    assert download.content == b"%PDF-1.4 landlord receipt"
    assert download.headers["content-type"] == "application/pdf"
    assert download.headers["content-disposition"].startswith("attachment")

    # Posting again replaces it.
    replaced = api.post(receipt_url, json={**PDF, "filename": "corrected.pdf",
                                           "content_base64": base64.b64encode(b"v2").decode()})
    assert replaced.json()["replaced_landlord_receipt"] is True
    assert api.get(receipt_url).content == b"v2"
    assert api.post(receipt_url, json={**PDF, "content_type": "text/html"}).status_code == 422

    # Bob pays his share; it is recorded against this month's rent.
    pay = api.post(f"/api/rent/cycle/{MONTH}/pay", json={
        "resident_id": "bob", "amount_paise": 1_500_000, "payment_date": "2026-11-06",
        "payment_mode": "bank_transfer", "reference": "NEFT-42", "note": None})
    assert pay.status_code == 200, pay.text
    bob = next(p for p in pay.json()["participants"] if p["resident_id"] == "bob")
    assert (bob["collection_status"], bob["outstanding_paise"]) == ("paid", 0)
    assert pay.json()["collected_count"] == 2, "bob, and alice who paid the landlord"
    for bad in ({"amount_paise": 15000.5}, {"amount_paise": 0}, {"payment_mode": "cheque"},
                {"resident_id": "nobody"}):
        refused = api.post(f"/api/rent/cycle/{MONTH}/pay", json={
            "resident_id": "chris", "amount_paise": 1_500_000,
            "payment_date": "2026-11-06", **bad})
        assert refused.status_code in (404, 422), refused.text

    again = api.post(f"/api/rent/cycle/{MONTH}/disburse", json=body)
    assert (again.status_code, again.json()["error"]["code"]) == (409, "rent_already_disbursed")


@pytest.mark.parametrize("path, body", [
    ("/api/rent/config", {**LEASE, "monthly_rent_paise": 45000.5}),
    ("/api/rent/config", {**LEASE, "monthly_rent_paise": 0}),
    ("/api/rent/config", {**LEASE, "due_day_of_month": 31}),
    ("/api/rent/config", {**LEASE, "rent": 1}),
    (f"/api/rent/cycle/{MONTH}/disburse", {"disbursement_mode": "cash", "reference": "x",
                                           "disbursed_date": "2026-11-04"}),
    ("/api/rent/cycle/2026-13/generate", {}),
])
def test_malformed_requests_are_rejected_before_reaching_the_ledger(api, path, body):
    response = api.post(path, json=body)
    assert response.status_code == 422, response.text
    assert api.get("/api/rent/config").json() == {"config": None}


# ------------------------------------------------- the contract term (0006) --

TERM = {"lease_start_date": "2026-04-01", "duration_months": 11, "notice_period_days": 30,
        "lock_in_months": 6, "security_deposit_paise": 13_500_000}


def _timeline(db, on):
    from datetime import date
    return rent.get_lease_lifecycle_status(rent.get_lease_config(db, APARTMENT),
                                           date.fromisoformat(on))


def _sqlite(path):
    """An old SQLite database file, opened the way the legacy runner expects."""
    from contextlib import closing
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return closing(conn)


def test_migration_0006_adds_the_term_columns_with_their_defaults(leased, tmp_path):
    from aem.persistence.postgres.migrate import latest_version
    from aem.persistence.sqlite_import.legacy_migrations import (
        FINAL_VERSION, current_version, discover, migrate)
    # The SQLite history still ends at 0006, and its runner still gets a file there.
    with _sqlite(tmp_path / "v6.sqlite") as conn:
        migrate(conn)
        assert current_version(conn) == max(m.version for m in discover()) >= 6
        assert current_version(conn) == FINAL_VERSION
        conn.execute("INSERT INTO apartment (apartment_id, name, created_at) VALUES (?,?,?)",
                     (APARTMENT, "Flat 402", "then"))
        conn.execute(
            "INSERT INTO rent_lease_config (config_id, apartment_id, landlord_name, "
            "monthly_rent_paise, created_at, updated_at) VALUES ('lease-1',?,?,?,?,?)",
            (APARTMENT, "Kumar Yadav", 2_850_000, "then", "then"))
        old = dict(conn.execute("SELECT * FROM rent_lease_config").fetchone())
        assert conn.execute("SELECT COUNT(*) FROM rent_lease_term").fetchone()[0] == 0
    # The PostgreSQL schema carries the same columns and the same defaults.
    assert leased.schema_version() == latest_version()
    for row in (old, _rows(leased, "SELECT * FROM rent_lease_config")[0]):
        assert (row["duration_months"], row["notice_period_days"],
                row["lock_in_months"]) == (11, 30, 6)
        assert (row["escalation_type"], row["escalation_percent"], row["lease_status"]) == (
            "percent", 5.0, "active")
        assert row["lease_start_date"] is None and row["security_deposit_paise"] is None
    assert _rows(leased, "SELECT 1 FROM rent_lease_term") == []


def test_migration_0006_upgrades_a_version_5_lease_without_touching_it(tmp_path,
                                                                       database_factory):
    from aem.persistence.sqlite_import.importer import import_sqlite
    from aem.persistence.sqlite_import.legacy_migrations import (
        current_version, discover, migrate, split_statements)

    from .support import config_of
    path = tmp_path / "v5.sqlite"
    with _sqlite(path) as conn:
        conn.execute("CREATE TABLE schema_migration (version INTEGER PRIMARY KEY, name TEXT "
                     "NOT NULL, checksum TEXT NOT NULL, applied_at TEXT NOT NULL, "
                     "adopted INTEGER NOT NULL DEFAULT 0)")
        for migration in discover():
            if migration.version > 5:
                continue
            for statement in split_statements(migration.sql):
                conn.execute(statement)
            conn.execute("INSERT INTO schema_migration VALUES (?,?,?,?,0)",
                         (migration.version, migration.name, migration.checksum, "then"))
        conn.execute("INSERT INTO apartment (apartment_id, name, created_at) VALUES (?,?,?)",
                     (APARTMENT, "Flat 402", "then"))
        conn.execute(
            "INSERT INTO rent_lease_config (config_id, apartment_id, landlord_name, "
            "monthly_rent_paise, created_at, updated_at) VALUES ('lease-1',?,?,?,?,?)",
            (APARTMENT, "Kumar Yadav", 2_850_000, "then", "then"))

    # The upgrade path is now the import: 0006 is applied to a temporary copy of
    # the version 5 file, and the lease is read from PostgreSQL.
    target = database_factory(migrated=False)
    report = import_sqlite(path, config_of(target))
    assert report.source_schema_version == 5
    assert [line for line in report.log if line.startswith("temporary copy: ")] == [
        "temporary copy: applied 0006_lease_lifecycle"]
    config = rent.get_lease_config(target, APARTMENT)
    assert (config["landlord_name"], config["monthly_rent_paise"]) == ("Kumar Yadav", 2_850_000)
    assert (config["duration_months"], config["lease_status"]) == (11, "active")
    # No start date yet, so there is no timeline to report.
    assert config["lifecycle"] is None and config["term_number"] == 1

    # The file itself was not touched by the import, and the SQLite runner still
    # takes it from 5 to 6 with the lease as it was.
    with _sqlite(path) as conn:
        assert current_version(conn) == 5
        assert migrate(conn) == ["applied 0006_lease_lifecycle"]
        row = dict(conn.execute("SELECT * FROM rent_lease_config").fetchone())
    assert (row["landlord_name"], row["monthly_rent_paise"]) == ("Kumar Yadav", 2_850_000)
    assert (row["duration_months"], row["lease_status"]) == (11, "active")


def test_the_term_is_saved_and_its_end_date_is_computed(apartment):
    config = _lease(apartment, **TERM)
    assert config["lease_end_date"] == "2027-02-28"
    assert config["security_deposit_paise"] == 13_500_000
    assert (config["notice_period_days"], config["lock_in_months"]) == (30, 6)
    # A start on the 31st ends on the last day a shorter month has.
    assert _lease(apartment, lease_start_date="2026-03-31",
                  duration_months=11)["lease_end_date"] == "2027-02-27"
    assert _lease(apartment, lease_start_date="2026-03-01",
                  duration_months=11)["lease_end_date"] == "2027-01-31"
    explicit = _lease(apartment, lease_start_date="2026-03-01", lease_end_date="2027-02-15")
    assert explicit["lease_end_date"] == "2027-02-15"


def test_a_save_that_leaves_the_term_out_keeps_it(apartment):
    _lease(apartment, **TERM, escalation_type="fixed", renewal_rent_paise=4_800_000)
    kept = _lease(apartment, monthly_rent_paise=4_600_000)
    assert kept["monthly_rent_paise"] == 4_600_000
    assert (kept["lease_start_date"], kept["lease_end_date"]) == ("2026-04-01", "2027-02-28")
    assert (kept["escalation_type"], kept["renewal_rent_paise"]) == ("fixed", 4_800_000)
    assert kept["security_deposit_paise"] == 13_500_000


@pytest.mark.parametrize("fields", [
    {"lease_start_date": "01-04-2026"},
    {"lease_start_date": "2026-04-01", "duration_months": 0},
    {"lease_start_date": "2026-04-01", "duration_months": 61},
    {"lease_start_date": "2026-04-01", "lease_end_date": "2026-04-01"},
    {"lease_end_date": "2027-02-28"},
    {"notice_period_days": -1},
    {"lock_in_months": 2.5},
    {"security_deposit_paise": 135000.5},
    {"security_deposit_paise": -1},
    {"escalation_type": "sometimes"},
    {"escalation_percent": 101},
    {"escalation_type": "fixed"},
    {"escalation_type": "fixed", "renewal_rent_paise": 0},
])
def test_an_invalid_term_is_refused(apartment, fields):
    with pytest.raises(ValidationError):
        _lease(apartment, **fields)
    assert rent.get_lease_config(apartment, APARTMENT) is None


def test_the_timeline_counts_months_and_opens_the_notice_window(apartment):
    _lease(apartment, **TERM)  # 1 Apr 2026 to 28 Feb 2027; notice from 29 Jan 2027

    before = _timeline(apartment, "2026-03-15")
    assert (before["current_month_index"], before["elapsed_percent"]) == (0, 0)
    assert not before["is_notice_window_active"] and not before["is_expired"]

    first = _timeline(apartment, "2026-04-01")
    assert (first["term_number"], first["current_month_index"]) == (1, 1)
    assert first["is_within_lock_in"] and first["lock_in_end_date"] == "2026-09-30"
    assert _timeline(apartment, "2026-04-30")["current_month_index"] == 1
    assert _timeline(apartment, "2026-05-01")["current_month_index"] == 2
    assert not _timeline(apartment, "2026-10-01")["is_within_lock_in"]

    tenth = _timeline(apartment, "2027-01-11")
    assert (tenth["current_month_index"], tenth["duration_months"]) == (10, 11)
    assert tenth["days_until_expiry"] == 48
    assert tenth["notice_deadline_date"] == "2027-01-29"
    assert not tenth["is_notice_window_active"]

    assert _timeline(apartment, "2027-01-29")["is_notice_window_active"]
    last_day = _timeline(apartment, "2027-02-28")
    assert last_day["is_notice_window_active"] and not last_day["is_expired"]
    assert (last_day["days_until_expiry"], last_day["current_month_index"]) == (0, 11)

    after = _timeline(apartment, "2027-03-01")
    assert after["is_expired"] and not after["is_notice_window_active"]
    assert (after["days_until_expiry"], after["current_month_index"]) == (-1, 11)
    assert after["elapsed_percent"] == 100


@pytest.mark.parametrize("clause, expected", [
    ({}, 2_992_500),                                                    # the default 5%
    ({"escalation_percent": 10}, 3_135_000),
    ({"escalation_percent": 7.5}, 3_063_750),
    ({"escalation_type": "none"}, 2_850_000),
    ({"escalation_type": "fixed", "renewal_rent_paise": 3_000_000}, 3_000_000),
])
def test_the_projected_renewal_rent_follows_the_escalation_clause(apartment, clause, expected):
    _lease(apartment, **TERM, monthly_rent_paise=2_850_000, **clause)
    projected = _timeline(apartment, "2027-01-11")["projected_renewal_rent_paise"]
    assert projected == expected and isinstance(projected, int)


def test_a_projection_that_is_not_a_whole_paisa_rounds_half_up(apartment):
    _lease(apartment, **TERM, monthly_rent_paise=1_000_010, escalation_percent=2.5)
    assert _timeline(apartment, "2027-01-11")["projected_renewal_rent_paise"] == 1_025_010
    _lease(apartment, **TERM, monthly_rent_paise=1_000_030, escalation_percent=2.5)
    assert _timeline(apartment, "2027-01-11")["projected_renewal_rent_paise"] == 1_025_031


def test_renewing_archives_the_term_and_starts_the_next_at_the_escalated_rent(apartment):
    _lease(apartment, **TERM, monthly_rent_paise=2_850_000)
    rent.serve_vacate_notice(apartment, APARTMENT, {
        "notice_served_date": "2027-01-29", "target_handover_date": "2027-02-28"})

    renewed = rent.renew_lease_contract(apartment, APARTMENT, {})
    assert (renewed["lease_start_date"], renewed["lease_end_date"]) == ("2027-03-01", "2028-01-31")
    assert renewed["monthly_rent_paise"] == 2_992_500
    assert (renewed["term_number"], renewed["lifecycle"]["term_number"]) == (2, 2)
    # Renewing withdraws a notice that had been served.
    assert (renewed["lease_status"], renewed["notice_served_date"]) == ("active", None)

    terms = rent.list_lease_terms(apartment, APARTMENT)
    assert len(terms) == 1
    assert (terms[0]["term_number"], terms[0]["start_date"], terms[0]["end_date"]) == (
        1, "2026-04-01", "2027-02-28")
    assert (terms[0]["monthly_rent_paise"], terms[0]["escalation_applied_percent"]) == (
        2_850_000, 5.0)

    again = rent.renew_lease_contract(apartment, APARTMENT, {
        "new_start_date": "2028-02-01", "duration_months": 12,
        "new_monthly_rent_paise": 3_100_000, "escalation_type": "none"})
    assert (again["lease_end_date"], again["term_number"]) == ("2029-01-31", 3)
    assert (again["monthly_rent_paise"], again["escalation_type"]) == (3_100_000, "none")
    assert [t["term_number"] for t in rent.list_lease_terms(apartment, APARTMENT)] == [1, 2]
    actions = [row["action"] for row in _rows(
        apartment, "SELECT action FROM audit_log WHERE entity_type='rent_lease_config'")]
    assert actions.count("renew") == 2 and "notice_served" in actions


def test_a_renewal_needs_a_term_and_must_start_after_it(apartment):
    with pytest.raises(ConflictError):
        rent.renew_lease_contract(apartment, APARTMENT, {})
    _lease(apartment)
    with pytest.raises(ConflictError) as no_term:
        rent.renew_lease_contract(apartment, APARTMENT, {})
    assert no_term.value.code == "lease_term_not_set"
    _lease(apartment, **TERM)
    for bad in ({"new_start_date": "2027-02-28"}, {"new_monthly_rent_paise": 0},
                {"duration_months": 61}, {"term": 2}):
        with pytest.raises(ValidationError):
            rent.renew_lease_contract(apartment, APARTMENT, bad)
    assert rent.list_lease_terms(apartment, APARTMENT) == []
    assert rent.get_lease_config(apartment, APARTMENT)["lease_end_date"] == "2027-02-28"


def test_serving_notice_records_the_dates_and_changes_no_money(leased):
    _lease(leased, **TERM)
    _generate(leased)
    positions = _positions(leased)

    served = rent.serve_vacate_notice(leased, APARTMENT, {
        "notice_served_date": "2026-12-28", "target_handover_date": "2027-01-31",
        "notes": "30-day notice provided"})
    assert served["lease_status"] == "notice_served"
    assert (served["notice_served_date"], served["target_handover_date"]) == (
        "2026-12-28", "2027-01-31")
    assert _positions(leased) == positions
    logged = _rows(leased, "SELECT reason FROM audit_log WHERE action='notice_served'")
    assert logged == [{"reason": "30-day notice provided"}]

    for bad in ({"notice_served_date": "2026-12-28", "target_handover_date": "2026-12-27"},
                {"notice_served_date": "2026-12-28"},
                {"notice_served_date": "soon", "target_handover_date": "2027-01-31"}):
        with pytest.raises(ValidationError):
            rent.serve_vacate_notice(leased, APARTMENT, bad)


def test_an_expired_or_notice_served_lease_still_generates_rent(apartment):
    _lease(apartment, lease_start_date="2025-01-01", duration_months=11)
    from datetime import date
    assert rent.get_lease_lifecycle_status(
        rent.get_lease_config(apartment, APARTMENT), date(2026, 11, 1))["is_expired"]
    rent.serve_vacate_notice(apartment, APARTMENT, {
        "notice_served_date": "2026-10-01", "target_handover_date": "2026-11-30"})
    assert _generate(apartment)["total_rent_paise"] == 4_500_000


def test_the_lease_endpoints_renew_and_serve_notice(household_client):
    client = household_client
    assert client.post("/api/rent/lease/renew", json={}).status_code == 409

    saved = client.post("/api/rent/config", json={
        "landlord_name": "Kumar Yadav", "monthly_rent_paise": 2_850_000,
        "lease_start_date": "2026-03-01", "security_deposit_paise": 13_500_000})
    assert saved.status_code == 200
    assert saved.json()["lease_end_date"] == "2027-01-31"
    assert saved.json()["lifecycle"]["projected_renewal_rent_paise"] == 2_992_500

    # A client that only knows the landlord fields does not wipe the term.
    edited = client.post("/api/rent/config", json={
        "landlord_name": "Kumar Yadav", "monthly_rent_paise": 2_850_000}).json()
    assert (edited["lease_start_date"], edited["security_deposit_paise"]) == (
        "2026-03-01", 13_500_000)
    assert client.post("/api/rent/config", json={
        "landlord_name": "K", "monthly_rent_paise": 1,
        "security_deposit_paise": 1.5}).status_code == 422

    notice = client.post("/api/rent/lease/vacate", json={
        "notice_served_date": "2026-12-28", "target_handover_date": "2027-01-31",
        "notes": "30-day notice provided"})
    assert notice.status_code == 200 and notice.json()["lease_status"] == "notice_served"
    assert client.post("/api/rent/lease/vacate", json={
        "notice_served_date": "2026-12-28"}).status_code == 422

    renewed = client.post("/api/rent/lease/renew", json={
        "new_start_date": "2027-02-01", "duration_months": 11,
        "new_monthly_rent_paise": 2_992_500, "escalation_type": "percent",
        "escalation_percent": 5.0})
    assert renewed.status_code == 200
    body = renewed.json()
    assert (body["lease_start_date"], body["lease_end_date"]) == ("2027-02-01", "2027-12-31")
    assert (body["monthly_rent_paise"], body["term_number"], body["lease_status"]) == (
        2_992_500, 2, "active")
    assert client.get("/api/rent/config").json()["config"] == body
    terms = client.get("/api/rent/lease/terms").json()["items"]
    assert [(t["term_number"], t["monthly_rent_paise"]) for t in terms] == [(1, 2_850_000)]
    assert client.post("/api/rent/lease/renew", json={
        "new_start_date": "2027-06-01"}).status_code == 422


# --------------------------------------------------------------------------- #
# The landlord is the household's owner contact
# --------------------------------------------------------------------------- #

def _owner_contacts(db):
    from aem.services import property as household
    return [c for c in household.list_contacts(db, APARTMENT)["items"] if c["role"] == "owner"]


def test_lease_save_syncs_to_owner_contact(configured):
    """With no owner on file, saving the lease creates one with its details."""
    _lease(configured)
    [owner] = _owner_contacts(configured)
    assert (owner["name"], owner["phone"], owner["pan"], owner["bank_account_no"],
            owner["bank_ifsc"], owner["upi_id"]) == (
        "Rajesh Kumar Sharma", "+91 98765 43210", "ABCPS1234K", "50100012348912",
        "HDFC0000128", "rajeshsharma@okaxis")
    entry = _rows(configured, "SELECT * FROM audit_log WHERE entity_type='household_contact'")
    assert [(e["action"], e["reason"]) for e in entry] == [("create", "synced from the lease")]


def test_a_later_lease_save_updates_the_same_owner_contact(configured):
    _lease(configured)
    _lease(configured, landlord_name="Rajesh K. Sharma", landlord_email="rajesh@example.com",
           landlord_upi_id=None)
    [owner] = _owner_contacts(configured)
    assert (owner["name"], owner["email"], owner["upi_id"]) == (
        "Rajesh K. Sharma", "rajesh@example.com", None)
    # What the owner contact holds that the lease does not is left alone.
    from aem.services import property as household
    household.update_contact(configured, APARTMENT, owner["contact_id"],
                             {"notes": "Prefers calls after 6pm"})
    _lease(configured, monthly_rent_paise=4_600_000)
    assert _owner_contacts(configured)[0]["notes"] == "Prefers calls after 6pm"


def test_saving_an_unchanged_landlord_does_not_touch_the_owner_contact(configured):
    _lease(configured)
    _lease(configured, monthly_rent_paise=4_600_000)
    assert len(_rows(configured, "SELECT 1 FROM audit_log "
                                 "WHERE entity_type='household_contact'")) == 1


def test_the_lease_checks_landlord_details_by_the_owner_contacts_rules(configured):
    for field, value in (("landlord_bank_account", "12345678"),
                         ("landlord_phone", "call after six"),
                         ("landlord_pan", "ABCPS1234"),
                         ("landlord_email", "rajesh@example")):
        with pytest.raises(ValidationError) as caught:
            _lease(configured, **{field: value})
        assert caught.value.details["field"] == field
    assert rent.get_lease_config(configured, APARTMENT) is None
    assert _owner_contacts(configured) == []


# --------------------------------------------------------------------------- #
# Room-tiered rent
# --------------------------------------------------------------------------- #

ROOM_RENTS = {"alice": 2_000_000, "bob": 1_500_000, "chris": 1_000_000}   # = ₹45,000


def _rooms(db, rents=ROOM_RENTS):
    from aem.services import rooms
    for index, (rid, rent_paise) in enumerate(rents.items(), start=1):
        rooms.create_room(db, APARTMENT, {"room_number": f"Room {index}",
                                          "assigned_resident_id": rid,
                                          "base_rent_paise": rent_paise})


def _preview(db, split_mode="equal", excluded=None):
    return rent.rent_month_overview(db, APARTMENT, MONTH, split_mode=split_mode,
                                    excluded_resident_ids=excluded)["preview"]


def test_room_tiered_rent_charges_each_resident_their_rooms_rent(configured):
    _lease(configured)
    _rooms(configured)
    preview = _preview(configured, "room_tiered")
    assert preview["can_generate"] and preview["split_mode"] == "room_tiered"
    assert _shares(preview) == ROOM_RENTS
    assert [row["room_number"] for row in preview["room_tiered"]["shares"]] == [
        "Room 1", "Room 2", "Room 3"]

    cycle = rent.generate_monthly_rent_cycle(configured, APARTMENT, MONTH,
                                             split_mode="room_tiered")
    assert _shares(cycle) == ROOM_RENTS
    assert sum(_shares(cycle).values()) == 4_500_000
    expense = _rows(configured, "SELECT allocation_method FROM expense_instance "
                                "WHERE expense_id=%s", (cycle["expense_id"],))[0]
    assert expense["allocation_method"] == "custom"
    # The ledger charges exactly those shares.
    charges = reconciliation.reconcile(configured, apartment_id=APARTMENT)[
        "allocated_charges_paise_by_resident"]
    assert charges == ROOM_RENTS


def test_the_preview_says_tiered_rent_is_available_without_choosing_it(configured):
    _lease(configured)
    _rooms(configured)
    preview = _preview(configured)
    assert preview["split_mode"] == "equal"
    assert set(_shares(preview).values()) == {1_500_000}
    assert preview["room_tiered"]["available"] is True
    assert preview["room_tiered"]["room_total_paise"] == 4_500_000


def test_tiered_rent_is_refused_when_someone_has_no_room(configured):
    _lease(configured)
    _rooms(configured, {"alice": 2_500_000, "bob": 2_000_000})
    preview = _preview(configured, "room_tiered")
    assert not preview["can_generate"]
    assert "All participating residents must occupy a room with a configured base rent" \
        in preview["blocked_reason"]
    assert "Chris" in preview["blocked_reason"]
    with pytest.raises(ValidationError) as caught:
        rent.generate_monthly_rent_cycle(configured, APARTMENT, MONTH, split_mode="room_tiered")
    assert caught.value.code == "room_tiered_unavailable"
    assert not _rows(configured, "SELECT 1 FROM rent_cycle")


def test_tiered_rent_is_refused_when_a_room_has_no_base_rent(configured):
    _lease(configured)
    _rooms(configured, {"alice": 2_000_000, "bob": 1_500_000, "chris": None})
    with pytest.raises(ValidationError, match="Not set for: Chris"):
        rent.generate_monthly_rent_cycle(configured, APARTMENT, MONTH, split_mode="room_tiered")


def test_tiered_rent_is_refused_when_the_rooms_do_not_add_up(configured):
    _lease(configured)
    _rooms(configured, {"alice": 2_000_000, "bob": 1_500_000, "chris": 900_000})
    with pytest.raises(ValidationError) as caught:
        rent.generate_monthly_rent_cycle(configured, APARTMENT, MONTH, split_mode="room_tiered")
    assert str(caught.value) == ("The sum of room rents (₹44,000.00) does not equal the "
                                 "monthly lease rent (₹45,000.00).")


def test_leaving_someone_out_breaks_the_room_balance(configured):
    _lease(configured)
    _rooms(configured)
    preview = _preview(configured, "room_tiered", excluded=["chris"])
    assert not preview["can_generate"]
    assert "does not equal the monthly lease rent" in preview["blocked_reason"]


def test_an_unknown_split_mode_is_refused(configured):
    _lease(configured)
    with pytest.raises(ValidationError):
        rent.generate_monthly_rent_cycle(configured, APARTMENT, MONTH, split_mode="weighted")


def test_paying_the_landlord_leaves_tiered_shares_alone(configured):
    _lease(configured)
    _rooms(configured)
    rent.generate_monthly_rent_cycle(configured, APARTMENT, MONTH, split_mode="room_tiered")
    result = _disburse(configured, mode="resident_advance", funding_resident_id="bob")
    assert _shares(rent.rent_month_overview(configured, APARTMENT, MONTH)["cycle"]) == ROOM_RENTS
    assert not any("divide evenly" in w for w in result.get("warnings", []))


def test_the_rent_api_previews_and_generates_tiered_rent(configured):
    from fastapi.testclient import TestClient
    from aem.main import create_app
    _lease(configured)
    _rooms(configured)
    with TestClient(create_app(get_settings(), database=configured)) as client:
        sign_in(client)
        preview = client.get(f"/api/rent/cycle/{MONTH}?split_mode=room_tiered").json()["preview"]
        assert _shares(preview) == ROOM_RENTS
        assert client.get(f"/api/rent/cycle/{MONTH}?split_mode=nope").status_code == 422
        made = client.post(f"/api/rent/cycle/{MONTH}/generate",
                           json={"split_mode": "room_tiered"})
        assert made.status_code == 201, made.text
        assert _shares(made.json()) == ROOM_RENTS


# --------------------------------------------------------------------------- #
# Saving part of the lease
# --------------------------------------------------------------------------- #

def test_a_partial_save_changes_only_the_fields_sent(configured):
    _lease(configured, security_deposit_paise=9_000_000, requires_landlord_receipt=True,
           lease_start_date="2026-04-01")
    before = rent.get_lease_config(configured, APARTMENT)
    after = rent.save_lease_config(configured, APARTMENT, {"notice_period_days": 45})
    assert after["notice_period_days"] == 45
    for field in ("landlord_name", "landlord_phone", "landlord_pan", "landlord_bank_account",
                  "landlord_bank_ifsc", "landlord_upi_id", "monthly_rent_paise",
                  "due_day_of_month", "requires_landlord_receipt", "security_deposit_paise",
                  "lease_start_date", "duration_months"):
        assert after[field] == before[field], field


def test_the_receipt_preference_and_deposit_can_be_saved_on_their_own(configured):
    _lease(configured)
    assert rent.save_lease_config(configured, APARTMENT, {"requires_landlord_receipt": True})[
        "requires_landlord_receipt"] is True
    saved = rent.save_lease_config(configured, APARTMENT, {"security_deposit_paise": 9_000_000})
    assert saved["security_deposit_paise"] == 9_000_000
    assert saved["requires_landlord_receipt"] is True
    assert saved["landlord_name"] == "Rajesh Kumar Sharma"


def test_sending_null_still_clears_a_field(configured):
    _lease(configured)
    assert rent.save_lease_config(configured, APARTMENT, {"landlord_upi_id": None})[
        "landlord_upi_id"] is None


def test_a_new_lease_still_needs_the_landlord_and_the_rent(configured):
    with pytest.raises(ValidationError):
        rent.save_lease_config(configured, APARTMENT, {"monthly_rent_paise": 4_500_000})
    with pytest.raises(ValidationError):
        rent.save_lease_config(configured, APARTMENT, {"landlord_name": "Rajesh"})
    assert rent.get_lease_config(configured, APARTMENT) is None


def test_the_api_saves_part_of_the_lease(configured):
    from fastapi.testclient import TestClient
    from aem.main import create_app
    _lease(configured)
    with TestClient(create_app(get_settings(), database=configured)) as client:
        sign_in(client)
        saved = client.post("/api/rent/config", json={"requires_landlord_receipt": True})
        assert saved.status_code == 200, saved.text
        body = saved.json()
        assert body["requires_landlord_receipt"] is True
        assert body["landlord_bank_account"] == "50100012348912"
        assert client.post("/api/rent/config", json={"landlord_name": None}).status_code == 422


# --------------------------------------------------------------------------- #
# The household's agreement model
# --------------------------------------------------------------------------- #

def test_a_lease_starts_with_the_equal_split_model(configured):
    assert _lease(configured)["rent_split_mode"] == "equal"


def test_the_agreement_model_is_saved_on_its_own_and_drives_the_month(configured):
    _lease(configured)
    _rooms(configured)
    saved = rent.save_lease_config(configured, APARTMENT, {"rent_split_mode": "room_tiered"})
    assert saved["rent_split_mode"] == "room_tiered"
    assert saved["landlord_name"] == "Rajesh Kumar Sharma"
    # With no split asked for, the preview and the generated month follow the model.
    preview = _preview_default(configured)
    assert preview["split_mode"] == "room_tiered"
    assert _shares(preview) == ROOM_RENTS
    cycle = rent.generate_monthly_rent_cycle(configured, APARTMENT, MONTH)
    assert cycle["split_mode"] == "room_tiered"
    assert _shares(cycle) == ROOM_RENTS


def test_a_month_can_still_be_split_another_way_than_the_model(configured):
    _lease(configured)
    _rooms(configured)
    rent.save_lease_config(configured, APARTMENT, {"rent_split_mode": "room_tiered"})
    cycle = rent.generate_monthly_rent_cycle(configured, APARTMENT, MONTH, split_mode="equal")
    assert cycle["split_mode"] == "equal"
    assert set(_shares(cycle).values()) == {1_500_000}


def test_an_unknown_agreement_model_is_refused(configured):
    _lease(configured)
    with pytest.raises(ValidationError):
        rent.save_lease_config(configured, APARTMENT, {"rent_split_mode": "sqft_ratio"})
    assert rent.get_lease_config(configured, APARTMENT)["rent_split_mode"] == "equal"


def test_a_room_tiered_model_that_does_not_add_up_blocks_generation_with_the_reason(configured):
    _lease(configured)
    rent.save_lease_config(configured, APARTMENT, {"rent_split_mode": "room_tiered"})
    preview = _preview_default(configured)
    assert not preview["can_generate"]
    assert "All participating residents must occupy a room" in preview["blocked_reason"]


def test_an_equal_month_reports_its_split(configured):
    _lease(configured)
    assert rent.generate_monthly_rent_cycle(configured, APARTMENT, MONTH)["split_mode"] == "equal"


def _preview_default(db):
    return rent.rent_month_overview(db, APARTMENT, MONTH)["preview"]


# ------------------------------------------------- paying the landlord in parts --
#
# The landlord is due on the 5th; Alice and Bob have paid the pool by then and
# Chris pays on the 7th. The household pays the landlord ₹30,000 on the 5th and
# the last ₹15,000 on the 7th.

def _tranche(db, amount_paise, mode="pool", **extra):
    return _disburse(db, mode, amount_paise=amount_paise, **extra)


def test_tranche_disbursement_two_parts(leased):
    _generate(leased)
    before = _positions(leased)
    first = _tranche(leased, 3_000_000, reference="UTR-0005", disbursed_date="2026-11-05",
                     note="What was collected by the due date")
    assert (first["disbursement_status"], first["disbursed_paise"],
            first["remaining_disbursement_paise"]) == ("partially_disbursed", 3_000_000, 1_500_000)
    assert [(t["amount_paise"], t["disbursement_reference"], t["note"])
            for t in first["disbursements"]] == [
        (3_000_000, "UTR-0005", "What was collected by the due date")]
    assert first["disbursement_id"] == first["disbursements"][0]["disbursement_id"]

    # Left out, the amount is everything still owed.
    second = _disburse(leased, "pool", reference="UTR-0007", disbursed_date="2026-11-07")
    assert (second["disbursement_status"], second["disbursed_paise"],
            second["remaining_disbursement_paise"]) == ("disbursed", 4_500_000, 0)
    assert [t["amount_paise"] for t in second["disbursements"]] == [3_000_000, 1_500_000]
    assert (second["disbursed_at"], second["disbursement_reference"]) == (
        "2026-11-07", "UTR-0007"), "the cycle shows the latest tranche"
    assert second["disbursed_by"] == "pool"
    assert _positions(leased) == before, "paying the landlord from the pool adds nothing"
    assert validation.validate(leased, APARTMENT)["issues"] == []

    with pytest.raises(ConflictError) as done:
        _tranche(leased, 100)
    assert done.value.code == "rent_already_disbursed"
    with connect(leased) as conn, pytest.raises(DatabaseRefusal):
        conn.execute("UPDATE rent_cycle SET disbursement_reference='edited'")


def test_disbursement_cannot_exceed_remaining(leased):
    _generate(leased)
    with pytest.raises(ValidationError) as over:
        _tranche(leased, 4_600_000)
    assert over.value.code == "tranche_exceeds_remaining"
    assert over.value.details["remaining_paise"] == 4_500_000
    _tranche(leased, 3_000_000)
    with pytest.raises(ValidationError) as over_rest:
        _tranche(leased, 1_500_001)
    assert over_rest.value.details["remaining_paise"] == 1_500_000
    for bad in (0, -1, 1500.5, True):
        with pytest.raises(ValidationError):
            _tranche(leased, bad)
    assert [r["amount_paise"] for r in _rows(leased, "SELECT amount_paise FROM rent_disbursement")] \
        == [3_000_000]


def test_a_tranche_out_of_order_does_not_move_the_latest_date_back(leased):
    _generate(leased)
    _tranche(leased, 1_000_000, reference="UTR-LATE", disbursed_date="2026-11-07")
    cycle = _tranche(leased, 1_000_000, reference="UTR-EARLY", disbursed_date="2026-11-03")
    assert (cycle["disbursed_at"], cycle["disbursement_reference"]) == ("2026-11-07", "UTR-LATE")
    assert [t["disbursed_at"] for t in cycle["disbursements"]] == ["2026-11-03", "2026-11-07"]


def test_resident_advance_tranche(leased):
    _generate(leased)
    _tranche(leased, 3_000_000)
    cycle = _tranche(leased, 1_500_000, "resident_advance", funding_resident_id="alice",
                     reference="UTR-ALICE")
    assert (cycle["disbursement_status"], cycle["disbursed_by"]) == ("disbursed", "mixed")
    advance = _rows(leased, "SELECT * FROM payment")
    assert len(advance) == 1
    assert (advance[0]["payment_kind"], advance[0]["payer_resident_id"],
            advance[0]["amount_paise"], advance[0]["expense_id"], advance[0]["reference"]) == (
        "provider_advance", "alice", 1_500_000, cycle["expense_id"], "UTR-ALICE")
    assert cycle["disbursements"][1]["funding_payment_id"] == advance[0]["payment_id"]
    assert cycle["disbursements"][1]["funding_resident_name"] == "Alice"
    # Alice paid ₹15,000 and owes ₹15,000: she is square; the others still owe.
    assert _positions(leased) == {"alice": 0, "bob": -1_500_000, "chris": -1_500_000,
                                  "__pool__": 3_000_000}
    assert expenses.read_expense(leased, cycle["expense_id"])["payer_resident_id"] is None, \
        "a part payment does not make Alice the expense's payer"
    status = {p["resident_id"]: p["collection_status"] for p in cycle["participants"]}
    assert status == {"alice": "funded_payout", "bob": "unpaid", "chris": "unpaid"}
    assert validation.validate(leased, APARTMENT)["issues"] == []


def test_an_advance_larger_than_the_share_covers_it_and_is_owed_back(leased):
    _generate(leased)
    first = _tranche(leased, 3_000_000, "resident_advance", funding_resident_id="alice")
    assert first["disbursement_status"] == "partially_disbursed"
    assert first["funding_resident_id"] == "alice"
    assert _positions(leased) == {"alice": 1_500_000, "bob": -1_500_000, "chris": -1_500_000,
                                  "__pool__": 1_500_000}
    assert (first["collected_paise"], first["collected_count"]) == (1_500_000, 1)
    # A part advance is no reason to refuse the rest from the pool.
    second = _tranche(leased, 1_500_000)
    assert second["disbursement_status"] == "disbursed"
    assert sum(_positions(leased).values()) == 0


def test_a_resident_whose_advance_covers_their_share_cannot_pay_it_again(owing):
    _tranche(owing, 500_000, "resident_advance", funding_resident_id="alice")
    # ₹5,000 does not cover Alice's ₹15,000: she can still pay her share.
    _pay_rent(owing, "alice", 1_500_000)
    _tranche(owing, 1_500_000, "resident_advance", funding_resident_id="bob")
    with pytest.raises(ConflictError) as covered:
        _pay_rent(owing, "bob", 1_500_000)
    assert covered.value.code == "rent_share_funded_by_payout"


def test_an_advance_behind_a_tranche_cannot_be_reversed(leased):
    _generate(leased)
    cycle = _tranche(leased, 1_000_000, "resident_advance", funding_resident_id="bob")
    with pytest.raises(ConflictError) as refused:
        payments.reverse_payment(leased, cycle["disbursements"][0]["funding_payment_id"],
                                 reason="wrong resident")
    assert refused.value.code == "rent_funding_payment"


def test_a_part_paid_month_keeps_its_record(leased):
    _generate(leased)
    cycle = _tranche(leased, 1_000_000)
    with pytest.raises(ConflictError) as kept:
        expenses.delete_expense(leased, cycle["expense_id"], apartment_id=APARTMENT)
    assert kept.value.code == "rent_cycle_disbursed"
    with connect(leased, as_owner=True) as conn:
        with pytest.raises(DatabaseRefusal):
            conn.execute("DELETE FROM rent_cycle")
        with pytest.raises(DatabaseRefusal):
            conn.execute("UPDATE rent_disbursement SET amount_paise = 1")
        with pytest.raises(DatabaseRefusal):
            conn.execute("DELETE FROM rent_disbursement")
    # Even written directly, tranches cannot add up to more than the rent.
    with connect(leased) as conn, pytest.raises(DatabaseRefusal) as overpaid:
        conn.execute(
            """INSERT INTO rent_disbursement (disbursement_id, cycle_id, apartment_id,
                   settlement_month, amount_paise, disbursed_at, disbursement_reference,
                   disbursed_by, created_at)
               VALUES ('x', %s, %s, %s, 3500001, '2026-11-06', 'UTR-X', 'pool', 'now')""",
            (cycle["cycle_id"], APARTMENT, MONTH))
    assert "overpaid_rent" in str(overpaid.value)


def test_the_pool_cannot_pay_in_parts_what_the_expense_says_a_resident_paid(configured):
    _lease(configured)
    recurring.generate(configured, apartment_id=APARTMENT, settlement_period=MONTH,
                       lines=[{"expense_definition_id": "rent", "payer_resident_id": "bob"}])
    _generate(configured)
    for attempt in (dict(amount_paise=1_000_000),
                    dict(amount_paise=1_000_000, mode="resident_advance",
                         funding_resident_id="bob")):
        mode = attempt.pop("mode", "pool")
        with pytest.raises(ConflictError) as refused:
            _disburse(configured, mode, **attempt)
        assert refused.value.code == "rent_already_funded"
    assert _rows(configured, "SELECT 1 FROM rent_disbursement") == []


def test_the_landlords_receipt_can_be_attached_after_the_first_tranche(receipted):
    cycle = _tranche(receipted, 3_000_000)
    assert cycle["landlord_receipt_status"] == "pending"
    attached = rent.attach_landlord_receipt(receipted, apartment_id=APARTMENT,
                                            settlement_month=MONTH, landlord_receipt=PDF)
    assert attached["landlord_receipt_status"] == "attached"
    assert attached["disbursement_status"] == "partially_disbursed"


def test_the_passbooks_list_each_tranche(leased):
    _generate(leased)
    _tranche(leased, 3_000_000, reference="UTR-0005", disbursed_date="2026-11-05")
    _tranche(leased, 1_500_000, "resident_advance", funding_resident_id="chris",
             reference="UTR-0007", disbursed_date="2026-11-07")
    books = pool_statements.get_payment_pools_statement(leased, APARTMENT)
    kitty = [e for e in books["pool_statement"]["items"] if e["category"] == "Rent Payout"]
    landlord = [e for e in books["landlord_statement"]["items"]
                if e["category"] == "Rent Payout"]
    assert [(e["amount_paise"], e["reference"]) for e in kitty] == [(3_000_000, "UTR-0005")]
    assert sorted((e["amount_paise"], e["counterparty_name"]) for e in landlord) == [
        (1_500_000, "Chris (Advance)"), (3_000_000, "House Kitty")]
    assert not [e for e in books["landlord_statement"]["items"]
                if e["category"] == "Direct Advance"], "the advance is listed once"


def test_tranches_over_http(api):
    api.post("/api/rent/config", json=LEASE)
    assert api.post(f"/api/rent/cycle/{MONTH}/generate", json={}).status_code == 201
    url = f"/api/rent/cycle/{MONTH}/disburse"
    body = {"disbursement_mode": "pool", "reference": "UTR-0005", "disbursed_date": "2026-11-05"}
    too_much = api.post(url, json={**body, "amount_paise": 4_600_000})
    assert (too_much.status_code, too_much.json()["error"]["code"]) == (
        422, "tranche_exceeds_remaining")
    assert api.post(url, json={**body, "amount_paise": 0}).status_code == 422
    assert api.post(url, json={**body, "amount_paise": 1500.5}).status_code == 422
    part = api.post(url, json={**body, "amount_paise": 3_000_000, "note": "By the due date"})
    assert part.status_code == 200, part.text
    assert (part.json()["disbursement_status"], part.json()["remaining_disbursement_paise"]) == (
        "partially_disbursed", 1_500_000)
    rest = api.post(url, json={**body, "reference": "UTR-0007", "disbursed_date": "2026-11-07"})
    assert rest.json()["disbursement_status"] == "disbursed"
    cycle = api.get(f"/api/rent/cycle/{MONTH}").json()["cycle"]
    assert [t["amount_paise"] for t in cycle["disbursements"]] == [3_000_000, 1_500_000]


def test_migration_0020_turns_each_paid_month_into_one_tranche(database_factory):
    db = database_factory(migrated=False)
    db.migrate(target_version=19)
    with connect(db, as_owner=True) as conn:
        conn.execute("INSERT INTO apartment (apartment_id, name, created_at) "
                     "VALUES ('home', 'Flat', '2026-10-01T00:00:00+05:30')")
        conn.execute(
            """INSERT INTO rent_cycle (cycle_id, apartment_id, settlement_month,
                   total_rent_paise, participant_count, disbursement_status, disbursed_at,
                   disbursement_reference, disbursed_by, created_at)
               VALUES ('paid', 'home', '2026-10', 4500000, 3, 'disbursed', '2026-10-05',
                       'UTR-OCT', 'pool', '2026-10-01T09:00:00+05:30'),
                      ('open', 'home', '2026-11', 4500000, 3, 'pending', NULL,
                       NULL, NULL, '2026-11-01T09:00:00+05:30')""")
    db.migrate()
    rows = _rows(db, "SELECT cycle_id, amount_paise, disbursed_at, disbursement_reference, "
                     "disbursed_by FROM rent_disbursement")
    assert rows == [{"cycle_id": "paid", "amount_paise": 4_500_000, "disbursed_at": "2026-10-05",
                     "disbursement_reference": "UTR-OCT", "disbursed_by": "pool"}]

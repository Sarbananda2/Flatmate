"""Furniture rental: the subscription, the items, and each month's bill.

The bill is an ordinary expense, so these tests pin what reaches the ledger:
the common items split equally with the left-over paise going one each in
resident order, each resident's own items charged to them alone, nothing
prorated, and a month billed exactly once.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from aem.config import get_settings
from aem.errors import ConflictError, ValidationError
from aem.services import (
    apartments,
    definitions,
    expenses,
    furniture_rental as furniture,
    payments,
    pool_statements,
    reconciliation,
    recurring,
    residents,
    statements,
    validation,
)

from .conftest import APARTMENT, RESIDENTS
from .support import DatabaseRefusal, connect, sign_in

MONTH = "2026-11"


def _rows(db, sql, params=()):
    with connect(db, read_only=True) as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _setup(db, **payload):
    return furniture.setup_furniture_rental(db, APARTMENT, payload, settlement_month=MONTH)


def _item(db, name, rent_paise, scope="common", resident=None, **extra):
    return furniture.add_item(db, APARTMENT, {
        "name": name, "category": extra.pop("category", "living_room"),
        "monthly_rent_paise": rent_paise, "allocation_scope": scope,
        "assigned_resident_id": resident, **extra})


def _month(db, month=MONTH):
    return furniture.get_overview(db, APARTMENT, month)["month"]


@pytest.fixture()
def rented(apartment):
    """Furlenco, with a sofa and a fridge for everyone and Bob's own bed."""
    _setup(apartment, provider_name="Furlenco", billing_day_of_month=5,
           deposit_paid_paise=500_000)
    _item(apartment, "Sofa", 100_000, delivery_date="2026-09-01")
    _item(apartment, "Fridge", 70_000, category="appliance", delivery_date="2026-09-01")
    _item(apartment, "Queen bed", 50_000, scope="resident", resident="bob",
          category="bedroom", delivery_date="2026-09-01")
    return apartment


# ----------------------------------------------------------------- setup --

def test_setup_starts_the_service_and_a_non_recurring_definition(apartment):
    overview = _setup(apartment)
    assert (overview["service"]["service_type"], overview["service"]["display_name"],
            overview["service"]["provider_name"]) == ("furniture_rental", "Furniture rental",
                                                      "Furlenco")
    assert (overview["config"]["deposit_status"], overview["deposit_held_paise"]) == ("held", 0)
    definition = _rows(apartment, "SELECT * FROM expense_definition WHERE expense_definition_id=%s",
                       (overview["config"]["expense_definition_id"],))[0]
    assert (definition["name"], definition["category"], definition["recurrence"],
            definition["is_recurring"]) == ("Furniture rental", "Housing", "none", 0)


def test_setup_uses_the_households_own_furniture_definition(apartment):
    definitions.create_expense_definition(
        apartment, apartment_id=APARTMENT, definition_id="furniture_rental",
        name="Furniture rental", category="Housing", period_rule="current_month",
        recurrence="monthly")
    assert _setup(apartment)["config"]["expense_definition_id"] == "furniture_rental"
    assert len(_rows(apartment, "SELECT 1 FROM expense_definition "
                                "WHERE lower(name)='furniture rental'")) == 1


def test_setting_up_again_changes_only_what_is_sent(apartment):
    first = _setup(apartment, provider_name="Rentomojo", deposit_paid_paise=500_000,
                   customer_id="RM-1182")
    again = _setup(apartment, deposit_refunded_paise=200_000)
    assert again["service"]["service_id"] == first["service"]["service_id"]
    assert (again["config"]["provider_name"], again["config"]["customer_id"]) == (
        "Rentomojo", "RM-1182")
    assert (again["config"]["deposit_status"], again["deposit_held_paise"]) == (
        "partially_refunded", 300_000)
    assert _setup(apartment, deposit_refunded_paise=500_000)["config"]["deposit_status"] == "refunded"
    with pytest.raises(ValidationError):
        _setup(apartment, deposit_refunded_paise=600_000)
    assert len(_rows(apartment, "SELECT 1 FROM household_service")) == 1


def test_next_billing_date_falls_on_the_billing_day_or_the_months_last():
    assert furniture.next_billing_date(5, "2026-11-03") == "2026-11-05"
    assert furniture.next_billing_date(5, "2026-11-05") == "2026-11-05"
    assert furniture.next_billing_date(5, "2026-11-06") == "2026-12-05"
    assert furniture.next_billing_date(31, "2026-11-20") == "2026-11-30"
    assert furniture.next_billing_date(10, "2026-12-15") == "2027-01-10"
    assert furniture.next_billing_date(None, "2026-11-20") is None


# ----------------------------------------------------------------- items --

def test_an_item_needs_the_service_first(apartment):
    with pytest.raises(ConflictError) as refused:
        _item(apartment, "Sofa", 100_000)
    assert refused.value.code == "furniture_not_configured"


@pytest.mark.parametrize("payload", [
    {"allocation_scope": "common", "assigned_resident_id": "bob"},
    {"allocation_scope": "resident", "assigned_resident_id": None},
    {"allocation_scope": "resident", "assigned_resident_id": "zoe"},
    {"monthly_rent_paise": 0},
    {"category": "kitchen"},
    {"status": "returned"},
    {"delivery_date": "2026-02-30"},
])
def test_an_invalid_item_is_refused(apartment, payload):
    _setup(apartment)
    item = {"name": "Desk", "category": "workstation", "monthly_rent_paise": 30_000,
            "allocation_scope": "common", "assigned_resident_id": None, **payload}
    with pytest.raises(ValidationError):
        furniture.add_item(apartment, APARTMENT, item)
    assert _rows(apartment, "SELECT 1 FROM furniture_rental_item") == []


def test_an_item_is_not_assigned_to_someone_who_has_moved_out(apartment):
    _setup(apartment)
    residents.deactivate_resident(apartment, "chris", active_until="2026-10-31")
    with pytest.raises(ValidationError):
        _item(apartment, "Desk", 30_000, scope="resident", resident="chris")


def test_an_item_is_changed_returned_and_brought_back(rented):
    bed = next(item for item in furniture.get_overview(rented, APARTMENT, MONTH)["items"]
               if item["name"] == "Queen bed")
    shared = furniture.update_item(rented, APARTMENT, bed["item_id"],
                                   {"allocation_scope": "common"})
    assert (shared["allocation_scope"], shared["assigned_resident_id"]) == ("common", None)

    with pytest.raises(ValidationError):
        furniture.update_item(rented, APARTMENT, bed["item_id"],
                              {"status": "returned", "return_date": "2026-08-01"})
    returned = furniture.update_item(rented, APARTMENT, bed["item_id"],
                                     {"status": "returned", "return_date": "2026-11-20"})
    assert (returned["status"], returned["return_date"]) == ("returned", "2026-11-20")
    back = furniture.update_item(rented, APARTMENT, bed["item_id"], {"status": "active"})
    assert (back["status"], back["return_date"]) == ("active", None)

    furniture.delete_item(rented, APARTMENT, bed["item_id"])
    assert [item["name"] for item in furniture.get_overview(rented, APARTMENT, MONTH)["items"]] == [
        "Sofa", "Fridge"]


@pytest.mark.parametrize("item, billed", [
    ({"status": "active", "delivery_date": "2026-11-28"}, True),
    ({"status": "active", "delivery_date": "2026-12-01"}, False),
    ({"status": "active", "delivery_date": None}, True),
    ({"status": "pending_delivery", "delivery_date": None}, False),
    # Nothing prorates: returned during the month is the month in full.
    ({"status": "returned", "delivery_date": "2026-09-01", "return_date": "2026-11-02"}, True),
    ({"status": "returned", "delivery_date": "2026-09-01", "return_date": "2026-10-31"}, False),
])
def test_a_month_bills_what_was_in_the_flat_at_any_point_in_it(item, billed):
    assert furniture.is_billable(item, MONTH) is billed


# ------------------------------------------------------------- the bill --

def test_common_items_split_equally_and_own_items_go_to_their_resident(rented):
    month = _month(rented)
    assert (month["common_total_paise"], month["private_total_paise"], month["total_paise"]) == (
        170_000, 50_000, 220_000)
    # ₹1,700 three ways: the two paise over go one each, in resident order.
    shares = {row["resident_id"]: (row["common_share_paise"], row["private_paise"],
                                   row["total_paise"]) for row in month["shares"]}
    assert shares == {"alice": (56_667, 0, 56_667), "bob": (56_667, 50_000, 106_667),
                      "chris": (56_666, 0, 56_666)}
    assert (month["base_share_paise"], month["remainder_paise"]) == (56_666, 2)
    assert month["can_generate"] and month["expense"] is None


def test_generating_records_one_expense_with_those_shares(rented):
    draft = statements.create_monthly_statement(rented, apartment_id=APARTMENT,
                                                settlement_month=MONTH)
    month = furniture.generate_month(rented, APARTMENT, MONTH)["month"]
    expense = _rows(rented, "SELECT * FROM expense_instance")[0]
    assert (expense["amount_paise"], expense["settlement_period"], expense["service_period"],
            expense["due_date"], expense["statement_id"], expense["provider_name"]) == (
        220_000, MONTH, MONTH, "2026-11-05", draft, "Furlenco")
    assert expense["description"] == "Furniture rental for November 2026"
    assert "Sofa ₹1,000.00" in expense["notes"] and "Bob: Queen bed ₹500.00" in expense["notes"]
    allocations = {row["resident_id"]: row["amount_paise"] for row in _rows(
        rented, "SELECT resident_id, amount_paise FROM allocation WHERE expense_id=%s",
        (expense["expense_id"],))}
    assert allocations == {"alice": 56_667, "bob": 106_667, "chris": 56_666}
    assert (month["expense"]["expense_id"], month["can_generate"]) == (expense["expense_id"], False)

    with pytest.raises(ConflictError) as again:
        furniture.generate_month(rented, APARTMENT, MONTH)
    assert again.value.code == "furniture_bill_exists"
    assert len(_rows(rented, "SELECT 1 FROM expense_instance")) == 1
    # Still an ordinary expense to the ledger, which stays balanced.
    assert sum(reconciliation.reconcile(
        rented, apartment_id=APARTMENT)["net_positions_paise_by_account"].values()) == 0
    assert validation.validate(rented, APARTMENT)["issues"] == []


def test_a_resident_left_out_shares_no_common_items(rented):
    furniture.generate_month(rented, APARTMENT, MONTH, ["chris"])
    allocations = {row["resident_id"]: row["amount_paise"] for row in _rows(
        rented, "SELECT resident_id, amount_paise FROM allocation")}
    assert allocations == {"alice": 85_000, "bob": 135_000}


@pytest.mark.parametrize("excluded, code", [
    (["bob"], "furniture_bill_blocked"),          # Bob's own bed, but Bob left out
    (["alice", "bob", "chris"], "furniture_bill_blocked"),
])
def test_a_bill_that_cannot_be_shared_is_refused(rented, excluded, code):
    with pytest.raises(ValidationError) as refused:
        furniture.generate_month(rented, APARTMENT, MONTH, excluded)
    assert refused.value.code == code
    assert _rows(rented, "SELECT 1 FROM expense_instance") == []


def test_a_month_with_nothing_in_the_flat_is_not_billed(rented):
    assert "nothing to bill" in _month(rented, "2026-08")["blocked_reason"]
    with pytest.raises(ValidationError):
        furniture.generate_month(rented, APARTMENT, "2026-08")


def test_a_finalized_month_is_not_billed(rented):
    statement = statements.create_monthly_statement(rented, apartment_id=APARTMENT,
                                                    settlement_month=MONTH)
    statements.finalize_statement(rented, statement_id=statement, finalized_by="operator",
                                  require_clean=False)
    assert _month(rented)["can_generate"] is False
    with pytest.raises(ConflictError) as refused:
        furniture.generate_month(rented, APARTMENT, MONTH)
    assert refused.value.code == "statement_not_draft"


def test_the_recurring_generator_sees_a_billed_month_as_done(apartment):
    definitions.create_expense_definition(
        apartment, apartment_id=APARTMENT, definition_id="furniture_rental",
        name="Furniture rental", category="Housing", period_rule="current_month",
        recurrence="monthly", default_amount_paise=220_000)
    _setup(apartment)
    _item(apartment, "Sofa", 100_000)
    furniture.generate_month(apartment, APARTMENT, MONTH)
    lines = {line["expense_definition_id"]: line for line in recurring.preview(
        apartment, apartment_id=APARTMENT, settlement_period=MONTH)["lines"]}
    assert lines["furniture_rental"]["status"] == "already_exists"


# ------------------------------------------------------ paying the bill --

@pytest.fixture()
def billed(rented):
    """November recorded: Alice ₹566.67, Bob ₹1,066.67 and Chris ₹566.66 of ₹2,200."""
    furniture.generate_month(rented, APARTMENT, MONTH)
    return rented


def _into_kitty(db, resident, paise, payment_id):
    payments.record_payment(db, apartment_id=APARTMENT, payment_id=payment_id,
                            payer_resident_id=resident, amount_paise=paise,
                            payment_date="2026-11-08")


def _net(db):
    return reconciliation.reconcile(db, apartment_id=APARTMENT)["net_positions_paise_by_account"]


def _statuses(month):
    return {row["resident_id"]: (row["status"], row["paid_paise"], row["paid_date"])
            for row in month["shares"]}


def _payout(db, mode="pool", **details):
    """A payout to Furlenco towards November's bill: from the kitty unless told otherwise."""
    details = {"reference": "UTR-4471", "disbursed_date": "2026-11-05", **details}
    return furniture.record_provider_payout(db, APARTMENT, MONTH, disbursement_mode=mode,
                                            **details)["month"]


def _advance(db, resident="alice", **details):
    """A resident paying Furlenco from their own account."""
    return _payout(db, "resident_advance", funding_resident_id=resident, **details)


def test_a_payment_into_the_kitty_settles_the_residents_share_first(billed):
    # Chris also owes for October's milk, which oldest-first alone would pay off first.
    expenses.create_expense(billed, apartment_id=APARTMENT, expense_id="milk-2026-10",
                            description="Milk", amount_paise=30_000, expense_date="2026-10-01",
                            allocations_paise={"alice": 0, "bob": 0, "chris": 30_000})
    month = furniture.record_furniture_payment(
        billed, APARTMENT, MONTH, "chris", amount_paise=56_666, payment_date="2026-11-08",
        payment_mode="upi", reference="UPI-301", note="GPay")["month"]
    payment = _rows(billed, "SELECT * FROM payment")[0]
    assert (payment["payment_kind"], payment["payer_resident_id"], payment["amount_paise"],
            payment["payment_mode"], payment["reference"], payment["note"]) == (
        "to_pool", "chris", 56_666, "upi", "UPI-301", "GPay")
    chris = next(row for row in month["shares"] if row["resident_id"] == "chris")
    assert (chris["status"], chris["paid_paise"], chris["outstanding_paise"],
            chris["paid_date"]) == ("paid", 56_666, 0, "2026-11-08")
    assert chris["payments"] == [{"payment_id": payment["payment_id"],
                                  "payment_date": "2026-11-08", "amount_paise": 56_666,
                                  "payment_mode": "upi", "reference": "UPI-301", "note": "GPay"}]
    assert _rows(billed, """SELECT pa.amount_paise FROM payment_application pa
                              JOIN allocation a ON a.allocation_id = pa.allocation_id
                             WHERE a.expense_id = 'milk-2026-10'""") == [], "the milk is still owed"

    with pytest.raises(ConflictError) as again:
        furniture.record_furniture_payment(billed, APARTMENT, MONTH, "chris", amount_paise=100,
                                           payment_date="2026-11-09")
    assert again.value.code == "furniture_share_already_paid"


def test_a_share_paid_by_paying_the_provider_or_not_on_the_bill_takes_no_payment(rented):
    furniture.generate_month(rented, APARTMENT, MONTH, ["chris"])
    _advance(rented)
    for resident, code in (("alice", "furniture_share_already_paid"),
                           ("chris", "not_a_furniture_participant")):
        with pytest.raises((ConflictError, ValidationError)) as refused:
            furniture.record_furniture_payment(rented, APARTMENT, MONTH, resident,
                                               amount_paise=10_000, payment_date="2026-11-08")
        assert refused.value.code == code
    # Only Alice's own ₹850 share, paid to Furlenco through the kitty.
    assert _rows(rented, "SELECT payer_resident_id, amount_paise FROM payment "
                         "WHERE payment_kind = 'to_pool'") == [
        {"payer_resident_id": "alice", "amount_paise": 85_000}]


def test_disburse_payout_from_pool_marks_provider_paid_with_no_reimbursement(billed):
    _into_kitty(billed, "bob", 106_667, "pay-bob")
    month = _payout(billed, notes="Paid on the billing day")
    tranche = _rows(billed, "SELECT * FROM service_disbursement")[0]
    assert (tranche["disbursed_by"], tranche["amount_paise"], tranche["disbursement_reference"],
            tranche["funding_resident_id"], tranche["funding_payment_id"],
            tranche["settlement_month"]) == ("pool", 220_000, "UTR-4471", None, None, MONTH)
    assert (month["payout_status"], month["disbursed_paise"],
            month["remaining_disbursement_paise"]) == ("disbursed", 220_000, 0)
    assert [(t["disbursed_by"], t["disbursed_at"], t["note"]) for t in month["disbursements"]] == [
        ("pool", "2026-11-05", "Paid on the billing day")]
    # The kitty paid, so nobody is owed anything back and no share is covered by it.
    assert month["reimbursement"] is None
    assert _statuses(month)["alice"] == ("unpaid", 0, None)
    # Nothing reaches the ledger: the bill was already the kitty's cost.
    assert _rows(billed, "SELECT payment_kind FROM payment") == [{"payment_kind": "to_pool"}]
    assert validation.validate(billed, APARTMENT)["issues"] == []

    with pytest.raises(ConflictError) as again:
        _payout(billed, amount_paise=1)
    assert again.value.code == "furniture_already_disbursed"


def test_internal_collection_tracks_flatmate_payments_towards_furniture_bill(billed):
    month = _month(billed)
    assert (month["collected_paise"], month["collected_count"], month["payout_status"],
            month["remaining_disbursement_paise"]) == (0, 0, "pending", 220_000)
    furniture.record_furniture_payment(billed, APARTMENT, MONTH, "bob", amount_paise=106_667,
                                       payment_date="2026-11-08", reference="UPI-1")
    month = furniture.record_furniture_payment(
        billed, APARTMENT, MONTH, "chris", amount_paise=30_000, payment_date="2026-11-08",
        reference="UPI-2")["month"]
    assert (month["collected_paise"], month["collected_count"]) == (136_667, 1)
    assert {row["resident_id"]: (row["settled_paise"], row["outstanding_paise"])
            for row in month["shares"]} == {"alice": (0, 56_667), "bob": (106_667, 0),
                                            "chris": (30_000, 26_666)}
    # Paying the provider herself covers Alice's share too.
    month = _advance(billed, amount_paise=100_000)
    assert (month["collected_paise"], month["collected_count"]) == (193_334, 2)
    assert month["payout_status"] == "partially_disbursed"


def test_a_resident_who_paid_the_provider_pays_their_share_through_the_kitty(billed):
    month = furniture.mark_paid_by_resident(billed, APARTMENT, MONTH, "alice",
                                            payment_date="2026-11-05",
                                            reference="UTR-4471")["month"]
    expense_id = month["expense"]["expense_id"]
    share, advance = (_rows(billed, "SELECT * FROM payment WHERE payment_kind = %s", (kind,))[0]
                      for kind in ("to_pool", "provider_advance"))
    # Her own ₹566.67 share went into the kitty, settling her charge on the bill...
    assert (share["payer_resident_id"], share["amount_paise"], share["payment_date"],
            share["reference"], share["expense_id"]) == (
        "alice", 56_667, "2026-11-05", "UTR-4471", None)
    # ...and only the ₹1,633.33 beyond it is an advance, linked to the bill.
    assert (advance["payer_resident_id"], advance["amount_paise"], advance["expense_id"],
            advance["payee_or_provider"], advance["reference"]) == (
        "alice", 163_333, expense_id, "Furlenco", "UTR-4471")
    assert [(t["disbursed_by"], t["funding_resident_id"], t["funding_payment_id"],
             t["amount_paise"]) for t in month["disbursements"]] == [
        ("pool", None, None, 56_667),
        ("resident_advance", "alice", advance["payment_id"], 163_333)]
    assert month["disbursements"][0]["disbursement_id"] == f"svcdisb_{share['payment_id']}"
    assert month["payout_status"] == "disbursed"
    assert _statuses(month) == {"alice": ("paid", 56_667, "2026-11-05"),
                                "bob": ("unpaid", 0, None), "chris": ("unpaid", 0, None)}
    alice = next(row for row in month["shares"] if row["resident_id"] == "alice")
    assert [p["payment_id"] for p in alice["payments"]] == [share["payment_id"]]
    owed = month["reimbursement"]
    assert (owed["total_fronted_paise"], owed["payer_share_paise"],
            owed["net_reimbursement_owed_paise"], owed["kitty_cash_available_paise"],
            owed["can_reimburse"]) == (220_000, 56_667, 163_333, 0, False)
    # Paid her share and fronted the rest: the household owes her the rest.
    assert _net(billed)["alice"] == 163_333
    assert validation.validate(billed, APARTMENT)["issues"] == []

    with pytest.raises(ConflictError) as again:
        _advance(billed, "bob")
    assert again.value.code == "furniture_already_disbursed"


def test_the_kitty_passbook_shows_a_share_paid_to_the_provider_coming_in_and_going_out(billed):
    _advance(billed)
    items = pool_statements.get_payment_pools_statement(billed, APARTMENT)["pool_statement"]["items"]
    entries = sorted((e["direction"], e["category"], e["amount_paise"]) for e in items)
    assert entries == [("in", "Kitty Payment", 56_667), ("out", "House Expense", 56_667)]
    payout = next(e for e in items if e["direction"] == "out")
    assert (payout["description"], payout["counterparty_name"], payout["reference"]) == (
        "Furniture rental payout · November 2026", "Furlenco", "UTR-4471")


@pytest.mark.parametrize("mode, details, code", [
    ("pool", {"amount_paise": 220_001}, "tranche_exceeds_remaining"),
    ("pool", {"reference": "  "}, None),
    ("pool", {"funding_resident_id": "alice"}, None),
    ("resident_advance", {}, None),
    ("cheque", {}, None),
])
def test_a_payout_needs_a_reference_and_is_never_more_than_is_owed(billed, mode, details, code):
    with pytest.raises(ValidationError) as refused:
        _payout(billed, mode, **details)
    if code:
        assert refused.value.code == code
    assert _rows(billed, "SELECT 1 FROM service_disbursement") == []
    assert _rows(billed, "SELECT 1 FROM payment") == []


def test_a_payout_needs_the_recorded_bill(rented):
    for pay in (lambda: _payout(rented),
                lambda: furniture.mark_paid_by_resident(rented, APARTMENT, MONTH, "alice",
                                                        payment_date="2026-11-05",
                                                        reference="UTR-1")):
        with pytest.raises(ConflictError) as unbilled:
            pay()
        assert unbilled.value.code == "furniture_bill_not_recorded"


def test_the_provider_is_paid_in_tranches_from_the_kitty_and_by_a_resident(billed):
    _into_kitty(billed, "bob", 106_667, "pay-bob")
    month = _payout(billed, amount_paise=100_000)
    assert (month["payout_status"], month["remaining_disbursement_paise"]) == (
        "partially_disbursed", 120_000)
    month = _advance(billed, reference="UTR-4472", disbursed_date="2026-11-07")
    # Alice's ₹1,200 is her own ₹566.67 share, through the kitty, and ₹633.33 extra.
    assert [(t["disbursed_by"], t["amount_paise"]) for t in month["disbursements"]] == [
        ("pool", 100_000), ("pool", 56_667), ("resident_advance", 63_333)]
    assert month["payout_status"] == "disbursed"
    # The kitty spent ₹1,000 of Bob's ₹1,066.67 on its own tranche: ₹66.67 is left
    # for Alice, who is owed the extra.
    owed = month["reimbursement"]
    assert (owed["total_fronted_paise"], owed["net_reimbursement_owed_paise"],
            owed["kitty_cash_available_paise"], owed["reimbursable_paise"]) == (
        120_000, 63_333, 6_667, 6_667)


def test_a_payout_is_kept_for_good(billed):
    month = _advance(billed, amount_paise=100_000)
    advance = next(d["funding_payment_id"] for d in month["disbursements"]
                   if d["funding_payment_id"])
    with pytest.raises(ConflictError) as reversal:
        payments.reverse_payment(billed, advance, reason="Wrong resident")
    assert reversal.value.code == "service_funding_payment"
    with connect(billed, as_owner=True) as conn:
        with pytest.raises(DatabaseRefusal):
            conn.execute("UPDATE service_disbursement SET amount_paise = 1")
        with pytest.raises(DatabaseRefusal):
            conn.execute("DELETE FROM service_disbursement")
    # Even written directly, tranches cannot add up to more than the bill.
    service_id = furniture.get_overview(billed, APARTMENT, MONTH)["service"]["service_id"]
    with connect(billed) as conn, pytest.raises(DatabaseRefusal) as overpaid:
        conn.execute(
            """INSERT INTO service_disbursement (disbursement_id, service_id, expense_id,
                   apartment_id, settlement_month, amount_paise, disbursed_at,
                   disbursement_reference, disbursed_by, created_at)
               VALUES ('x', %s, %s, %s, %s, 120001, '2026-11-06', 'UTR-X', 'pool', 'now')""",
            (service_id, month["expense"]["expense_id"], APARTMENT, MONTH))
    assert "overpaid_service" in str(overpaid.value)


def test_a_bill_paid_out_from_the_kitty_cannot_be_deleted(billed):
    month = _payout(billed, amount_paise=50_000)
    with pytest.raises(ConflictError) as deleted:
        expenses.delete_expense(billed, month["expense"]["expense_id"], apartment_id=APARTMENT)
    assert deleted.value.code == "service_bill_disbursed"


def test_a_bill_already_paid_outside_this_screen_takes_no_payout(billed):
    expense_id = _month(billed)["expense"]["expense_id"]
    payments.record_payment(billed, apartment_id=APARTMENT, payment_id="adv-1",
                            payer_resident_id="bob", amount_paise=50_000,
                            payment_date="2026-11-05", payment_kind="provider_advance",
                            expense_id=expense_id)
    for mode, details in (("pool", {}), ("resident_advance", {"funding_resident_id": "alice"})):
        with pytest.raises(ConflictError) as refused:
            _payout(billed, mode, amount_paise=10_000, **details)
        assert refused.value.code == "furniture_already_funded"
    assert _rows(billed, "SELECT 1 FROM service_disbursement") == []


def test_migration_0023_turns_each_furniture_advance_into_a_tranche(database_factory):
    db = database_factory(migrated=False)
    db.migrate(target_version=22)
    apartments.create_apartment(db, apartment_id=APARTMENT, name="Flat 402")
    for resident_id, name in RESIDENTS:
        residents.create_resident(db, apartment_id=APARTMENT, resident_id=resident_id,
                                  display_name=name, active_from="2026-08-01")
    definition = _setup(db)["config"]["expense_definition_id"]
    expenses.create_expense(db, apartment_id=APARTMENT, expense_id="furniture-nov",
                            description="Furniture rental for November 2026",
                            amount_paise=150_000, expense_date="2026-11-01",
                            allocations_paise={"alice": 50_000, "bob": 50_000, "chris": 50_000},
                            expense_definition_id=definition, settlement_period=MONTH)
    for payment_id, resident, paise, reference in (("adv-alice", "alice", 100_000, "UTR-1"),
                                                   ("adv-bob", "bob", 50_000, None)):
        payments.record_payment(db, apartment_id=APARTMENT, payment_id=payment_id,
                                payer_resident_id=resident, amount_paise=paise,
                                payment_date="2026-11-05", payment_kind="provider_advance",
                                expense_id="furniture-nov", reference=reference)
    db.migrate()
    rows = _rows(db, """SELECT funding_payment_id, funding_resident_id, amount_paise,
                               disbursed_at, disbursement_reference, disbursed_by,
                               settlement_month
                          FROM service_disbursement ORDER BY row_seq""")
    assert rows == [
        {"funding_payment_id": "adv-alice", "funding_resident_id": "alice",
         "amount_paise": 100_000, "disbursed_at": "2026-11-05",
         "disbursement_reference": "UTR-1", "disbursed_by": "resident_advance",
         "settlement_month": MONTH},
        {"funding_payment_id": "adv-bob", "funding_resident_id": "bob", "amount_paise": 50_000,
         "disbursed_at": "2026-11-05", "disbursement_reference": "Not recorded",
         "disbursed_by": "resident_advance", "settlement_month": MONTH}]


def test_flatmates_payments_into_the_kitty_show_against_their_share(billed):
    _advance(billed)
    _into_kitty(billed, "bob", 106_667, "pay-bob")
    _into_kitty(billed, "chris", 30_000, "pay-chris")
    month = _month(billed)
    statuses = _statuses(month)
    assert (statuses["bob"], statuses["chris"]) == (("paid", 106_667, "2026-11-08"),
                                                    ("partial", 30_000, "2026-11-08"))
    owed = month["reimbursement"]
    assert (owed["kitty_cash_available_paise"], owed["pending_reimbursement_paise"],
            owed["reimbursable_paise"], owed["can_reimburse"]) == (136_667, 163_333, 136_667,
                                                                   True)


def test_the_kitty_pays_back_the_resident_who_paid_the_provider(billed):
    _advance(billed)
    _into_kitty(billed, "bob", 106_667, "pay-bob")
    for recipient, paise, code in (("alice", 106_668, "exceeds_kitty_cash"),
                                   ("bob", 1_000, "not_the_payout_resident")):
        with pytest.raises(ValidationError) as refused:
            furniture.reimburse_from_kitty(billed, APARTMENT, MONTH, recipient,
                                           amount_paise=paise, payment_date="2026-11-09")
        assert refused.value.code == code

    month = furniture.reimburse_from_kitty(billed, APARTMENT, MONTH, "alice",
                                           amount_paise=106_667, payment_date="2026-11-09",
                                           reference="UPI-88")["month"]
    refund = _rows(billed, "SELECT * FROM payment WHERE payment_kind='refund'")[0]
    assert (refund["payer_resident_id"], refund["amount_paise"], refund["reference"]) == (
        "alice", 106_667, "UPI-88")
    owed = month["reimbursement"]
    assert (owed["reimbursed_so_far_paise"], owed["pending_reimbursement_paise"],
            owed["kitty_cash_available_paise"], owed["can_reimburse"]) == (106_667, 56_666, 0,
                                                                           False)
    assert [row["payment_id"] for row in owed["reimbursements"]] == [refund["payment_id"]]

    # Chris pays in, and the rest goes back to Alice: no more than she is owed.
    _into_kitty(billed, "chris", 56_666, "pay-chris")
    with pytest.raises(ValidationError) as beyond:
        furniture.reimburse_from_kitty(billed, APARTMENT, MONTH, "alice", amount_paise=56_667,
                                       payment_date="2026-11-12")
    assert beyond.value.code == "exceeds_pending_reimbursement"
    owed = furniture.reimburse_from_kitty(billed, APARTMENT, MONTH, "alice", amount_paise=56_666,
                                          payment_date="2026-11-12")["month"]["reimbursement"]
    assert (owed["pending_reimbursement_paise"], owed["can_reimburse"]) == (0, False)
    # Everyone is square, the kitty included.
    assert set(_net(billed).values()) == {0}
    assert validation.validate(billed, APARTMENT)["issues"] == []

    # A reimbursement reversed is owed again.
    payments.reverse_payment(billed, refund["payment_id"], reason="Sent to the wrong account")
    owed = _month(billed)["reimbursement"]
    assert (owed["reimbursed_so_far_paise"], owed["pending_reimbursement_paise"]) == (56_666,
                                                                                      106_667)


def test_a_payer_who_already_paid_their_share_into_the_kitty_is_owed_the_whole_bill(billed):
    # Alice pays her share into the kitty first, then fronts the whole bill: none
    # of it is her share any more, so all of it is owed back to her.
    _into_kitty(billed, "alice", 56_667, "pay-alice")
    month = _advance(billed)
    assert [(t["disbursed_by"], t["amount_paise"]) for t in month["disbursements"]] == [
        ("resident_advance", 220_000)]
    assert _statuses(month)["alice"] == ("paid", 56_667, "2026-11-08")
    owed = month["reimbursement"]
    assert (owed["net_reimbursement_owed_paise"], owed["kitty_cash_available_paise"]) == (
        220_000, 56_667)
    assert _net(billed)["alice"] == 220_000


def test_an_unbilled_month_has_no_payments_to_show(rented):
    month = _month(rented)
    assert {row["status"] for row in month["shares"]} == {None}
    assert (month["collected_paise"], month["payout_status"], month["disbursements"],
            month["reimbursement"]) == (0, "pending", [], None)


# ------------------------------------------------------ several providers --

def _payouts(month):
    return {row["provider_name"]: (row["total_paise"], row["remaining_disbursement_paise"],
                                   row["payout_status"]) for row in month["provider_payouts"]}


def test_multiple_providers_link_items_and_settle_payouts_independently(apartment):
    furlenco = _setup(apartment, provider_name="Furlenco", billing_day_of_month=5)
    furlenco_id = furlenco["service"]["service_id"]
    both = furniture.setup_furniture_rental(
        apartment, APARTMENT, {"provider_name": "Rentomojo", "billing_day_of_month": 10,
                               "create_new": True}, settlement_month=MONTH)
    assert [entry["config"]["provider_name"] for entry in both["services"]] == [
        "Furlenco", "Rentomojo"]
    rentomojo_id = both["services"][1]["service"]["service_id"]
    # Both providers' items are billed together, under one definition.
    assert len({entry["config"]["expense_definition_id"] for entry in both["services"]}) == 1
    assert both["service"]["service_id"] == furlenco_id, "the first stays the default"

    with pytest.raises(ValidationError) as unsure:
        _item(apartment, "Lamp", 10_000)
    assert unsure.value.code == "furniture_service_required"
    sofa = _item(apartment, "Sofa", 120_000, service_id=furlenco_id, delivery_date="2026-09-01")
    bed = _item(apartment, "Queen bed", 100_000, scope="resident", resident="alice",
                category="bedroom", service_id=rentomojo_id, delivery_date="2026-09-01")
    assert (sofa["provider_name"], bed["provider_name"]) == ("Furlenco", "Rentomojo")
    overview = furniture.get_overview(apartment, APARTMENT, MONTH)
    assert (overview["monthly_total_paise"], overview["active_item_count"]) == (220_000, 2)
    assert [(e["config"]["provider_name"], e["monthly_total_paise"])
            for e in overview["services"]] == [("Furlenco", 120_000), ("Rentomojo", 100_000)]

    month = furniture.generate_month(apartment, APARTMENT, MONTH)["month"]
    expense = _rows(apartment, "SELECT * FROM expense_instance")[0]
    assert (expense["amount_paise"], expense["provider_name"], expense["due_date"]) == (
        220_000, "Furlenco, Rentomojo", "2026-11-05")
    assert _payouts(month) == {"Furlenco": (120_000, 120_000, "pending"),
                               "Rentomojo": (100_000, 100_000, "pending")}

    # Which provider is being paid has to be said.
    with pytest.raises(ValidationError) as which:
        _payout(apartment)
    assert which.value.code == "furniture_service_required"
    month = _payout(apartment, service_id=furlenco_id)
    assert _payouts(month) == {"Furlenco": (120_000, 0, "disbursed"),
                               "Rentomojo": (100_000, 100_000, "pending")}
    assert month["payout_status"] == "partially_disbursed"
    with pytest.raises(ConflictError) as again:
        _payout(apartment, service_id=furlenco_id, amount_paise=1)
    assert again.value.code == "furniture_already_disbursed"
    with pytest.raises(ValidationError) as too_much:
        _payout(apartment, service_id=rentomojo_id, amount_paise=100_001)
    assert too_much.value.code == "tranche_exceeds_remaining"

    month = _payout(apartment, service_id=rentomojo_id, reference="UTR-R1")
    assert _payouts(month) == {"Furlenco": (120_000, 0, "disbursed"),
                               "Rentomojo": (100_000, 0, "disbursed")}
    assert month["payout_status"] == "disbursed"
    assert [(t["provider_name"], t["amount_paise"]) for t in month["disbursements"]] == [
        ("Furlenco", 120_000), ("Rentomojo", 100_000)]
    assert validation.validate(apartment, APARTMENT)["issues"] == []

    # Ending one provider leaves the other; the month it was paid keeps its payout.
    with pytest.raises(ConflictError) as outstanding:
        furniture.retire_furniture_rental(apartment, APARTMENT, service_id=furlenco_id)
    assert outstanding.value.code == "furniture_items_outstanding"
    furniture.update_item(apartment, APARTMENT, sofa["item_id"],
                          {"status": "returned", "return_date": "2026-11-30"})
    after = furniture.retire_furniture_rental(apartment, APARTMENT, service_id=furlenco_id,
                                              settlement_month=MONTH)
    assert [entry["config"]["provider_name"] for entry in after["services"]] == ["Rentomojo"]
    assert [item["name"] for item in after["items"]] == ["Queen bed"]
    assert _payouts(after["month"]) == {"Rentomojo": (100_000, 0, "disbursed"),
                                        "Furlenco": (120_000, 0, "disbursed")}


def test_a_resident_paying_two_providers_is_owed_the_extra_on_both(apartment):
    furlenco_id = _setup(apartment, provider_name="Furlenco")["service"]["service_id"]
    rentomojo_id = furniture.setup_furniture_rental(
        apartment, APARTMENT, {"provider_name": "Rentomojo", "create_new": True},
        settlement_month=MONTH)["services"][1]["service"]["service_id"]
    _item(apartment, "Sofa", 120_000, service_id=furlenco_id)
    _item(apartment, "Fridge", 90_000, service_id=rentomojo_id, category="appliance")
    furniture.generate_month(apartment, APARTMENT, MONTH)
    # Alice pays both. Her ₹700 share goes through the kitty from the first; the rest is extra.
    _advance(apartment, service_id=furlenco_id)
    month = _advance(apartment, service_id=rentomojo_id, reference="UTR-R2")
    assert [entry["recipient_resident_id"] for entry in month["reimbursements"]] == ["alice"]
    owed = month["reimbursement"]
    assert (owed["total_fronted_paise"], owed["payer_share_paise"],
            owed["net_reimbursement_owed_paise"]) == (210_000, 70_000, 140_000)
    # Bob pays his share in: the kitty can pass it to Alice.
    _into_kitty(apartment, "bob", 70_000, "pay-bob")
    with pytest.raises(ValidationError) as other:
        furniture.reimburse_from_kitty(apartment, APARTMENT, MONTH, "bob", amount_paise=1_000,
                                       payment_date="2026-11-09")
    assert other.value.code == "not_the_payout_resident"
    owed = furniture.reimburse_from_kitty(apartment, APARTMENT, MONTH, "alice",
                                          amount_paise=70_000,
                                          payment_date="2026-11-09")["month"]["reimbursement"]
    assert (owed["reimbursed_so_far_paise"], owed["pending_reimbursement_paise"]) == (
        70_000, 70_000)
    assert validation.validate(apartment, APARTMENT)["issues"] == []


def test_several_providers_over_http(api):
    base = "/api/services/furniture"
    first = api.post(f"{base}/setup", params={"month": MONTH}, json={"provider_name": "Furlenco"})
    assert first.status_code == 200, first.text
    second = api.post(f"{base}/setup", params={"month": MONTH},
                      json={"provider_name": "Rentomojo", "create_new": True})
    assert [e["config"]["provider_name"] for e in second.json()["services"]] == [
        "Furlenco", "Rentomojo"]
    rentomojo = second.json()["services"][1]["service"]["service_id"]
    lamp = {"name": "Lamp", "category": "other", "monthly_rent_paise": 10_000,
            "allocation_scope": "common"}
    assert api.post(f"{base}/items", json=lamp).status_code == 422
    added = api.post(f"{base}/items", json={**lamp, "service_id": rentomojo})
    assert (added.status_code, added.json()["provider_name"]) == (201, "Rentomojo")
    renamed = api.post(f"{base}/setup", params={"month": MONTH},
                       json={"service_id": rentomojo, "customer_id": "RM-9"})
    assert renamed.json()["services"][1]["config"]["customer_id"] == "RM-9"
    assert api.post(f"{base}/setup", json={"service_id": "svc-nope"}).status_code == 404
    api.patch(f"{base}/items/{added.json()['item_id']}",
              json={"status": "returned", "return_date": "2026-11-30"})
    ended = api.post(f"{base}/retire", params={"month": MONTH, "service_id": rentomojo})
    assert ended.status_code == 200, ended.text
    assert [e["config"]["provider_name"] for e in ended.json()["services"]] == ["Furlenco"]


# ----------------------------------------------------------- retirement --

def test_the_subscription_ends_only_once_everything_has_gone_back(rented):
    with pytest.raises(ConflictError) as refused:
        furniture.retire_furniture_rental(rented, APARTMENT, settlement_month=MONTH)
    assert refused.value.code == "furniture_items_outstanding"
    old = furniture.get_overview(rented, APARTMENT, MONTH)
    for item in old["items"]:
        furniture.update_item(rented, APARTMENT, item["item_id"],
                              {"status": "returned", "return_date": "2026-11-30"})
    assert furniture.retire_furniture_rental(rented, APARTMENT, settlement_month=MONTH)["service"] is None

    # A new subscription starts afresh; the old one stays for the record.
    fresh = _setup(rented, provider_name="Rentomojo")
    assert fresh["service"]["service_id"] != old["service"]["service_id"]
    assert fresh["items"] == []
    assert [row["status"] for row in _rows(
        rented, "SELECT status FROM household_service ORDER BY row_seq")] == ["retired", "active"]


# --------------------------------------------------------------- the API --

@pytest.fixture()
def api(apartment):
    from fastapi.testclient import TestClient

    from aem.main import create_app

    settings = replace(get_settings(), auto_close_expired_statements=False)
    with TestClient(create_app(settings, database=apartment)) as client:
        sign_in(client)
        yield client


def test_furniture_rental_over_http(api):
    base = "/api/services/furniture"
    assert api.get(base).status_code == 422, "the month is required"
    assert api.get(base, params={"month": MONTH}).json()["service"] is None

    setup = api.post(f"{base}/setup", params={"month": MONTH}, json={
        "provider_name": "Furlenco", "billing_day_of_month": 5, "deposit_paid_paise": 500_000})
    assert setup.status_code == 200, setup.text
    sofa = api.post(f"{base}/items", json={
        "name": "Sofa", "category": "living_room", "monthly_rent_paise": 120_000,
        "allocation_scope": "common", "delivery_date": "2026-09-01"})
    assert sofa.status_code == 201, sofa.text
    desk = api.post(f"{base}/items", json={
        "name": "Desk", "category": "workstation", "monthly_rent_paise": 30_000,
        "allocation_scope": "resident", "assigned_resident_id": "alice"}).json()
    assert api.patch(f"{base}/items/{desk['item_id']}",
                     json={"monthly_rent_paise": 45_000}).json()["monthly_rent_paise"] == 45_000
    assert api.delete(f"{base}/items/{desk['item_id']}").status_code == 204
    assert api.patch(f"{base}/items/{desk['item_id']}", json={"name": "x"}).status_code == 404

    for body in ({"name": "Lamp", "category": "lighting", "monthly_rent_paise": 1000,
                  "allocation_scope": "common"},
                 {"name": "Lamp", "category": "other", "monthly_rent_paise": 0,
                  "allocation_scope": "common"}):
        assert api.post(f"{base}/items", json=body).status_code == 422, body
    assert api.post(f"{base}/generate-cycle", json={"settlement_month": "2026-13"}).status_code == 422

    generated = api.post(f"{base}/generate-cycle", json={"settlement_month": MONTH})
    assert generated.status_code == 201, generated.text
    assert generated.json()["month"]["expense"]["amount_paise"] == 120_000
    assert api.post(f"{base}/generate-cycle", json={"settlement_month": MONTH}).status_code == 409
    assert api.post(f"{base}/retire").status_code == 409


def test_paying_the_bill_over_http(api):
    base = "/api/services/furniture"
    api.post(f"{base}/setup", params={"month": MONTH}, json={"provider_name": "Furlenco"})
    api.post(f"{base}/items", json={"name": "Sofa", "category": "living_room",
                                    "monthly_rent_paise": 90_000, "allocation_scope": "common"})
    payout = {"settlement_month": MONTH, "disbursement_mode": "pool", "reference": "UTR-1",
              "disbursed_date": "2026-11-05", "amount_paise": 30_000}
    assert api.post(f"{base}/disburse-payout", json=payout).status_code == 409, \
        "the bill is recorded first"
    api.post(f"{base}/generate-cycle", json={"settlement_month": MONTH})
    for wrong in ({"reference": ""}, {"disbursement_mode": "cash"}, {"amount_paise": 90_001}):
        assert api.post(f"{base}/disburse-payout",
                        json={**payout, **wrong}).status_code == 422, wrong
    first = api.post(f"{base}/disburse-payout", json=payout)
    assert first.status_code == 201, first.text
    assert (first.json()["month"]["payout_status"],
            first.json()["month"]["remaining_disbursement_paise"]) == ("partially_disbursed",
                                                                       60_000)

    # The original endpoint records a resident's payout, for the rest by default.
    advance = {"settlement_month": MONTH, "payer_resident_id": "bob",
               "payment_date": "2026-11-06", "reference": "UTR-2"}
    assert api.post(f"{base}/mark-paid-by-resident",
                    json={**advance, "reference": None}).status_code == 422
    rest = api.post(f"{base}/mark-paid-by-resident", json=advance)
    assert rest.status_code == 201, rest.text
    assert [(t["disbursed_by"], t["amount_paise"])
            for t in rest.json()["month"]["disbursements"]] == [
        ("pool", 30_000), ("pool", 30_000), ("resident_advance", 30_000)]
    assert api.post(f"{base}/mark-paid-by-resident", json=advance).status_code == 409

    refund = {"settlement_month": MONTH, "recipient_resident_id": "bob", "amount_paise": 30_000,
              "payment_date": "2026-11-09"}
    assert api.post(f"{base}/reimburse-from-kitty", json=refund).status_code == 422, \
        "nothing has been paid into the kitty for it yet"
    share = {"settlement_month": MONTH, "resident_id": "alice", "amount_paise": 30_000,
             "payment_date": "2026-11-08", "payment_mode": "upi", "reference": "UPI-7"}
    assert api.post(f"{base}/record-payment",
                    json={**share, "payment_mode": "cheque"}).status_code == 422
    for resident in ("alice", "chris"):
        paid_in = api.post(f"{base}/record-payment", json={**share, "resident_id": resident})
        assert paid_in.status_code == 201, paid_in.text
    assert {row["resident_id"]: row["status"] for row in paid_in.json()["month"]["shares"]} == {
        "alice": "paid", "bob": "paid", "chris": "paid"}
    # Of the ₹600 Alice and Chris paid in, the kitty sent ₹300 to Furlenco: the
    # other ₹300 is Bob's extra.
    reimbursed = api.post(f"{base}/reimburse-from-kitty", json=refund)
    assert reimbursed.status_code == 201, reimbursed.text
    assert reimbursed.json()["month"]["reimbursement"]["pending_reimbursement_paise"] == 0

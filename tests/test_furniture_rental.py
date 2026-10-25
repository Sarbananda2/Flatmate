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
    definitions,
    furniture_rental as furniture,
    payments,
    reconciliation,
    recurring,
    residents,
    statements,
    validation,
)

from .conftest import APARTMENT
from .support import connect, sign_in

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


def test_a_resident_who_paid_the_provider_is_credited_with_the_bill(billed):
    month = furniture.mark_paid_by_resident(billed, APARTMENT, MONTH, "alice",
                                            payment_date="2026-11-05",
                                            reference="UTR-4471")["month"]
    advance = _rows(billed, "SELECT * FROM payment")[0]
    assert (advance["payment_kind"], advance["payer_resident_id"], advance["amount_paise"],
            advance["expense_id"], advance["payee_or_provider"], advance["reference"]) == (
        "provider_advance", "alice", 220_000, month["expense"]["expense_id"], "Furlenco",
        "UTR-4471")
    assert month["vendor_payout"] == {"payer_resident_id": "alice", "payer_name": "Alice",
                                      "amount_paise": 220_000, "payment_date": "2026-11-05",
                                      "reference": "UTR-4471"}
    # Paying the provider covers her own share; nobody has paid into the kitty yet.
    assert _statuses(month) == {"alice": ("funded_payout", 56_667, "2026-11-05"),
                                "bob": ("unpaid", 0, None), "chris": ("unpaid", 0, None)}
    owed = month["reimbursement"]
    assert (owed["total_fronted_paise"], owed["payer_share_paise"],
            owed["net_reimbursement_owed_paise"], owed["kitty_cash_available_paise"],
            owed["can_reimburse"]) == (220_000, 56_667, 163_333, 0, False)
    # Credited the bill and charged her share: the household owes her the rest.
    assert _net(billed)["alice"] == 163_333
    assert validation.validate(billed, APARTMENT)["issues"] == []

    with pytest.raises(ConflictError) as again:
        furniture.mark_paid_by_resident(billed, APARTMENT, MONTH, "bob",
                                        payment_date="2026-11-05")
    assert again.value.code == "furniture_payout_exists"


def test_a_payout_needs_the_recorded_bill_and_pays_it_in_full(rented):
    with pytest.raises(ConflictError) as unbilled:
        furniture.mark_paid_by_resident(rented, APARTMENT, MONTH, "alice",
                                        payment_date="2026-11-05")
    assert unbilled.value.code == "furniture_bill_not_recorded"
    furniture.generate_month(rented, APARTMENT, MONTH)
    with pytest.raises(ValidationError) as short:
        furniture.mark_paid_by_resident(rented, APARTMENT, MONTH, "alice",
                                        payment_date="2026-11-05", amount_paise=200_000)
    assert short.value.code == "payout_amount_mismatch"
    assert _rows(rented, "SELECT 1 FROM payment") == []


def test_flatmates_payments_into_the_kitty_show_against_their_share(billed):
    furniture.mark_paid_by_resident(billed, APARTMENT, MONTH, "alice", payment_date="2026-11-05")
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
    furniture.mark_paid_by_resident(billed, APARTMENT, MONTH, "alice", payment_date="2026-11-05")
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


def test_a_payer_who_also_paid_their_share_into_the_kitty_is_owed_the_whole_bill(billed):
    furniture.mark_paid_by_resident(billed, APARTMENT, MONTH, "alice", payment_date="2026-11-05")
    # Her advance never settles her own charge, so her payment into the kitty does.
    _into_kitty(billed, "alice", 56_667, "pay-alice")
    month = _month(billed)
    assert _statuses(month)["alice"] == ("paid", 56_667, "2026-11-08")
    owed = month["reimbursement"]
    assert (owed["net_reimbursement_owed_paise"], owed["kitty_cash_available_paise"]) == (
        220_000, 56_667)
    assert _net(billed)["alice"] == 220_000


def test_an_unbilled_month_has_no_payments_to_show(rented):
    month = _month(rented)
    assert {row["status"] for row in month["shares"]} == {None}
    assert (month["vendor_payout"], month["reimbursement"]) == (None, None)


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
    payout = {"settlement_month": MONTH, "payer_resident_id": "bob",
              "payment_date": "2026-11-05", "reference": "UTR-1"}
    assert api.post(f"{base}/mark-paid-by-resident", json=payout).status_code == 409, \
        "the bill is recorded first"
    api.post(f"{base}/generate-cycle", json={"settlement_month": MONTH})
    assert api.post(f"{base}/mark-paid-by-resident",
                    json={**payout, "payment_date": "5 Nov"}).status_code == 422

    paid = api.post(f"{base}/mark-paid-by-resident", json=payout)
    assert paid.status_code == 201, paid.text
    assert paid.json()["month"]["vendor_payout"]["payer_name"] == "Bob"
    assert api.post(f"{base}/mark-paid-by-resident", json=payout).status_code == 409

    refund = {"settlement_month": MONTH, "recipient_resident_id": "bob", "amount_paise": 30_000,
              "payment_date": "2026-11-09"}
    assert api.post(f"{base}/reimburse-from-kitty", json=refund).status_code == 422, \
        "nothing has been paid into the kitty for it yet"
    api.post("/api/payments", json={"payer_resident_id": "alice", "amount_paise": 30_000,
                                    "payment_date": "2026-11-08"})
    reimbursed = api.post(f"{base}/reimburse-from-kitty", json=refund)
    assert reimbursed.status_code == 201, reimbursed.text
    assert reimbursed.json()["month"]["reimbursement"]["pending_reimbursement_paise"] == 30_000

"""The landlord and the household pool as payees (migration 0015).

Residents pay two standing counterparties besides each other: the landlord,
whose payment details are on the lease, and the household pool, whose name,
bank account, IFSC and UPI ID are kept on the apartment. These tests pin that
the pool's details are validated like the owner contact's, that a deposit
payment records which of them received it and is named after them, that rows
written before the party was recorded still read correctly, and that money owed
to the pool is asked for at the pool's own UPI ID once it has one.
"""
from __future__ import annotations

from datetime import date

import pytest

from aem.errors import ValidationError
from aem.services import (
    apartments,
    deposits,
    expenses,
    reconciliation,
    rent,
    residents,
    resident_statements,
    sharing,
    statements,
)

from .conftest import APARTMENT
from .support import DatabaseRefusal, connect

TODAY = date.today().isoformat()
MASTER = 15_000_000
SHARE = 5_000_000
LEASE = {"landlord_name": "Kumar Yadav", "monthly_rent_paise": 2_850_000,
         "security_deposit_paise": MASTER, "landlord_upi_id": "kumar.yadav@okhdfcbank",
         "landlord_bank_account": "50100123456789", "landlord_bank_ifsc": "HDFC0001234"}
POOL = {"pool_name": "Flat 402 Kitty", "pool_bank_account": "123456789012",
        "pool_bank_ifsc": "sbin0005678", "pool_upi_id": "flat402@okicici"}


def _summary(db):
    return deposits.deposit_summary(db, APARTMENT)


def _slot(summary, resident_id):
    return next(slot for slot in summary["slots"] if slot["assigned_resident_id"] == resident_id)


def _fund(db, resident_id, **extra):
    return deposits.record_slot_funding(
        db, _slot(_summary(db), resident_id)["slot_id"], apartment_id=APARTMENT,
        amount_paise=extra.pop("amount_paise", SHARE), paid_date=TODAY,
        payment_method=extra.pop("payment_method", "upi"), **extra)


def _slots(db, mode, signatory=None):
    summary = deposits.configure_mode(db, APARTMENT, mode, signatory)
    deposits.initialize_or_update_slots(db, APARTMENT, [
        {key: slot[key] for key in ("slot_number", "slot_label", "target_deposit_paise",
                                    "assigned_resident_id")}
        for slot in summary["proposal"]])
    return db


@pytest.fixture()
def leased(apartment):
    rent.save_lease_config(apartment, APARTMENT, dict(LEASE))
    return apartment


@pytest.fixture()
def co_tenants(leased):
    return _slots(leased, "direct_co_tenants")


@pytest.fixture()
def signatory(leased):
    return _slots(leased, "primary_signatory", "alice")


# --------------------------------------------------------- the pool's details --

def test_a_pool_starts_with_its_default_name_and_no_payment_details(apartment):
    saved = apartments.read_apartment(apartment, APARTMENT)
    assert saved["pool_name"] == "Household Pool"
    assert (saved["pool_bank_account"], saved["pool_bank_ifsc"], saved["pool_upi_id"]) == (
        None, None, None)


def test_the_pools_details_are_saved_validated_and_audited(apartment):
    saved = apartments.update_apartment(apartment, APARTMENT, **POOL)
    assert saved["pool_name"] == "Flat 402 Kitty"
    assert saved["pool_bank_account"] == "123456789012"
    assert saved["pool_bank_ifsc"] == "SBIN0005678", "an IFSC is uppercased"
    assert saved["pool_upi_id"] == "flat402@okicici"
    with connect(apartment, read_only=True) as conn:
        audited = {row["field_name"] for row in conn.execute(
            "SELECT field_name FROM audit_log WHERE entity_type='apartment' "
            "AND action='update'").fetchall()}
    assert set(POOL) <= audited


def test_a_pool_field_left_out_is_kept_and_the_name_falls_back_to_the_default(apartment):
    apartments.update_apartment(apartment, APARTMENT, **POOL)
    kept = apartments.update_apartment(apartment, APARTMENT, pool_upi_id=None)
    assert kept["pool_upi_id"] is None
    assert kept["pool_name"] == "Flat 402 Kitty" and kept["pool_bank_account"] == "123456789012"
    for empty in (None, "", "   "):
        reset = apartments.update_apartment(apartment, APARTMENT, pool_name=empty)
        assert reset["pool_name"] == "Household Pool"


@pytest.mark.parametrize("field, value", [
    ("pool_bank_account", "12345678"),           # too short
    ("pool_bank_account", "1234 5678 9012"),
    ("pool_bank_ifsc", "SBIN1005678"),           # the fifth character is a zero
    ("pool_upi_id", "flat402"),
    ("pool_name", "x" * 201),
])
def test_an_invalid_pool_detail_is_refused_and_nothing_changes(apartment, field, value):
    with pytest.raises(ValidationError) as refused:
        apartments.update_apartment(apartment, APARTMENT, **{"pool_name": "Kitty", field: value})
    assert refused.value.details["field"] == field
    assert apartments.read_apartment(apartment, APARTMENT)["pool_name"] == "Household Pool"


def test_the_database_itself_refuses_bad_pool_details(apartment):
    for column, value in (("pool_name", "  "), ("pool_bank_account", "12ab"),
                          ("pool_bank_ifsc", "sbin0005678"), ("pool_upi_id", "@x")):
        with connect(apartment) as conn, pytest.raises(DatabaseRefusal):
            conn.execute(f"UPDATE apartment SET {column}=%s", (value,))


def test_the_apartment_endpoint_saves_and_refuses_pool_details(client):
    client.post("/api/apartments", json={"apartment_id": APARTMENT, "name": "Flat 402"})
    saved = client.patch(f"/api/apartments/{APARTMENT}", json=POOL)
    assert saved.status_code == 200
    assert saved.json()["pool_bank_ifsc"] == "SBIN0005678"
    refused = client.patch(f"/api/apartments/{APARTMENT}", json={"pool_upi_id": "not-a-upi"})
    assert refused.status_code == 422
    assert client.get(f"/api/apartments/{APARTMENT}").json()["pool_upi_id"] == "flat402@okicici"


# ------------------------------------------------- who a deposit was paid to --

def test_the_summary_carries_both_payees_details(co_tenants):
    apartments.update_apartment(co_tenants, APARTMENT, **POOL)
    summary = _summary(co_tenants)
    assert summary["landlord_instance"] == {
        "instance_id": "__landlord__", "party": "landlord", "name": "Kumar Yadav",
        "bank_account": "50100123456789", "bank_ifsc": "HDFC0001234",
        "upi_id": "kumar.yadav@okhdfcbank"}
    assert summary["pool_instance"] == {
        "instance_id": "__pool__", "party": "pool", "name": "Flat 402 Kitty",
        "bank_account": "123456789012", "bank_ifsc": "SBIN0005678",
        "upi_id": "flat402@okicici"}
    assert summary["primary_signatory_upi_id"] is None


def test_co_tenants_pay_the_landlord_or_the_pool_and_each_is_named(co_tenants):
    _fund(co_tenants, "alice", paid_to_party="landlord", payment_reference="UTR1")
    _fund(co_tenants, "bob", paid_to_party="pool")
    # Left out, as before the pool could be named: the landlord.
    summary = _fund(co_tenants, "chris")

    names = {resident_id: (_slot(summary, resident_id)["paid_to_party"],
                           _slot(summary, resident_id)["paid_to_display_name"])
             for resident_id in ("alice", "bob", "chris")}
    assert names == {"alice": ("landlord", "Landlord (Kumar Yadav)"),
                     "bob": ("pool", "Household Pool"),
                     "chris": ("landlord", "Landlord (Kumar Yadav)")}
    bob_tx = _slot(summary, "bob")["transactions"][0]
    assert (bob_tx["counterparty_party"], bob_tx["counterparty_display_name"],
            bob_tx["counterparty_resident_id"]) == ("pool", "Household Pool", None)
    with connect(co_tenants, read_only=True) as conn:
        stored = conn.execute(
            "SELECT counterparty_party FROM household_deposit_transaction ORDER BY row_seq"
        ).fetchall()
    assert [row["counterparty_party"] for row in stored] == ["landlord", "pool", "landlord"]


def test_a_refund_comes_back_from_wherever_the_deposit_was_paid(co_tenants):
    _fund(co_tenants, "bob", paid_to_party="pool")
    settled = deposits.vacate_and_settle_slot(
        co_tenants, _slot(_summary(co_tenants), "bob")["slot_id"], apartment_id=APARTMENT,
        settlement_path="signatory_refund", deductions_paise=500_000,
        refund_amount_paise=4_500_000, refund_date=TODAY, deduction_reason="Repainting")
    slot = next(slot for slot in settled["slots"] if slot["slot_number"] == 2)
    assert [(tx["tx_type"], tx["counterparty_party"], tx["counterparty_display_name"])
            for tx in slot["transactions"]] == [
        ("funding_payment", "pool", "Household Pool"),
        ("move_out_deduction", "pool", "Household Pool"),
        ("move_out_refund", "pool", "Household Pool")]
    assert (slot["paid_to_party"], slot["paid_to_display_name"]) == (None, None)


def test_a_signatorys_slot_was_paid_to_the_landlord_and_reimbursements_go_to_them(signatory):
    alice = _slot(_summary(signatory), "alice")
    assert (alice["paid_to_party"], alice["paid_to_display_name"]) == (
        "landlord", "Landlord (Kumar Yadav)")
    residents.update_resident(signatory, "alice", upi_id="alice@okaxis")
    summary = _fund(signatory, "bob")
    assert summary["primary_signatory_upi_id"] == "alice@okaxis"
    bob = _slot(summary, "bob")
    assert (bob["paid_to_party"], bob["paid_to_display_name"]) == ("resident", "Alice")
    assert bob["transactions"][0]["counterparty_party"] == "resident"


@pytest.mark.parametrize("party", ["landlord", "pool"])
def test_under_a_signatory_only_the_signatory_can_be_paid(signatory, party):
    with pytest.raises(ValidationError) as refused:
        _fund(signatory, "bob", paid_to_party=party)
    assert refused.value.details["field"] == "paid_to_party"
    assert _slot(_summary(signatory), "bob")["funded_amount_paise"] == 0


@pytest.mark.parametrize("fields", [
    {"paid_to_party": "resident"},                           # but nobody named
    {"paid_to_party": "pool", "paid_to_resident_id": "alice"},
    {"paid_to_party": "pool", "payment_method": "direct_to_landlord"},
    {"paid_to_party": "escrow"},
])
def test_a_payee_that_does_not_add_up_is_refused(co_tenants, fields):
    with pytest.raises(ValidationError):
        _fund(co_tenants, "bob", **fields)
    assert _slot(_summary(co_tenants), "bob")["funded_amount_paise"] == 0


def test_rows_recorded_before_the_party_existed_still_read_correctly(co_tenants):
    """A slot and a payment as migration 0015 found them: no party recorded."""
    _fund(co_tenants, "bob", payment_method="direct_to_landlord")
    slot = _slot(_summary(co_tenants), "bob")
    with connect(co_tenants) as conn:
        conn.execute("UPDATE household_deposit_slot SET paid_to_party=NULL WHERE slot_id=%s",
                     (slot["slot_id"],))
        conn.execute(
            """INSERT INTO household_deposit_transaction
               (tx_id, slot_id, apartment_id, resident_id, tx_type, amount_paise, tx_date,
                payment_method, created_at)
               VALUES ('legacy', %s, %s, 'bob', 'funding_payment', 1, %s,
                       'direct_to_landlord', %s)""",
            (slot["slot_id"], APARTMENT, TODAY, TODAY))
    slot = _slot(_summary(co_tenants), "bob")
    assert (slot["paid_to_party"], slot["paid_to_display_name"]) == (
        "landlord", "Landlord (Kumar Yadav)")
    legacy = next(tx for tx in slot["transactions"] if tx["tx_id"] == "legacy")
    assert (legacy["counterparty_party"], legacy["counterparty_display_name"]) == (
        "landlord", "Landlord (Kumar Yadav)")


def test_the_fund_endpoint_takes_the_payee(client):
    client.post("/api/apartments", json={"apartment_id": APARTMENT, "name": "Flat 402"})
    for resident_id in ("alice", "bob"):
        client.post("/api/residents", json={"resident_id": resident_id,
                                            "display_name": resident_id.title()})
    client.post("/api/rent/config", json={**LEASE, "security_deposit_paise": 2 * SHARE})
    configured = client.post("/api/rent/deposits/config",
                             json={"funding_mode": "direct_co_tenants"}).json()
    created = client.post("/api/rent/deposits/slots", json={"slots": [
        {key: slot[key] for key in ("slot_number", "slot_label", "target_deposit_paise",
                                    "assigned_resident_id")}
        for slot in configured["proposal"]]}).json()
    slot_id = _slot(created, "bob")["slot_id"]
    refused = client.post(f"/api/rent/deposits/slots/{slot_id}/fund", json={
        "amount_paise": SHARE, "paid_date": TODAY, "payment_method": "upi",
        "paid_to_party": "escrow"})
    assert refused.status_code == 422
    funded = client.post(f"/api/rent/deposits/slots/{slot_id}/fund", json={
        "amount_paise": SHARE, "paid_date": TODAY, "payment_method": "upi",
        "paid_to_party": "pool"})
    assert funded.status_code == 200
    assert _slot(funded.json(), "bob")["paid_to_display_name"] == "Household Pool"


# --------------------------------------------- paying the pool by its UPI ID --

@pytest.fixture()
def pool_funded(apartment):
    """The pool paid ₹3,000 for the Wi-Fi; each resident owes it ₹1,000."""
    expenses.create_expense(
        apartment, apartment_id=APARTMENT, expense_id="wifi", description="Wi-Fi",
        amount_paise=300_000, expense_date="2026-10-02", period_rule="transaction_date",
        allocation_method="equal")
    return apartment


def test_the_pool_account_is_named_and_says_where_to_pay_it(pool_funded):
    apartments.update_apartment(pool_funded, APARTMENT, **POOL)
    result = reconciliation.reconcile(pool_funded, apartment_id=APARTMENT)
    pool = next(row for row in result["accounts"] if row["account_type"] == "pool")
    assert (pool["display_name"], pool["upi_id"], pool["bank_account"], pool["bank_ifsc"]) == (
        "Flat 402 Kitty", "flat402@okicici", "123456789012", "SBIN0005678")
    assert {row["to_display_name"] for row in result["transfers"]} == {"Flat 402 Kitty"}


def test_a_transfer_to_the_pool_gets_the_pools_upi_link(pool_funded):
    statement_id = statements.create_monthly_statement(
        pool_funded, apartment_id=APARTMENT, settlement_month="2026-10")
    statements.attach_eligible_expenses(pool_funded, statement_id)
    without = sharing.whatsapp_summary(pool_funded, statement_id)
    assert all(t["upi_uri"] is None for t in without["transfers"])

    apartments.update_apartment(pool_funded, APARTMENT, pool_upi_id="flat402@okicici")
    summary = sharing.whatsapp_summary(pool_funded, statement_id)
    to_pool = [t for t in summary["transfers"] if t["to_account"] == "__pool__"]
    assert len(to_pool) == 3
    bob = next(t for t in to_pool if t["from_account"] == "bob")
    assert bob["upi_id"] == "flat402@okicici"
    assert bob["upi_uri"] == ("upi://pay?pa=flat402@okicici&pn=Household%20Pool&am=1000.00"
                              "&cu=INR&tn=October%202026%20Flat%20402")
    assert "UPI flat402@okicici" in summary["text"]


def test_a_resident_who_owes_the_pool_is_asked_to_pay_the_pool(pool_funded):
    before = resident_statements.get_resident_monthly_statement(pool_funded, APARTMENT, "bob", "2026-10")
    assert before["net_due_paise"] == 100_000 and before["recipient_upi_id"] is None

    apartments.update_apartment(pool_funded, APARTMENT, pool_name="Flat 402 Kitty",
                                pool_upi_id="flat402@okicici")
    statement = resident_statements.get_resident_monthly_statement(pool_funded, APARTMENT, "bob", "2026-10")
    assert (statement["recipient_upi_id"], statement["recipient_name"]) == (
        "flat402@okicici", "Flat 402 Kitty")
    assert statement["upi_uri"].startswith("upi://pay?pa=flat402@okicici&pn=Flat%20402%20Kitty&")
    assert "💳 Pay via UPI: flat402@okicici (Flat 402 Kitty)" in statement["whatsapp_text"]

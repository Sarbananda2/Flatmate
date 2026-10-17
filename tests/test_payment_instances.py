"""The landlord and the household pool as payees (migrations 0015 and 0016).

Residents pay two standing counterparties besides each other: the landlord,
whose payment details are on the lease, and the household pool, whose name,
bank account, IFSC and UPI ID are kept on the apartment. These tests pin that
the pool's details are validated like the owner contact's, that a deposit
payment records which of them received it and is named after them, that rows
written before the party was recorded still read correctly, and that money owed
to the pool is asked for at the pool's own UPI ID once it has one -- and that
the landlord's payout account may be in someone else's name without that name
ever taking the landlord's place.
"""
from __future__ import annotations

from datetime import date

import pytest

from aem.errors import ValidationError
from aem.services import (
    apartments,
    deposits,
    expenses,
    payments,
    pool_statements,
    reconciliation,
    rent,
    residents,
    resident_statements,
    sharing,
    statements,
)
from aem.services import property as household

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
    assert saved["pool_name"] == "House Kitty"
    assert (saved["pool_account_holder"], saved["pool_bank_account"], saved["pool_bank_ifsc"],
            saved["pool_upi_id"]) == (None, None, None, None)


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
        assert reset["pool_name"] == "House Kitty"


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
    assert apartments.read_apartment(apartment, APARTMENT)["pool_name"] == "House Kitty"


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
                     "bob": ("pool", "House Kitty"),
                     "chris": ("landlord", "Landlord (Kumar Yadav)")}
    bob_tx = _slot(summary, "bob")["transactions"][0]
    assert (bob_tx["counterparty_party"], bob_tx["counterparty_display_name"],
            bob_tx["counterparty_resident_id"]) == ("pool", "House Kitty", None)
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
        ("funding_payment", "pool", "House Kitty"),
        ("move_out_deduction", "pool", "House Kitty"),
        ("move_out_refund", "pool", "House Kitty")]
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
    assert _slot(funded.json(), "bob")["paid_to_display_name"] == "House Kitty"


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
    assert bob["upi_uri"] == ("upi://pay?pa=flat402@okicici&pn=House%20Kitty&am=1000.00"
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


# ------------------------------------------------- the Payment Pools passbooks --

def _deposit_paid(db, resident_id, party, paid_date, reference=None):
    deposits.record_slot_funding(
        db, _slot(_summary(db), resident_id)["slot_id"], apartment_id=APARTMENT,
        amount_paise=SHARE, paid_date=paid_date, payment_method="upi",
        payment_reference=reference, paid_to_party=party)


def _leaves(db, resident_id, deduction, refund_date, reason):
    deposits.vacate_and_settle_slot(
        db, _slot(_summary(db), resident_id)["slot_id"], apartment_id=APARTMENT,
        settlement_path="signatory_refund", deductions_paise=deduction,
        refund_amount_paise=SHARE - deduction, refund_date=refund_date,
        deduction_reason=reason)


def _pay(db, payment_id, resident_id, amount, payment_date, kind="to_pool", **extra):
    payments.record_payment(
        db, apartment_id=APARTMENT, payment_id=payment_id, payer_resident_id=resident_id,
        amount_paise=amount, payment_date=payment_date, payment_kind=kind, **extra)


def _expense(db, expense_id, description, amount, expense_date, **extra):
    expenses.create_expense(
        db, apartment_id=APARTMENT, expense_id=expense_id, description=description,
        amount_paise=amount, expense_date=expense_date, period_rule="transaction_date",
        allocation_method="equal", **extra)


@pytest.fixture()
def passbooks(configured):
    """Co-tenants with deposits paid to the landlord and to the kitty, two months'
    rent paid to the landlord, the kitty's own payments in and out, and
    household expenses paid by the kitty, by a resident, and by an advance."""
    db = configured
    rent.save_lease_config(db, APARTMENT, dict(LEASE))
    _slots(db, "direct_co_tenants")                  # Room 1 Alice, 2 Bob, 3 Chris
    _deposit_paid(db, "alice", "landlord", "2026-10-05", reference="UTR-A")
    _deposit_paid(db, "chris", "pool", "2026-10-05")
    _deposit_paid(db, "bob", "landlord", "2026-10-06")
    _leaves(db, "chris", 1_000_000, "2026-10-10", "Broken window")     # the pool refunds
    _leaves(db, "bob", 500_000, "2026-10-12", "Repainting")            # the landlord refunds
    # Before any rent exists, so it is held as Bob's credit, not rent.
    _pay(db, "bob-kitty", "bob", 100_000, "2026-11-01")
    rent.generate_monthly_rent_cycle(db, APARTMENT, "2026-11")
    rent.record_rent_payment(db, apartment_id=APARTMENT, settlement_month="2026-11",
                             resident_id="alice", amount_paise=950_000,
                             payment_date="2026-11-03", payment_mode="upi", reference="UPI-77")
    rent.record_landlord_disbursement(db, apartment_id=APARTMENT, settlement_month="2026-11",
                                      disbursement_mode="pool", reference="UTR-NOV",
                                      disbursed_date="2026-11-04")
    _expense(db, "broadband", "Broadband", 120_000, "2026-11-05", provider_name="ACT Fibernet")
    _expense(db, "groceries", "Groceries", 60_000, "2026-11-05", payer_resident_id="alice")
    _expense(db, "power-bill", "Electricity bill", 30_000, "2026-11-09")
    _pay(db, "chris-back", "chris", 50_000, "2026-11-06", kind="refund")
    _pay(db, "plumbing", "alice", 200_000, "2026-11-08", kind="provider_advance",
         payee_or_provider="kumar yadav", note="Plumbing, paid to the landlord")
    # Bob paid the electricity bill himself, so the kitty did not.
    _pay(db, "power", "bob", 30_000, "2026-11-09", kind="provider_advance",
         payee_or_provider="BESCOM", expense_id="power-bill")
    rent.generate_monthly_rent_cycle(db, APARTMENT, "2026-12")
    rent.record_landlord_disbursement(db, apartment_id=APARTMENT, settlement_month="2026-12",
                                      disbursement_mode="resident_advance",
                                      funding_resident_id="bob", reference="UTR-DEC",
                                      disbursed_date="2026-12-04")
    return db


def _rows(statement):
    return [(item["date"], item["category"], item["description"], item["counterparty_name"],
             item["direction"], item["amount_paise"], item["balance_paise"])
            for item in statement["items"]]


def test_the_kitty_passbook_runs_its_balance_through_every_movement(passbooks):
    pool = pool_statements.get_payment_pools_statement(passbooks, APARTMENT)["pool_statement"]
    # The account keeps the name stored for it.
    assert pool["account"] == {"instance_id": "__pool__", "name": "House Kitty",
                               "account_holder": None}
    # Newest first; each balance is the kitty's after that entry.
    assert _rows(pool) == [
        ("2026-11-06", "Resident Refund", "Refund from House Kitty", "Chris", "out",
         50_000, -970_000),
        ("2026-11-05", "House Expense", "Broadband", "ACT Fibernet", "out",
         120_000, -920_000),
        ("2026-11-04", "Rent Payout", "Rent payout · November 2026", "Kumar Yadav", "out",
         2_850_000, -800_000),
        ("2026-11-03", "Rent Collection", "Rent for November 2026", "Alice", "in",
         950_000, 2_050_000),
        ("2026-11-01", "Kitty Payment", "Payment into House Kitty", "Bob", "in",
         100_000, 1_100_000),
        ("2026-10-10", "Deposit Refund", "Security deposit refunded · Room 3", "Chris", "out",
         4_000_000, 1_000_000),
        ("2026-10-05", "Security Deposit", "Security deposit funded · Room 3", "Chris", "in",
         SHARE, SHARE),
    ]
    assert [item["seq"] for item in pool["items"]] == [7, 6, 5, 4, 3, 2, 1]
    assert (pool["total_in_paise"], pool["total_out_paise"], pool["closing_balance_paise"]) == (
        6_050_000, 7_020_000, -970_000)
    rent_in = pool["items"][3]
    assert (rent_in["payment_mode"], rent_in["reference"], rent_in["counterparty_resident_id"]) == (
        "upi", "UPI-77", "alice")
    assert rent_in["payment_id"] and rent_in["source_type"] == "payment"
    # What the kitty kept from Chris's deposit never left it, so it is no movement.
    assert all(item["category"] != "Deposit Deduction" for item in pool["items"])
    # Only the kitty's own expenses: not Alice's groceries, not the bill Bob's
    # advance paid, and not the rent, whose payout is already its own line.
    broadband = pool["items"][1]
    assert (broadband["entry_id"], broadband["source_type"]) == ("expense:broadband", "expense")
    assert [item["description"] for item in pool["items"] if item["category"] == "House Expense"] == [
        "Broadband"]


def test_the_landlord_passbook_counts_rent_advances_and_the_deposit_held(passbooks):
    landlord = pool_statements.get_payment_pools_statement(
        passbooks, APARTMENT)["landlord_statement"]
    assert landlord["account"] == {"instance_id": "__landlord__", "name": "Kumar Yadav",
                                   "owner_name": "Kumar Yadav"}
    assert _rows(landlord) == [
        ("2026-12-04", "Rent Payout", "Monthly rent · December 2026", "Bob (Advance)", "in",
         2_850_000, 10_900_000),
        ("2026-11-08", "Direct Advance", "Plumbing, paid to the landlord", "Alice", "in",
         200_000, 8_050_000),
        ("2026-11-04", "Rent Payout", "Monthly rent · November 2026", "House Kitty", "in",
         2_850_000, 7_850_000),
        ("2026-10-12", "Deposit Refund", "Deposit refunded by landlord · Room 2", "Bob", "out",
         4_500_000, 5_000_000),
        ("2026-10-12", "Deposit Deduction", "Deposit deduction · Room 2", "Bob", "out",
         500_000, 9_500_000),
        ("2026-10-06", "Security Deposit", "Security deposit · Room 2", "Bob", "in",
         SHARE, 10_000_000),
        ("2026-10-05", "Security Deposit", "Security deposit · Room 1", "Alice", "in",
         SHARE, SHARE),
    ]
    # A payment to any other provider is not the landlord's.
    assert all(item["counterparty_name"] != "Bob" or item["category"] != "Direct Advance"
               for item in landlord["items"])
    assert landlord["total_rent_paid_paise"] == 5_700_000
    assert landlord["deposit_held_paise"] == 5_000_000            # Alice's, still held
    assert (landlord["total_in_paise"], landlord["total_out_paise"]) == (15_900_000, 5_000_000)
    assert landlord["closing_balance_paise"] == 10_900_000
    december = landlord["items"][0]
    assert december["payment_id"] and december["counterparty_resident_id"] == "bob"
    assert landlord["items"][4]["note"] == "Repainting"


def test_a_signatory_fronted_deposit_is_on_the_landlords_passbook(leased):
    _slots(leased, "primary_signatory", "alice")
    landlord = pool_statements.get_payment_pools_statement(
        leased, APARTMENT)["landlord_statement"]
    assert [(item["category"], item["description"], item["counterparty_name"],
             item["amount_paise"]) for item in landlord["items"]] == [
        ("Security Deposit", "Security deposit fronted by Alice", "Alice", MASTER)]
    assert landlord["deposit_held_paise"] == MASTER
    # Reimbursing the signatory is between flatmates: nothing reaches the landlord.
    _fund(leased, "bob")
    assert len(pool_statements.get_payment_pools_statement(
        leased, APARTMENT)["landlord_statement"]["items"]) == 1


def test_an_empty_flat_has_empty_passbooks(apartment):
    statement = pool_statements.get_payment_pools_statement(apartment, APARTMENT)
    assert statement["pool_statement"]["items"] == []
    assert statement["pool_statement"]["closing_balance_paise"] == 0
    assert statement["landlord_statement"]["account"]["name"] == "Landlord"
    assert statement["landlord_statement"]["deposit_held_paise"] == 0


def test_the_statement_endpoint_returns_both_passbooks(client):
    client.post("/api/apartments", json={"apartment_id": APARTMENT, "name": "Flat 402"})
    client.post("/api/residents", json={"resident_id": "alice", "display_name": "Alice"})
    client.post("/api/payments", json={"payer_resident_id": "alice", "amount_paise": 250_000,
                                       "payment_date": "2026-10-02", "payment_kind": "to_pool"})
    response = client.get("/api/payments/pools/statement")
    assert response.status_code == 200
    body = response.json()
    assert [(item["category"], item["balance_paise"]) for item in body["pool_statement"]["items"]] == [
        ("Kitty Payment", 250_000)]
    assert body["landlord_statement"]["items"] == []


# ------------------------------------- whose name the landlord's account is in --

BENEFICIARY = {"payout_beneficiary_name": "  Anita Yadav ",
               "landlord_bank_account": "50100987654321", "landlord_bank_ifsc": "hdfc0004321",
               "landlord_upi_id": "anita.yadav@okaxis"}


def _lease_updates(db):
    with connect(db, read_only=True) as conn:
        return {row["field_name"] for row in conn.execute(
            "SELECT field_name FROM audit_log "
            "WHERE entity_type='rent_lease_config' AND action='update'").fetchall()}


def test_without_a_beneficiary_the_account_is_the_landlords_own(leased):
    config = rent.get_lease_config(leased, APARTMENT)
    assert config["payout_beneficiary_name"] is None
    assert config["effective_payout_name"] == "Kumar Yadav"


def test_a_beneficiary_is_kept_beside_the_landlord_not_in_their_place(leased):
    saved = rent.save_lease_config(leased, APARTMENT, dict(BENEFICIARY))
    assert (saved["landlord_name"], saved["payout_beneficiary_name"],
            saved["effective_payout_name"]) == ("Kumar Yadav", "Anita Yadav", "Anita Yadav")
    assert saved["landlord_bank_ifsc"] == "HDFC0004321"
    assert "payout_beneficiary_name" in _lease_updates(leased)
    assert "landlord_name" not in _lease_updates(leased)
    # The owner contact is the same landlord, paid into the same account.
    [owner] = household.list_contacts(leased, APARTMENT)["items"]
    assert (owner["name"], owner["beneficiary_name"], owner["upi_id"]) == (
        "Kumar Yadav", "Anita Yadav", "anita.yadav@okaxis")


@pytest.mark.parametrize("name", [None, "", "  ", "kumar yadav", " Kumar Yadav "])
def test_naming_nobody_or_the_landlord_themself_leaves_no_beneficiary(leased, name):
    rent.save_lease_config(leased, APARTMENT, {"payout_beneficiary_name": "Anita Yadav"})
    saved = rent.save_lease_config(leased, APARTMENT, {"payout_beneficiary_name": name})
    assert saved["payout_beneficiary_name"] is None
    assert (saved["landlord_name"], saved["effective_payout_name"]) == (
        "Kumar Yadav", "Kumar Yadav")


def test_a_save_that_leaves_the_beneficiary_out_keeps_it(leased):
    rent.save_lease_config(leased, APARTMENT, {"payout_beneficiary_name": "Anita Yadav"})
    saved = rent.save_lease_config(leased, APARTMENT, {"landlord_upi_id": "anita@okaxis"})
    assert saved["payout_beneficiary_name"] == "Anita Yadav"


def test_the_owner_contacts_beneficiary_is_the_leases(leased):
    [owner] = household.list_contacts(leased, APARTMENT)["items"]
    household.update_contact(leased, APARTMENT, owner["contact_id"],
                             {"beneficiary_name": "Anita Yadav"})
    config = rent.get_lease_config(leased, APARTMENT)
    assert (config["landlord_name"], config["payout_beneficiary_name"]) == (
        "Kumar Yadav", "Anita Yadav")
    # Only an owner is paid into an account in someone else's name.
    with pytest.raises(ValidationError):
        household.create_contact(leased, APARTMENT, {
            "role": "broker", "name": "Ravi Brokers", "beneficiary_name": "Ravi"})


def test_the_database_itself_refuses_a_blank_beneficiary(leased):
    for table, column in (("rent_lease_config", "payout_beneficiary_name"),
                          ("household_contact", "beneficiary_name")):
        with connect(leased) as conn, pytest.raises(DatabaseRefusal):
            conn.execute(f"UPDATE {table} SET {column}=%s", ("  ",))


def test_the_landlords_account_goes_by_the_beneficiarys_name(passbooks):
    rent.save_lease_config(passbooks, APARTMENT, {"payout_beneficiary_name": "Anita Yadav"})
    landlord = pool_statements.get_payment_pools_statement(
        passbooks, APARTMENT)["landlord_statement"]
    assert landlord["account"] == {"instance_id": "__landlord__", "name": "Anita Yadav",
                                   "owner_name": "Kumar Yadav"}
    summary = _summary(passbooks)
    assert summary["landlord_instance"]["name"] == "Anita Yadav"
    # Who was paid is still the landlord, as the lease names them.
    assert _slot(summary, "alice")["paid_to_display_name"] == "Landlord (Kumar Yadav)"
    # A resident who paid the account's holder directly paid the landlord.
    _pay(passbooks, "gate", "alice", 40_000, "2026-12-10", kind="provider_advance",
         payee_or_provider="anita yadav", note="Gate repair")
    newest = pool_statements.get_payment_pools_statement(
        passbooks, APARTMENT)["landlord_statement"]["items"][0]
    assert (newest["category"], newest["description"], newest["counterparty_name"],
            newest["amount_paise"], newest["balance_paise"]) == (
        "Direct Advance", "Gate repair", "Alice", 40_000, 10_940_000)


def test_the_lease_endpoint_saves_the_beneficiary(client):
    client.post("/api/apartments", json={"apartment_id": APARTMENT, "name": "Flat 402"})
    assert client.post("/api/rent/config", json=LEASE).status_code == 200
    saved = client.post("/api/rent/config", json={"payout_beneficiary_name": "Anita Yadav"})
    assert saved.status_code == 200
    assert (saved.json()["landlord_name"], saved.json()["effective_payout_name"]) == (
        "Kumar Yadav", "Anita Yadav")
    [owner] = client.get("/api/property/contacts").json()["items"]
    assert owner["beneficiary_name"] == "Anita Yadav"
    cleared = client.patch(f"/api/property/contacts/{owner['contact_id']}",
                           json={"beneficiary_name": None})
    assert cleared.status_code == 200
    assert client.get("/api/rent/config").json()["config"]["payout_beneficiary_name"] is None

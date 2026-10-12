"""A resident's own monthly statement.

These tests pin what makes a personal statement safe to send: its net due is
the ledger's own figure to the paisa, one month's net due is the next month's
brought-forward amount, every line is the resident's real share of a real
expense, and the message never asks for more than is owed today.
"""
from __future__ import annotations

import pytest

from aem.errors import NotFoundError, ValidationError
from aem.services import (
    deposits,
    payments,
    reconciliation,
    rent,
    resident_statements,
    residents,
)

from .conftest import APARTMENT


def _statement(db, resident_id, month="2026-10"):
    return resident_statements.get_resident_monthly_statement(
        db, APARTMENT, resident_id, month)


def _net_position(db, resident_id, as_of_date=None):
    result = reconciliation.reconcile(db, apartment_id=APARTMENT, as_of_date=as_of_date)
    return result["net_positions_paise_by_account"][resident_id]


# --------------------------------------------------------------------------- #
# Invariant D1: the net due is the ledger's figure, with nothing left over
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("resident_id", ["alice", "bob", "chris"])
@pytest.mark.parametrize("month", ["2026-09", "2026-10"])
def test_net_due_is_brought_forward_plus_charges_less_credits(household, resident_id, month):
    statement = _statement(household, resident_id, month)
    assert statement["total_charges_paise"] == sum(
        item["resident_share_paise"] for item in statement["items"])
    assert statement["total_payments_paise"] == sum(
        item["amount_paise"] for item in statement["payments"])
    assert statement["net_due_paise"] == (
        statement["brought_forward_paise"] + statement["total_charges_paise"]
        - statement["total_payments_paise"])


@pytest.mark.parametrize("resident_id", ["alice", "bob", "chris"])
def test_the_latest_net_due_is_exactly_the_ledger_balance(household, resident_id):
    assert _statement(household, resident_id)["net_due_paise"] == -_net_position(
        household, resident_id)


@pytest.mark.parametrize("resident_id", ["alice", "bob", "chris"])
def test_one_months_net_due_is_brought_forward_into_the_next(household, resident_id):
    september = _statement(household, resident_id, "2026-09")
    october = _statement(household, resident_id, "2026-10")
    assert september["brought_forward_paise"] == 0
    assert october["brought_forward_paise"] == september["net_due_paise"]


def test_the_flatmates_net_dues_mirror_the_pool(household):
    listing = resident_statements.list_flatmate_statements(household, APARTMENT, "2026-10")
    pool = reconciliation.reconcile(household, apartment_id=APARTMENT)[
        "net_positions_paise_by_account"]["__pool__"]
    assert listing["totals"]["net_due_paise"] == pool
    assert listing["totals"]["net_due_paise"] == sum(
        item["net_due_paise"] for item in listing["items"])


# --------------------------------------------------------------------------- #
# Invariant D2: every line is itemized
# --------------------------------------------------------------------------- #

def test_charges_are_itemized_by_category_with_the_share_and_the_whole(household):
    statement = _statement(household, "alice", "2026-09")
    by_category = {item["category"]: item for item in statement["items"]}
    assert set(by_category) == {"Rent", "Broadband", "Maid", "Groceries", "Electricity"}
    rent_line = by_category["Rent"]
    assert rent_line["total_expense_paise"] == 4_500_000
    assert rent_line["resident_share_paise"] == 1_500_000
    assert rent_line["allocation_method"] == "equal"
    assert rent_line["service_period"] == "2026-09"
    # Maid is weighted 2:1:1, so Alice carries half of it.
    assert by_category["Maid"]["allocation_method"] == "weighted"
    assert by_category["Maid"]["resident_share_paise"] == 150_000
    # Groceries and electricity are August's, settled in September.
    assert by_category["Groceries"]["service_period"] == "2026-08"
    assert by_category["Electricity"]["service_period"] == "2026-08"


def test_an_electricity_line_shows_the_components_that_make_it_up(household):
    for resident_id in ("alice", "bob", "chris"):
        line = next(item for item in _statement(household, resident_id, "2026-09")["items"]
                    if item["category"] == "Electricity")
        assert line["total_expense_paise"] == 735_000
        assert [c["name"] for c in line["components"]] == ["Energy charges", "Fixed charges"]
        assert sum(c["resident_share_paise"] for c in line["components"]) == \
            line["resident_share_paise"]
    shares = [next(i for i in _statement(household, rid, "2026-09")["items"]
                   if i["category"] == "Electricity")["resident_share_paise"]
              for rid in ("alice", "bob", "chris")]
    assert sum(shares) == 735_000
    assert shares[0] > shares[1] > shares[2]   # usage: 42.5, 31 and 18.25 kWh


def test_an_expense_with_no_definition_is_listed_as_other(household):
    line = next(item for item in _statement(household, "bob")["items"]
                if item["expense_id"] == "repair-2026-10")
    assert line["category"] == "Other"
    assert line["description"] == "Geyser repair"


# --------------------------------------------------------------------------- #
# Payments and credits
# --------------------------------------------------------------------------- #

def test_payments_advances_and_funded_expenses_are_all_credited(household):
    chris = _statement(household, "chris", "2026-09")
    assert [(p["payment_id"], p["payment_kind"], p["amount_paise"])
            for p in chris["payments"]] == [("pay-chris-2026-09", "to_pool", 2_500_000)]
    # Bob paid the provider for the whole electricity bill, and a little into the pool.
    bob = {p["payment_id"]: p for p in _statement(household, "bob", "2026-09")["payments"]}
    assert bob["advance-electricity-2026-09"]["payment_kind"] == "provider_advance"
    assert bob["advance-electricity-2026-09"]["amount_paise"] == 735_000
    assert bob["pay-bob-2026-09"]["amount_paise"] == 50_000
    # The linked advance replaces the payer credit: the bill is credited once.
    assert "electricity-2026-09" not in bob
    # Alice bought the groceries, which is a credit against her own charges.
    alice = _statement(household, "alice", "2026-09")["payments"]
    assert [(p["payment_id"], p["payment_kind"], p["amount_paise"]) for p in alice] == [
        ("groceries-2026-09", "expense_funded", 333_401)]


def test_an_adjustment_against_a_resident_is_a_negative_credit(household):
    credits = _statement(household, "chris")["payments"]
    assert [(p["payment_id"], p["payment_kind"], p["amount_paise"]) for p in credits] == [
        ("adj-chris-oct", "ledger_adjustment", -25_000)]


def test_a_reversed_payment_is_not_credited(household):
    before = _statement(household, "alice")
    payments.record_payment(
        household, apartment_id=APARTMENT, payment_id="pay-alice-oct",
        payer_resident_id="alice", amount_paise=100_000, payment_date="2026-10-10",
        payment_mode="upi", reference="UPI/123")
    paid = _statement(household, "alice")
    assert paid["net_due_paise"] == before["net_due_paise"] - 100_000
    assert paid["payments"][-1]["payment_mode"] == "upi"
    assert paid["payments"][-1]["reference"] == "UPI/123"
    payments.reverse_payment(household, "pay-alice-oct", reason="Entered twice")
    assert _statement(household, "alice") == before


# --------------------------------------------------------------------------- #
# Who is paid, and how much
# --------------------------------------------------------------------------- #

def _with_signatory(db, upi_id="alice@okaxis"):
    residents.update_resident(db, "alice", apartment_id=APARTMENT, upi_id=upi_id)
    rent.save_lease_config(db, APARTMENT, {
        "landlord_name": "Landlord", "monthly_rent_paise": 4_500_000,
        "security_deposit_paise": 9_000_000})
    return deposits.configure_mode(db, APARTMENT, "primary_signatory", "alice")


def test_without_a_signatory_there_is_no_one_to_pay(household):
    statement = _statement(household, "bob")
    assert statement["net_due_paise"] > 0
    assert statement["recipient_upi_id"] is None
    assert statement["upi_uri"] is None
    assert "Pay via UPI" not in statement["whatsapp_text"]


def test_the_primary_signatory_is_the_upi_recipient(household):
    _with_signatory(household)
    statement = _statement(household, "bob")
    assert statement["recipient_upi_id"] == "alice@okaxis"
    assert statement["recipient_name"] == "Alice"
    rupees = f"{statement['net_due_paise'] // 100}.{statement['net_due_paise'] % 100:02d}"
    assert statement["upi_uri"].startswith("upi://pay?pa=alice@okaxis&")
    assert f"am={rupees}&" in statement["upi_uri"]
    assert "💳 Pay via UPI: alice@okaxis (Alice)" in statement["whatsapp_text"]
    # The signatory is not asked to pay themselves.
    assert _statement(household, "alice")["recipient_upi_id"] is None


def test_a_past_statement_never_asks_for_more_than_is_owed_today(household):
    _with_signatory(household)
    september = _statement(household, "bob", "2026-09")
    assert september["payable_now_paise"] == september["net_due_paise"] > 0
    payments.record_payment(
        household, apartment_id=APARTMENT, payment_id="pay-bob-oct", payer_resident_id="bob",
        amount_paise=_statement(household, "bob")["net_due_paise"],
        payment_date="2026-10-11")
    settled = _statement(household, "bob", "2026-09")
    assert settled["net_due_paise"] == september["net_due_paise"]   # history is unchanged
    assert settled["payable_now_paise"] == 0
    assert settled["upi_uri"] is None
    assert "Paid in full since this statement" in settled["whatsapp_text"]
    assert "Pay via UPI" not in settled["whatsapp_text"]
    assert _statement(household, "bob")["direction"] == "settled"


def test_a_resident_the_pool_owes_is_not_asked_to_pay(household):
    payments.record_payment(
        household, apartment_id=APARTMENT, payment_id="pay-bob-oct", payer_resident_id="bob",
        amount_paise=_statement(household, "bob")["net_due_paise"] + 50_000,
        payment_date="2026-10-11")
    statement = _statement(household, "bob")
    assert statement["net_due_paise"] == -50_000
    assert statement["direction"] == "owed_to_resident"
    assert statement["payable_now_paise"] == 0
    assert "👉 Net Due: ₹500.00 (Owed to You by the Pool)" in statement["whatsapp_text"]


# --------------------------------------------------------------------------- #
# The WhatsApp message
# --------------------------------------------------------------------------- #

def test_the_whatsapp_message_itemizes_and_totals(household):
    statement = _statement(household, "alice", "2026-09")
    text = statement["whatsapp_text"]
    lines = text.splitlines()
    assert lines[0] == "🏠 Flat 402 — September 2026 Statement"
    assert lines[1] == "👤 Resident: Alice"
    assert "📋 Itemized Breakdown:" in lines
    assert lines[4:9] == [
        "• Rent: ₹15,000.00", "• Maid: ₹1,500.00", "• Groceries: ₹1,111.33",
        "• Broadband: ₹400.00", "• Electricity (August electricity bill): ₹3,294.24"]
    assert "Total Charges: ₹21,305.57" in lines
    assert "Payments Credited: -₹3,334.01" in lines
    assert lines[-1] == "👉 Net Due: ₹17,971.56 (Owed to Pool)"   # closed: no draft note
    october = _statement(household, "bob")["whatsapp_text"].splitlines()
    assert "• Geyser repair: ₹833.33" in october
    assert "Brought Forward (due): ₹11,890.85" in october
    assert "Payments Credited: ₹0.00" in october
    assert october[-1] == "📝 Draft — figures can still change"


def test_the_message_names_the_room_and_what_was_brought_forward(household):
    summary = _with_signatory(household)
    created = deposits.initialize_or_update_slots(household, APARTMENT, [
        {key: slot[key] for key in ("slot_number", "slot_label", "target_deposit_paise",
                                    "assigned_resident_id")}
        for slot in summary["proposal"]])
    room = next(slot["slot_label"] for slot in created["slots"]
                if slot["assigned_resident_id"] == "bob")
    statement = _statement(household, "bob")
    assert statement["room_label"] == room
    assert f"👤 Resident: Bob ({room})" in statement["whatsapp_text"]
    assert statement["brought_forward_paise"] > 0
    assert "Brought Forward (due): " in statement["whatsapp_text"]


def test_an_empty_month_is_a_statement_with_nothing_on_it(household):
    statement = _statement(household, "alice", "2027-03")
    assert statement["statement_status"] == "not_created"
    assert statement["items"] == [] and statement["payments"] == []
    assert statement["net_due_paise"] == statement["brought_forward_paise"] == \
        _statement(household, "alice")["net_due_paise"]
    assert "• No charges this month" in statement["whatsapp_text"]


def test_statement_status_follows_the_months_statement(household):
    assert _statement(household, "alice", "2026-09")["statement_status"] == "closed"
    assert _statement(household, "alice", "2026-10")["statement_status"] == "draft"


# --------------------------------------------------------------------------- #
# Who is listed, and what is refused
# --------------------------------------------------------------------------- #

def test_a_former_resident_is_listed_only_while_the_month_concerns_them(household):
    listing = resident_statements.list_flatmate_statements(household, APARTMENT, "2026-10")
    assert [item["resident_id"] for item in listing["items"]] == ["alice", "bob", "chris"]
    residents.create_resident(household, apartment_id=APARTMENT, resident_id="dev",
                              display_name="Dev", active_from="2026-08-01")
    residents.deactivate_resident(household, "dev", active_until="2026-08-31")
    listing = resident_statements.list_flatmate_statements(household, APARTMENT, "2026-10")
    assert "dev" not in [item["resident_id"] for item in listing["items"]]
    residents.deactivate_resident(household, "chris", active_until="2026-10-31")
    listing = resident_statements.list_flatmate_statements(household, APARTMENT, "2026-10")
    assert "chris" in [item["resident_id"] for item in listing["items"]]


def test_an_unknown_resident_or_a_bad_month_is_refused(household):
    with pytest.raises(NotFoundError):
        _statement(household, "ghost")
    with pytest.raises(ValidationError):
        _statement(household, "alice", "October")


def test_reading_a_statement_writes_nothing(household):
    from .support import connect
    tables = ("audit_log", "payment", "allocation", "monthly_statement")
    with connect(household, read_only=True) as conn:
        before = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    _statement(household, "alice")
    resident_statements.list_flatmate_statements(household, APARTMENT, "2026-10")
    with connect(household, read_only=True) as conn:
        assert before == {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                          for t in tables}


# --------------------------------------------------------------------------- #
# The API
# --------------------------------------------------------------------------- #

def test_the_resident_statement_endpoint(household_client):
    response = household_client.get("/api/residents/bob/statements/2026-10")
    assert response.status_code == 200
    body = response.json()
    assert body["resident_id"] == "bob" and body["settlement_month"] == "2026-10"
    assert body["net_due_paise"] == (body["brought_forward_paise"]
                                     + body["total_charges_paise"]
                                     - body["total_payments_paise"])
    assert body["whatsapp_text"].startswith("🏠 Flat 402 — October 2026 Statement")
    assert household_client.get("/api/residents/ghost/statements/2026-10").status_code == 404
    assert household_client.get("/api/residents/bob/statements/2026-13").status_code == 422


def test_the_flatmates_endpoint_lists_everyone_for_the_month(household_client):
    response = household_client.get("/api/statements/2026-10/flatmates")
    assert response.status_code == 200
    body = response.json()
    assert body["settlement_month"] == "2026-10"
    assert body["statement_status"] == "draft"
    assert [item["display_name"] for item in body["items"]] == ["Alice", "Bob", "Chris"]
    assert all(item["whatsapp_text"] for item in body["items"])
    assert household_client.get("/api/statements/nonsense/flatmates").status_code == 422


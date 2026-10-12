"""A resident's guided exit clearance.

These tests pin what makes a move-out trustworthy: the cash figure is exactly
the deposit put in, less deductions, plus the resident's operating balance; the
ledger payment that clears that balance leaves it at precisely zero; the deposit
slot is settled for its full value whatever is netted against it; and the whole
thing happens together or not at all.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from aem.errors import ConflictError, NotFoundError, ValidationError
from aem.services import (
    clearance,
    deposits,
    expenses,
    payments,
    reconciliation,
    rent,
    residents,
)

from .conftest import APARTMENT
from .support import DatabaseRefusal, connect

MASTER = 15_000_000   # ₹1,50,000 held by the landlord
SHARE = 5_000_000     # ₹50,000 a slot
DUES = 240_000        # ₹2,400 each of a ₹7,200 bill
TODAY = date.today().isoformat()
TOMORROW = (date.today() + timedelta(days=1)).isoformat()


def _rows(db, sql, params=()):
    with connect(db, read_only=True) as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _positions(db):
    return reconciliation.reconcile(
        db, apartment_id=APARTMENT)["net_positions_paise_by_account"]


def _slot(db, key):
    field = "slot_number" if isinstance(key, int) else "assigned_resident_id"
    return next(slot for slot in deposits.deposit_summary(db, APARTMENT)["slots"]
                if slot[field] == key)


def _clear(db, resident_id="bob", **overrides):
    request = {"move_out_date": TODAY, "settlement_date": TODAY,
               "settlement_path": "signatory_refund", "deductions_paise": 0, **overrides}
    return clearance.execute_clearance(db, APARTMENT, resident_id, request)


def _snapshot(db):
    """Everything a clearance could touch, to prove a refused one touched nothing."""
    return {
        "positions": _positions(db),
        "slots": [(s["slot_number"], s["assigned_resident_id"], s["funded_amount_paise"])
                  for s in deposits.deposit_summary(db, APARTMENT)["slots"]],
        "active": sorted(r["resident_id"] for r in residents.read_residents(
            db, APARTMENT, active_only=True)),
        "payments": len(_rows(db, "SELECT 1 FROM payment")),
        "transactions": len(_rows(db, "SELECT 1 FROM household_deposit_transaction")),
        "clearances": len(_rows(db, "SELECT 1 FROM resident_exit_clearance")),
    }


@pytest.fixture()
def flat(apartment):
    """Alice fronted a ₹1,50,000 deposit; Bob has funded his ₹50,000 slot, Chris
    has paid nothing; and everyone owes ₹2,400 on a shared bill."""
    rent.save_lease_config(apartment, APARTMENT, {
        "landlord_name": "Kumar Yadav", "monthly_rent_paise": 2_850_000,
        "security_deposit_paise": MASTER})
    summary = deposits.configure_mode(apartment, APARTMENT, "primary_signatory", "alice")
    created = deposits.initialize_or_update_slots(apartment, APARTMENT, [
        {key: slot[key] for key in ("slot_number", "slot_label", "target_deposit_paise",
                                    "assigned_resident_id")}
        for slot in summary["proposal"]])
    bob_slot = next(s for s in created["slots"] if s["assigned_resident_id"] == "bob")
    deposits.record_slot_funding(
        apartment, bob_slot["slot_id"], apartment_id=APARTMENT, amount_paise=SHARE,
        paid_date=TODAY, payment_method="upi")
    expenses.create_expense(
        apartment, apartment_id=APARTMENT, expense_id="wifi-maid-groceries",
        description="Wi-Fi, maid and groceries", amount_paise=3 * DUES,
        expense_date=TODAY, period_rule="transaction_date", allocation_method="equal")
    return apartment


# ------------------------------------------------------------------- preview --

def test_the_preview_shows_the_slot_and_the_live_operating_balance(flat):
    preview = clearance.get_clearance_preview(flat, APARTMENT, "bob")
    assert (preview["display_name"], preview["is_active"]) == ("Bob", True)
    assert (preview["slot"]["slot_label"], preview["slot"]["funded_amount_paise"]) == (
        "Room 2", SHARE)
    assert preview["ledger_outstanding_charges_paise"] == DUES
    assert preview["ledger_unapplied_credits_paise"] == 0
    assert preview["operating_net_balance_paise"] == -DUES
    assert preview["default_move_out_date"] == TODAY
    assert preview["allowed_settlement_paths"] == [
        "signatory_refund", "direct_replacement_transfer"]
    assert (preview["primary_signatory_name"], preview["blocking_reason"]) == ("Alice", None)
    # Nobody is free to take the slot until a newcomer is added.
    assert preview["incoming_candidates"] == []
    residents.create_resident(flat, apartment_id=APARTMENT, resident_id="dana",
                              display_name="Dana")
    assert clearance.get_clearance_preview(flat, APARTMENT, "bob")["incoming_candidates"] == [
        {"resident_id": "dana", "display_name": "Dana"}]


def test_the_preview_says_who_cannot_be_cleared_and_why(flat):
    signatory = clearance.get_clearance_preview(flat, APARTMENT, "alice")
    assert "primary signatory" in signatory["blocking_reason"]
    residents.deactivate_resident(flat, "chris", apartment_id=APARTMENT)
    assert "already moved out" in clearance.get_clearance_preview(
        flat, APARTMENT, "chris")["blocking_reason"]
    with pytest.raises(NotFoundError):
        clearance.get_clearance_preview(flat, APARTMENT, "nobody")


# ---------------------------------------------------- the settlement itself --

def test_the_worked_example_nets_deposit_deductions_and_dues(flat):
    """₹50,000 put in, ₹5,000 for painting, ₹2,400 of dues: ₹42,600 back to Bob."""
    slip = _clear(flat, deductions_paise=500_000, deduction_reason="Painting",
                  settlement_reference="UTR428919028471", notes="Moving to Pune")

    # Invariant C1.
    assert (slip["deposit_funded_paise"], slip["deposit_deductions_paise"]) == (SHARE, 500_000)
    assert (slip["ledger_outstanding_paise"], slip["ledger_credit_paise"]) == (DUES, 0)
    assert slip["net_settlement_paise"] == SHARE - 500_000 - DUES == 4_260_000
    assert slip["settlement_direction"] == "refund_to_resident"
    assert (slip["deposit_refund_paise"], slip["ledger_offset_paise"]) == (4_500_000, -DUES)
    assert slip["net_ledger_offset_applied"] is True

    # Invariant C2.1: the slot is settled for its full value and stands vacant.
    slot = _slot(flat, 2)
    assert (slot["status"], slot["assigned_resident_id"], slot["funded_amount_paise"]) == (
        "vacant", None, 0)
    assert [(tx["tx_type"], tx["amount_paise"]) for tx in slot["transactions"]] == [
        ("funding_payment", SHARE), ("move_out_deduction", 500_000),
        ("move_out_refund", 4_500_000)]
    assert (slip["slot_label"], slip["counterparty_display_name"]) == ("Room 2", "Alice")

    # Invariant C2.2: one pool payment, and Bob's operating balance is exactly zero.
    payment = _rows(flat, "SELECT * FROM payment WHERE payment_id=%s",
                    (slip["clearing_payment_id"],))[0]
    assert (payment["payment_kind"], payment["amount_paise"], payment["reference"]) == (
        "to_pool", DUES, "Move-out deposit offset")
    assert (payment["payer_resident_id"], payment["payment_date"]) == ("bob", TODAY)
    positions = _positions(flat)
    assert positions["bob"] == 0
    assert sum(positions.values()) == 0, "the ledger still balances"

    # Invariant C2.3: Bob has moved out, on the date given.
    bob = residents.read_resident(flat, "bob")
    assert (bob["is_active"], bob["active_until"]) == (0, TODAY)

    # Invariant C2.4: one audited record.
    audited = _rows(flat, "SELECT entity_id, action, reason FROM audit_log "
                          "WHERE entity_type='resident_clearance'")
    assert audited == [{"entity_id": slip["clearance_id"], "action": "execute",
                        "reason": "Moving to Pune"}]
    assert clearance.get_clearance(flat, APARTMENT, "bob") == slip


def test_without_the_offset_the_ledger_is_left_exactly_as_it_was(flat):
    before = _positions(flat)
    slip = _clear(flat, net_ledger_offset=False)
    assert slip["net_settlement_paise"] == SHARE
    assert (slip["net_ledger_offset_applied"], slip["clearing_payment_id"]) == (False, None)
    assert slip["ledger_offset_paise"] == 0
    # What he owed is still recorded on the slip, and still owed on the ledger.
    assert slip["ledger_outstanding_paise"] == DUES
    assert _positions(flat) == before
    assert _rows(flat, "SELECT 1 FROM payment") == []
    assert _slot(flat, 2)["status"] == "vacant"


def test_a_direct_replacement_takes_the_slot_and_the_dues_are_still_cleared(flat):
    residents.create_resident(flat, apartment_id=APARTMENT, resident_id="dana",
                              display_name="Dana")
    slip = _clear(flat, settlement_path="direct_replacement_transfer",
                  incoming_resident_id="dana", deductions_paise=500_000,
                  deduction_reason="Wardrobe door")
    assert slip["net_settlement_paise"] == 4_260_000
    assert (slip["counterparty_resident_id"], slip["counterparty_display_name"]) == (
        "dana", "Dana")
    slot = _slot(flat, 2)
    # Dana holds the slot having paid ₹45,000 of it; Bob's dues are his own.
    assert (slot["assigned_resident_id"], slot["funded_amount_paise"], slot["status"]) == (
        "dana", 4_500_000, "occupied_pending")
    assert slot["transactions"][-1]["tx_type"] == "direct_replacement_transfer"
    assert _positions(flat)["bob"] == 0


def test_someone_who_owes_more_than_their_deposit_pays_the_difference(flat):
    # Chris put nothing into his slot and owes ₹2,400.
    slip = _clear(flat, "chris")
    assert (slip["deposit_funded_paise"], slip["net_settlement_paise"]) == (0, -DUES)
    assert slip["settlement_direction"] == "payment_from_resident"
    assert _positions(flat)["chris"] == 0 and _slot(flat, 3)["status"] == "vacant"


def test_an_exactly_balanced_exit_is_recorded_as_such(flat):
    payments.record_payment(flat, apartment_id=APARTMENT, payment_id="chris-pays",
                            payer_resident_id="chris", amount_paise=DUES, payment_date=TODAY)
    slip = _clear(flat, "chris")
    assert (slip["net_settlement_paise"], slip["settlement_direction"]) == (0, "zero_balanced")
    assert slip["clearing_payment_id"] is None, "nothing to clear, so no payment is invented"
    assert slip["upi_uri"] is None


def test_money_the_household_owes_the_leaver_is_added_and_refunded(flat):
    # Bob overpaid: ₹5,000 into the pool against ₹2,400 of charges.
    payments.record_payment(flat, apartment_id=APARTMENT, payment_id="bob-overpays",
                            payer_resident_id="bob", amount_paise=500_000, payment_date=TODAY)
    preview = clearance.get_clearance_preview(flat, APARTMENT, "bob")
    assert (preview["ledger_outstanding_charges_paise"],
            preview["ledger_unapplied_credits_paise"],
            preview["operating_net_balance_paise"]) == (0, 260_000, 260_000)

    slip = _clear(flat)
    assert (slip["ledger_outstanding_paise"], slip["ledger_credit_paise"]) == (0, 260_000)
    assert slip["net_settlement_paise"] == SHARE + 260_000
    refund = _rows(flat, "SELECT * FROM payment WHERE payment_id=%s",
                   (slip["clearing_payment_id"],))[0]
    assert (refund["payment_kind"], refund["amount_paise"]) == ("refund", 260_000)
    assert _positions(flat)["bob"] == 0


def test_the_balance_cleared_is_the_net_position_not_just_open_charges(flat):
    """Bob paid a ₹9,000 bill for everyone. His ₹2,400 and ₹3,000 charges look
    outstanding, but the household owes him ₹9,000: on balance it owes him ₹3,600."""
    expenses.create_expense(
        flat, apartment_id=APARTMENT, expense_id="plumber", description="Plumber",
        amount_paise=900_000, expense_date=TODAY, period_rule="transaction_date",
        allocation_method="equal", payer_resident_id="bob")
    preview = clearance.get_clearance_preview(flat, APARTMENT, "bob")
    assert preview["ledger_outstanding_charges_paise"] == DUES + 300_000
    assert preview["operating_net_balance_paise"] == 900_000 - 300_000 - DUES == 360_000

    slip = _clear(flat)
    assert slip["net_settlement_paise"] == SHARE + 360_000
    assert (slip["ledger_outstanding_paise"], slip["ledger_credit_paise"]) == (0, 360_000)
    positions = _positions(flat)
    assert positions["bob"] == 0 and sum(positions.values()) == 0


def test_a_resident_with_no_deposit_slot_gets_a_standalone_clearance(flat):
    residents.create_resident(flat, apartment_id=APARTMENT, resident_id="dana",
                              display_name="Dana")
    payments.record_payment(flat, apartment_id=APARTMENT, payment_id="dana-credit",
                            payer_resident_id="dana", amount_paise=100_000, payment_date=TODAY)
    preview = clearance.get_clearance_preview(flat, APARTMENT, "dana")
    assert (preview["slot"], preview["allowed_settlement_paths"]) == (
        None, ["standalone_clearance"])

    for path in ("signatory_refund", "direct_replacement_transfer"):
        with pytest.raises(ValidationError):
            _clear(flat, "dana", settlement_path=path,
                   **({"incoming_resident_id": "bob"} if path.startswith("direct") else {}))
    with pytest.raises(ValidationError):
        _clear(flat, "dana", settlement_path="standalone_clearance",
               deductions_paise=100, deduction_reason="Nothing to deduct from")

    slip = _clear(flat, "dana", settlement_path="standalone_clearance")
    assert (slip["slot_id"], slip["deposit_funded_paise"]) == (None, 0)
    assert (slip["net_settlement_paise"], slip["counterparty_resident_id"]) == (100_000, None)
    assert _positions(flat)["dana"] == 0
    # And the reverse: somebody with a slot cannot skip settling it.
    with pytest.raises(ValidationError):
        _clear(flat, settlement_path="standalone_clearance")


# ------------------------------------------------- refusals and atomicity --

@pytest.mark.parametrize("overrides", [
    {"settlement_path": None}, {"settlement_path": "landlord"},
    {"move_out_date": TOMORROW}, {"move_out_date": "12-10-2026"}, {"move_out_date": None},
    {"settlement_date": TOMORROW}, {"settlement_date": None},
    {"deductions_paise": -1}, {"deductions_paise": 500.5}, {"deductions_paise": True},
    {"deductions_paise": 500_000},                               # no reason given
    {"deductions_paise": 500_000, "deduction_reason": "   "},
    {"deductions_paise": SHARE + 1, "deduction_reason": "More than was put in"},
    {"net_ledger_offset": "yes"},
    {"settlement_path": "direct_replacement_transfer"},          # nobody named
    {"incoming_resident_id": "chris"},                           # named, but a refund
    {"settlement_path": "direct_replacement_transfer", "incoming_resident_id": "bob"},
    {"surprise": 1},
])
def test_an_invalid_clearance_is_refused_and_changes_nothing(flat, overrides):
    before = _snapshot(flat)
    with pytest.raises(ValidationError):
        _clear(flat, **overrides)
    assert _snapshot(flat) == before


def test_a_clearance_that_fails_part_way_leaves_no_trace(flat):
    before = _snapshot(flat)
    # Chris already holds a slot, so he cannot take Bob's: refused after validation.
    with pytest.raises(ConflictError) as held:
        _clear(flat, settlement_path="direct_replacement_transfer",
               incoming_resident_id="chris")
    assert held.value.code == "deposit_slot_already_held"
    # The primary signatory's slot cannot be vacated.
    with pytest.raises(ConflictError) as fronted:
        _clear(flat, "alice")
    assert fronted.value.code == "deposit_signatory_slot"
    # A move-out date before they moved in is refused by the resident record,
    # after the slot and the ledger have already been dealt with in the transaction.
    with pytest.raises(ValidationError):
        _clear(flat, move_out_date="2026-07-31")
    with pytest.raises(NotFoundError):
        _clear(flat, "nobody")
    assert _snapshot(flat) == before


def test_a_resident_is_cleared_once(flat):
    _clear(flat)
    with pytest.raises(ConflictError) as again:
        _clear(flat)
    assert again.value.code == "resident_already_moved_out"
    assert len(_rows(flat, "SELECT 1 FROM resident_exit_clearance")) == 1


def test_someone_who_returns_and_leaves_again_gets_a_second_slip(flat):
    first = _clear(flat, "chris")
    residents.reactivate_resident(flat, "chris", apartment_id=APARTMENT)
    second = _clear(flat, "chris", settlement_path="standalone_clearance")
    assert second["clearance_id"] != first["clearance_id"]
    assert clearance.get_clearance(flat, APARTMENT, "chris")["clearance_id"] == \
        second["clearance_id"]
    assert clearance.get_clearance(flat, APARTMENT, "alice") is None


def test_the_database_itself_guards_the_clearance_record(flat):
    slip = _clear(flat, deductions_paise=500_000, deduction_reason="Painting")
    for column, value in (("net_settlement_paise", -1),          # contradicts its direction
                          ("settlement_direction", "zero_balanced"),
                          ("settlement_path", "standalone_clearance"),  # but it has a slot
                          ("deposit_deductions_paise", SHARE + 1),
                          ("ledger_credit_paise", 1),             # dues and credit together
                          ("move_out_date", "12-10-2026")):
        with connect(flat, as_owner=True) as conn, pytest.raises(DatabaseRefusal):
            conn.execute(f"UPDATE resident_exit_clearance SET {column}=%s "
                         "WHERE clearance_id=%s", (value, slip["clearance_id"]))
    # And the application's own role cannot change or remove a slip at all.
    with connect(flat) as conn, pytest.raises(DatabaseRefusal):
        conn.execute("UPDATE resident_exit_clearance SET notes='edited'")
    with connect(flat) as conn, pytest.raises(DatabaseRefusal):
        conn.execute("DELETE FROM resident_exit_clearance")


# ------------------------------------------------------------------ the slip --

def test_the_slip_carries_a_message_and_a_payment_link_for_the_net_amount(flat):
    residents.update_resident(flat, "bob", apartment_id=APARTMENT, upi_id="bob@okaxis")
    slip = _clear(flat, deductions_paise=500_000, deduction_reason="Painting",
                  settlement_reference="UTR428919028471")
    readable = f"{date.today().day} {date.today().strftime('%b %Y')}"
    assert slip["summary_text"].splitlines() == [
        "🏠 Flat 402 — Move-Out Clearance Statement",
        "Resident: Bob (Room 2)",
        f"Effective Date: {readable}",
        "",
        "💰 Deposit Refund: ₹50,000.00",
        "🛠️ Deductions (Painting): -₹5,000.00",
        "📊 Operating Ledger Dues: -₹2,400.00",
        "────────────────────────────────",
        "💵 Net Settlement: ₹42,600.00 (Payable to Bob)",
        "Status: Settled (UTR: UTR428919028471)",
    ]
    # Money is due to Bob, so the link pays Bob, for exactly the net amount.
    assert (slip["payee_resident_id"], slip["payee_upi_id"]) == ("bob", "bob@okaxis")
    assert slip["upi_uri"].startswith("upi://pay?pa=bob@okaxis&pn=Bob&am=42600.00&cu=INR")


def test_the_link_pays_the_signatory_when_the_leaver_owes_and_needs_a_upi_id(flat):
    owed = _clear(flat, "chris")
    assert (owed["payee_resident_id"], owed["upi_uri"]) == ("alice", None), "no UPI ID on file"
    assert "Payable by Chris" in owed["summary_text"]

    residents.update_resident(flat, "alice", apartment_id=APARTMENT, upi_id="alice@okhdfcbank")
    slip = clearance.get_clearance(flat, APARTMENT, "chris")
    assert slip["upi_uri"].startswith("upi://pay?pa=alice@okhdfcbank&pn=Alice&am=2400.00")
    unsettled = _clear(flat, net_ledger_offset=False)
    assert "settled separately" in unsettled["summary_text"]


# -------------------------------------------------------------------- the API --

def test_the_clearance_endpoints_preview_execute_and_read_back(client):
    client.post("/api/apartments", json={"apartment_id": APARTMENT, "name": "Flat 402"})
    for resident_id in ("alice", "bob", "chris"):
        client.post("/api/residents", json={"resident_id": resident_id,
                                            "display_name": resident_id.title()})
    client.post("/api/rent/config", json={
        "landlord_name": "Kumar Yadav", "monthly_rent_paise": 2_850_000,
        "security_deposit_paise": MASTER})
    proposal = client.post("/api/rent/deposits/config", json={
        "funding_mode": "primary_signatory",
        "primary_signatory_resident_id": "alice"}).json()["proposal"]
    slots = client.post("/api/rent/deposits/slots", json={"slots": [
        {key: slot[key] for key in ("slot_number", "slot_label", "target_deposit_paise",
                                    "assigned_resident_id")} for slot in proposal]}).json()
    bob_slot = next(s for s in slots["slots"] if s["assigned_resident_id"] == "bob")
    client.post(f"/api/rent/deposits/slots/{bob_slot['slot_id']}/fund", json={
        "amount_paise": SHARE, "paid_date": TODAY, "payment_method": "upi"})
    client.post("/api/expenses", json={
        "expense_id": "shared-bill", "description": "Wi-Fi, maid and groceries",
        "amount_paise": 3 * DUES, "expense_date": TODAY, "period_rule": "transaction_date",
        "allocation_method": "equal"})

    assert client.get("/api/residents/bob/clearance").json() == {"clearance": None}
    preview = client.get("/api/residents/bob/clearance/preview")
    assert preview.status_code == 200
    assert preview.json()["operating_net_balance_paise"] == -DUES
    assert client.get("/api/residents/nobody/clearance/preview").status_code == 404

    request = {"move_out_date": TODAY, "settlement_date": TODAY,
               "deductions_paise": 500_000, "deduction_reason": "Painting",
               "settlement_reference": "UTR428919028471"}
    assert client.post("/api/residents/bob/clearance", json=request).status_code == 422, \
        "the settlement path is required and has no default"
    assert client.post("/api/residents/bob/clearance", json={
        **request, "settlement_path": "signatory_refund",
        "deductions_paise": 5000.5}).status_code == 422
    assert client.post("/api/residents/bob/clearance", json={
        **request, "settlement_path": "signatory_refund",
        "deduction_reason": None}).status_code == 422

    done = client.post("/api/residents/bob/clearance", json={
        **request, "settlement_path": "signatory_refund"})
    assert done.status_code == 200
    slip = done.json()
    assert (slip["net_settlement_paise"], slip["settlement_direction"]) == (
        4_260_000, "refund_to_resident")
    assert client.get("/api/residents/bob/clearance").json()["clearance"] == slip
    assert client.post("/api/residents/bob/clearance", json={
        **request, "settlement_path": "signatory_refund"}).status_code == 409

    bob = client.get("/api/residents/bob").json()
    assert bob["resident"]["is_active"] == 0
    assert bob["account"]["net_position_paise"] == 0

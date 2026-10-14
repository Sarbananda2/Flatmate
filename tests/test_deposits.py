"""Household security deposit slots and the sub-escrow between flatmates.

These tests pin the rules that keep deposit money honest: the funding mode is
always chosen and never defaulted, the slots' targets add up to exactly the
deposit on the lease, a payment never exceeds what a slot is owed, and when an
occupant leaves, what they are handed plus what is kept back is exactly what
they put in. They also pin that slots are permanent -- people come and go, the
slot stays -- and that nothing here ever reaches the operating ledger.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from aem.errors import ConflictError, NotFoundError, ValidationError
from aem.services import deposits, reconciliation, rent, residents

from .conftest import APARTMENT
from .support import DatabaseRefusal, connect

MASTER = 15_000_000   # ₹1,50,000 held by the landlord
SHARE = 5_000_000     # ₹50,000 a slot between three
TODAY = date.today().isoformat()
TOMORROW = (date.today() + timedelta(days=1)).isoformat()

LEASE = {"landlord_name": "Kumar Yadav", "monthly_rent_paise": 2_850_000,
         "security_deposit_paise": MASTER}


def _rows(db, sql, params=()):
    with connect(db, read_only=True) as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _summary(db):
    return deposits.deposit_summary(db, APARTMENT)


def _slot(summary, key):
    """A slot by number, or by its occupant's resident ID."""
    field = "slot_number" if isinstance(key, int) else "assigned_resident_id"
    matches = [slot for slot in summary["slots"] if slot[field] == key]
    assert len(matches) == 1, (key, matches)
    return matches[0]


def _proposed(summary):
    return [{key: slot[key] for key in ("slot_number", "slot_label", "target_deposit_paise",
                                        "assigned_resident_id")}
            for slot in summary["proposal"]]


def _fund(db, key, amount=SHARE, **extra):
    return deposits.record_slot_funding(
        db, _slot(_summary(db), key)["slot_id"], apartment_id=APARTMENT,
        amount_paise=amount, paid_date=extra.pop("paid_date", TODAY),
        payment_method=extra.pop("payment_method", "upi"), **extra)


def _vacate(db, key, deductions, refund, path="signatory_refund", **extra):
    return deposits.vacate_and_settle_slot(
        db, _slot(_summary(db), key)["slot_id"], apartment_id=APARTMENT,
        settlement_path=path, deductions_paise=deductions, refund_amount_paise=refund,
        refund_date=extra.pop("refund_date", TODAY), **extra)


def _assign(db, number, resident_id, **overrides):
    slot = _slot(_summary(db), number)
    return deposits.initialize_or_update_slots(db, APARTMENT, [{
        "slot_number": number, "slot_label": slot["slot_label"],
        "target_deposit_paise": slot["target_deposit_paise"],
        "assigned_resident_id": resident_id, **overrides}])


@pytest.fixture()
def leased(apartment):
    """Three residents and a lease with a ₹1,50,000 deposit; no funding mode yet."""
    rent.save_lease_config(apartment, APARTMENT, dict(LEASE))
    return apartment


@pytest.fixture()
def signatory(leased):
    """Alice fronted the deposit; three ₹50,000 slots, one per resident."""
    summary = deposits.configure_mode(leased, APARTMENT, "primary_signatory", "alice")
    deposits.initialize_or_update_slots(leased, APARTMENT, _proposed(summary))
    return leased


@pytest.fixture()
def co_tenants(leased):
    """Everyone pays the landlord directly; three ₹50,000 slots."""
    summary = deposits.configure_mode(leased, APARTMENT, "direct_co_tenants")
    deposits.initialize_or_update_slots(leased, APARTMENT, _proposed(summary))
    return leased


@pytest.fixture()
def with_dana(signatory):
    """Bob has funded slot 2 and is leaving; Dana is moving in."""
    residents.create_resident(signatory, apartment_id=APARTMENT, resident_id="dana",
                              display_name="Dana")
    _fund(signatory, "bob")
    return signatory


# ------------------------------------------------------------- the migration --

def test_migration_0008_adds_the_mode_columns_and_the_slot_tables(leased):
    lease = _rows(leased, "SELECT * FROM rent_lease_config")[0]
    # Invariant B0: nothing is chosen for the operator.
    assert lease["deposit_funding_mode"] is None
    assert lease["deposit_primary_signatory_resident_id"] is None
    assert _rows(leased, "SELECT 1 FROM household_deposit_slot") == []
    assert _rows(leased, "SELECT 1 FROM household_deposit_transaction") == []


def test_the_database_itself_refuses_impossible_slots(signatory):
    slot = _slot(_summary(signatory), "bob")
    for column, value in (("target_deposit_paise", 0), ("funded_amount_paise", -1),
                          ("funded_amount_paise", SHARE + 1), ("slot_number", 0),
                          ("slot_number", 1),                # another slot has that number
                          ("slot_label", "  "), ("status", "let"), ("status", "vacant"),
                          ("payment_method", "cheque"), ("funded_date", "12-10-2026"),
                          ("assigned_resident_id", "alice"),  # she already has a slot
                          ("assigned_resident_id", None)):    # occupied status, no occupant
        with connect(signatory) as conn, pytest.raises(DatabaseRefusal):
            conn.execute(
                f"UPDATE household_deposit_slot SET {column}=%s WHERE slot_id=%s",
                (value, slot["slot_id"]))
    with connect(signatory) as conn, pytest.raises(DatabaseRefusal):
        conn.execute("UPDATE rent_lease_config SET deposit_funding_mode='shared'")


def test_deposit_transactions_can_only_be_added(with_dana):
    with connect(with_dana) as conn, pytest.raises(DatabaseRefusal):
        conn.execute("UPDATE household_deposit_transaction SET amount_paise=1")
    with connect(with_dana) as conn, pytest.raises(DatabaseRefusal):
        conn.execute("DELETE FROM household_deposit_transaction")
    assert len(_rows(with_dana, "SELECT 1 FROM household_deposit_transaction")) == 1


# -------------------------------------------------------- the funding mode --

def test_nothing_is_recorded_before_a_lease_or_a_mode(apartment):
    empty = _summary(apartment)
    assert empty["is_lease_configured"] is False and empty["funding_mode"] is None
    with pytest.raises(ConflictError) as no_lease:
        deposits.configure_mode(apartment, APARTMENT, "direct_co_tenants")
    assert no_lease.value.code == "rent_not_configured"

    rent.save_lease_config(apartment, APARTMENT, dict(LEASE))
    with pytest.raises(ConflictError) as no_mode:
        deposits.initialize_or_update_slots(apartment, APARTMENT, _proposed(_summary(apartment)))
    assert no_mode.value.code == "deposit_mode_not_set"


@pytest.mark.parametrize("mode, signatory_id", [
    (None, None), ("", None), ("landlord", None),
    ("primary_signatory", None),          # the signatory must be named
    ("primary_signatory", "  "),
    ("direct_co_tenants", "alice"),       # and must not be, when there is none
])
def test_the_mode_must_be_chosen_explicitly_and_consistently(leased, mode, signatory_id):
    with pytest.raises(ValidationError):
        deposits.configure_mode(leased, APARTMENT, mode, signatory_id)
    assert _summary(leased)["funding_mode"] is None


def test_the_primary_signatory_must_be_a_current_resident_of_the_apartment(leased):
    with pytest.raises(NotFoundError):
        deposits.configure_mode(leased, APARTMENT, "primary_signatory", "nobody")
    residents.deactivate_resident(leased, "chris", apartment_id=APARTMENT)
    with pytest.raises(ValidationError):
        deposits.configure_mode(leased, APARTMENT, "primary_signatory", "chris")

    summary = deposits.configure_mode(leased, APARTMENT, "primary_signatory", "alice")
    assert (summary["funding_mode"], summary["primary_signatory_id"],
            summary["primary_signatory_name"]) == ("primary_signatory", "alice", "Alice")


def test_the_mode_can_change_until_slots_exist_and_not_after(leased):
    deposits.configure_mode(leased, APARTMENT, "primary_signatory", "alice")
    changed = deposits.configure_mode(leased, APARTMENT, "direct_co_tenants")
    assert (changed["funding_mode"], changed["primary_signatory_id"]) == (
        "direct_co_tenants", None)

    deposits.initialize_or_update_slots(leased, APARTMENT, _proposed(changed))
    with pytest.raises(ConflictError) as locked:
        deposits.configure_mode(leased, APARTMENT, "primary_signatory", "alice")
    assert locked.value.code == "deposit_mode_locked"
    # Saying the same thing again is not a change.
    deposits.configure_mode(leased, APARTMENT, "direct_co_tenants")


# ------------------------------------------------------------------ the slots --

def test_the_proposal_is_one_slot_per_resident_split_equally_to_the_paisa(apartment):
    rent.save_lease_config(apartment, APARTMENT, {**LEASE, "security_deposit_paise": 10_000_001})
    proposal = deposits.configure_mode(apartment, APARTMENT, "direct_co_tenants")["proposal"]
    assert [(slot["slot_number"], slot["slot_label"], slot["assigned_resident_id"],
             slot["target_deposit_paise"]) for slot in proposal] == [
        (1, "Room 1", "alice", 3_333_334), (2, "Room 2", "bob", 3_333_334),
        (3, "Room 3", "chris", 3_333_333)]
    # Invariant B2, to the paisa, in integers.
    assert sum(slot["target_deposit_paise"] for slot in proposal) == 10_000_001
    assert all(isinstance(slot["target_deposit_paise"], int) for slot in proposal)


def test_the_signatorys_own_slot_counts_as_funded_and_the_rest_are_owed(signatory):
    summary = _summary(signatory)
    alice, bob = _slot(summary, "alice"), _slot(summary, "bob")
    assert (alice["status"], alice["funded_amount_paise"], alice["payment_method"]) == (
        "occupied_funded", SHARE, "direct_to_landlord")
    assert alice["is_primary_signatory"] is True
    assert (bob["status"], bob["funded_amount_paise"], bob["outstanding_paise"]) == (
        "occupied_pending", 0, SHARE)
    assert bob["assigned_since"] == TODAY

    assert (summary["total_slots_count"], summary["vacant_slots_count"]) == (3, 0)
    assert summary["total_target_paise"] == MASTER and summary["is_balanced"] is True
    assert summary["total_funded_paise"] == SHARE
    assert summary["unfunded_gap_paise"] == 2 * SHARE
    assert summary["proposal"] == [] and summary["unassigned_residents"] == []
    # Nothing reimbursed yet: the whole deposit is Alice's money.
    assert summary["reimbursements_received_paise"] == 0
    assert summary["signatory_capital_exposure_paise"] == MASTER


def test_under_direct_co_tenants_every_slot_starts_owing(co_tenants):
    summary = _summary(co_tenants)
    assert {slot["status"] for slot in summary["slots"]} == {"occupied_pending"}
    assert summary["total_funded_paise"] == 0
    assert summary["signatory_capital_exposure_paise"] is None


def test_targets_can_be_unequal_but_must_total_the_deposit_exactly(leased):
    deposits.configure_mode(leased, APARTMENT, "direct_co_tenants")
    custom = [
        {"slot_number": 1, "slot_label": "Master bedroom", "target_deposit_paise": 7_000_000,
         "assigned_resident_id": "alice"},
        {"slot_number": 2, "slot_label": "Room 2", "target_deposit_paise": 5_000_000,
         "assigned_resident_id": "bob"},
        {"slot_number": 3, "slot_label": "Study", "target_deposit_paise": 3_000_000,
         "assigned_resident_id": None},
    ]
    for wrong in (2_999_999, 3_000_001):               # a paisa under, a paisa over
        with pytest.raises(ValidationError) as unbalanced:
            deposits.initialize_or_update_slots(
                leased, APARTMENT, custom[:2] + [{**custom[2], "target_deposit_paise": wrong}])
        assert unbalanced.value.details["difference_paise"] == wrong - 3_000_000
    assert _summary(leased)["slots"] == [], "a refused set-up leaves nothing behind"

    summary = deposits.initialize_or_update_slots(leased, APARTMENT, custom)
    assert [(slot["slot_label"], slot["target_deposit_paise"], slot["status"])
            for slot in summary["slots"]] == [
        ("Master bedroom", 7_000_000, "occupied_pending"),
        ("Room 2", 5_000_000, "occupied_pending"), ("Study", 3_000_000, "vacant")]
    # A vacant slot is an unfunded gap the household can see.
    assert (summary["vacant_slots_count"], summary["unfunded_gap_paise"]) == (1, MASTER)
    assert summary["unassigned_residents"] == [{"resident_id": "chris", "display_name": "Chris"}]

    # Moving money between two slots keeps the total; changing one alone does not.
    with pytest.raises(ValidationError):
        deposits.initialize_or_update_slots(leased, APARTMENT, [
            {**custom[0], "target_deposit_paise": 6_000_000}])
    moved = deposits.initialize_or_update_slots(leased, APARTMENT, [
        {**custom[0], "target_deposit_paise": 6_000_000},
        {**custom[2], "target_deposit_paise": 4_000_000, "slot_label": "Room 3"}])
    assert moved["total_target_paise"] == MASTER and moved["total_slots_count"] == 3
    assert _slot(moved, 3)["slot_label"] == "Room 3"


@pytest.mark.parametrize("slot", [
    {"slot_number": 0}, {"slot_number": -1}, {"slot_number": 1.5}, {"slot_number": True},
    {"slot_label": ""}, {"slot_label": "   "}, {"slot_label": None},
    {"target_deposit_paise": 0}, {"target_deposit_paise": -5},
    {"target_deposit_paise": 50000.5}, {"target_deposit_paise": True},
])
def test_an_invalid_slot_is_refused_and_nothing_is_saved(leased, slot):
    deposits.configure_mode(leased, APARTMENT, "direct_co_tenants")
    proposed = _proposed(_summary(leased))
    with pytest.raises(ValidationError):
        deposits.initialize_or_update_slots(
            leased, APARTMENT, [{**proposed[0], **slot}, *proposed[1:]])
    assert _summary(leased)["slots"] == []


def test_slots_need_distinct_numbers_distinct_current_occupants_and_a_deposit(apartment):
    rent.save_lease_config(apartment, APARTMENT,
                           {"landlord_name": "Kumar Yadav", "monthly_rent_paise": 2_850_000})
    deposits.configure_mode(apartment, APARTMENT, "direct_co_tenants")
    one = {"slot_number": 1, "slot_label": "Room 1", "target_deposit_paise": MASTER,
           "assigned_resident_id": "alice"}
    with pytest.raises(ConflictError) as no_amount:
        deposits.initialize_or_update_slots(apartment, APARTMENT, [one])
    assert no_amount.value.code == "deposit_amount_not_set"

    rent.save_lease_config(apartment, APARTMENT, dict(LEASE))
    proposed = _proposed(_summary(apartment))
    with pytest.raises(ValidationError):                       # the same number twice
        deposits.initialize_or_update_slots(
            apartment, APARTMENT, [proposed[0], {**proposed[1], "slot_number": 1}, proposed[2]])
    with pytest.raises(ValidationError):                       # the same person twice
        deposits.initialize_or_update_slots(
            apartment, APARTMENT,
            [proposed[0], {**proposed[1], "assigned_resident_id": "alice"}, proposed[2]])
    with pytest.raises(NotFoundError):
        deposits.initialize_or_update_slots(
            apartment, APARTMENT,
            [proposed[0], proposed[1], {**proposed[2], "assigned_resident_id": "nobody"}])
    with pytest.raises(ValidationError):
        deposits.initialize_or_update_slots(apartment, APARTMENT, [])
    residents.deactivate_resident(apartment, "chris", apartment_id=APARTMENT)
    with pytest.raises(ValidationError):                       # moved out
        deposits.initialize_or_update_slots(apartment, APARTMENT, proposed)
    assert _summary(apartment)["slots"] == []


# ------------------------------------------------------------------- funding --

def test_reimbursements_reduce_the_signatorys_capital_at_risk(signatory):
    after_bob = _fund(signatory, "bob", payment_reference="UTR428919028471")
    bob = _slot(after_bob, "bob")
    assert (bob["status"], bob["funded_amount_paise"], bob["funded_date"]) == (
        "occupied_funded", SHARE, TODAY)
    # Under primary_signatory the money goes to the signatory.
    assert (bob["paid_to_resident_id"], bob["paid_to_display_name"]) == ("alice", "Alice")
    assert bob["payment_reference"] == "UTR428919028471"
    assert after_bob["signatory_capital_exposure_paise"] == MASTER - SHARE
    assert [(tx["tx_type"], tx["amount_paise"], tx["resident_display_name"],
             tx["counterparty_display_name"]) for tx in bob["transactions"]] == [
        ("funding_payment", SHARE, "Bob", "Alice")]

    after_chris = _fund(signatory, "chris")
    # The spec's worked example: ₹1,50,000 fronted, ₹1,00,000 back, ₹50,000 at risk.
    assert after_chris["reimbursements_received_paise"] == 2 * SHARE
    assert after_chris["signatory_capital_exposure_paise"] == SHARE
    assert after_chris["unfunded_gap_paise"] == 0


def test_part_payments_add_up_and_cannot_overfund(co_tenants):
    part = _fund(co_tenants, "bob", 2_000_000, payment_method="direct_to_landlord")
    bob = _slot(part, "bob")
    assert (bob["status"], bob["funded_amount_paise"], bob["outstanding_paise"]) == (
        "occupied_pending", 2_000_000, 3_000_000)
    assert bob["paid_to_resident_id"] is None

    with pytest.raises(ValidationError):
        _fund(co_tenants, "bob", 3_000_001)
    full = _fund(co_tenants, "bob", 3_000_000, payment_method="cash")
    assert (_slot(full, "bob")["status"], _slot(full, "bob")["funded_amount_paise"]) == (
        "occupied_funded", SHARE)
    assert len(_slot(full, "bob")["transactions"]) == 2
    with pytest.raises(ConflictError) as done:
        _fund(co_tenants, "bob", 1)
    assert done.value.code == "deposit_slot_fully_funded"


@pytest.mark.parametrize("fields", [
    {"amount": 0}, {"amount": -100}, {"amount": 5000.5}, {"amount": True},
    {"paid_date": TOMORROW}, {"paid_date": "12-10-2026"}, {"paid_date": None},
    {"payment_method": "cheque"}, {"payment_method": None},
    # The signatory already paid the landlord, and is who gets reimbursed.
    {"payment_method": "direct_to_landlord"}, {"paid_to_resident_id": "chris"},
])
def test_an_invalid_payment_is_refused_and_nothing_changes(signatory, fields):
    with pytest.raises(ValidationError):
        _fund(signatory, "bob", fields.pop("amount", SHARE), **fields)
    assert _slot(_summary(signatory), "bob")["funded_amount_paise"] == 0
    assert _rows(signatory, "SELECT 1 FROM household_deposit_transaction") == []


def test_only_an_occupied_slot_of_this_apartment_can_be_funded(leased):
    summary = deposits.configure_mode(leased, APARTMENT, "direct_co_tenants")
    proposed = _proposed(summary)
    deposits.initialize_or_update_slots(
        leased, APARTMENT, proposed[:2] + [{**proposed[2], "assigned_resident_id": None}])
    with pytest.raises(ConflictError) as vacant:
        _fund(leased, 3)
    assert vacant.value.code == "deposit_slot_vacant"
    with pytest.raises(NotFoundError):
        deposits.record_slot_funding(
            leased, "depslot_missing", apartment_id=APARTMENT, amount_paise=SHARE,
            paid_date=TODAY, payment_method="upi")


# ------------------------------------------------------ vacating and settling --

def test_a_refund_and_its_deductions_are_exactly_what_was_put_in(with_dana):
    for deductions, refund in ((500_000, 4_600_000), (500_000, 4_400_000), (0, SHARE - 1)):
        with pytest.raises(ValidationError):
            _vacate(with_dana, "bob", deductions, refund, deduction_reason="Repainting")
    with pytest.raises(ValidationError):                       # a reason is required
        _vacate(with_dana, "bob", 500_000, 4_500_000)
    with pytest.raises(ValidationError):                       # more than was put in
        _vacate(with_dana, "bob", SHARE + 1, 0, deduction_reason="Everything")
    assert _slot(_summary(with_dana), "bob")["status"] == "occupied_funded"

    settled = _vacate(with_dana, "bob", 500_000, 4_500_000,
                      deduction_reason="Repainting the bedroom",
                      refund_reference="UTR900100200")
    slot = _slot(settled, 2)
    # Path A: the slot survives its occupant, and now stands vacant and unfunded.
    assert (slot["status"], slot["assigned_resident_id"], slot["funded_amount_paise"]) == (
        "vacant", None, 0)
    assert (slot["slot_label"], slot["target_deposit_paise"]) == ("Room 2", SHARE)
    assert [(tx["tx_type"], tx["amount_paise"], tx["resident_id"],
             tx["counterparty_resident_id"]) for tx in slot["transactions"]] == [
        ("funding_payment", SHARE, "bob", "alice"),
        ("move_out_deduction", 500_000, "bob", None),
        ("move_out_refund", 4_500_000, "bob", "alice")]      # refunded by the signatory
    assert slot["transactions"][1]["notes"] == "Repainting the bedroom"

    # Alice gave back ₹45,000 of the ₹50,000 she had from Bob and kept ₹5,000.
    assert settled["retained_deductions_paise"] == 500_000
    assert settled["signatory_capital_exposure_paise"] == MASTER - 500_000
    assert (settled["vacant_slots_count"], settled["total_slots_count"]) == (1, 3)
    assert settled["unfunded_gap_paise"] == 2 * SHARE          # Chris's slot, and the gap
    with pytest.raises(ConflictError) as again:
        deposits.vacate_and_settle_slot(
            with_dana, slot["slot_id"], apartment_id=APARTMENT,
            settlement_path="signatory_refund", deductions_paise=0,
            refund_amount_paise=0, refund_date=TODAY)
    assert again.value.code == "deposit_slot_vacant"


def test_a_vacant_slot_is_filled_by_assigning_the_next_occupant(with_dana):
    _vacate(with_dana, "bob", 0, SHARE)
    assert _summary(with_dana)["unassigned_residents"] == [
        {"resident_id": "bob", "display_name": "Bob"},
        {"resident_id": "dana", "display_name": "Dana"}]

    assigned = _assign(with_dana, 2, "dana")
    dana = _slot(assigned, "dana")
    assert (dana["slot_number"], dana["status"], dana["outstanding_paise"]) == (
        2, "occupied_pending", SHARE)
    assert assigned["total_slots_count"] == 3, "the same slot, refilled"
    funded = _fund(with_dana, "dana")
    # Dana's payment replaces Bob's: Alice is back to one slot reimbursed.
    assert funded["signatory_capital_exposure_paise"] == MASTER - SHARE


def test_a_direct_transfer_hands_the_slot_to_whoever_pays_the_leaver(with_dana):
    before = _summary(with_dana)
    after = _vacate(with_dana, "bob", 0, SHARE, "direct_transfer",
                    incoming_resident_id="dana", refund_reference="UPI-DANA-BOB")
    slot = _slot(after, 2)
    # Path B: no vacancy in between.
    assert (slot["assigned_resident_id"], slot["assigned_display_name"], slot["status"]) == (
        "dana", "Dana", "occupied_funded")
    assert (slot["funded_amount_paise"], slot["paid_to_resident_id"]) == (SHARE, "bob")
    assert slot["transactions"][-1]["tx_type"] == "direct_replacement_transfer"
    assert (slot["transactions"][-1]["resident_id"],
            slot["transactions"][-1]["counterparty_resident_id"]) == ("bob", "dana")

    # One slot changed hands: no capital appeared or disappeared.
    for total in ("total_target_paise", "total_funded_paise", "vacant_slots_count",
                  "signatory_capital_exposure_paise", "unfunded_gap_paise"):
        assert after[total] == before[total], total


def test_deductions_on_a_transfer_leave_the_newcomer_owing_the_difference(with_dana):
    before = _summary(with_dana)
    with pytest.raises(ValidationError):                       # must add up
        _vacate(with_dana, "bob", 500_000, SHARE, "direct_transfer",
                incoming_resident_id="dana", deduction_reason="x")
    after = _vacate(with_dana, "bob", 500_000, 4_500_000, "direct_transfer",
                    incoming_resident_id="dana", deduction_reason="Broken wardrobe door")
    dana = _slot(after, "dana")
    # Dana paid ₹45,000 into a ₹50,000 slot, so ₹5,000 is still owed on it.
    assert (dana["status"], dana["funded_amount_paise"], dana["outstanding_paise"]) == (
        "occupied_pending", 4_500_000, 500_000)
    # Alice still holds all ₹50,000 Bob paid her: ₹45,000 as Dana's, ₹5,000 kept back.
    assert after["signatory_capital_exposure_paise"] == before["signatory_capital_exposure_paise"]
    assert _slot(_fund(with_dana, "dana", 500_000), "dana")["status"] == "occupied_funded"


@pytest.mark.parametrize("path, extra", [
    ("landlord", {}), (None, {}),
    ("direct_transfer", {}),                                   # nobody named
    ("direct_transfer", {"incoming_resident_id": "bob"}),      # the leaver themselves
    ("signatory_refund", {"incoming_resident_id": "dana"}),    # named, but not a transfer
    ("signatory_refund", {"refund_date": TOMORROW}),
    ("signatory_refund", {"refund_date": "12-10-2026"}),
])
def test_an_invalid_settlement_is_refused_and_the_slot_is_untouched(with_dana, path, extra):
    with pytest.raises(ValidationError):
        _vacate(with_dana, "bob", 0, SHARE, path, **extra)
    assert _slot(_summary(with_dana), "bob")["status"] == "occupied_funded"
    assert len(_rows(with_dana, "SELECT 1 FROM household_deposit_transaction")) == 1


def test_a_transfer_needs_a_current_resident_with_no_slot_and_something_to_hand_over(with_dana):
    with pytest.raises(ConflictError) as held:
        _vacate(with_dana, "bob", 0, SHARE, "direct_transfer", incoming_resident_id="chris")
    assert held.value.code == "deposit_slot_already_held"
    with pytest.raises(NotFoundError):
        _vacate(with_dana, "bob", 0, SHARE, "direct_transfer", incoming_resident_id="nobody")
    with pytest.raises(ValidationError):                       # all of it kept back
        _vacate(with_dana, "bob", SHARE, 0, "direct_transfer",
                incoming_resident_id="dana", deduction_reason="Everything")
    residents.deactivate_resident(with_dana, "dana", apartment_id=APARTMENT)
    with pytest.raises(ValidationError):
        _vacate(with_dana, "bob", 0, SHARE, "direct_transfer", incoming_resident_id="dana")
    with pytest.raises(ConflictError) as fronted:
        _vacate(with_dana, "alice", 0, SHARE)
    assert fronted.value.code == "deposit_signatory_slot"
    assert _slot(_summary(with_dana), "bob")["status"] == "occupied_funded"


def test_an_occupant_who_paid_nothing_just_leaves_the_slot(signatory):
    left = _vacate(signatory, "chris", 0, 0)
    assert _slot(left, 3)["status"] == "vacant"
    assert [tx["amount_paise"] for tx in _slot(left, 3)["transactions"]] == [0]


def test_a_funded_slot_must_be_settled_before_it_is_reassigned(with_dana):
    with pytest.raises(ConflictError) as funded:
        _assign(with_dana, 2, "dana")
    assert funded.value.code == "deposit_slot_funded"
    with pytest.raises(ConflictError):
        _assign(with_dana, 2, None)
    with pytest.raises(ValidationError):                       # below what Bob put in
        deposits.initialize_or_update_slots(with_dana, APARTMENT, [
            {"slot_number": 2, "slot_label": "Room 2", "target_deposit_paise": SHARE - 1,
             "assigned_resident_id": "bob"},
            {"slot_number": 3, "slot_label": "Room 3", "target_deposit_paise": SHARE + 1,
             "assigned_resident_id": "chris"}])
    # An unfunded slot's occupant can simply be swapped.
    swapped = _assign(with_dana, 3, "dana")
    assert _slot(swapped, 3)["assigned_resident_id"] == "dana"
    with pytest.raises(ConflictError) as held:
        _assign(with_dana, 3, "bob")                           # Bob still has slot 2
    assert held.value.code == "deposit_slot_already_held"


# ------------------------------------------------- segregation and the trail --

def test_deposits_never_reach_the_operating_ledger(with_dana):
    """Invariant B1: escrow is not operating money."""
    ledger = ("expense_instance", "allocation", "payment", "payment_application",
              "ledger_adjustment", "monthly_statement", "opening_balance")
    _fund(with_dana, "chris", 1_000_000)
    _vacate(with_dana, "bob", 500_000, 4_500_000, "direct_transfer",
            incoming_resident_id="dana", deduction_reason="Wardrobe")
    _vacate(with_dana, "chris", 0, 1_000_000)

    for table in ledger:
        assert _rows(with_dana, f"SELECT COUNT(*) AS n FROM {table}")[0]["n"] == 0, table
    positions = reconciliation.reconcile(
        with_dana, apartment_id=APARTMENT)["net_positions_paise_by_account"]
    assert set(positions.values()) == {0}


def test_every_deposit_action_is_audited(with_dana):
    _vacate(with_dana, "bob", 500_000, 4_500_000, deduction_reason="Wardrobe")
    _assign(with_dana, 2, "dana")
    _fund(with_dana, "dana")

    actions = [row["action"] for row in _rows(
        with_dana, "SELECT action FROM audit_log WHERE entity_type=%s ORDER BY row_seq",
        ("household_deposit_slot",))]
    assert actions[:5] == ["configure_mode", "create", "create", "create", "funding_payment"]
    assert actions[5] == "vacate"
    assert set(actions[6:-1]) == {"update"} and actions[-1] == "funding_payment"


# -------------------------------------------------------------------- the API --

def test_the_deposit_endpoints_run_the_whole_lifecycle(client):
    client.post("/api/apartments", json={"apartment_id": APARTMENT, "name": "Flat"})
    for resident_id in ("alice", "bob", "chris"):
        client.post("/api/residents", json={"resident_id": resident_id,
                                            "display_name": resident_id.title()})
    assert client.get("/api/rent/deposits").json()["is_lease_configured"] is False
    client.post("/api/rent/config", json=LEASE)

    # The mode is required, and has no default.
    assert client.post("/api/rent/deposits/config", json={}).status_code == 422
    assert client.post("/api/rent/deposits/config", json={
        "funding_mode": "landlord"}).status_code == 422
    configured = client.post("/api/rent/deposits/config", json={
        "funding_mode": "primary_signatory", "primary_signatory_resident_id": "alice"})
    assert configured.status_code == 200
    proposal = configured.json()["proposal"]
    assert [slot["target_deposit_paise"] for slot in proposal] == [SHARE, SHARE, SHARE]

    slots = _proposed(configured.json())
    assert client.post("/api/rent/deposits/slots", json={"slots": [
        {**slots[0], "target_deposit_paise": 50000.5}]}).status_code == 422
    unbalanced = client.post("/api/rent/deposits/slots", json={"slots": slots[:2]})
    assert unbalanced.status_code == 422
    assert unbalanced.json()["error"]["details"]["difference_paise"] == -SHARE
    created = client.post("/api/rent/deposits/slots", json={"slots": slots})
    assert created.status_code == 200
    slot_2 = _slot(created.json(), 2)["slot_id"]

    funded = client.post(f"/api/rent/deposits/slots/{slot_2}/fund", json={
        "amount_paise": SHARE, "paid_date": TODAY, "payment_method": "upi",
        "payment_reference": "UTR1"})
    assert funded.status_code == 200
    assert funded.json()["signatory_capital_exposure_paise"] == MASTER - SHARE
    assert client.post(f"/api/rent/deposits/slots/{slot_2}/fund", json={
        "amount_paise": 1, "paid_date": TODAY, "payment_method": "upi"}).status_code == 409
    assert client.post("/api/rent/deposits/slots/depslot_nope/fund", json={
        "amount_paise": 1, "paid_date": TODAY, "payment_method": "upi"}).status_code == 404

    off_by_one = client.post(f"/api/rent/deposits/slots/{slot_2}/vacate", json={
        "settlement_path": "signatory_refund", "deductions_paise": 0,
        "refund_amount_paise": SHARE - 1, "refund_date": TODAY})
    assert off_by_one.status_code == 422
    assert off_by_one.json()["error"]["details"]["expected_refund_paise"] == SHARE
    assert client.post(f"/api/rent/deposits/slots/{slot_2}/vacate", json={
        "deductions_paise": 0, "refund_amount_paise": SHARE,
        "refund_date": TODAY}).status_code == 422, "the path is required"

    client.post("/api/residents", json={"resident_id": "dana", "display_name": "Dana"})
    transferred = client.post(f"/api/rent/deposits/slots/{slot_2}/vacate", json={
        "settlement_path": "direct_transfer", "incoming_resident_id": "dana",
        "deductions_paise": 500_000, "deduction_reason": "Wardrobe",
        "refund_amount_paise": 4_500_000, "refund_date": TODAY,
        "refund_reference": "UPI-DANA-BOB"})
    assert transferred.status_code == 200
    body = transferred.json()
    assert _slot(body, "dana")["outstanding_paise"] == 500_000
    assert body == client.get("/api/rent/deposits").json()

    # The lease reports the mode too, and saving the lease again leaves it alone.
    lease = client.post("/api/rent/config", json=LEASE).json()
    assert (lease["deposit_funding_mode"], lease["deposit_primary_signatory_resident_id"]) == (
        "primary_signatory", "alice")


# ------------------------------------------------------- the rooms slots back --

def _room(db, number, **fields):
    from aem.services import rooms
    return rooms.create_room(db, APARTMENT, {"room_number": number, **fields})


def test_a_slot_shows_the_room_it_backs(signatory):
    room = _room(signatory, "Room 1", has_attached_washroom=True, base_rent_paise=1_200_000)
    slot = _slot(_summary(signatory), 1)
    assert (slot["room_number"], slot["base_rent_paise"]) == (None, None)
    summary = deposits.link_slot_room(signatory, APARTMENT, slot["slot_id"], room["room_id"])
    linked = _slot(summary, 1)
    assert (linked["room_id"], linked["room_number"], linked["has_attached_washroom"],
            linked["has_balcony"], linked["base_rent_paise"]) == (
        room["room_id"], "Room 1", True, False, 1_200_000)
    entry = _rows(signatory, "SELECT * FROM audit_log WHERE action='link_room'")[0]
    assert entry["entity_id"] == slot["slot_id"]


def test_a_slot_can_be_unlinked(signatory):
    room = _room(signatory, "Room 1")
    slot_id = _slot(_summary(signatory), 1)["slot_id"]
    deposits.link_slot_room(signatory, APARTMENT, slot_id, room["room_id"])
    summary = deposits.link_slot_room(signatory, APARTMENT, slot_id, None)
    assert _slot(summary, 1)["room_id"] is None
    assert _rows(signatory, "SELECT action FROM audit_log WHERE entity_id=%s "
                            "ORDER BY row_seq DESC LIMIT 1", (slot_id,))[0]["action"] == \
        "unlink_room"


def test_two_slots_cannot_back_one_room(signatory):
    room = _room(signatory, "Room 1")
    summary = _summary(signatory)
    deposits.link_slot_room(signatory, APARTMENT, _slot(summary, 1)["slot_id"], room["room_id"])
    with pytest.raises(ConflictError) as caught:
        deposits.link_slot_room(signatory, APARTMENT, _slot(summary, 2)["slot_id"],
                                room["room_id"])
    assert caught.value.code == "room_has_slot"
    assert _slot(_summary(signatory), 2)["room_id"] is None


def test_saving_slots_can_set_their_rooms_and_otherwise_leaves_them(signatory):
    room = _room(signatory, "Room 2")
    _assign(signatory, 2, "bob", room_id=room["room_id"])
    assert _slot(_summary(signatory), 2)["room_number"] == "Room 2"
    # A later save that does not mention the room keeps the link.
    _assign(signatory, 2, "bob")
    assert _slot(_summary(signatory), 2)["room_number"] == "Room 2"
    _assign(signatory, 2, "bob", room_id=None)
    assert _slot(_summary(signatory), 2)["room_id"] is None


def test_a_room_from_another_flat_cannot_be_linked(signatory):
    from aem.services import apartments, rooms
    apartments.create_apartment(signatory, apartment_id="other", name="Other")
    other = rooms.create_room(signatory, "other", {"room_number": "Room 1"})
    with pytest.raises(NotFoundError):
        deposits.link_slot_room(signatory, APARTMENT, _slot(_summary(signatory), 1)["slot_id"],
                                other["room_id"])


def test_the_link_room_api(signatory):
    from fastapi.testclient import TestClient
    from aem.config import get_settings
    from aem.main import create_app
    room = _room(signatory, "Room 3")
    slot_id = _slot(_summary(signatory), 3)["slot_id"]
    with TestClient(create_app(get_settings(), database=signatory)) as client:
        linked = client.post(f"/api/rent/deposits/slots/{slot_id}/link-room",
                             json={"room_id": room["room_id"]})
        assert linked.status_code == 200, linked.text
        assert _slot(linked.json(), 3)["room_number"] == "Room 3"
        assert client.post(f"/api/rent/deposits/slots/{slot_id}/link-room",
                           json={"room_id": "nope"}).status_code == 404
        unlinked = client.post(f"/api/rent/deposits/slots/{slot_id}/link-room",
                               json={"room_id": None})
        assert _slot(unlinked.json(), 3)["room_id"] is None

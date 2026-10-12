"""Direct settlements, shared summaries, the hourly close, and the Tapo connector.

Each of these touches the accounts from an unusual angle, so the tests pin the
property that matters for it: a direct settlement is atomic and is never an
expense; a summary only reads; the scheduler closes exactly what has expired;
and a meter sync reports what it could not measure instead of filling it in.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from aem.config import TapoPlug, get_settings
from aem.db import connect
from aem.errors import ConflictError, NotFoundError, ValidationError
from aem.main import _close_expired_periodically
from aem.services import (
    expenses,
    payments,
    reconciliation,
    residents,
    sharing,
    statements,
    tapo_sync,
)

from .conftest import APARTMENT


@pytest.fixture()
def shared_dinner(apartment):
    """Alice pays ₹3,000 split three ways: Bob and Chris each owe her ₹1,000."""
    expenses.create_expense(
        apartment, apartment_id=APARTMENT, expense_id="dinner", description="Dinner",
        amount_paise=300_000, expense_date="2026-10-02", period_rule="transaction_date",
        allocation_method="equal", payer_resident_id="alice")
    return apartment


def _positions(db):
    return reconciliation.reconcile(db, apartment_id=APARTMENT)


def _row_count(db, table):
    with connect(db, read_only=True) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# ------------------------------------------------------------ direct settlement --

def test_a_direct_settlement_clears_the_suggested_transfer(shared_dinner):
    before = _positions(shared_dinner)
    assert {(t["from_account"], t["to_account"], t["amount_paise"])
            for t in before["transfers"]} == {("bob", "alice", 100_000),
                                              ("chris", "alice", 100_000)}

    result = payments.settle_peer_to_peer(
        shared_dinner, apartment_id=APARTMENT, from_resident_id="bob",
        to_resident_id="alice", amount_paise=100_000, payment_date="2026-10-11",
        reference="UPI Ref #12345")

    after = _positions(shared_dinner)
    assert after["net_positions_paise_by_account"] == {
        "alice": 100_000, "bob": 0, "chris": -100_000, "__pool__": 0}
    assert [(t["from_account"], t["to_account"]) for t in after["transfers"]] == [
        ("chris", "alice")]
    assert result["paid"]["payment_kind"] == "to_pool"
    assert result["received"]["payment_kind"] == "refund"


def test_a_direct_settlement_is_never_a_household_expense(shared_dinner):
    spent = _positions(shared_dinner)["totals"]["household_expenses_paise"]
    payments.settle_peer_to_peer(
        shared_dinner, apartment_id=APARTMENT, from_resident_id="bob",
        to_resident_id="alice", amount_paise=100_000, payment_date="2026-10-11")
    after = _positions(shared_dinner)
    assert after["totals"]["household_expenses_paise"] == spent
    assert after["net_positions_sum_paise"] == 0


def test_the_payers_leg_settles_their_oldest_charge(shared_dinner):
    result = payments.settle_peer_to_peer(
        shared_dinner, apartment_id=APARTMENT, from_resident_id="bob",
        to_resident_id="alice", amount_paise=100_000, payment_date="2026-10-11")
    assert sum(a["amount_paise"] for a in result["paid"]["applications"]) == 100_000
    assert result["paid"]["unapplied_credit_paise"] == 0
    assert _positions(shared_dinner)["outstanding_charges_paise_by_resident"]["bob"] == 0


def test_a_settlement_that_fails_half_way_records_nothing(shared_dinner):
    """The second leg collides with an existing payment ID; the first must not survive."""
    payments.record_payment(
        shared_dinner, apartment_id=APARTMENT, payment_id="clash-received",
        payer_resident_id="chris", amount_paise=1, payment_date="2026-10-01")
    count = _row_count(shared_dinner, "payment")
    audit_rows = _row_count(shared_dinner, "audit_log")

    with pytest.raises(ConflictError):
        payments.settle_peer_to_peer(
            shared_dinner, apartment_id=APARTMENT, settlement_id="clash",
            from_resident_id="bob", to_resident_id="alice", amount_paise=100_000,
            payment_date="2026-10-11")

    assert _row_count(shared_dinner, "payment") == count
    assert _row_count(shared_dinner, "audit_log") == audit_rows
    with pytest.raises(NotFoundError):
        payments.read_payment(shared_dinner, "clash-paid")


@pytest.mark.parametrize("changes", [
    {"to_resident_id": "bob"},
    {"amount_paise": 0},
    {"payment_date": "11-10-2026"},
])
def test_an_invalid_settlement_is_refused(shared_dinner, changes):
    request = dict(apartment_id=APARTMENT, from_resident_id="bob", to_resident_id="alice",
                   amount_paise=100_000, payment_date="2026-10-11")
    with pytest.raises(ValidationError):
        payments.settle_peer_to_peer(shared_dinner, **{**request, **changes})
    assert _row_count(shared_dinner, "payment") == 0


def test_settling_with_somebody_outside_the_apartment_is_refused(shared_dinner):
    with pytest.raises(NotFoundError):
        payments.settle_peer_to_peer(
            shared_dinner, apartment_id=APARTMENT, from_resident_id="bob",
            to_resident_id="nobody", amount_paise=100_000, payment_date="2026-10-11")


def test_the_settlement_endpoint_records_both_legs(household_client):
    response = household_client.post("/api/payments/settle-p2p", json={
        "from_resident_id": "bob", "to_resident_id": "alice", "amount_paise": 412_000,
        "payment_date": "2026-10-11", "reference": "UPI Ref #12345"})
    assert response.status_code == 201
    body = response.json()
    for leg in ("paid", "received"):
        payment = household_client.get(f"/api/payments/{body[leg]['payment_id']}").json()
        assert payment["amount_paise"] == 412_000
        assert payment["reference"] == "UPI Ref #12345"

    refused = household_client.post("/api/payments/settle-p2p", json={
        "from_resident_id": "bob", "to_resident_id": "alice", "amount_paise": 41.2,
        "payment_date": "2026-10-11"})
    assert refused.status_code == 422


# ---------------------------------------------------------------------- sharing --

def test_a_upi_id_must_look_like_one(apartment):
    saved = residents.update_resident(apartment, "alice", upi_id="alice@okaxis")
    assert saved["upi_id"] == "alice@okaxis"
    with pytest.raises(ValidationError):
        residents.update_resident(apartment, "alice", upi_id="not a upi id")
    assert residents.update_resident(apartment, "alice", upi_id=None)["upi_id"] is None


def test_a_draft_summary_lists_shares_and_a_payable_link(shared_dinner):
    residents.update_resident(shared_dinner, "alice", upi_id="alice@okaxis")
    statement_id = statements.create_monthly_statement(
        shared_dinner, apartment_id=APARTMENT, settlement_month="2026-10")
    statements.attach_eligible_expenses(shared_dinner, statement_id)

    summary = sharing.whatsapp_summary(shared_dinner, statement_id)

    assert "Flat 402 — October 2026 settlement" in summary["text"]
    assert "Bob pays Alice:* ₹1,000.00" in summary["text"]
    assert "Draft — figures can still change" in summary["text"]
    transfer = next(t for t in summary["transfers"] if t["from_account"] == "bob")
    assert transfer["upi_uri"] == (
        "upi://pay?pa=alice@okaxis&pn=Alice&am=1000.00&cu=INR&tn=October%202026%20Flat%20402")
    assert summary["checksum"] == {
        "net_positions_sum_paise": 0, "balanced": True, "sealed": False,
        "snapshot_hash": None, "hash_matches": None}
    assert [row["charged_paise"] for row in summary["resident_charges"]] == [100_000] * 3


def test_a_transfer_to_someone_without_a_upi_id_has_no_link(shared_dinner):
    statement_id = statements.create_monthly_statement(
        shared_dinner, apartment_id=APARTMENT, settlement_month="2026-10")
    summary = sharing.whatsapp_summary(shared_dinner, statement_id)
    assert all(t["upi_uri"] is None for t in summary["transfers"])
    assert "upi://" not in summary["text"]


def test_a_sealed_summary_quotes_the_stored_snapshot(household_client, household):
    before = _row_count(household, "audit_log")
    response = household_client.get("/api/statements/stmt-2026-09/share/whatsapp")
    assert response.status_code == 200
    body = response.json()
    stored = household_client.get("/api/statements/stmt-2026-09/snapshot").json()

    assert body["checksum"]["sealed"] is True
    assert body["checksum"]["hash_matches"] is True
    assert body["checksum"]["snapshot_hash"] == stored["snapshot_hash"]
    assert stored["snapshot_hash"][:12] in body["text"]
    assert body["share_url"].startswith("https://wa.me/?text=")
    assert _row_count(household, "audit_log") == before, "building a summary writes nothing"


@pytest.fixture()
def sealed_dinner(shared_dinner):
    """The dinner month, sealed: Bob and Chris each owe Alice ₹1,000."""
    residents.update_resident(shared_dinner, "alice", upi_id="alice@okaxis")
    statement_id = statements.create_monthly_statement(
        shared_dinner, apartment_id=APARTMENT, settlement_month="2026-10")
    statements.attach_eligible_expenses(shared_dinner, statement_id)
    statements.finalize_statement(shared_dinner, statement_id=statement_id,
                                  finalized_by="operator")
    return shared_dinner, statement_id


def _status(summary):
    return {t["from_account"]: t["settled"] for t in summary["transfers"]}


def test_a_sealed_summary_marks_every_unpaid_transfer_pending(sealed_dinner):
    db, statement_id = sealed_dinner
    summary = sharing.whatsapp_summary(db, statement_id)
    assert _status(summary) == {"bob": False, "chris": False}
    assert "⏳ [Pending] *Bob pays Alice:* ₹1,000.00" in summary["text"]
    assert "⏳ [Pending] *Chris pays Alice:* ₹1,000.00" in summary["text"]
    assert "[Settled]" not in summary["text"]


def test_a_draft_summary_carries_no_settlement_status(shared_dinner):
    statement_id = statements.create_monthly_statement(
        shared_dinner, apartment_id=APARTMENT, settlement_month="2026-10")
    summary = sharing.whatsapp_summary(shared_dinner, statement_id)
    assert all(t["settled"] is None for t in summary["transfers"])
    assert "[Pending]" not in summary["text"] and "[Settled]" not in summary["text"]


def test_a_settlement_after_sealing_flags_its_transfer_and_freezes_the_rest(sealed_dinner):
    db, statement_id = sealed_dinner
    before = sharing.whatsapp_summary(db, statement_id)

    payments.settle_peer_to_peer(
        db, apartment_id=APARTMENT, from_resident_id="bob", to_resident_id="alice",
        amount_paise=100_000, payment_date="2026-10-12", settlement_id="bob-pays")
    after = sharing.whatsapp_summary(db, statement_id)

    assert _status(after) == {"bob": True, "chris": False}
    assert "✅ [Settled] *Bob pays Alice:* ₹1,000.00" in after["text"]
    assert "⏳ [Pending] *Chris pays Alice:* ₹1,000.00" in after["text"]
    # The sealed figures do not move: same amounts, same shares, same seal.
    assert after["checksum"] == before["checksum"]
    assert after["checksum"]["hash_matches"] is True
    assert after["resident_charges"] == before["resident_charges"]
    assert ([t["amount_paise"] for t in after["transfers"]]
            == [t["amount_paise"] for t in before["transfers"]])
    # Only the transfer still owed keeps its pay link.
    assert after["text"].count("upi://pay") == 1


def test_a_plain_pool_payment_settles_a_transfer_too(sealed_dinner):
    db, statement_id = sealed_dinner
    payments.record_payment(
        db, apartment_id=APARTMENT, payment_id="chris-pays", payer_resident_id="chris",
        amount_paise=100_000, payment_date="2026-10-12")
    assert _status(sharing.whatsapp_summary(db, statement_id)) == {"bob": False, "chris": True}


def test_a_payment_dated_before_the_seal_still_counts_once_recorded(sealed_dinner):
    db, statement_id = sealed_dinner
    payments.record_payment(
        db, apartment_id=APARTMENT, payment_id="chris-backdated", payer_resident_id="chris",
        amount_paise=100_000, payment_date="2026-10-03")
    assert _status(sharing.whatsapp_summary(db, statement_id))["chris"] is True


def test_a_partial_payment_leaves_the_transfer_pending(sealed_dinner):
    db, statement_id = sealed_dinner
    payments.record_payment(
        db, apartment_id=APARTMENT, payment_id="bob-part-1", payer_resident_id="bob",
        amount_paise=99_999, payment_date="2026-10-12")
    assert _status(sharing.whatsapp_summary(db, statement_id))["bob"] is False

    payments.record_payment(
        db, apartment_id=APARTMENT, payment_id="bob-part-2", payer_resident_id="bob",
        amount_paise=1, payment_date="2026-10-13")
    assert _status(sharing.whatsapp_summary(db, statement_id))["bob"] is True


def test_reversing_a_settlement_reopens_its_transfer(sealed_dinner):
    db, statement_id = sealed_dinner
    payments.settle_peer_to_peer(
        db, apartment_id=APARTMENT, from_resident_id="bob", to_resident_id="alice",
        amount_paise=100_000, payment_date="2026-10-12", settlement_id="bob-pays")
    assert _status(sharing.whatsapp_summary(db, statement_id))["bob"] is True

    for leg in ("bob-pays-paid", "bob-pays-received"):
        payments.reverse_payment(db, leg, reason="recorded by mistake")
    summary = sharing.whatsapp_summary(db, statement_id)
    assert _status(summary) == {"bob": False, "chris": False}
    assert summary["text"].count("upi://pay") == 2


def test_a_payment_already_in_the_snapshot_settles_nothing(shared_dinner):
    """Bob pays half before the seal, so the sealed transfer is the other half."""
    payments.record_payment(
        shared_dinner, apartment_id=APARTMENT, payment_id="bob-early",
        payer_resident_id="bob", amount_paise=50_000, payment_date="2026-10-04")
    statement_id = statements.create_monthly_statement(
        shared_dinner, apartment_id=APARTMENT, settlement_month="2026-10")
    statements.attach_eligible_expenses(shared_dinner, statement_id)
    statements.finalize_statement(shared_dinner, statement_id=statement_id,
                                  finalized_by="operator", require_clean=False)

    summary = sharing.whatsapp_summary(shared_dinner, statement_id)
    bob = next(t for t in summary["transfers"] if t["from_account"] == "bob")
    assert (bob["amount_paise"], bob["settled"]) == (50_000, False)


def test_one_payers_money_is_spent_on_their_transfers_in_order():
    transfers = [
        {"from_account": "bob", "to_account": "alice", "amount_paise": 300},
        {"from_account": "bob", "to_account": "__pool__", "amount_paise": 200},
        {"from_account": "__pool__", "to_account": "chris", "amount_paise": 100},
    ]
    assert sharing._settled_flags(transfers, {("bob", "to_pool"): 499}) == [True, False, False]
    assert sharing._settled_flags(
        transfers, {("bob", "to_pool"): 500, ("chris", "refund"): 100}) == [True, True, True]
    # A pool payment by the receiver is not a refund to them.
    assert sharing._settled_flags(transfers, {("chris", "to_pool"): 100})[2] is False


def test_the_sealed_summary_endpoint_reports_live_status(household_client, household):
    before = _row_count(household, "audit_log")
    body = household_client.get("/api/statements/stmt-2026-09/share/whatsapp").json()
    assert body["transfers"], "the fixture leaves September with money still owed"
    assert all(t["settled"] is False for t in body["transfers"])
    assert _row_count(household, "audit_log") == before


def test_a_summary_for_an_unknown_statement_is_not_found(household_client):
    assert household_client.get("/api/statements/nope/share/whatsapp").status_code == 404


# ------------------------------------------------------------- lifecycle worker --

def _finalize(db, month, *, hours_ago):
    statement_id = statements.create_monthly_statement(
        db, apartment_id=APARTMENT, settlement_month=month)
    statements.finalize_statement(
        db, statement_id=statement_id, finalized_by="operator",
        now=datetime.now(timezone.utc) - timedelta(hours=hours_ago), require_clean=False)
    return statement_id


def test_the_scheduler_closes_only_statements_past_their_window(apartment):
    expired = _finalize(apartment, "2026-08", hours_ago=100)
    still_open = _finalize(apartment, "2026-09", hours_ago=1)

    async def run_one_pass():
        task = asyncio.create_task(_close_expired_periodically(apartment, 0.01))
        for _ in range(200):
            await asyncio.sleep(0.01)
            if statements.read_statement(apartment, expired)["status"] == "closed":
                break
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run_one_pass())

    closed = statements.read_statement(apartment, expired)
    assert closed["status"] == "closed"
    assert closed["closed_by"] == "scheduler"
    assert statements.read_statement(apartment, still_open)["status"] == (
        "finalized_correction_window")


def test_the_scheduler_runs_hourly_unless_told_otherwise(monkeypatch):
    assert get_settings().auto_close_interval_minutes == 60
    monkeypatch.setenv("AEM_AUTO_CLOSE_INTERVAL_MINUTES", "0")
    assert get_settings().auto_close_interval_minutes == 0


# ------------------------------------------------------------------- tapo sync --

PLUGS = (TapoPlug("alice", "192.168.1.21", "Room 1 AC"),
         TapoPlug("bob", "192.168.1.22", "Room 2 AC"))


def _settings(**changes):
    return replace(get_settings(), tapo_plugs=PLUGS, **changes)


def _days(first, count, wh):
    return {first + timedelta(days=offset): wh for offset in range(count)}


def test_a_sync_totals_each_plug_in_whole_watt_hours(apartment):
    async def fetch(ip_address, first, last):
        assert (first, last) == (date(2026, 9, 1), date(2026, 9, 30))
        return _days(first, 30, 1_417 if ip_address.endswith("21") else 1_033)

    result = tapo_sync.sync_readings(
        apartment, apartment_id=APARTMENT, settings=_settings(),
        period_start="2026-09-01", period_end="2026-09-30", fetch=fetch)

    assert [(r["resident_id"], r["energy_wh"], r["kwh"]) for r in result["readings"]] == [
        ("alice", 42_510, "42.510"), ("bob", 30_990, "30.990")]
    assert result["total_energy_wh"] == 73_500
    assert result["complete"] is True
    assert result["saved"] is False
    assert _row_count(apartment, "smart_plug_reading") == 0, "a sync records nothing"


def test_a_sync_reports_days_a_plug_did_not_measure(apartment):
    async def fetch(ip_address, first, last):
        return _days(first, 28, 1_000)

    result = tapo_sync.sync_readings(
        apartment, apartment_id=APARTMENT, settings=_settings(),
        period_start="2026-09-01", period_end="2026-09-30", fetch=fetch)

    reading = result["readings"][0]
    assert reading["energy_wh"] == 28_000
    assert reading["missing_days"] == ["2026-09-29", "2026-09-30"]
    assert reading["complete"] is False and result["complete"] is False


def test_an_unreachable_plug_is_reported_beside_the_ones_that_answered(apartment):
    async def fetch(ip_address, first, last):
        if ip_address.endswith("22"):
            raise OSError("connection timed out")
        return _days(first, 30, 1_000)

    result = tapo_sync.sync_readings(
        apartment, apartment_id=APARTMENT, settings=_settings(),
        period_start="2026-09-01", period_end="2026-09-30", fetch=fetch)

    assert [r["resident_id"] for r in result["readings"]] == ["alice"]
    assert result["errors"] == [{"resident_id": "bob", "ip_address": "192.168.1.22",
                                 "device_label": "Room 2 AC",
                                 "message": "connection timed out"}]
    assert result["complete"] is False


@pytest.mark.parametrize("start, end", [
    ("2026-09-30", "2026-09-01"),
    ("2026-01-01", "2026-09-30"),
    ("2026-09-01", date.today().isoformat()),
])
def test_a_sync_refuses_a_period_it_cannot_measure(apartment, start, end):
    async def fetch(ip_address, first, last):  # pragma: no cover - never reached
        raise AssertionError("no plug should be contacted")

    with pytest.raises(ValidationError):
        tapo_sync.sync_readings(apartment, apartment_id=APARTMENT, settings=_settings(),
                                period_start=start, period_end=end, fetch=fetch)


def test_a_plug_mapped_to_an_unknown_resident_is_refused(apartment):
    async def fetch(ip_address, first, last):  # pragma: no cover - never reached
        raise AssertionError("no plug should be contacted")

    settings = replace(get_settings(), tapo_plugs=(TapoPlug("dave", "192.168.1.9", "Plug"),))
    with pytest.raises(ValidationError) as caught:
        tapo_sync.sync_readings(apartment, apartment_id=APARTMENT, settings=settings,
                                period_start="2026-09-01", period_end="2026-09-30",
                                fetch=fetch)
    assert caught.value.code == "tapo_unknown_resident"


def test_the_sync_endpoint_says_when_the_connector_is_not_configured(household_client):
    response = household_client.post("/api/electricity/sync-tapo", json={
        "period_start": "2026-09-01", "period_end": "2026-09-30"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "tapo_not_configured"


def test_credentials_never_appear_in_the_health_report(monkeypatch):
    monkeypatch.setenv("AEM_TAPO_USERNAME", "operator@example.com")
    monkeypatch.setenv("AEM_TAPO_PASSWORD", "hunter2")
    monkeypatch.setenv("AEM_TAPO_PLUGS", "alice=192.168.1.21:Room 1 AC, bob=192.168.1.22, bad")
    settings = get_settings()
    assert settings.tapo_plugs == (TapoPlug("alice", "192.168.1.21", "Room 1 AC"),
                                   TapoPlug("bob", "192.168.1.22", "Tapo plug"))
    described = settings.describe()
    assert described["tapo_configured"] is True and described["tapo_plug_count"] == 2
    assert "hunter2" not in repr(described) and "operator@example.com" not in repr(described)


def test_daily_figures_are_requested_one_quarter_at_a_time():
    assert tapo_sync._quarter_starts(date(2026, 9, 20), date(2026, 10, 19)) == [
        date(2026, 7, 1), date(2026, 10, 1)]
    assert tapo_sync._quarter_starts(date(2026, 12, 15), date(2027, 1, 14)) == [
        date(2026, 10, 1), date(2027, 1, 1)]


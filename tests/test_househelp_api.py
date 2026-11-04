"""House help over HTTP: the workers and their roles, a month's attendance, and paying it.

The arithmetic is pinned in tests/test_househelp.py; these check that each
endpoint reaches its service with what was sent, answers with what it returns,
and refuses malformed input with the usual error envelope.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from aem.config import get_settings
from aem.services import househelp

from .support import connect, sign_in

BASE = "/api/services/househelp"
MONTH = "2026-11"


@pytest.fixture()
def clock(monkeypatch):
    """It is 10 November 2026."""
    monkeypatch.setattr(househelp, "now_timestamp", lambda value=None: "2026-11-10T09:00:00+05:30")
    monkeypatch.setattr(househelp, "_today", lambda: "2026-11-10")


@pytest.fixture()
def api(apartment, clock):
    from fastapi.testclient import TestClient

    from aem.main import create_app

    settings = replace(get_settings(), auto_close_expired_statements=False)
    with TestClient(create_app(settings, database=apartment)) as client:
        sign_in(client)
        yield client


def _rows(db, sql, params=()):
    with connect(db, read_only=True) as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _code(response):
    return response.json()["error"]["code"]


def _ok(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def _shares(month):
    return {row["resident_id"]: row["total_paise"] for row in month["shares"]}


def _set_up(api):
    """Sunita cleans for everyone and cooks for Alice and Bob."""
    sunita = _ok(api.post(f"{BASE}/workers", json={"name": "Sunita"}), 201)["worker_id"]
    cleaning = _ok(api.post(f"{BASE}/roles", json={
        "worker_id": sunita, "role_name": "Cleaning", "monthly_salary_paise": 300_000}), 201)
    cooking = _ok(api.post(f"{BASE}/roles", json={
        "worker_id": sunita, "role_name": "Cooking", "monthly_salary_paise": 450_000,
        "split_method": "opt_in", "participants": ["alice", "bob"]}), 201)
    return sunita, cleaning["role_id"], cooking["role_id"]


def test_workers_and_their_roles_over_http(api):
    assert _ok(api.get(f"{BASE}/workers")) == {"items": [], "unassigned_roles": []}
    worker = _ok(api.post(f"{BASE}/workers", json={
        "name": "Sunita", "phone": "98100 12345", "upi_id": "sunita@okaxis",
        "notes": "Mornings", "allowed_leaves_per_month": 3}), 201)
    assert (worker["name"], worker["upi_id"], worker["is_active"],
            worker["allowed_leaves_per_month"]) == ("Sunita", "sunita@okaxis", True, 3)
    worker_id = worker["worker_id"]

    role = _ok(api.post(f"{BASE}/roles", json={
        "worker_id": worker_id, "role_name": "Cooking", "monthly_salary_paise": 450_000,
        "split_method": "opt_in", "participants": ["bob", "alice"]}), 201)
    assert (role["participant_ids"], "allowed_leaves_per_month" in role) == (
        ["alice", "bob"], False)
    # Leave is the worker's, not the role's.
    assert api.post(f"{BASE}/roles", json={
        "worker_id": worker_id, "role_name": "Ironing", "monthly_salary_paise": 80_000,
        "allowed_leaves_per_month": 3}).status_code == 422
    role_id = role["role_id"]
    changed = _ok(api.patch(f"{BASE}/roles/{role_id}", json={"monthly_salary_paise": 480_000}))
    assert (changed["role_name"], changed["monthly_salary_paise"]) == ("Cooking", 480_000)
    assert _ok(api.put(f"{BASE}/roles/{role_id}/participants",
                       json={"participants": ["chris"]}))["participant_ids"] == ["chris"]
    stranger = api.put(f"{BASE}/roles/{role_id}/participants", json={"participants": ["zed"]})
    assert (stranger.status_code, _code(stranger)) == (422, "househelp_participant_not_resident")

    # Before any month is saved with it, the role can go to another worker.
    ramesh = _ok(api.post(f"{BASE}/workers", json={"name": "Ramesh"}), 201)["worker_id"]
    moved = _ok(api.patch(f"{BASE}/roles/{role_id}", json={"worker_id": ramesh}))
    assert (moved["worker_id"], moved["worker_name"]) == (ramesh, "Ramesh")

    # A role may wait for its helper, and go back to waiting until a month is saved with it.
    waiting = _ok(api.post(f"{BASE}/roles", json={
        "role_name": "Utensils", "monthly_salary_paise": 150_000}), 201)
    assert (waiting["worker_id"], waiting["worker_name"]) == (None, None)
    assert [row["role_name"] for row in _ok(api.get(f"{BASE}/workers"))["unassigned_roles"]] == [
        "Utensils"]
    assert _ok(api.patch(f"{BASE}/roles/{waiting['role_id']}",
                         json={"worker_id": ramesh}))["worker_name"] == "Ramesh"
    assert _ok(api.patch(f"{BASE}/roles/{waiting['role_id']}",
                         json={"worker_id": None}))["worker_id"] is None
    _ok(api.delete(f"{BASE}/roles/{waiting['role_id']}"))

    left = _ok(api.patch(f"{BASE}/workers/{worker_id}", json={
        "is_active": False, "phone": None, "allowed_leaves_per_month": 4}))
    assert (left["is_active"], left["phone"], left["notes"], left["allowed_leaves_per_month"]) == (
        False, None, "Mornings", 4)
    for bad in [-1, 32, None]:
        assert api.patch(f"{BASE}/workers/{worker_id}",
                         json={"allowed_leaves_per_month": bad}).status_code == 422
    active = _ok(api.get(f"{BASE}/workers", params={"include_inactive": "false"}))["items"]
    assert [(row["name"], [r["role_name"] for r in row["roles"]]) for row in active] == [
        ("Ramesh", ["Cooking"])]

    gone = _ok(api.delete(f"{BASE}/roles/{role_id}"))
    assert (gone["workers"][0]["roles"], gone["unassigned_roles"]) == ([], [])

    # Malformed or unknown: the usual envelope.
    assert api.post(f"{BASE}/workers", json={"name": ""}).status_code == 422
    bad_upi = api.post(f"{BASE}/workers", json={"name": "Geeta", "upi_id": "geeta"})
    assert (bad_upi.status_code, _code(bad_upi)) == (422, "validation_error")
    assert api.post(f"{BASE}/workers", json={"name": "Geeta", "age": 40}).status_code == 422
    assert _code(api.patch(f"{BASE}/workers/hhworker_nobody", json={"name": "X"})) == (
        "househelp_worker_not_found")
    assert api.post(f"{BASE}/roles", json={
        "worker_id": ramesh, "role_name": "Ironing", "monthly_salary_paise": 0}).status_code == 422
    assert api.post(f"{BASE}/roles", json={
        "worker_id": ramesh, "role_name": "Ironing", "monthly_salary_paise": 80_000,
        "split_method": "weighted"}).status_code == 422
    deleted = api.put(f"{BASE}/roles/{role_id}/participants", json={"participants": []})
    assert (deleted.status_code, _code(deleted)) == (404, "househelp_role_not_found")


def test_a_month_saved_and_paid_over_http(api, apartment):
    sunita, cleaning, cooking = _set_up(api)

    # Not saved yet: a preview, at full salary.
    preview = _ok(api.get(f"{BASE}/month/{MONTH}"))
    assert (preview["bill_state"], preview["saved"], preview["total_paise"],
            preview["active_worker_count"]) == ("not_billed", False, 750_000, 1)
    assert api.get(f"{BASE}/month/2026-13").status_code == 422

    # Sunita was away five days, two allowed: the cleaning loses three days' pay;
    # for the cooking an agreed nothing, and a Diwali bonus.
    month = _ok(api.post(f"{BASE}/month/{MONTH}/attendance", json={"records": [
        {"role_id": cleaning, "days_absent": 5},
        {"role_id": cooking, "days_absent": 5, "deduction_paise": 0,
         "bonus_or_extra_paise": 30_000, "extra_notes": "Diwali"}]}))
    assert (month["bill_state"], month["total_paise"]) == ("live", 750_000)
    assert _shares(month) == {"alice": 330_000, "bob": 330_000, "chris": 90_000}
    assert [(row["role_name"], row["deduction_paise"], row["final_salary_paise"])
            for row in month["roles"]] == [("Cleaning", 30_000, 270_000),
                                           ("Cooking", 0, 480_000)]
    for bad in [{"role_id": cleaning, "days_absent": 40},
                {"role_id": cleaning, "excess_leave_action": "halve"},
                {"role_id": cleaning, "bonus_or_extra_paise": -1}]:
        assert api.post(f"{BASE}/month/{MONTH}/attendance",
                        json={"records": [bad]}).status_code == 422
    differs = api.post(f"{BASE}/month/{MONTH}/attendance", json={"records": [
        {"role_id": cleaning, "days_absent": 5}, {"role_id": cooking, "days_absent": 2}]})
    assert (differs.status_code, _code(differs)) == (422, "househelp_worker_attendance_differs")
    early = api.post(f"{BASE}/month/2026-12/attendance", json={"records": []})
    assert (early.status_code, _code(early)) == (409, "househelp_month_not_started")
    # A saved role stays its worker's.
    moved = api.patch(f"{BASE}/roles/{cleaning}", json={
        "worker_id": _ok(api.post(f"{BASE}/workers", json={"name": "Ramesh"}), 201)["worker_id"]})
    assert (moved.status_code, _code(moved)) == (409, "househelp_role_has_months")

    # Chris pays his share into the kitty; Bob pays Sunita the whole ₹7,500 himself.
    paid = _ok(api.post(f"{BASE}/record-payment", json={
        "settlement_month": MONTH, "resident_id": "chris", "amount_paise": 90_000,
        "payment_date": "2026-11-05", "payment_mode": "upi", "reference": "UPI-9"}), 201)
    chris = next(row for row in paid["shares"] if row["resident_id"] == "chris")
    assert (chris["status"], chris["outstanding_paise"]) == ("paid", 0)
    payout = _ok(api.post(f"{BASE}/disburse-payout", json={
        "settlement_month": MONTH, "worker_id": sunita, "amount_paise": 750_000,
        "disbursement_mode": "resident_advance", "funding_resident_id": "bob",
        "disbursed_date": "2026-11-07", "reference": "UPI-SAL-11"}), 201)
    worker = payout["workers"][0]
    assert (worker["paid_to_worker_paise"], worker["still_owed_paise"],
            worker["payout_status"]) == (750_000, 0, "disbursed")
    bob = next(row for row in payout["shares"] if row["resident_id"] == "bob")
    assert bob["payments"][0]["payout_id"] == worker["payouts"][0]["payout_id"]
    assert [(row["recipient_resident_id"], row["pending_reimbursement_paise"])
            for row in payout["reimbursements"]] == [("bob", 420_000)]
    too_much = api.post(f"{BASE}/disburse-payout", json={
        "settlement_month": MONTH, "worker_id": sunita, "amount_paise": 1,
        "disbursement_mode": "pool", "disbursed_date": "2026-11-07", "reference": "X"})
    assert (too_much.status_code, _code(too_much)) == (409, "househelp_worker_already_paid")
    assert api.post(f"{BASE}/disburse-payout", json={
        "settlement_month": MONTH, "worker_id": sunita, "disbursement_mode": "pool",
        "disbursed_date": "2026-11-07", "reference": "X"}).status_code == 422

    # The kitty pays Bob back what Chris paid in.
    refunded = _ok(api.post(f"{BASE}/reimburse-from-kitty", json={
        "settlement_month": MONTH, "recipient_resident_id": "bob", "amount_paise": 90_000,
        "disbursed_date": "2026-11-08", "payment_mode": "bank_transfer"}), 201)
    assert refunded["reimbursements"][0]["pending_reimbursement_paise"] == 330_000
    assert [(row["amount_paise"], row["payment_mode"], row["payment_date"]) for row in _rows(
        apartment, "SELECT * FROM payment WHERE payment_kind='refund'")] == [
        (90_000, "bank_transfer", "2026-11-08")]
    more = api.post(f"{BASE}/reimburse-from-kitty", json={
        "settlement_month": MONTH, "recipient_resident_id": "bob", "amount_paise": 1,
        "disbursed_date": "2026-11-08"})
    assert (more.status_code, _code(more)) == (422, "exceeds_kitty_cash")


def test_mistakes_are_deleted_over_http(api):
    sunita, cleaning, _ = _set_up(api)
    _ok(api.post(f"{BASE}/month/{MONTH}/attendance", json={"records": [{"role_id": cleaning}]}))
    paid = _ok(api.post(f"{BASE}/record-payment", json={
        "settlement_month": MONTH, "resident_id": "alice", "amount_paise": 100_000,
        "payment_date": "2026-11-05"}), 201)
    payment_id = next(row for row in paid["shares"]
                      if row["resident_id"] == "alice")["payments"][0]["payment_id"]
    deleted = _ok(api.delete(f"{BASE}/payments/{payment_id}"))
    assert deleted["deleted"]["deleted_payment_ids"] == [payment_id]
    assert api.delete(f"{BASE}/payments/{payment_id}").status_code == 404

    payout = _ok(api.post(f"{BASE}/disburse-payout", json={
        "settlement_month": MONTH, "worker_id": sunita, "amount_paise": 100_000,
        "disbursement_mode": "pool", "disbursed_date": "2026-11-07", "reference": "UTR-1"}), 201)
    payout_id = payout["workers"][0]["payouts"][0]["payout_id"]
    # Only under its own month.
    assert api.delete(f"{BASE}/month/2026-10/disbursements/{payout_id}").status_code == 404
    gone = _ok(api.delete(f"{BASE}/month/{MONTH}/disbursements/{payout_id}"))
    assert (gone["deleted"]["deleted_disbursement_ids"],
            gone["workers"][0]["paid_to_worker_paise"]) == ([payout_id], 0)

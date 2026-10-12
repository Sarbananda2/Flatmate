"""The full operator journey, over HTTP, from an empty database.

This is the definition-of-done check: set up the apartment, add residents and
categories, generate the recurring expenses, enter an electricity bill, record
payments, finalize, correct inside the window, close, adjust afterwards, and
confirm that the exports contain real reconciled figures and that everything
survives a restart.
"""
from __future__ import annotations

import csv
import io
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from aem.config import get_settings
from aem.main import create_app


def _check(response, *expected):
    """Assert the status and return the parsed body, naming the request on failure."""
    expected = expected or (200, 201)
    assert response.status_code in expected, (
        f"{response.request.method} {response.request.url} -> {response.status_code}: "
        f"{response.text[:400]}")
    return response.json() if response.content else None


def test_the_whole_household_workflow(db, tmp_path):
    from fastapi.testclient import TestClient

    settings = replace(get_settings(), database_path=db,
                       auto_close_expired_statements=False)

    with TestClient(create_app(settings)) as client:
        # ---------------------------------------------------------- setup ----
        assert _check(client.get("/health"))["apartment_count"] == 0
        _check(client.post("/api/apartments",
                           json={"apartment_id": "home", "name": "Flat 402"}), 201)
        for resident_id, name in (("alice", "Alice"), ("bob", "Bob"), ("chris", "Chris")):
            _check(client.post("/api/residents", json={
                "resident_id": resident_id, "display_name": name,
                "active_from": "2026-08-01"}), 201)
        assert _check(client.get("/api/residents"))["total"] == 3

        seeded = _check(client.post("/api/apartments/home/starter-definitions"), 201)
        assert seeded["created_count"] == 8
        # Nothing about those categories is built in: rename one to prove it.
        _check(client.patch("/api/expense-definitions/maid",
                            json={"name": "Housekeeping", "reason": "we call it that"}))
        assert _check(client.get("/api/expense-definitions/maid"))["name"] == "Housekeeping"

        _check(client.patch("/api/expense-definitions/rent", json={
            "default_amount_paise": 4_500_000, "day_of_month": 1}))
        _check(client.patch("/api/expense-definitions/broadband", json={
            "default_amount_paise": 120_000, "day_of_month": 5}))

        # ------------------------------------------------ september draft ----
        september = _check(client.post("/api/statements",
                                       json={"settlement_month": "2026-09"}), 201)
        september_id = september["statement_id"]
        assert september["status"] == "draft"

        preview = _check(client.get("/api/recurring/preview",
                                    params={"settlement_period": "2026-09"}))
        assert preview["eligible_count"] == 2
        assert preview["eligible_total_paise"] == 4_620_000

        generated = _check(client.post("/api/recurring/generate", json={
            "settlement_period": "2026-09", "confirm": True, "statement_id": september_id,
            "lines": [{"expense_definition_id": "rent"},
                      {"expense_definition_id": "broadband"}]}), 201)
        assert generated["created_count"] == 2
        # Pressing it again must not duplicate anything.
        again = _check(client.post("/api/recurring/generate", json={
            "settlement_period": "2026-09", "confirm": True, "statement_id": september_id,
            "lines": [{"expense_definition_id": "rent"},
                      {"expense_definition_id": "broadband"}]}), 201)
        assert (again["created_count"], again["skipped_count"]) == (0, 2)

        # Groceries under the previous-month rule: August service, September settlement.
        groceries = _check(client.post("/api/expenses", json={
            "expense_id": "groceries-sep", "description": "Groceries",
            "amount_paise": 333_401, "expense_date": "2026-09-03",
            "expense_definition_id": "groceries", "allocation_method": "equal",
            "payer_resident_id": "alice", "statement_id": september_id}), 201)
        assert groceries["service_period"] == "2026-08"
        assert groceries["settlement_period"] == "2026-09"
        assert sum(row["amount_paise"] for row in groceries["allocations"]) == 333_401

        # ------------------------------------------------- electricity ----
        bill_payload = {
            "expense_id": "electricity-sep",
            "description": "August electricity bill",
            "expense_date": "2026-09-08",
            "total_amount_paise": 735_000,
            "expense_definition_id": "electricity",
            "statement_id": september_id,
            "payer_resident_id": "bob",
            "bill_number": "SEB/26/09/1188",
            "smart_plug_readings": [
                {"resident_id": "alice", "kwh": "42.500", "device_label": "AC"},
                {"resident_id": "bob", "kwh": "31.000", "device_label": "AC"},
                {"resident_id": "chris", "kwh": "18.250", "device_label": "AC"},
            ],
            "components": [
                {"id": "energy", "name": "Energy charges", "amount_paise": 650_000,
                 "allocation_method": "usage"},
                {"id": "fixed", "name": "Fixed charges", "amount_paise": 85_000,
                 "allocation_method": "equal"},
            ],
        }
        review = _check(client.post("/api/electricity/preview", json={
            key: bill_payload[key]
            for key in ("total_amount_paise", "smart_plug_readings", "components",
                        "payer_resident_id", "expense_date")}))
        assert review["reconciles"] is True
        assert sum(row["amount_paise"] for row in review["allocations"]) == 735_000
        saved = _check(client.post("/api/electricity/bills", json=bill_payload), 201)
        assert saved["reconciles"] is True
        assert sum(saved["resident_totals_paise"].values()) == 735_000

        # Bob's advance funds that bill. Linking it keeps his credit to one.
        _check(client.post("/api/payments", json={
            "payment_id": "advance-electricity", "payer_resident_id": "bob",
            "amount_paise": 735_000, "payment_date": "2026-09-08",
            "payment_kind": "provider_advance",
            "payee_or_provider": "State Electricity Board",
            "expense_id": "electricity-sep"}), 201)
        advances = _check(client.get("/api/provider-advances"))
        assert advances["unlinked_count"] == 0
        assert advances["total_paise"] == 735_000

        # -------------------------------------------------- payments ----
        chris_payment = _check(client.post("/api/payments", json={
            "payment_id": "pay-chris", "payer_resident_id": "chris",
            "amount_paise": 2_500_000, "payment_date": "2026-09-20"}), 201)
        applied = sum(row["amount_paise"] for row in chris_payment["applications"])
        assert applied + chris_payment["unapplied_credit_paise"] == 2_500_000

        overpayment = _check(client.post("/api/payments", json={
            "payment_id": "overpay-alice", "payer_resident_id": "alice",
            "amount_paise": 9_000_000, "payment_date": "2026-09-21"}), 201)
        assert overpayment["unapplied_credit_paise"] > 0
        credits = _check(client.get("/api/credits"))
        assert credits["by_resident_paise"]["alice"] == overpayment["unapplied_credit_paise"]

        # -------------------------------------------------- review ----
        review = _check(client.get(f"/api/statements/{september_id}/preview"))
        assert review["can_finalize"] is True
        assert review["blocking_issues"] == []
        assert review["expense_count"] == 4
        assert review["statement_total_paise"] == 4_500_000 + 120_000 + 333_401 + 735_000
        assert review["net_positions_sum_paise"] == 0

        settlement = _check(client.get("/api/reconciliation"))
        assert settlement["net_positions_sum_paise"] == 0
        assert settlement["validation"]["error_count"] == 0

        # ------------------------------------------------ finalize ----
        finalized = _check(client.post(f"/api/statements/{september_id}/finalize", json={
            "finalized_by": "operator", "confirm": True, "require_clean": True}))
        assert finalized["status"] == "finalized_correction_window"
        snapshot_hash = finalized["snapshot_hash"]
        stored = _check(client.get(f"/api/statements/{september_id}/snapshot"))
        assert stored["hash_matches"] is True
        assert stored["snapshot_hash"] == snapshot_hash

        # A new expense cannot join a finalized statement.
        blocked = client.post("/api/expenses", json={
            "description": "Too late", "amount_paise": 100, "expense_date": "2026-09-30",
            "settlement_period": "2026-09", "statement_id": september_id,
            "allocation_method": "equal"})
        assert blocked.status_code == 409

        # ------------------------- correction inside the window ----
        no_reason = client.patch("/api/expenses/groceries-sep",
                                 json={"description": "Groceries (corrected)"})
        assert no_reason.status_code == 422
        assert no_reason.json()["error"]["code"] == "revision_reason_required"

        corrected = _check(client.patch("/api/expenses/groceries-sep", json={
            "description": "Groceries (corrected)",
            "reason": "receipt re-checked against the till roll"}))
        assert corrected["description"] == "Groceries (corrected)"
        revisions = _check(client.get(f"/api/statements/{september_id}/revisions"))
        assert revisions["total"] == 2
        assert revisions["items"][0]["reason"] == "initial_finalization"
        assert "receipt re-checked" in revisions["items"][1]["reason"]

        # -------------------------------------------------- close ----
        too_early = client.post(f"/api/statements/{september_id}/close",
                                json={"confirm": True})
        assert too_early.status_code == 409
        assert too_early.json()["error"]["code"] == "correction_window_open"

        closed = _check(client.post(f"/api/statements/{september_id}/close",
                                    json={"confirm": True, "force": True}))
        assert closed["status"] == "closed"

        locked = client.patch("/api/expenses/groceries-sep",
                              json={"description": "Nope", "reason": "late"})
        assert locked.status_code == 409
        assert locked.json()["error"]["code"] == "closed_statement"

        before_adjustment = _check(
            client.get(f"/api/statements/{september_id}/snapshot"))["snapshot_hash"]

        adjustment = _check(client.post("/api/adjustments", json={
            "resident_id": "chris", "amount_paise": -25_000,
            "reason": "September electricity share corrected after close",
            "statement_id": september_id, "effective_date": "2026-10-06"}), 201)
        assert adjustment["adjustment_id"]
        after_adjustment = _check(
            client.get(f"/api/statements/{september_id}/snapshot"))
        assert after_adjustment["snapshot_hash"] == before_adjustment, (
            "an adjustment must not rewrite the finalized snapshot")
        assert after_adjustment["hash_matches"] is True

        # --------------------------------------------- october month ----
        october = _check(client.post("/api/statements",
                                     json={"settlement_month": "2026-10"}), 201)
        october_id = october["statement_id"]
        _check(client.post("/api/recurring/generate", json={
            "settlement_period": "2026-10", "confirm": True, "statement_id": october_id,
            "lines": [{"expense_definition_id": "rent"},
                      {"expense_definition_id": "broadband"}]}), 201)
        _check(client.post("/api/expenses", json={
            "expense_id": "repair-oct", "description": "Geyser repair",
            "amount_paise": 250_000, "expense_date": "2026-10-07",
            "period_rule": "transaction_date", "allocation_method": "weighted",
            "weights": {"alice": 1, "bob": 1, "chris": 0}}), 201)
        attached = _check(client.post(f"/api/statements/{october_id}/attach-expenses",
                                      json={}))
        assert attached["attached_count"] == 1

        october_review = _check(client.get(f"/api/statements/{october_id}/preview"))
        assert october_review["statement_total_paise"] == 4_500_000 + 120_000 + 250_000
        assert october_review["net_positions_sum_paise"] == 0

        # ------------------------------- cross-month reconciliation ----
        full = _check(client.get("/api/reconciliation"))
        assert full["net_positions_sum_paise"] == 0
        assert set(full["household_expenses_paise_by_settlement_period"]) == {
            "2026-09", "2026-10"}
        assert full["totals"]["household_expenses_paise"] == (
            4_500_000 + 120_000 + 333_401 + 735_000 + 4_500_000 + 120_000 + 250_000)
        assert full["adjustment_net_paise_by_resident"]["chris"] == -25_000
        assert full["unapplied_credits_paise_by_resident"]["alice"] > 0

        historical = _check(client.get("/api/reconciliation",
                                       params={"as_of_date": "2026-09-30"}))
        assert "2026-10" not in historical["household_expenses_paise_by_settlement_period"]
        assert historical["net_positions_sum_paise"] == 0

        # --------------------------------------------- exports ----
        balances = _check(client.get("/api/reports/resident_payments_and_balances"))
        assert balances["row_count"] == 3
        assert sum(row["net_position_paise"] for row in balances["rows"]) == -(
            full["net_positions_paise_by_account"]["__pool__"])

        export = client.get("/api/reports/resident_payments_and_balances/csv")
        assert export.status_code == 200
        rows = list(csv.DictReader(io.StringIO(export.text)))
        assert len(rows) == 3
        assert {row["resident_id"] for row in rows} == {"alice", "bob", "chris"}
        for row in rows:
            # Both representations of every amount agree.
            paise = int(row["net_position_paise"])
            rupees = row["net_position_rupees"]
            assert abs(paise) == abs(int(rupees.replace("-", "").replace(".", "")))

        statement_export = _check(client.get(f"/api/statements/{september_id}/export"))
        assert statement_export["source"] == "stored_snapshot"
        assert statement_export["snapshot_hash_matches"] is True
        assert sum(row["share_paise"] for row in statement_export["detail_rows"]) == (
            statement_export["statement"]["statement_total_paise"])

        dashboard = _check(client.get("/api/dashboard",
                                      params={"settlement_period": "2026-10"}))
        assert dashboard["statement_status"] == "draft"
        assert dashboard["month"]["household_expenses_paise"] == 4_870_000
        assert dashboard["all_time"]["unapplied_credits_paise"] > 0
        assert not dashboard["has_blocking_issues"]

        transactions = _check(client.get("/api/transactions", params={"limit": 500}))
        assert transactions["total"] >= 11
        audit = _check(client.get("/api/audit", params={"limit": 500}))
        assert audit["total"] > 20

        expected_totals = full["totals"]
        expected_positions = full["net_positions_paise_by_account"]

    # ------------------------------------------- survives a restart ----
    with TestClient(create_app(settings)) as restarted:
        assert _check(restarted.get("/health"))["apartment_count"] == 1
        again = _check(restarted.get("/api/reconciliation"))
        assert again["totals"] == expected_totals
        assert again["net_positions_paise_by_account"] == expected_positions
        statements = _check(restarted.get("/api/statements"))
        assert {row["settlement_month"]: row["status"] for row in statements["items"]} == {
            "2026-09": "closed", "2026-10": "draft"}
        snapshot = _check(restarted.get(f"/api/statements/{september_id}/snapshot"))
        assert snapshot["snapshot_hash"] == snapshot_hash
        assert snapshot["hash_matches"] is True


def test_expired_correction_windows_close_at_startup(db):
    """A window that elapses while the app is not running is closed on the next start."""
    from fastapi.testclient import TestClient

    settings = replace(get_settings(), database_path=db)
    with TestClient(create_app(settings)) as client:
        _check(client.post("/api/apartments", json={"apartment_id": "home", "name": "Flat"}), 201)
        _check(client.post("/api/residents",
                           json={"resident_id": "alice", "display_name": "Alice"}), 201)
        statement = _check(client.post("/api/statements",
                                       json={"settlement_month": "2026-09"}), 201)
        _check(client.post(f"/api/statements/{statement['statement_id']}/finalize",
                           json={"finalized_by": "operator", "confirm": True}))

    # Backdate the deadline the way the passage of time would.
    from aem.db import connect

    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")
    with connect(db) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE monthly_statement SET correction_deadline=? WHERE statement_id=?",
                     (past, statement["statement_id"]))
        conn.execute("COMMIT")

    with TestClient(create_app(settings)) as restarted:
        row = _check(restarted.get(f"/api/statements/{statement['statement_id']}"))
        assert row["status"] == "closed"
        assert row["closed_by"] == "startup"

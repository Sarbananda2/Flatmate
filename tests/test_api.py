"""API contract tests.

These exercise the HTTP layer the frontend actually calls: the error envelope,
the confirmation guards, and the fact that a validation failure never leaves a
partial record behind.
"""
from __future__ import annotations

import csv
import io

import pytest

from tests.conftest import APARTMENT


def test_health_reports_the_schema_version_and_the_database_path(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["schema_version"] == body["checks"]["latest_schema_version"]
    assert body["checks"]["foreign_keys"] is True
    assert body["checks"]["integrity"] == "ok"
    assert body["settings"]["binds_loopback_only"] is True


def test_the_openapi_document_is_served_and_documents_the_endpoints(client):
    response = client.get("/openapi.json")
    assert response.status_code == 200
    document = response.json()
    assert document["openapi"].startswith("3.")
    assert document["info"]["title"] == "Apartment Expense Manager"
    paths = document["paths"]
    for path in ("/health", "/api/dashboard", "/api/expenses", "/api/electricity/bills",
                 "/api/payments", "/api/statements", "/api/reconciliation",
                 "/api/transactions", "/api/reports"):
        assert path in paths, path


def test_a_request_before_setup_explains_what_is_missing(client):
    response = client.get("/api/dashboard")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "no_apartment"


def test_every_error_uses_the_same_envelope(household_client):
    response = household_client.get("/api/expenses/does-not-exist")
    assert response.status_code == 404
    error = response.json()["error"]
    assert set(error) == {"code", "message", "details"}
    assert error["code"] == "not_found"
    assert error["details"]["expense_id"] == "does-not-exist"


def test_request_validation_errors_name_the_fields(household_client):
    response = household_client.post("/api/expenses", json={
        "description": "", "amount_paise": -5, "expense_date": "not-a-date"})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "request_validation_error"
    fields = {item["field"] for item in error["details"]["fields"]}
    assert {"description", "amount_paise", "expense_date"} <= fields


def test_an_unknown_field_is_rejected_rather_than_ignored(household_client):
    response = household_client.post("/api/expenses", json={
        "description": "Rent", "amount_paise": 1000, "expense_date": "2026-11-01",
        "allocation_method": "equal", "sneaky_field": "surprise"})
    assert response.status_code == 422


def test_finalizing_requires_explicit_confirmation(client):
    client.post("/api/apartments", json={"apartment_id": APARTMENT, "name": "Flat"})
    client.post("/api/residents", json={"resident_id": "alice", "display_name": "Alice"})
    created = client.post("/api/statements", json={"settlement_month": "2026-11"}).json()
    statement_id = created["statement_id"]

    refused = client.post(f"/api/statements/{statement_id}/finalize",
                          json={"finalized_by": "operator", "confirm": False})
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "request_validation_error"
    assert client.get(f"/api/statements/{statement_id}").json()["status"] == "draft"

    accepted = client.post(f"/api/statements/{statement_id}/finalize",
                           json={"finalized_by": "operator", "confirm": True})
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "finalized_correction_window"


def test_generating_recurring_expenses_requires_explicit_confirmation(client):
    client.post("/api/apartments", json={"apartment_id": APARTMENT, "name": "Flat"})
    client.post("/api/residents", json={"resident_id": "alice", "display_name": "Alice"})
    client.post("/api/expense-definitions", json={
        "definition_id": "rent", "name": "Rent", "category": "Housing",
        "period_rule": "current_month", "recurrence": "monthly",
        "default_amount_paise": 100_000})

    refused = client.post("/api/recurring/generate", json={
        "settlement_period": "2026-11", "confirm": False,
        "lines": [{"expense_definition_id": "rent"}]})
    assert refused.status_code == 422
    assert client.get("/api/expenses").json()["total"] == 0

    accepted = client.post("/api/recurring/generate", json={
        "settlement_period": "2026-11", "confirm": True,
        "lines": [{"expense_definition_id": "rent"}]})
    assert accepted.status_code == 201
    assert accepted.json()["created_count"] == 1


def test_the_allocation_preview_writes_nothing(household_client):
    before = household_client.get("/api/expenses").json()["total"]
    response = household_client.post("/api/expenses/allocation-preview", json={
        "amount_paise": 100_000, "method": "equal", "payer_resident_id": "alice"})
    assert response.status_code == 200
    body = response.json()
    assert sum(body["shares_paise"].values()) == 100_000
    assert body["lines"][0]["display_name"]
    assert household_client.get("/api/expenses").json()["total"] == before


def test_an_unbalanced_custom_allocation_is_refused_with_the_difference(household_client):
    response = household_client.post("/api/expenses/allocation-preview", json={
        "amount_paise": 100_000, "method": "custom",
        "shares_paise": {"alice": 40_000, "bob": 40_000, "chris": 10_000}})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "allocation_total_mismatch"
    assert error["details"]["difference_paise"] == -10_000


def test_the_electricity_preview_reports_whether_it_reconciles(household_client):
    response = household_client.post("/api/electricity/preview", json={
        "total_amount_paise": 100_000,
        "smart_plug_readings": [{"resident_id": "alice", "kwh": "10"},
                                {"resident_id": "bob", "kwh": "30"}],
        "payer_resident_id": "alice"})
    assert response.status_code == 200
    body = response.json()
    assert body["reconciles"] is True
    assert sum(item["amount_paise"] for item in body["allocations"]) == 100_000
    alice = next(row for row in body["residents"] if row["resident_id"] == "alice")
    assert alice["kwh"] == "10.000"
    assert alice["usage_share_percent"] == 25.0
    assert alice["is_payer"] is True


def test_a_closed_statement_rejects_an_edit_with_a_conflict(household_client):
    response = household_client.patch("/api/expenses/groceries-2026-09", json={
        "description": "Changed", "reason": "trying anyway"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "closed_statement"


def test_the_dashboard_reports_the_three_transaction_kinds_separately(household_client):
    response = household_client.get("/api/dashboard", params={"settlement_period": "2026-09"})
    assert response.status_code == 200
    body = response.json()
    month = body["month"]
    assert month["household_expenses_paise"] > 0
    assert month["resident_pool_payments_paise"] > 0
    assert month["provider_advances_paise"] > 0
    assert set(body["transaction_type_legend"]) == {
        "household_expense", "resident_transfer", "provider_payment"}
    assert body["statement_status"] == "closed"
    assert body["all_time"]["unapplied_credits_paise"] >= 0


def test_reconciliation_over_the_api_is_zero_sum_and_lists_its_entries(household_client):
    settlement = household_client.get("/api/reconciliation").json()
    assert settlement["net_positions_sum_paise"] == 0
    assert settlement["entry_count"] > 0
    entries = household_client.get("/api/reconciliation/entries",
                                   params={"limit": 2000}).json()
    assert entries["sum_paise"] == 0
    assert entries["total"] == settlement["entry_count"]
    kinds = {entry["kind"] for entry in entries["items"]}
    assert {"expense_share", "expense_incurred", "provider_advance",
            "pool_payment", "ledger_adjustment"} <= kinds


def test_a_payment_response_says_exactly_which_charges_it_settled(household_client):
    response = household_client.post("/api/payments", json={
        "payer_resident_id": "alice", "amount_paise": 100_000,
        "payment_date": "2026-10-25", "payment_kind": "to_pool"})
    assert response.status_code == 201
    body = response.json()
    applied = sum(item["amount_paise"] for item in body["applications"])
    assert applied + body["unapplied_credit_paise"] == 100_000
    detail = household_client.get(f"/api/payments/{body['payment_id']}").json()
    assert detail["applied_paise"] == applied
    assert len(detail["applications"]) == len(body["applications"])
    for application in detail["applications"]:
        assert application["amount_paise"] <= application["charge_amount_paise"]


def test_reversing_a_payment_requires_a_reason_and_keeps_both_rows(household_client):
    created = household_client.post("/api/payments", json={
        "payer_resident_id": "alice", "amount_paise": 50_000,
        "payment_date": "2026-10-25"}).json()
    payment_id = created["payment_id"]

    refused = household_client.post(f"/api/payments/{payment_id}/reverse", json={"reason": ""})
    assert refused.status_code == 422

    reversed_response = household_client.post(
        f"/api/payments/{payment_id}/reverse", json={"reason": "entered twice"})
    assert reversed_response.status_code == 200
    reversal_id = reversed_response.json()["reversal_payment_id"]

    original = household_client.get(f"/api/payments/{payment_id}").json()
    assert original["status"] == "reversed"
    assert original["applications"] == []
    mirror = household_client.get(f"/api/payments/{reversal_id}").json()
    assert mirror["status"] == "reversal"
    assert mirror["reverses_payment_id"] == payment_id

    settlement = household_client.get("/api/reconciliation").json()
    assert settlement["net_positions_sum_paise"] == 0


def test_a_reversal_cannot_be_reversed_again(household_client):
    created = household_client.post("/api/payments", json={
        "payer_resident_id": "alice", "amount_paise": 50_000,
        "payment_date": "2026-10-25"}).json()
    household_client.post(f"/api/payments/{created['payment_id']}/reverse",
                          json={"reason": "first"})
    again = household_client.post(f"/api/payments/{created['payment_id']}/reverse",
                                  json={"reason": "second"})
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "payment_not_active"


def test_the_transaction_ledger_filters_and_totals_by_type(household_client):
    everything = household_client.get("/api/transactions", params={"limit": 500}).json()
    assert everything["total"] > 0
    assert set(everything["totals_by_type"]) <= {"expense", "payment", "adjustment"}

    by_resident = household_client.get("/api/transactions",
                                       params={"resident_id": "bob", "limit": 500}).json()
    assert by_resident["total"] > 0
    assert by_resident["total"] <= everything["total"]

    only_payments = household_client.get("/api/transactions",
                                         params={"transaction_type": "payment"}).json()
    assert {row["transaction_type"] for row in only_payments["items"]} == {"payment"}

    september = household_client.get("/api/transactions",
                                     params={"settlement_period": "2026-09"}).json()
    assert september["total"] > 0
    assert all(row["settlement_period"] == "2026-09" for row in september["items"])


def test_filter_options_only_offer_values_that_exist(household_client):
    options = household_client.get("/api/transactions/filter-options").json()
    assert {resident["resident_id"] for resident in options["residents"]} == {
        "alice", "bob", "chris"}
    assert "Housing" in options["categories"]
    assert "2026-09" in options["settlement_periods"]


def test_the_audit_trail_records_every_change_with_old_and_new_values(household_client):
    audit = household_client.get("/api/audit", params={"limit": 500}).json()
    assert audit["total"] > 10
    actions = {row["action"] for row in audit["items"]}
    assert {"create", "finalize", "close"} <= actions
    finalize = next(row for row in audit["items"] if row["action"] == "finalize")
    assert "draft" in (finalize["old_value"] or "")
    assert "finalized_correction_window" in (finalize["new_value"] or "")

    for_expense = household_client.get("/api/audit", params={
        "entity_type": "expense", "entity_id": "groceries-2026-09"}).json()
    assert for_expense["total"] >= 1
    assert all(row["entity_id"] == "groceries-2026-09" for row in for_expense["items"])


@pytest.mark.parametrize("kind", [
    "monthly_household_expenses",
    "resident_expense_breakdown",
    "resident_payments_and_balances",
    "provider_advances",
    "electricity_consumption",
    "credits_and_adjustments",
    "statement_summary",
    "transaction_ledger",
    "audit_trail",
])
def test_every_report_runs_and_exports_real_rows(household_client, kind):
    report = household_client.get(f"/api/reports/{kind}").json()
    assert report["kind"] == kind
    assert report["row_count"] == len(report["rows"])

    export = household_client.get(f"/api/reports/{kind}/csv")
    assert export.status_code == 200
    assert export.headers["content-type"].startswith("text/csv")
    assert "attachment" in export.headers["content-disposition"]
    if report["row_count"]:
        rows = list(csv.DictReader(io.StringIO(export.text)))
        assert len(rows) == report["row_count"]
        # Every paise column is exported alongside a plain rupee column.
        for column in rows[0]:
            if column.endswith("_paise"):
                assert column[:-6] + "_rupees" in rows[0]


def test_a_statement_detail_export_matches_the_stored_snapshot(household_client):
    export = household_client.get("/api/statements/stmt-2026-09/export").json()
    assert export["source"] == "stored_snapshot"
    assert export["snapshot_hash_matches"] is True
    detail_total = sum(row["share_paise"] for row in export["detail_rows"])
    assert detail_total == export["statement"]["statement_total_paise"]

    csv_export = household_client.get("/api/statements/stmt-2026-09/export/csv")
    rows = list(csv.DictReader(io.StringIO(csv_export.text)))
    assert len(rows) == len(export["detail_rows"])
    assert sum(int(row["share_paise"]) for row in rows) == detail_total
    assert all(row["source"] == "stored_snapshot" for row in rows)


def test_an_unknown_report_is_rejected(household_client):
    response = household_client.get("/api/reports/not_a_report")
    assert response.status_code == 422
    assert "allowed" in response.json()["error"]["details"]


def test_a_resident_is_deactivated_without_losing_history(household_client):
    before = household_client.get("/api/residents/chris").json()
    assert before["charges"]
    response = household_client.post("/api/residents/chris/deactivate", json={
        "active_until": "2026-10-31", "reason": "moved out"})
    assert response.status_code == 200
    after = household_client.get("/api/residents/chris").json()
    assert after["resident"]["is_active"] == 0
    assert after["resident"]["active_until"] == "2026-10-31"
    assert after["charges"] == before["charges"]
    assert after["payments"] == before["payments"]
    assert after["account"]["net_position_paise"] == before["account"]["net_position_paise"]
    settlement = household_client.get("/api/reconciliation").json()
    assert "chris" in settlement["net_positions_paise_by_account"], (
        "a former resident with an open position stays visible")


def test_no_endpoint_accepts_raw_sql_from_the_client(household_client):
    injection = "'; DROP TABLE expense_instance; --"
    for params in (
        {"search": injection},
        {"resident_id": injection},
        {"sort": injection},
    ):
        response = household_client.get("/api/expenses", params=params)
        assert response.status_code in (200, 422), params
    assert household_client.get("/health").json()["checks"]["integrity"] == "ok"
    assert household_client.get("/api/expenses").json()["total"] > 0


def test_pagination_bounds_are_enforced(household_client):
    assert household_client.get("/api/expenses", params={"limit": 0}).status_code == 422
    assert household_client.get("/api/expenses", params={"limit": 10_000}).status_code == 422
    assert household_client.get("/api/expenses", params={"offset": -1}).status_code == 422


def test_the_services_summary_matches_the_rent_and_electricity_endpoints(household_client):
    client = household_client
    bills = client.get("/api/electricity/bills").json()["items"]
    month = bills[0]["settlement_period"]

    empty = client.get("/api/services", params={"month": month}).json()
    assert empty["rent"] == {"config": None, "cycle": None}

    client.post("/api/rent/config", json={
        "landlord_name": "Kumar Yadav", "monthly_rent_paise": 2_850_000})
    summary = client.get("/api/services", params={"month": month}).json()

    assert summary["settlement_month"] == month
    assert summary["rent"]["config"] == client.get("/api/rent/config").json()["config"]
    assert summary["rent"]["cycle"] == client.get(f"/api/rent/cycle/{month}").json()["cycle"]
    assert summary["electricity"]["latest_bill"] == bills[0]
    assert summary["electricity"]["bill"] == bills[0]
    assert summary["electricity"]["bills_for_month"] == sum(
        1 for bill in bills if bill["settlement_period"] == month)

    quiet = client.get("/api/services", params={"month": "2031-01"}).json()
    assert quiet["electricity"]["bill"] is None
    assert quiet["electricity"]["latest_bill"] == bills[0]


def test_the_services_summary_requires_a_valid_month(household_client):
    assert household_client.get("/api/services").status_code == 422
    assert household_client.get("/api/services", params={"month": "2026-13"}).status_code == 422

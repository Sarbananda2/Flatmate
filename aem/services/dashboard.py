"""The dashboard: every figure derived from records, none of them invented.

The dashboard separates the three things that are easy to confuse:

* **household expenses** -- money the household spent;
* **resident transfers** -- money moving between a resident and the pool, which
  settles liabilities and is never an expense;
* **payments to external providers** -- money leaving the household, recorded
  either as the expense's payer or as a provider advance linked to it.

Every number comes from :mod:`aem.services.reconciliation` or a direct query.
If the data cannot be reconciled, the warnings say so instead of showing a
figure that looks fine.
"""
from __future__ import annotations

from typing import Any

from .. import db
from ..db import connect, now_timestamp
from ..periods import normalize_month, shift_month
from .common import POOL_ACCOUNT_ID, get_apartment
from .ledger_view import list_transactions
from .reconciliation import reconcile
from .validation import SEVERITY_ERROR


def dashboard(db_path, *, apartment_id: str, settlement_period: str | None = None,
              as_of_date: str | None = None) -> dict[str, Any]:
    period = normalize_month(settlement_period or now_timestamp()[:7], "settlement_period")
    reconciliation = reconcile(db_path, apartment_id=apartment_id, as_of_date=as_of_date)
    previous = shift_month(period, -1)

    with connect(db_path, read_only=True) as conn:
        apartment = get_apartment(conn, apartment_id)
        month_expenses = db.fetch_all(
            conn,
            """SELECT e.expense_id, e.description, e.amount_paise, e.expense_date,
                      e.service_period, e.settlement_period, e.expense_kind,
                      e.payer_resident_id, e.provider_name, e.statement_id,
                      d.category, r.display_name AS payer_display_name
                 FROM expense_instance e
                 LEFT JOIN expense_definition d
                        ON d.expense_definition_id = e.expense_definition_id
                 LEFT JOIN resident r ON r.resident_id = e.payer_resident_id
                WHERE e.apartment_id = ? AND e.settlement_period = ?
                ORDER BY e.expense_date DESC, e.rowid DESC""", (apartment_id, period))
        month_payments = db.fetch_all(
            conn,
            """SELECT p.payment_id, p.payer_resident_id, p.amount_paise, p.payment_date,
                      p.payment_kind, p.payee_or_provider, p.status, p.expense_id,
                      r.display_name AS payer_display_name
                 FROM payment p JOIN resident r ON r.resident_id = p.payer_resident_id
                WHERE p.apartment_id = ? AND substr(p.payment_date, 1, 7) = ?
                  AND p.status = 'active'
                ORDER BY p.payment_date DESC, p.rowid DESC""", (apartment_id, period))
        statement = db.fetch_one(
            conn,
            """SELECT statement_id, settlement_month, status, finalized_at,
                      correction_deadline, closed_at, finalized_by
                 FROM monthly_statement WHERE apartment_id=? AND settlement_month=?""",
            (apartment_id, period))
        previous_statement = db.fetch_one(
            conn, "SELECT statement_id, settlement_month, status FROM monthly_statement "
                  "WHERE apartment_id=? AND settlement_month=?", (apartment_id, previous))
        month_statement_charges = db.fetch_all(
            conn,
            """SELECT a.resident_id, r.display_name, SUM(a.amount_paise) AS charged_paise
                 FROM allocation a
                 JOIN expense_instance e ON e.expense_id = a.expense_id
                 JOIN resident r ON r.resident_id = a.resident_id
                WHERE e.apartment_id = ? AND e.settlement_period = ?
                GROUP BY a.resident_id ORDER BY r.display_name""", (apartment_id, period))
        resident_count = int(db.scalar(
            conn, "SELECT COUNT(*) FROM resident WHERE apartment_id=? AND is_active=1",
            (apartment_id,), default=0))
        definition_count = int(db.scalar(
            conn, "SELECT COUNT(*) FROM expense_definition WHERE apartment_id=? AND is_active=1",
            (apartment_id,), default=0))

    # The dashboard's "latest transactions" is the same unified ledger the
    # Transactions screen shows, so the two can never disagree.
    latest_transactions = list_transactions(db_path, apartment_id=apartment_id,
                                            limit=12)["items"]

    totals = reconciliation["totals"]
    month_expense_total = sum(row["amount_paise"] for row in month_expenses)
    pool_payments_in_month = sum(row["amount_paise"] for row in month_payments
                                 if row["payment_kind"] == "to_pool")
    advances_in_month = sum(row["amount_paise"] for row in month_payments
                            if row["payment_kind"] == "provider_advance")
    refunds_in_month = sum(row["amount_paise"] for row in month_payments
                           if row["payment_kind"] == "refund")
    direct_provider_payments = sum(row["amount_paise"] for row in month_expenses
                                   if row["payer_resident_id"])

    issues = reconciliation["validation"]["issues"]
    warnings = [
        {"severity": issue["severity"], "code": issue["code"], "message": issue["message"],
         "entity_type": issue["entity_type"], "entity_id": issue["entity_id"],
         "suggested_action": issue["suggested_action"]}
        for issue in issues
    ]
    if not month_expenses and not month_payments:
        warnings.append({
            "severity": "info", "code": "no_activity",
            "message": f"Nothing has been recorded for {period} yet.",
            "entity_type": "period", "entity_id": period,
            "suggested_action": "Record an expense, or generate the recurring ones."})
    if resident_count == 0:
        warnings.append({
            "severity": "error", "code": "no_residents",
            "message": "No active residents are registered, so expenses cannot be allocated.",
            "entity_type": "apartment", "entity_id": apartment_id,
            "suggested_action": "Add residents first."})
    if definition_count == 0:
        warnings.append({
            "severity": "info", "code": "no_definitions",
            "message": "No expense categories are defined yet.",
            "entity_type": "apartment", "entity_id": apartment_id,
            "suggested_action": "Create expense definitions, or add the starter set."})

    return {
        "apartment": apartment,
        "settlement_period": period,
        "previous_settlement_period": previous,
        "as_of_date": as_of_date,
        "generated_at": now_timestamp(),
        "statement": statement,
        "previous_statement": previous_statement,
        "statement_status": statement["status"] if statement else "not_created",
        "month": {
            "household_expenses_paise": month_expense_total,
            "expense_count": len(month_expenses),
            "resident_pool_payments_paise": pool_payments_in_month,
            "provider_advances_paise": advances_in_month,
            "refunds_paise": refunds_in_month,
            "direct_provider_payments_paise": direct_provider_payments,
            "charges_by_resident": month_statement_charges,
            "expenses": month_expenses[:10],
            "payments": month_payments[:10],
        },
        "all_time": {
            "household_expenses_paise": totals["household_expenses_paise"],
            "outstanding_charges_paise": totals["outstanding_charges_paise"],
            "unapplied_credits_paise": totals["unapplied_credits_paise"],
            "provider_advances_paise": totals["provider_advances_paise"],
            "expense_funding_paise": totals["expense_funding_paise"],
            "pool_payments_paise": totals["pool_payments_paise"],
            "adjustments_paise": totals["adjustments_paise"],
            "pool_net_position_paise": totals["pool_net_position_paise"],
        },
        "accounts": reconciliation["accounts"],
        "suggested_transfers": reconciliation["transfers"],
        "latest_transactions": latest_transactions,
        "warnings": warnings,
        "has_blocking_issues": any(i["severity"] == SEVERITY_ERROR for i in issues),
        "resident_count": resident_count,
        "definition_count": definition_count,
        "transaction_type_legend": {
            "household_expense": "A cost the household incurred. Allocated to residents "
                                 "as charges.",
            "resident_transfer": "Money between a resident and the pool. Settles charges; "
                                 "never an expense.",
            "provider_payment": "Money paid to an external provider, recorded as the "
                                "expense's payer or as a linked provider advance.",
        },
    }

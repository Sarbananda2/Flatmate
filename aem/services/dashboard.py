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

from ..ids import now_timestamp
from ..periods import normalize_month, shift_month
from ..persistence import Database
from .common import POOL_ACCOUNT_ID, get_apartment
from .ledger_view import list_transactions
from .reconciliation import reconcile
from .validation import SEVERITY_ERROR


def dashboard(db: Database, *, apartment_id: str, settlement_period: str | None = None,
              as_of_date: str | None = None) -> dict[str, Any]:
    period = normalize_month(settlement_period or now_timestamp()[:7], "settlement_period")
    reconciliation = reconcile(db, apartment_id=apartment_id, as_of_date=as_of_date)
    previous = shift_month(period, -1)

    with db.read() as uow:
        apartment = get_apartment(uow, apartment_id)
        month_expenses = uow.dashboard.expenses_in_period(apartment_id, period)
        month_payments = uow.dashboard.active_payments_in_month(apartment_id, period)
        statement = uow.dashboard.statement_for_month(apartment_id, period)
        previous_statement = uow.dashboard.statement_summary_for_month(apartment_id, previous)
        month_statement_charges = uow.dashboard.charges_by_resident_in_period(
            apartment_id, period)
        resident_count = uow.dashboard.count_active_residents(apartment_id)
        definition_count = uow.dashboard.count_active_definitions(apartment_id)

    # The dashboard's "latest transactions" is the same unified ledger the
    # Transactions screen shows, so the two can never disagree.
    latest_transactions = list_transactions(db, apartment_id=apartment_id,
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

"""Accounting validation: find and name every ambiguous or inconsistent record.

The application never invents a balancing entry. When the data cannot be
reconciled, or when it *can* but only because of an assumption the operator
should confirm, this module reports it as an identified exception against a
specific record. Severity decides what the UI does with it:

``error``
    The ledger is internally inconsistent. Reconciliation refuses to produce
    settlement figures until the operator fixes it.

``warning``
    The ledger reconciles, but a likely mistake or a judgement call is hiding
    in it -- most importantly an unlinked provider advance that looks like it
    funds an expense which already names a payer. That is the double-count
    this household is most exposed to, so it is surfaced by name.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from ..persistence import Database, UnitOfWork
from .common import get_apartment, resident_ids

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"


def _issue(code: str, severity: str, message: str, *, entity_type: str,
           entity_id: str, details: dict[str, Any] | None = None,
           suggested_action: str | None = None) -> dict[str, Any]:
    return {"code": code, "severity": severity, "message": message,
            "entity_type": entity_type, "entity_id": entity_id,
            "details": details or {}, "suggested_action": suggested_action}


def _within_days(first: Any, second: Any, days: int) -> bool:
    """Whether two stored dates are at most `days` apart.

    False when either is not a date, so a malformed value leaves a record out
    of a check instead of failing the whole validation run.
    """
    try:
        gap = datetime.fromisoformat(first) - datetime.fromisoformat(second)
    except (TypeError, ValueError):
        return False
    return abs(gap.total_seconds()) <= days * 86400


def validate_apartment(uow: UnitOfWork, apartment_id: str) -> list[dict[str, Any]]:
    """Run every integrity check for one apartment and return the issues found."""
    issues: list[dict[str, Any]] = []
    residents = set(resident_ids(uow, apartment_id))

    # 1. Each expense's allocations must equal its total, exactly.
    for row in uow.validation.expenses_with_allocated_total(apartment_id):
        if row["allocated_paise"] != row["amount_paise"]:
            issues.append(_issue(
                "allocation_total_mismatch", SEVERITY_ERROR,
                f"Expense '{row['description']}' is {row['amount_paise']} paise but its "
                f"resident allocations add up to {row['allocated_paise']} paise.",
                entity_type="expense", entity_id=row["expense_id"],
                details={"amount_paise": row["amount_paise"],
                         "allocated_paise": row["allocated_paise"],
                         "difference_paise": row["allocated_paise"] - row["amount_paise"],
                         "settlement_period": row["settlement_period"]},
                suggested_action="Edit the expense's allocations so they sum to its amount."))

    # 2. Allocations must belong to residents of this apartment.
    for row in uow.validation.allocations_for_apartment(apartment_id):
        if row["resident_id"] not in residents:
            issues.append(_issue(
                "allocation_foreign_resident", SEVERITY_ERROR,
                "An allocation is charged to a resident who does not belong to this apartment.",
                entity_type="allocation", entity_id=row["allocation_id"],
                details={"resident_id": row["resident_id"], "expense_id": row["expense_id"]},
                suggested_action="Reassign or remove the allocation."))

    # 3. Expenses with no allocations at all.
    for row in uow.validation.expenses_without_allocations(apartment_id):
        issues.append(_issue(
            "expense_not_allocated", SEVERITY_ERROR,
            f"Expense '{row['description']}' has no resident allocations.",
            entity_type="expense", entity_id=row["expense_id"],
            details={"amount_paise": row["amount_paise"]},
            suggested_action="Allocate the expense to residents, or delete it."))

    # 4. Expenses with no apartment: created before the upgrade and never linked.
    for row in uow.validation.unlinked_expenses_allocated_to(apartment_id):
        issues.append(_issue(
            "expense_missing_apartment", SEVERITY_WARNING,
            f"Expense '{row['description']}' is allocated to this apartment's residents but "
            "is not itself linked to the apartment.",
            entity_type="expense", entity_id=row["expense_id"],
            suggested_action="Open and save the expense to relink it."))

    # 5. A payment can never be applied beyond its own amount.
    for row in uow.validation.payments_with_applied_total(apartment_id):
        if row["applied_paise"] > row["amount_paise"]:
            issues.append(_issue(
                "payment_overapplied", SEVERITY_ERROR,
                "A payment has been applied to more than its own amount.",
                entity_type="payment", entity_id=row["payment_id"],
                details={"amount_paise": row["amount_paise"],
                         "applied_paise": row["applied_paise"]},
                suggested_action="Reverse and re-record the payment."))

    # 6. A charge can never be settled beyond its own amount.
    for row in uow.validation.allocations_with_settled_total(apartment_id):
        if row["settled_paise"] > row["amount_paise"]:
            issues.append(_issue(
                "charge_overapplied", SEVERITY_ERROR,
                "A resident charge has been settled beyond its own amount.",
                entity_type="allocation", entity_id=row["allocation_id"],
                details={"amount_paise": row["amount_paise"],
                         "settled_paise": row["settled_paise"],
                         "resident_id": row["resident_id"]},
                suggested_action="Reverse the payment that over-settled this charge."))

    # 7. Linked advances must not exceed the expense they fund.
    for row in uow.validation.expenses_with_linked_advances(apartment_id):
        if row["linked_paise"] > row["amount_paise"]:
            issues.append(_issue(
                "advance_exceeds_expense", SEVERITY_ERROR,
                f"Provider advances linked to '{row['description']}' total more than the "
                "expense itself.",
                entity_type="expense", entity_id=row["expense_id"],
                details={"amount_paise": row["amount_paise"],
                         "linked_advance_paise": row["linked_paise"]},
                suggested_action="Reverse the surplus advance, or correct the expense amount."))
        elif (row["linked_paise"] < row["amount_paise"]
              # A month's rent paid to the landlord, or a service's bill paid to
              # its provider, in tranches: the tranches record what the pool
              # paid, so there is nothing to warn about.
              and row["tranche_paise"] != row["linked_paise"]):
            remaining = row["amount_paise"] - row["linked_paise"]
            issues.append(_issue(
                "expense_partially_funded", SEVERITY_WARNING,
                f"'{row['description']}' is funded by linked advances of "
                f"{row['linked_paise']} paise, leaving {remaining} paise funded by the pool.",
                entity_type="expense", entity_id=row["expense_id"],
                details={"amount_paise": row["amount_paise"],
                         "linked_advance_paise": row["linked_paise"],
                         "funded_by_pool_paise": remaining,
                         "payer_resident_id_ignored": row["payer_resident_id"]},
                suggested_action="If the payer funded the rest, record the remaining advance "
                                 "and link it to this expense. A linked advance replaces the "
                                 "expense's payer field entirely."))

    # 8. The double count this household is most exposed to: an expense that
    #    names a payer, plus an *unlinked* advance by that same resident for the
    #    same amount. Reconciliation would credit the resident twice.
    for row in uow.validation.unlinked_advances_matching_payer_expenses(apartment_id):
        if not _within_days(row["payment_date"], row["expense_date"], 7):
            continue
        issues.append(_issue(
            "possible_double_counted_advance", SEVERITY_WARNING,
            f"Provider advance {row['payment_id']} matches expense '{row['description']}' "
            "in resident, amount, and date, but is not linked to it. If they are the same "
            "payment, the resident is being credited twice.",
            entity_type="payment", entity_id=row["payment_id"],
            details={"expense_id": row["expense_id"],
                     "amount_paise": row["amount_paise"],
                     "resident_id": row["payer_resident_id"],
                     "payment_date": row["payment_date"],
                     "expense_date": row["expense_date"]},
            suggested_action="Link the advance to that expense, or clear the expense's payer "
                             "field if the advance is the real funding record."))

    # 9. A reversed payment must not keep applications; a reversal must point at
    #    a payment that is actually marked reversed.
    for row in uow.validation.inactive_payments_with_application_count(apartment_id):
        if row["application_count"]:
            issues.append(_issue(
                "reversed_payment_still_applied", SEVERITY_ERROR,
                "A reversed payment still has applications against resident charges.",
                entity_type="payment", entity_id=row["payment_id"],
                details={"status": row["status"],
                         "application_count": row["application_count"]},
                suggested_action="Remove the stale applications, then re-record the payment."))

    # 10. Statement/period consistency.
    for row in uow.validation.expenses_on_wrong_period_statement(apartment_id):
        issues.append(_issue(
            "statement_period_mismatch", SEVERITY_WARNING,
            f"'{row['description']}' settles in {row['settlement_period']} but is attached to "
            f"the {row['settlement_month']} statement.",
            entity_type="expense", entity_id=row["expense_id"],
            details={"settlement_period": row["settlement_period"],
                     "statement_settlement_month": row["settlement_month"],
                     "statement_id": row["statement_id"]},
            suggested_action="Move the expense to the statement for its settlement month."))

    # 11. A finalized statement should have a snapshot; a closed one must.
    for row in uow.validation.non_draft_statements(apartment_id):
        if not row["snapshot_json"]:
            issues.append(_issue(
                "statement_missing_snapshot", SEVERITY_ERROR,
                f"The {row['settlement_month']} statement is {row['status']} but has no "
                "saved snapshot, so its finalized figures cannot be reproduced.",
                entity_type="statement", entity_id=row["statement_id"],
                details={"status": row["status"]},
                suggested_action="This should not happen. Restore from a backup and report it."))

    # 12. Residents who are inactive but still carry an open position are not an
    #     error; they must simply remain visible. Reported so the dashboard can
    #     say so rather than hiding them.
    for row in uow.validation.inactive_residents_with_outstanding(apartment_id):
        if row["outstanding_paise"]:
            issues.append(_issue(
                "inactive_resident_open_balance", SEVERITY_WARNING,
                f"{row['display_name']} has moved out but still has "
                f"{row['outstanding_paise']} paise of unsettled charges.",
                entity_type="resident", entity_id=row["resident_id"],
                details={"outstanding_paise": row["outstanding_paise"]},
                suggested_action="Settle the balance, or record a ledger adjustment that "
                                 "documents how it was resolved."))

    order = {SEVERITY_ERROR: 0, SEVERITY_WARNING: 1}
    issues.sort(key=lambda item: (order.get(item["severity"], 9), item["code"],
                                  item["entity_id"]))
    return issues


def validate(db: Database, apartment_id: str) -> dict[str, Any]:
    with db.read() as uow:
        get_apartment(uow, apartment_id)
        issues = validate_apartment(uow, apartment_id)
    errors = [i for i in issues if i["severity"] == SEVERITY_ERROR]
    return {
        "apartment_id": apartment_id,
        "issues": issues,
        "error_count": len(errors),
        "warning_count": len(issues) - len(errors),
        "is_reconcilable": not errors,
    }

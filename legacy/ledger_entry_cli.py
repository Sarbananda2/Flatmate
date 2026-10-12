"""Terminal workflow for general expenses, payments, and monthly statements."""
from __future__ import annotations

import argparse
import sqlite3
from decimal import Decimal, InvalidOperation
from pathlib import Path

from electricity_workflow import initialize_database
from ledger_workflow import (
    LedgerWorkflowError,
    create_expense,
    create_expense_definition,
    create_monthly_statement,
    finalize_statement,
    list_expense_definitions,
    record_payment,
    record_adjustment,
    statement_snapshot,
    close_expired_statements,
)


def required(prompt: str) -> str:
    while True:
        value = input(prompt).strip()
        if value:
            return value
        print("This field is required.")


def money(prompt: str, *, positive: bool = False) -> int:
    while True:
        try:
            value = Decimal(input(prompt).strip().replace(",", ""))
            if not value.is_finite() or value < (Decimal("0.01") if positive else 0) or value.as_tuple().exponent < -2:
                raise ValueError
            return int(value * 100)
        except (InvalidOperation, ValueError):
            print("Enter a valid rupee amount with at most two decimal places.")


def residents(db: Path, apartment_id: str) -> list[dict[str, str]]:
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(
            "SELECT resident_id, display_name FROM resident WHERE apartment_id=? ORDER BY display_name",
            (apartment_id,),
        ).fetchall()]


def select_resident(people: list[dict[str, str]], prompt: str, optional: bool = False) -> str | None:
    for i, person in enumerate(people, 1):
        print(f"  {i}. {person['display_name']} [{person['resident_id']}]")
    raw = input(prompt).strip()
    if optional and not raw:
        return None
    try:
        return people[int(raw)-1]["resident_id"]
    except (ValueError, IndexError):
        raise ValueError("Invalid resident selection.")


def add_definition(db: Path, apartment_id: str) -> None:
    create_expense_definition(
        db, apartment_id=apartment_id, definition_id=required("Definition ID: "),
        name=required("Expense name: "), category=required("Category: "),
        period_rule=input("Period rule [previous_month/current_month/transaction_date/custom]: ").strip(),
        default_allocation_method=input("Default allocation [equal/weighted/custom/measured/host_guest]: ").strip() or "equal",
        is_recurring=(input("Recurring? [Y/n]: ").strip().lower() != "n"),
    )
    print("Expense definition created.")


def add_statement(db: Path, apartment_id: str) -> None:
    statement_id = create_monthly_statement(
        db, apartment_id=apartment_id, settlement_month=required("Settlement month (YYYY-MM): ")
    )
    print("Created draft statement:", statement_id)


def add_expense(db: Path, apartment_id: str) -> None:
    people = residents(db, apartment_id)
    if not people:
        print("No residents registered.")
        return
    definitions = list_expense_definitions(db, apartment_id)
    definition_id = None
    if definitions:
        print("Expense definitions:")
        for i, definition in enumerate(definitions, 1):
            print(f"  {i}. {definition['name']} ({definition['expense_definition_id']}) — {definition['period_rule']}")
        raw = input("Select definition number, or Enter for one-off expense: ").strip()
        if raw:
            definition_id = definitions[int(raw)-1]["expense_definition_id"]
    amount = money("Total expense (₹): ")
    print("Enter each resident's share. Shares must sum exactly to the total.")
    shares = {}
    for person in people:
        shares[person["resident_id"]] = money(f"  {person['display_name']} share (₹): ")
    if sum(shares.values()) != amount:
        raise LedgerWorkflowError(
            f"Resident shares total ₹{sum(shares.values())/100:.2f}, but expense total is ₹{amount/100:.2f}."
        )
    print("Who paid? Enter a resident number, or press Enter if unknown/not recorded.")
    payer = select_resident(people, "Payer number: ", optional=True)
    statement_id = input("Draft statement ID (optional): ").strip() or None
    result = create_expense(
        db, apartment_id=apartment_id, expense_id=required("Unique expense ID: "),
        description=required("Description: "), amount_paise=amount,
        expense_date=required("Expense date (YYYY-MM-DD): "),
        expense_period=required("Expense period (YYYY-MM): "),
        allocations_paise=shares, payer_resident_id=payer,
        provider_name=input("Provider/store (optional): ").strip() or None,
        expense_definition_id=definition_id, statement_id=statement_id,
        allocation_method="custom",
        allocation_basis={"method": "custom", "entered_via": "terminal"},
    )
    print("Saved expense:", result["expense_id"])


def add_payment(db: Path, apartment_id: str) -> None:
    people = residents(db, apartment_id)
    if not people:
        print("No residents registered.")
        return
    print("Who made the payment?")
    payer = select_resident(people, "Payer number: ")
    kind = input("Payment kind [to_pool/provider_advance/refund/adjustment] (default to_pool): ").strip() or "to_pool"
    linked_expense_id = None
    if kind == "provider_advance":
        linked_expense_id = input("Linked expense ID (optional; use to prevent double counting): ").strip() or None
    result = record_payment(
        db, apartment_id=apartment_id, payment_id=required("Unique payment ID: "),
        payer_resident_id=payer, amount_paise=money("Payment amount (₹): ", positive=True),
        payment_date=required("Payment date (YYYY-MM-DD): "),
        payment_kind=kind,
        payee_or_provider=input("Payee/provider (optional): ").strip() or None,
        reference=input("Reference (optional): ").strip() or None,
        note=input("Note (optional): ").strip() or None,
        expense_id=linked_expense_id,
    )
    print("Saved payment:", result["payment_id"])
    print("Applied to charges: ₹{:.2f}".format(sum(a["amount_paise"] for a in result["applications"])/100))
    print("Unapplied credit: ₹{:.2f}".format(result["unapplied_credit_paise"]/100))


def add_adjustment(db: Path, apartment_id: str) -> None:
    people = residents(db, apartment_id)
    if not people:
        print("No residents registered.")
        return
    print("Positive amount = credit to resident; negative amount = debit to resident.")
    resident_id = select_resident(people, "Resident number: ")
    amount = money("Absolute adjustment amount (₹): ", positive=True)
    sign = input("Direction [credit/debit]: ").strip().lower()
    if sign not in {"credit", "debit"}:
        raise ValueError("Choose credit or debit.")
    signed_amount = amount if sign == "credit" else -amount
    adjustment_id = record_adjustment(
        db, apartment_id=apartment_id, resident_id=resident_id,
        amount_paise=signed_amount, reason=required("Reason: "),
        created_by=required("Operator identifier: "),
        statement_id=input("Related statement ID (optional): ").strip() or None,
    )
    print("Recorded adjustment:", adjustment_id)


def preview_statement(db: Path) -> None:
    statement_id = required("Statement ID: ")
    snapshot = statement_snapshot(db, statement_id)
    print(f"\nStatement {snapshot['settlement_month']} — {snapshot['status']}")
    print("\nExpenses:")
    for expense in snapshot["expenses"]:
        print(f"  {expense['expense_date']}  {expense['description']}: ₹{expense['amount_paise']/100:.2f}")
        for allocation in expense["allocations"]:
            print(f"    {allocation['resident_id']}: ₹{allocation['amount_paise']/100:.2f}")
    print("\nCharges in this statement:")
    for resident_id, amount in snapshot["statement_charges_paise_by_resident"].items():
        print(f"  {resident_id}: ₹{amount/100:.2f}")
    print("\nFull ledger net positions (positive = account is owed; negative = account owes):")
    for account_id, amount in snapshot["net_positions_paise_by_account"].items():
        print(f"  {account_id}: ₹{amount/100:.2f}")
    print("\nSuggested transfers:")
    for transfer in snapshot["suggested_transfers"]:
        print(f"  {transfer['from_account']} -> {transfer['to_account']}: ₹{transfer['amount_paise']/100:.2f}")
    print("\nNote:", snapshot["warning"])


def finalize(db: Path) -> None:
    result = finalize_statement(
        db, statement_id=required("Statement ID: "),
        finalized_by=required("Operator identifier: "),
    )
    print("Statement finalized.")
    print("Correction deadline:", result["correction_deadline"])


def main() -> int:
    parser = argparse.ArgumentParser(description="Apartment Expense Manager — ledger workflow")
    parser.add_argument("--db", required=True)
    parser.add_argument("--apartment", required=True)
    args = parser.parse_args()
    db = Path(args.db)
    if not db.exists():
        print("Database not found. Run apartment_setup_cli.py first.")
        return 2
    initialize_database(db)
    with sqlite3.connect(db) as conn:
        if not conn.execute("SELECT 1 FROM apartment WHERE apartment_id=?", (args.apartment,)).fetchone():
            print("Apartment not found.")
            return 2

    actions = {
        "1": ("Create expense definition", lambda: add_definition(db, args.apartment)),
        "2": ("Create monthly statement", lambda: add_statement(db, args.apartment)),
        "3": ("Record general expense", lambda: add_expense(db, args.apartment)),
        "4": ("Record payment", lambda: add_payment(db, args.apartment)),
        "5": ("Preview statement", lambda: preview_statement(db)),
        "6": ("Finalize statement", lambda: finalize(db)),
        "7": ("Close expired statements", lambda: print("Closed:", ", ".join(close_expired_statements(db)) or "none")),
        "8": ("Record signed ledger adjustment", lambda: add_adjustment(db, args.apartment)),
    }
    while True:
        print("\nApartment Expense Manager")
        for key, (label, _) in actions.items():
            print(f"  {key}. {label}")
        print("  0. Exit")
        choice = input("> ").strip()
        if choice == "0":
            return 0
        if choice not in actions:
            print("Choose one of the listed options.")
            continue
        try:
            actions[choice][1]()
        except (LedgerWorkflowError, ValueError, sqlite3.Error) as exc:
            print("Operation failed:", exc)


if __name__ == "__main__":
    raise SystemExit(main())

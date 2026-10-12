"""Interactive terminal entry for an electricity bill.

Usage:
  python electricity_entry_cli.py --db apartment.sqlite --apartment home

The database and apartment/residents must already exist. The wizard collects
bill information and smart-plug readings, then calls the transactional workflow.
"""
from __future__ import annotations

import argparse
import sqlite3
from decimal import Decimal, InvalidOperation
from pathlib import Path

from electricity_workflow import (
    ElectricityWorkflowError,
    create_electricity_expense,
    get_electricity_expense,
    initialize_database,
)


def rupees_to_paise(prompt: str) -> int:
    while True:
        raw = input(prompt).strip().replace(",", "")
        try:
            amount = Decimal(raw)
        except (InvalidOperation, ValueError):
            print("Enter a valid rupee amount, e.g. 1250.50.")
            continue
        if not amount.is_finite() or amount < 0 or amount.as_tuple().exponent < -2:
            print("Amount must be non-negative and have at most two decimal places.")
            continue
        return int(amount * 100)


def required(prompt: str) -> str:
    while True:
        value = input(prompt).strip()
        if value:
            return value
        print("This field is required.")


def choose(prompt: str, options: list[str], allow_blank: bool = False) -> str | None:
    while True:
        print(prompt)
        for index, option in enumerate(options, 1):
            print(f"  {index}. {option}")
        if allow_blank:
            print("  Enter = none")
        raw = input("> ").strip()
        if allow_blank and not raw:
            return None
        try:
            index = int(raw)
            if 1 <= index <= len(options):
                return options[index - 1]
        except ValueError:
            pass
        print("Choose one of the listed numbers.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Enter and save an apartment electricity bill.")
    parser.add_argument("--db", required=True, help="Path to the SQLite database")
    parser.add_argument("--apartment", required=True, help="Existing apartment ID")
    args = parser.parse_args()
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Database not found: {db_path}")
        print("Initialize the database and add the apartment/residents before using this wizard.")
        return 2
    initialize_database(db_path)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        apartment = conn.execute(
            "SELECT name FROM apartment WHERE apartment_id = ?", (args.apartment,)
        ).fetchone()
        if not apartment:
            print(f"Apartment ID {args.apartment!r} was not found.")
            return 2
        residents = conn.execute(
            "SELECT resident_id, display_name FROM resident WHERE apartment_id = ? ORDER BY display_name",
            (args.apartment,),
        ).fetchall()
    if not residents:
        print("No residents are registered for this apartment.")
        return 2

    print(f"\nElectricity bill entry — {apartment['name']}")
    print("Money is entered in rupees and stored as integer paise.")
    expense_id = required("Unique expense ID (e.g. electricity-2026-10): ")
    description = required("Description: ")
    expense_date = required("Bill/transaction date (YYYY-MM-DD): ")
    expense_period = required("Expense period (YYYY-MM): ")
    provider = input("Provider name [Electricity provider]: ").strip() or "Electricity provider"
    total_paise = rupees_to_paise("Actual bill total (₹): ")

    print("\nWho paid the provider bill?")
    payer_choice = choose(
        "Select payer:",
        [f"{r['display_name']} [{r['resident_id']}]" for r in residents],
        allow_blank=True,
    )
    payer_id = None
    if payer_choice:
        payer_id = payer_choice.rsplit("[", 1)[1][:-1]

    component_count_raw = input("Number of itemized bill components [1 = whole bill]: ").strip() or "1"
    try:
        component_count = int(component_count_raw)
        if component_count < 1:
            raise ValueError
    except ValueError:
        print("Component count must be a positive integer.")
        return 2

    components = []
    if component_count == 1:
        components.append({
            "id": "bill-total",
            "name": "Total electricity bill",
            "amount_paise": total_paise,
            "allocation_method": choose("Allocation method:", [
                "usage — proportional to smart-plug kWh",
                "equal — equal split",
                "custom — enter exact resident amounts",
            ]),
        })
    else:
        print("\nEnter each component amount. Component amounts must add up exactly to the bill total.")
        for i in range(component_count):
            print(f"\nComponent {i + 1}")
            name = required("Component name: ")
            amount = rupees_to_paise("Component amount (₹): ")
            method_label = choose("Allocation method:", [
                "usage — proportional to smart-plug kWh",
                "equal — equal split",
                "custom — enter exact resident amounts",
            ])
            components.append({
                "id": f"component-{i + 1}",
                "name": name,
                "amount_paise": amount,
                "allocation_method": method_label.split(" — ", 1)[0],
            })
    if component_count == 1:
        components[0]["allocation_method"] = components[0]["allocation_method"].split(" — ", 1)[0]

    for component in components:
        if component["allocation_method"] == "custom":
            print(f"\nCustom allocation for {component['name']} (must sum to ₹{component['amount_paise']/100:.2f})")
            component["allocations_paise"] = {
                r["resident_id"]: rupees_to_paise(f"  {r['display_name']} amount (₹): ")
                for r in residents
            }

    readings = []
    print("\nEnter smart-plug kWh readings. Guest usage should be included under the host resident.")
    for r in residents:
        while True:
            raw = input(f"  {r['display_name']} [{r['resident_id']}] kWh (0 if none): ").strip() or "0"
            try:
                kwh = Decimal(raw)
                if not kwh.is_finite() or kwh < 0 or (kwh * 1000) != (kwh * 1000).to_integral_value():
                    raise ValueError
                break
            except (InvalidOperation, ValueError):
                print("Enter non-negative kWh with up to three decimal places.")
        readings.append({
            "resident_id": r["resident_id"],
            "kwh": str(kwh),
            "device_label": input("    Device label [Smart plug]: ").strip() or "Smart plug",
            "period_start": expense_date,
            "period_end": expense_date,
        })

    try:
        result = create_electricity_expense(
            db_path,
            apartment_id=args.apartment,
            expense_id=expense_id,
            description=description,
            expense_date=expense_date,
            expense_period=expense_period,
            total_amount_paise=total_paise,
            smart_plug_readings=readings,
            components=components,
            payer_resident_id=payer_id,
            provider_name=provider,
        )
    except (ElectricityWorkflowError, ValueError) as exc:
        print(f"\nExpense was not saved: {exc}")
        return 1

    saved = get_electricity_expense(db_path, expense_id)
    print("\nSaved successfully. Resident allocations:")
    for allocation in result.allocations:
        display = next((r["display_name"] for r in residents if r["resident_id"] == allocation.resident_id),
                       allocation.resident_id)
        print(f"  {display}: ₹{allocation.amount_paise / 100:.2f}")
    print(f"Total: ₹{result.total_amount_paise / 100:.2f}")
    print(f"Saved {len(saved['components'])} component(s), {len(saved['readings'])} reading(s), "
          f"and {len(saved['allocations'])} allocation row(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

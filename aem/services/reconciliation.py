"""Full household reconciliation.

One auditable calculation covering every recorded transaction across every
month. It works by turning the records into a flat list of signed **ledger
entries** against named accounts -- one account per resident, plus the
synthetic ``__pool__`` account that stands for cash held by or owed to the
collection pool. Every entry names the record it came from, so any number on
the dashboard can be traced back to a row.

Sign convention, for residents and for the pool alike:

* **positive** -- this account is owed money (the pool owes a resident; or,
  for ``__pool__``, residents owe the pool in aggregate);
* **negative** -- this account owes money.

By construction every event writes equal and opposite entries, so the accounts
always sum to zero. The sum is asserted anyway: if it is ever non-zero that is
a bug, and the calculation refuses to report figures rather than papering over
it.

What is *not* double counted
----------------------------

An expense, the resident share of it, and the funding of it are three distinct
entries, not three independent expenses:

1. the expense debits each resident's share and credits the pool;
2. the funding credits whoever actually paid and debits the pool;
3. a payment into the pool credits the payer and debits the pool.

Funding is read from exactly one source per expense. If any ``provider_advance``
payment is linked to the expense, the linked advances are the funding record
and ``expense_instance.payer_resident_id`` is ignored for that expense. Only
when no advance is linked does the payer field act as the funding record. That
rule is what stops the expense payer and the advance being credited twice.

Reversed payments and their mirrored reversal rows are both excluded, so a
reversal nets to nothing while both rows stay in the audit trail.
"""
from __future__ import annotations

from typing import Any

from ..engine.ledger import net_settlement_transfers
from ..errors import ReconciliationError, ValidationError
from ..money import format_inr
from ..periods import normalize_month, parse_iso_date
from ..persistence import Database, UnitOfWork
from .common import POOL_ACCOUNT_ID, get_apartment, list_residents, resident_ids
from .validation import SEVERITY_ERROR, validate_apartment

# Entry kinds, in the order the audit view presents them.
ENTRY_EXPENSE_SHARE = "expense_share"
ENTRY_EXPENSE_INCURRED = "expense_incurred"
ENTRY_EXPENSE_FUNDED = "expense_funded_by_payer"
ENTRY_PROVIDER_ADVANCE = "provider_advance"
ENTRY_POOL_PAYMENT = "pool_payment"
ENTRY_REFUND = "refund_to_resident"
ENTRY_LEGACY_PAYMENT_ADJUSTMENT = "legacy_payment_adjustment"
ENTRY_ADJUSTMENT = "ledger_adjustment"
ENTRY_OPENING_BALANCE = "opening_balance"


def _entry(account: str, amount_paise: int, kind: str, *, source_type: str,
           source_id: str, entry_date: str | None, description: str,
           settlement_period: str | None = None,
           counter_account: str | None = None) -> dict[str, Any]:
    return {"account_id": account, "amount_paise": amount_paise, "kind": kind,
            "source_type": source_type, "source_id": source_id, "date": entry_date,
            "description": description, "settlement_period": settlement_period,
            "counter_account_id": counter_account}


def build_ledger_entries(uow: UnitOfWork, apartment_id: str, *,
                         as_of_date: str | None = None) -> list[dict[str, Any]]:
    """Turn every record for one apartment into signed ledger entries."""
    residents = set(resident_ids(uow, apartment_id))
    entries: list[dict[str, Any]] = []

    expenses = uow.reconciliation.expenses_for_ledger(apartment_id, as_of_date)

    # Expenses whose funding is recorded as a linked provider advance. For these
    # the payer field is deliberately ignored, so the same money is credited once.
    linked_advance_totals = {
        row["expense_id"]: int(row["linked_paise"])
        for row in uow.reconciliation.linked_advance_totals(apartment_id, as_of_date)}

    for expense in expenses:
        expense_id = expense["expense_id"]
        allocations = uow.reconciliation.allocations_for_expense(expense_id, apartment_id)
        allocated = sum(row["amount_paise"] for row in allocations)
        if allocated != expense["amount_paise"]:
            raise ReconciliationError(
                f"Expense {expense_id} allocations do not reconcile to its total.",
                code="allocation_total_mismatch",
                details={"expense_id": expense_id,
                         "amount_paise": expense["amount_paise"],
                         "allocated_paise": allocated})
        period = expense["settlement_period"] or expense["expense_period"]
        for row in allocations:
            entries.append(_entry(
                row["resident_id"], -row["amount_paise"], ENTRY_EXPENSE_SHARE,
                source_type="allocation", source_id=row["allocation_id"],
                entry_date=expense["expense_date"],
                description=f"Share of {expense['description']}",
                settlement_period=period, counter_account=POOL_ACCOUNT_ID))
        entries.append(_entry(
            POOL_ACCOUNT_ID, expense["amount_paise"], ENTRY_EXPENSE_INCURRED,
            source_type="expense", source_id=expense_id,
            entry_date=expense["expense_date"],
            description=f"Household expense: {expense['description']}",
            settlement_period=period))

        payer = expense["payer_resident_id"]
        if payer and payer in residents and expense_id not in linked_advance_totals:
            entries.append(_entry(
                payer, expense["amount_paise"], ENTRY_EXPENSE_FUNDED,
                source_type="expense", source_id=expense_id,
                entry_date=expense["expense_date"],
                description=f"Funded {expense['description']}",
                settlement_period=period, counter_account=POOL_ACCOUNT_ID))
            entries.append(_entry(
                POOL_ACCOUNT_ID, -expense["amount_paise"], ENTRY_EXPENSE_FUNDED,
                source_type="expense", source_id=expense_id,
                entry_date=expense["expense_date"],
                description=f"{expense['description']} funded by {payer}",
                settlement_period=period, counter_account=payer))

    payments = uow.reconciliation.active_payments_for_ledger(apartment_id, as_of_date)

    for payment in payments:
        rid = payment["payer_resident_id"]
        amount = payment["amount_paise"]
        kind = payment["payment_kind"]
        period = payment["payment_date"][:7] if payment["payment_date"] else None
        if kind == "provider_advance":
            entry_kind, sign, text = ENTRY_PROVIDER_ADVANCE, 1, (
                f"Paid {payment['payee_or_provider'] or 'provider'} on the household's behalf")
        elif kind == "to_pool":
            entry_kind, sign, text = ENTRY_POOL_PAYMENT, 1, "Payment into the household pool"
        elif kind == "refund":
            entry_kind, sign, text = ENTRY_REFUND, -1, "Refund paid out of the household pool"
        else:  # 'adjustment' -- the prototype's extra-amount-due record
            entry_kind, sign, text = ENTRY_LEGACY_PAYMENT_ADJUSTMENT, -1, (
                "Legacy payment adjustment (amount due from resident)")
        entries.append(_entry(
            rid, sign * amount, entry_kind, source_type="payment",
            source_id=payment["payment_id"], entry_date=payment["payment_date"],
            description=text, settlement_period=period, counter_account=POOL_ACCOUNT_ID))
        entries.append(_entry(
            POOL_ACCOUNT_ID, -sign * amount, entry_kind, source_type="payment",
            source_id=payment["payment_id"], entry_date=payment["payment_date"],
            description=f"{text} ({rid})", settlement_period=period, counter_account=rid))

    adjustments = uow.reconciliation.adjustments_for_ledger(apartment_id, as_of_date)

    for adjustment in adjustments:
        rid = adjustment["resident_id"]
        amount = adjustment["amount_paise"]
        effective = adjustment["effective_date"] or adjustment["created_at"][:10]
        entries.append(_entry(
            rid, amount, ENTRY_ADJUSTMENT, source_type="ledger_adjustment",
            source_id=adjustment["adjustment_id"], entry_date=effective,
            description=f"Adjustment: {adjustment['reason']}",
            settlement_period=effective[:7], counter_account=POOL_ACCOUNT_ID))
        entries.append(_entry(
            POOL_ACCOUNT_ID, -amount, ENTRY_ADJUSTMENT, source_type="ledger_adjustment",
            source_id=adjustment["adjustment_id"], entry_date=effective,
            description=f"Adjustment for {rid}: {adjustment['reason']}",
            settlement_period=effective[:7], counter_account=rid))

    # What each resident owed, or was owed, before the household started
    # recording here. Signed like every other entry, with the pool opposite.
    opening_balances = uow.reconciliation.opening_balances_for_ledger(apartment_id, as_of_date)

    for opening in opening_balances:
        rid = opening["resident_id"]
        amount = opening["amount_paise"]
        text = ("Opening balance: owed by the resident" if amount < 0
                else "Opening balance: owed to the resident")
        entries.append(_entry(
            rid, amount, ENTRY_OPENING_BALANCE, source_type="opening_balance",
            source_id=opening["opening_balance_id"], entry_date=opening["as_of_date"],
            description=text, settlement_period=opening["as_of_date"][:7],
            counter_account=POOL_ACCOUNT_ID))
        entries.append(_entry(
            POOL_ACCOUNT_ID, -amount, ENTRY_OPENING_BALANCE, source_type="opening_balance",
            source_id=opening["opening_balance_id"], entry_date=opening["as_of_date"],
            description=f"{text} ({rid})", settlement_period=opening["as_of_date"][:7],
            counter_account=rid))

    return entries


def _settlement_view(uow: UnitOfWork, apartment_id: str, *,
                     as_of_date: str | None) -> dict[str, dict[str, int]]:
    """Charge-level view: what is charged, what is settled, what is left over.

    This is a second, independent reading of the same records -- by charge and
    by payment application rather than by net position -- and the two are
    cross-checked against each other in :func:`reconcile`.
    """
    residents = resident_ids(uow, apartment_id)
    view = {rid: {"allocated_charges_paise": 0, "settled_paise": 0,
                  "outstanding_charges_paise": 0, "unapplied_credit_paise": 0,
                  "opening_debt_paise": 0, "opening_debt_outstanding_paise": 0,
                  "opening_credit_paise": 0}
            for rid in residents}

    for row in uow.reconciliation.charges_with_settled(apartment_id, as_of_date):
        bucket = view.setdefault(row["resident_id"], {
            "allocated_charges_paise": 0, "settled_paise": 0,
            "outstanding_charges_paise": 0, "unapplied_credit_paise": 0,
            "opening_debt_paise": 0, "opening_debt_outstanding_paise": 0,
            "opening_credit_paise": 0})
        bucket["allocated_charges_paise"] += row["amount_paise"]
        bucket["settled_paise"] += int(row["settled"])
        bucket["outstanding_charges_paise"] += row["amount_paise"] - int(row["settled"])

    # An opening debt is the resident's oldest charge, settled by applications
    # like any other. An opening credit is nothing a payment can settle, so it
    # is carried beside the charges rather than among them.
    for row in uow.reconciliation.opening_balances_with_settled(apartment_id, as_of_date):
        bucket = view.get(row["resident_id"])
        if bucket is None:
            continue
        if row["amount_paise"] > 0:
            bucket["opening_credit_paise"] += row["amount_paise"]
            continue
        debt, settled = -row["amount_paise"], int(row["settled"])
        bucket["allocated_charges_paise"] += debt
        bucket["settled_paise"] += settled
        bucket["outstanding_charges_paise"] += debt - settled
        bucket["opening_debt_paise"] += debt
        bucket["opening_debt_outstanding_paise"] += debt - settled

    for row in uow.reconciliation.unapplied_pool_payments(apartment_id, as_of_date):
        if row["rid"] in view:
            view[row["rid"]]["unapplied_credit_paise"] += int(row["unapplied"])
    return view


def reconcile(db: Database, *, apartment_id: str, as_of_date: str | None = None,
              require_clean: bool = False) -> dict[str, Any]:
    """Reconcile the full apartment ledger into account positions and transfers.

    ``as_of_date`` restricts dated expenses, payments, and adjustments, which is
    how a historical position is reproduced. ``require_clean`` refuses to report
    settlement figures while any ``error``-severity validation issue stands.
    """
    if as_of_date is not None:
        try:
            parse_iso_date(as_of_date, "as_of_date")
        except ValidationError:
            raise ValidationError("as_of_date must use YYYY-MM-DD format.",
                                  details={"field": "as_of_date"}) from None

    with db.read() as uow:
        get_apartment(uow, apartment_id)
        residents = list_residents(uow, apartment_id)
        resident_id_list = sorted(row["resident_id"] for row in residents)
        issues = validate_apartment(uow, apartment_id)
        blocking = [issue for issue in issues if issue["severity"] == SEVERITY_ERROR]
        if blocking and require_clean:
            raise ReconciliationError(
                "The ledger has unresolved accounting errors, so settlement figures are "
                "not being reported. Resolve the listed issues first.",
                code="unreconciled_ledger",
                details={"issues": blocking})

        entries = build_ledger_entries(uow, apartment_id, as_of_date=as_of_date)
        charge_view = _settlement_view(uow, apartment_id, as_of_date=as_of_date)

        positions = {rid: 0 for rid in resident_id_list}
        positions[POOL_ACCOUNT_ID] = 0
        by_kind: dict[str, dict[str, int]] = {}
        for entry in entries:
            account = entry["account_id"]
            positions[account] = positions.get(account, 0) + entry["amount_paise"]
            by_kind.setdefault(account, {}).setdefault(entry["kind"], 0)
            by_kind[account][entry["kind"]] += entry["amount_paise"]

        total = sum(positions.values())
        if total != 0:
            raise ReconciliationError(
                "Internal reconciliation error: pooled positions do not sum to zero.",
                code="pool_not_zero_sum",
                details={"net_positions_sum_paise": total, "positions": positions})

        def kind_total(account: str, kind: str) -> int:
            return by_kind.get(account, {}).get(kind, 0)

        charges = {rid: -kind_total(rid, ENTRY_EXPENSE_SHARE) for rid in resident_id_list}
        payer_funding = {rid: kind_total(rid, ENTRY_EXPENSE_FUNDED) for rid in resident_id_list}
        advances = {rid: kind_total(rid, ENTRY_PROVIDER_ADVANCE) for rid in resident_id_list}
        pool_payments = {rid: kind_total(rid, ENTRY_POOL_PAYMENT) for rid in resident_id_list}
        refunds = {rid: -kind_total(rid, ENTRY_REFUND) for rid in resident_id_list}
        legacy = {rid: -kind_total(rid, ENTRY_LEGACY_PAYMENT_ADJUSTMENT)
                  for rid in resident_id_list}
        adjustment_net = {rid: kind_total(rid, ENTRY_ADJUSTMENT) for rid in resident_id_list}
        opening = {rid: kind_total(rid, ENTRY_OPENING_BALANCE) for rid in resident_id_list}
        provider_credits = {rid: payer_funding[rid] + advances[rid] for rid in resident_id_list}

        # Cross-check the two independent readings of the same records against
        # each other. They must agree exactly:
        #   net = unapplied credit - outstanding charges + funding + adjustments
        #         - refunds - legacy adjustments + opening credit
        # where an opening debt is one of the outstanding charges.
        cross_check: list[dict[str, Any]] = []
        for rid in resident_id_list:
            view = charge_view.get(rid, {})
            expected = (view.get("unapplied_credit_paise", 0)
                        - view.get("outstanding_charges_paise", 0)
                        + provider_credits[rid] + adjustment_net[rid]
                        - refunds[rid] - legacy[rid]
                        + view.get("opening_credit_paise", 0))
            if expected != positions[rid]:
                cross_check.append({"resident_id": rid, "net_position_paise": positions[rid],
                                    "charge_view_paise": expected,
                                    "difference_paise": positions[rid] - expected})
        if cross_check:
            raise ReconciliationError(
                "The net-position view and the charge-level view of the same records "
                "disagree. This is a data integrity problem, not a rounding difference.",
                code="views_disagree", details={"residents": cross_check})

        names = {row["resident_id"]: row["display_name"] for row in residents}
        account_rows: list[dict[str, Any]] = []
        for rid in resident_id_list:
            view = charge_view.get(rid, {})
            account_rows.append({
                "account_id": rid,
                "account_type": "resident",
                "display_name": names.get(rid, rid),
                "is_active": bool(next((r["is_active"] for r in residents
                                        if r["resident_id"] == rid), 1)),
                "allocated_charges_paise": charges[rid],
                "settled_paise": view.get("settled_paise", 0),
                "outstanding_charges_paise": view.get("outstanding_charges_paise", 0),
                "unapplied_credit_paise": view.get("unapplied_credit_paise", 0),
                "expense_funding_paise": payer_funding[rid],
                "provider_advances_paise": advances[rid],
                "funding_credits_paise": provider_credits[rid],
                "pool_payments_paise": pool_payments[rid],
                "refunds_paise": refunds[rid],
                "legacy_payment_adjustments_paise": legacy[rid],
                "adjustments_paise": adjustment_net[rid],
                "opening_balance_paise": opening[rid],
                "opening_debt_outstanding_paise": view.get(
                    "opening_debt_outstanding_paise", 0),
                "net_position_paise": positions[rid],
            })
        account_rows.append({
            "account_id": POOL_ACCOUNT_ID,
            "account_type": "pool",
            "display_name": "Household pool",
            "is_active": True,
            "net_position_paise": positions[POOL_ACCOUNT_ID],
            "expenses_incurred_paise": by_kind.get(POOL_ACCOUNT_ID, {}).get(
                ENTRY_EXPENSE_INCURRED, 0),
            "cash_received_paise": -by_kind.get(POOL_ACCOUNT_ID, {}).get(
                ENTRY_POOL_PAYMENT, 0),
            "cash_paid_out_paise": by_kind.get(POOL_ACCOUNT_ID, {}).get(ENTRY_REFUND, 0),
            "funded_by_residents_paise": -(
                by_kind.get(POOL_ACCOUNT_ID, {}).get(ENTRY_EXPENSE_FUNDED, 0)
                + by_kind.get(POOL_ACCOUNT_ID, {}).get(ENTRY_PROVIDER_ADVANCE, 0)),
        })

        transfers = net_settlement_transfers(positions)
        transfer_rows = [
            {"from_account": debtor, "to_account": creditor, "amount_paise": amount,
             "from_display_name": names.get(debtor, "Household pool"),
             "to_display_name": names.get(creditor, "Household pool")}
            for debtor, creditor, amount in transfers
        ]
        # A transfer plan must never move more than the position it settles.
        for row in transfer_rows:
            if row["amount_paise"] > -positions[row["from_account"]] \
                    or row["amount_paise"] > positions[row["to_account"]]:
                raise ReconciliationError(
                    "A suggested transfer exceeds the balance it settles.",
                    code="transfer_exceeds_balance", details={"transfer": row})

        period_totals = {}
        for entry in entries:
            if entry["kind"] != ENTRY_EXPENSE_INCURRED:
                continue
            period_totals[entry["settlement_period"]] = period_totals.get(
                entry["settlement_period"], 0) + entry["amount_paise"]

        return {
            "apartment_id": apartment_id,
            "as_of_date": as_of_date,
            "pool_account_id": POOL_ACCOUNT_ID,
            # Keys kept stable from the prototype's contract:
            "allocated_charges_paise_by_resident": charges,
            "provider_credits_paise_by_resident": provider_credits,
            "pool_payments_paise_by_resident": pool_payments,
            "adjustment_net_paise_by_resident": adjustment_net,
            "net_positions_paise_by_account": positions,
            "net_positions_sum_paise": total,
            "suggested_transfers": [
                {"from_account": row["from_account"], "to_account": row["to_account"],
                 "amount_paise": row["amount_paise"]} for row in transfer_rows
            ],
            # Richer view added for the application:
            "accounts": account_rows,
            "transfers": transfer_rows,
            "outstanding_charges_paise_by_resident": {
                rid: charge_view.get(rid, {}).get("outstanding_charges_paise", 0)
                for rid in resident_id_list},
            "unapplied_credits_paise_by_resident": {
                rid: charge_view.get(rid, {}).get("unapplied_credit_paise", 0)
                for rid in resident_id_list},
            "expense_funding_paise_by_resident": payer_funding,
            "provider_advances_paise_by_resident": advances,
            "refunds_paise_by_resident": refunds,
            "opening_balances_paise_by_resident": opening,
            "household_expenses_paise_by_settlement_period": dict(sorted(period_totals.items())),
            "totals": {
                "household_expenses_paise": sum(period_totals.values()),
                "allocated_charges_paise": sum(charges.values()),
                "outstanding_charges_paise": sum(
                    charge_view.get(rid, {}).get("outstanding_charges_paise", 0)
                    for rid in resident_id_list),
                "unapplied_credits_paise": sum(
                    charge_view.get(rid, {}).get("unapplied_credit_paise", 0)
                    for rid in resident_id_list),
                "provider_advances_paise": sum(advances.values()),
                "expense_funding_paise": sum(payer_funding.values()),
                "pool_payments_paise": sum(pool_payments.values()),
                "adjustments_paise": sum(adjustment_net.values()),
                "opening_balances_paise": sum(opening.values()),
                "pool_net_position_paise": positions[POOL_ACCOUNT_ID],
            },
            "entry_count": len(entries),
            "validation": {
                "issues": issues,
                "error_count": len(blocking),
                "warning_count": len(issues) - len(blocking),
                "is_reconcilable": not blocking,
            },
            "notes": [
                "Positive means the account is owed money; negative means it owes money.",
                "__pool__ is the collection pool. A positive pool position means residents "
                "owe the pool in aggregate.",
                "A linked provider advance replaces an expense's payer field as the funding "
                "record, so the same payment is never credited twice.",
                "Suggested transfers are advice only. Nothing in this calculation changes a "
                "record.",
            ],
        }


_MOVEMENT_FIELDS = ("opening_balance_paise", "brought_forward_paise",
                    "statement_charges_paise", "unattached_charges_paise",
                    "payments_applied_paise", "provider_advances_paise",
                    "adjustments_paise", "carried_forward_paise")


def _formula_explanation(row: dict[str, Any]) -> str:
    """The working in words, leaving out the terms that are zero this month."""
    parts = [f"{format_inr(row['brought_forward_paise'])} (brought forward)"]
    for sign, key, label in (
            ("-", "statement_charges_paise", "charges"),
            ("-", "unattached_charges_paise", "charges not on this statement"),
            ("+", "payments_applied_paise", "payments"),
            ("+", "provider_advances_paise", "advances"),
            ("+", "adjustments_paise", "adjustments")):
        amount = row[key]
        if amount == 0 and key != "statement_charges_paise":
            continue
        if amount < 0:
            sign, amount = ("+" if sign == "-" else "-"), -amount
        parts.append(f"{sign} {format_inr(amount)} ({label})")
    return (" ".join(parts)
            + f" = {format_inr(row['carried_forward_paise'])} (carried forward)")


def carry_forward(uow: UnitOfWork, apartment_id: str, settlement_month: str, *,
                  as_of_date: str | None = None,
                  statement_charges: dict[str, int] | None = None) -> dict[str, Any]:
    """How each resident's balance moved across one settlement month.

    Every figure is a sum of that resident's own ledger entries, grouped by the
    settlement period each entry belongs to, so for every resident::

        brought forward - charges + payments + advances + adjustments
            = carried forward

    holds to the paisa and the carried-forward figure of one month is the
    brought-forward figure of the next. Balances keep the ledger's sign: positive
    is owed to the resident, negative is owed by them.

    * An opening balance is where the history starts, so it is part of what is
      brought forward into the month it is dated in, not a movement within it.
    * ``statement_charges`` are the shares on the statement itself; a charge that
      settles in this month without being attached to the statement is reported
      apart, as ``unattached_charges_paise``, so the working still closes.
    * Payments are what the resident paid into the pool in the month, less any
      refund paid back to them. Advances are what they paid a provider on the
      household's behalf, whether recorded as an advance or as an expense's payer.
    """
    statement_charges = statement_charges or {}
    names = {row["resident_id"]: row["display_name"]
             for row in list_residents(uow, apartment_id)}
    rows = {rid: {"resident_id": rid, "display_name": names[rid],
                  **{field: 0 for field in _MOVEMENT_FIELDS},
                  "statement_charges_paise": int(statement_charges.get(rid, 0))}
            for rid in sorted(names, key=lambda rid: (names[rid], rid))}
    charged = {rid: 0 for rid in rows}

    for entry in build_ledger_entries(uow, apartment_id, as_of_date=as_of_date):
        row = rows.get(entry["account_id"])
        if row is None:
            continue
        period = entry["settlement_period"] or (entry["date"] or "")[:7]
        if period > settlement_month:
            continue
        amount, kind = entry["amount_paise"], entry["kind"]
        row["carried_forward_paise"] += amount
        if kind == ENTRY_OPENING_BALANCE:
            row["opening_balance_paise"] += amount
            row["brought_forward_paise"] += amount
        elif period < settlement_month:
            row["brought_forward_paise"] += amount
        elif kind == ENTRY_EXPENSE_SHARE:
            charged[entry["account_id"]] -= amount
        elif kind == ENTRY_ADJUSTMENT:
            row["adjustments_paise"] += amount
        elif kind in (ENTRY_PROVIDER_ADVANCE, ENTRY_EXPENSE_FUNDED):
            row["provider_advances_paise"] += amount
        else:  # pool payments, less refunds and legacy amounts due
            row["payments_applied_paise"] += amount

    for rid, row in rows.items():
        row["unattached_charges_paise"] = charged[rid] - row["statement_charges_paise"]
        row["formula_explanation"] = _formula_explanation(row)
    items = list(rows.values())
    return {
        "settlement_month": settlement_month,
        "residents": items,
        "totals": {field: sum(item[field] for item in items) for field in _MOVEMENT_FIELDS},
    }


def ledger_entries(db: Database, *, apartment_id: str, as_of_date: str | None = None,
                   account_id: str | None = None, kind: str | None = None,
                   settlement_period: str | None = None, limit: int = 500,
                   offset: int = 0) -> dict[str, Any]:
    """The reconciliation's own entries, for auditing a number back to its record."""
    if settlement_period:
        settlement_period = normalize_month(settlement_period, "settlement_period")
    with db.read() as uow:
        get_apartment(uow, apartment_id)
        entries = build_ledger_entries(uow, apartment_id, as_of_date=as_of_date)
    if account_id:
        entries = [e for e in entries if e["account_id"] == account_id]
    if kind:
        entries = [e for e in entries if e["kind"] == kind]
    if settlement_period:
        entries = [e for e in entries if e["settlement_period"] == settlement_period]
    entries.sort(key=lambda e: (e["date"] or "", e["kind"], e["source_id"], e["account_id"]))
    total = len(entries)
    return {"items": entries[offset:offset + limit], "total": total,
            "limit": limit, "offset": offset,
            "sum_paise": sum(e["amount_paise"] for e in entries)}


# Prototype-compatible entry point. The richer `reconcile` is the same
# calculation; this name and its return keys are preserved for existing callers.
def reconcile_pooled_settlement(db: Database, *, apartment_id: str,
                                as_of_date: str | None = None) -> dict[str, Any]:
    """Reconcile the full apartment ledger into resident and pool positions.

    Positive means the pool owes the account; negative means the account owes
    the pool. The synthetic __pool__ account ensures the result is zero-sum.
    """
    return reconcile(db, apartment_id=apartment_id, as_of_date=as_of_date)

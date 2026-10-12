"""Pure accounting primitives: splits, payment allocation, pooled settlement.

All monetary values are integer paise. No database or UI dependencies, and no
floating-point arithmetic anywhere in this module.

This is the original prototype engine, kept as the source of truth for the
financial rules. Additions are documented where they appear.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


class PeriodRule(str, Enum):
    PREVIOUS_MONTH = "previous_month"
    CURRENT_MONTH = "current_month"
    TRANSACTION_DATE = "transaction_date"


class StatementStatus(str, Enum):
    DRAFT = "draft"
    FINALIZED_CORRECTION_WINDOW = "finalized_correction_window"
    CLOSED = "closed"


@dataclass(frozen=True)
class Charge:
    charge_id: str
    resident_id: str
    amount_paise: int
    due_date: date
    description: str
    statement_id: Optional[str] = None
    sequence: int = 0
    """Stable creation order within one due date.

    Oldest-first allocation orders by due date first. When two charges share a
    due date -- normal, because charges settled in the same month share one --
    `sequence` decides, and only then the charge ID. Callers pass the database
    insertion order so the result never depends on a random identifier.
    """

    def __post_init__(self):
        if self.amount_paise < 0:
            raise ValueError("Charge amount cannot be negative.")


@dataclass
class PaymentApplication:
    charge_id: str
    amount_paise: int


@dataclass
class PaymentAllocationResult:
    applications: List[PaymentApplication]
    unapplied_credit_paise: int


@dataclass
class Statement:
    statement_id: str
    settlement_month: str
    status: StatementStatus = StatementStatus.DRAFT
    finalized_at: Optional[datetime] = None
    correction_deadline: Optional[datetime] = None
    snapshot: Optional[dict] = None
    revisions: List[dict] = field(default_factory=list)


def equal_split(
    total_paise: int,
    participants: Sequence[str],
    payer_id: Optional[str] = None,
) -> Dict[str, int]:
    """Split an amount equally, giving unavoidable remainder paise to non-payers first.

    Participant order is the deterministic tie-break order. A payer is assigned
    the base share before any remainder is distributed. If there are no
    non-payers, the payer necessarily receives the remainder.
    """
    if total_paise < 0:
        raise ValueError("Total cannot be negative.")
    if not participants:
        if total_paise == 0:
            return {}
        raise ValueError("At least one participant is required for a non-zero total.")
    if len(set(participants)) != len(participants):
        raise ValueError("Participants must be unique.")

    base, remainder = divmod(total_paise, len(participants))
    result = {resident_id: base for resident_id in participants}
    remainder_order = [p for p in participants if p != payer_id] + [
        p for p in participants if p == payer_id
    ]
    for resident_id in remainder_order[:remainder]:
        result[resident_id] += 1
    return result


def weighted_split(total_paise: int, weights: Mapping[str, int]) -> Dict[str, int]:
    """Split by non-negative integer weights using the largest-remainder method."""
    if total_paise < 0:
        raise ValueError("Total cannot be negative.")
    if not weights:
        if total_paise == 0:
            return {}
        raise ValueError("At least one weight is required.")
    if any(weight < 0 for weight in weights.values()):
        raise ValueError("Weights cannot be negative.")
    weight_total = sum(weights.values())
    if weight_total <= 0:
        raise ValueError("At least one weight must be positive.")

    floors: Dict[str, int] = {}
    remainders: List[Tuple[int, str]] = []
    for resident_id, weight in weights.items():
        numerator = total_paise * weight
        floors[resident_id], remainder = divmod(numerator, weight_total)
        remainders.append((remainder, resident_id))

    left = total_paise - sum(floors.values())
    # Descending remainder; stable resident ID tie-break.
    order = sorted(remainders, key=lambda item: (-item[0], item[1]))
    for _, resident_id in order[:left]:
        floors[resident_id] += 1
    return floors


def custom_allocation(total_paise: int, shares: Mapping[str, int]) -> Dict[str, int]:
    """Validate explicit allocations."""
    if total_paise < 0:
        raise ValueError("Total cannot be negative.")
    if any(amount < 0 for amount in shares.values()):
        raise ValueError("Shares cannot be negative.")
    if sum(shares.values()) != total_paise:
        raise ValueError("Custom shares must sum exactly to the expense total.")
    return dict(shares)


def host_guest_electricity(host_id: str, guest_cost_paise: int) -> Dict[str, int]:
    """Assign guest electricity to the host, never to a guest account."""
    if guest_cost_paise < 0:
        raise ValueError("Guest electricity cost cannot be negative.")
    return {host_id: guest_cost_paise}


def apply_payment_oldest_first(
    payment_paise: int,
    charges: Sequence[Charge],
    already_applied: Optional[Mapping[str, int]] = None,
) -> PaymentAllocationResult:
    """Apply a resident's payment to their charges by due date, then charge ID.

    Pass only the charges belonging to the resident making the payment.
    Any excess remains as an unapplied credit.

    Ordering is (due date, sequence, charge ID), which is total and therefore
    deterministic: the same inputs always produce the same applications.
    """
    if payment_paise < 0:
        raise ValueError("Payment cannot be negative.")
    already_applied = dict(already_applied or {})
    if any(value < 0 for value in already_applied.values()):
        raise ValueError("Applied amounts cannot be negative.")

    remaining = payment_paise
    applications: List[PaymentApplication] = []
    ordered = sorted(charges, key=lambda c: (c.due_date, c.sequence, c.charge_id))
    for charge in ordered:
        previously = already_applied.get(charge.charge_id, 0)
        if previously > charge.amount_paise:
            raise ValueError(f"Applied amount exceeds charge {charge.charge_id}.")
        open_amount = charge.amount_paise - previously
        applied = min(open_amount, remaining)
        if applied:
            applications.append(PaymentApplication(charge.charge_id, applied))
            remaining -= applied
        if remaining == 0:
            break
    return PaymentAllocationResult(applications, remaining)


def pooled_net_positions(
    allocated_charges: Mapping[str, int],
    provider_advances: Mapping[str, int],
    other_credits: Optional[Mapping[str, int]] = None,
    other_debits: Optional[Mapping[str, int]] = None,
) -> Dict[str, int]:
    """Calculate pooled net positions in paise.

    Positive = household owes resident.
    Negative = resident owes the pool.

    `allocated_charges` are resident expense shares.
    `provider_advances` are amounts residents paid directly to external providers
    on behalf of the household. They must not be counted as new expenses.
    `other_credits` and `other_debits` are explicit ledger entries, not duplicate
    representations of provider advances or expense charges.
    """
    other_credits = dict(other_credits or {})
    other_debits = dict(other_debits or {})
    residents = set(allocated_charges) | set(provider_advances) | set(other_credits) | set(other_debits)
    result = {}
    for resident_id in residents:
        charges = allocated_charges.get(resident_id, 0)
        advances = provider_advances.get(resident_id, 0)
        credits = other_credits.get(resident_id, 0)
        debits = other_debits.get(resident_id, 0)
        if min(charges, advances, credits, debits) < 0:
            raise ValueError("Ledger amounts must be non-negative.")
        result[resident_id] = advances + credits - charges - debits
    return result


def net_settlement_transfers(net_positions: Mapping[str, int]) -> List[Tuple[str, str, int]]:
    """Create a deterministic greedy transfer plan.

    Returns (debtor_id, creditor_id, amount_paise). This minimizes the number
    of transfers for the greedy order but does not promise a globally minimal
    transfer count for every possible input.
    """
    if sum(net_positions.values()) != 0:
        raise ValueError("Pooled net positions must sum to zero before settlement.")
    debtors = [[rid, -amount] for rid, amount in sorted(net_positions.items()) if amount < 0]
    creditors = [[rid, amount] for rid, amount in sorted(net_positions.items()) if amount > 0]
    transfers: List[Tuple[str, str, int]] = []
    i = j = 0
    while i < len(debtors) and j < len(creditors):
        amount = min(debtors[i][1], creditors[j][1])
        if amount:
            transfers.append((debtors[i][0], creditors[j][0], amount))
        debtors[i][1] -= amount
        creditors[j][1] -= amount
        if debtors[i][1] == 0:
            i += 1
        if creditors[j][1] == 0:
            j += 1
    return transfers


def finalize_statement(statement: Statement, now: datetime, snapshot: dict) -> Statement:
    """Finalize a draft and start a 72-hour correction window."""
    if statement.status != StatementStatus.DRAFT:
        raise ValueError("Only draft statements can be finalized.")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Finalization time must be timezone-aware.")
    statement.status = StatementStatus.FINALIZED_CORRECTION_WINDOW
    statement.finalized_at = now
    statement.correction_deadline = now + timedelta(hours=72)
    statement.snapshot = snapshot.copy()
    statement.revisions.append({
        "timestamp": now.isoformat(),
        "reason": "initial_finalization",
        "snapshot": snapshot.copy(),
    })
    return statement


def revise_statement(statement: Statement, now: datetime, reason: str, snapshot: dict) -> Statement:
    """Revise a statement only during its correction window."""
    if statement.status != StatementStatus.FINALIZED_CORRECTION_WINDOW:
        raise ValueError("Statement is not in its correction window.")
    if statement.correction_deadline is None or now > statement.correction_deadline:
        statement.status = StatementStatus.CLOSED
        raise ValueError("Correction window has expired.")
    if not reason.strip():
        raise ValueError("A revision reason is required.")
    statement.snapshot = snapshot.copy()
    statement.revisions.append({
        "timestamp": now.isoformat(),
        "reason": reason,
        "snapshot": snapshot.copy(),
    })
    return statement


def close_expired_statement(statement: Statement, now: datetime) -> Statement:
    if statement.status == StatementStatus.FINALIZED_CORRECTION_WINDOW:
        if statement.correction_deadline is not None and now >= statement.correction_deadline:
            statement.status = StatementStatus.CLOSED
    return statement

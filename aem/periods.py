"""Accounting period rules.

Two distinct periods are tracked for every expense, because conflating them is
the single most common source of double counting:

`service_period`
    The month the money was *for*. September's electricity consumption has a
    September service period even though the bill arrives in October.

`settlement_period`
    The month whose statement *charges* the expense to residents. That is the
    month the operator settles up in.

A definition's `period_rule` maps a transaction date to those two periods:

====================  ==================  =====================
period_rule           service_period      settlement_period
====================  ==================  =====================
``current_month``     month(txn date)     month(txn date)
``previous_month``    month(txn date) − 1 month(txn date)
``transaction_date``  month(txn date)     month(txn date)
``custom``            operator-supplied   operator-supplied
====================  ==================  =====================

``current_month`` suits rent, broadband, maintenance, and furniture rental:
October's rent is charged in the October statement. ``previous_month`` suits
electricity and groceries: the September bill paid in early October is charged
in the October statement, and its service period stays September so
consumption reports remain truthful.

``transaction_date`` behaves like ``current_month`` for period selection; it
exists to record that the transaction date itself — not a month boundary — is
the authoritative marker for the charge (one-off purchases, repairs).

Every rule can be overridden per expense, and ``custom`` requires an explicit
period, so the operator is never forced into a rule that misstates the facts.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date

from .errors import ValidationError

PERIOD_RULES = ("previous_month", "current_month", "transaction_date", "custom")

_MONTH_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")


def parse_month(value: str, label: str = "period") -> tuple[int, int]:
    """Parse a YYYY-MM string into a (year, month) pair."""
    if not isinstance(value, str):
        raise ValidationError(f"{label} must be a YYYY-MM string.",
                              details={"field": label, "value": repr(value)})
    match = _MONTH_RE.match(value.strip())
    if not match:
        raise ValidationError(f"{label} must use YYYY-MM format.",
                              details={"field": label, "value": value})
    return int(match.group(1)), int(match.group(2))


def normalize_month(value: str, label: str = "period") -> str:
    year, month = parse_month(value, label)
    return f"{year:04d}-{month:02d}"


def parse_iso_date(value: str, label: str = "date") -> date:
    if not isinstance(value, str):
        raise ValidationError(f"{label} must be a YYYY-MM-DD string.",
                              details={"field": label, "value": repr(value)})
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise ValidationError(f"{label} must use YYYY-MM-DD format.",
                              details={"field": label, "value": value}) from None


def month_of(value: date) -> str:
    return f"{value.year:04d}-{value.month:02d}"


def shift_month(month: str, delta: int) -> str:
    """Return `month` shifted by `delta` months, e.g. ('2026-01', -1) -> '2025-12'."""
    year, mon = parse_month(month)
    index = year * 12 + (mon - 1) + delta
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def month_start(month: str) -> date:
    year, mon = parse_month(month)
    return date(year, mon, 1)


def month_end(month: str) -> date:
    year, mon = parse_month(month)
    return date(year, mon, calendar.monthrange(year, mon)[1])


def month_range(first: str, last: str) -> list[str]:
    """Inclusive list of months from `first` to `last`."""
    start, end = normalize_month(first), normalize_month(last)
    if start > end:
        return []
    months = [start]
    while months[-1] < end:
        months.append(shift_month(months[-1], 1))
    return months


@dataclass(frozen=True)
class ResolvedPeriods:
    service_period: str
    settlement_period: str
    rule: str


def resolve_periods(
    period_rule: str,
    transaction_date: str | date,
    *,
    service_period: str | None = None,
    settlement_period: str | None = None,
) -> ResolvedPeriods:
    """Apply a period rule, honouring any explicit operator override.

    An override is always accepted for the non-``custom`` rules too, because the
    operator sometimes knows better than the rule (a bill that covers two
    months, a late entry). ``custom`` *requires* both periods so the rule can
    never silently guess.
    """
    if period_rule not in PERIOD_RULES:
        raise ValidationError(
            f"Unsupported period_rule {period_rule!r}.",
            details={"field": "period_rule", "allowed": list(PERIOD_RULES)})
    txn = transaction_date if isinstance(transaction_date, date) else parse_iso_date(
        transaction_date, "transaction_date")
    txn_month = month_of(txn)

    if period_rule == "custom":
        if not settlement_period:
            raise ValidationError(
                "A custom period rule requires an explicit settlement_period.",
                details={"field": "settlement_period"})
        resolved_settlement = normalize_month(settlement_period, "settlement_period")
        resolved_service = normalize_month(service_period or settlement_period,
                                           "service_period")
    elif period_rule == "previous_month":
        resolved_settlement = normalize_month(settlement_period or txn_month,
                                              "settlement_period")
        resolved_service = normalize_month(
            service_period or shift_month(txn_month, -1), "service_period")
    else:  # current_month, transaction_date
        resolved_settlement = normalize_month(settlement_period or txn_month,
                                              "settlement_period")
        resolved_service = normalize_month(service_period or txn_month, "service_period")

    return ResolvedPeriods(resolved_service, resolved_settlement, period_rule)


def default_due_date(settlement_period: str) -> str:
    """Default charge due date: the last day of the settlement month.

    Oldest-first payment allocation orders by due date. Using the end of the
    settlement month means all charges settled in the same month share a due
    date and then fall back to creation order, which keeps the ordering stable
    and matches how the household actually settles up.
    """
    return month_end(settlement_period).isoformat()

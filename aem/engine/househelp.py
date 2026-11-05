"""House help and cook salaries: what a month costs, and who pays it.

A worker -- a maid, a cook, a cleaner -- is paid a fixed monthly salary for each
role they do. The worker may take some days off a month; a day absent beyond
them is a day away from every role, and costs each role that day's pro-rata
pay, ``base // days_in_month`` (floored to the paisa), unless the household
waives it. A bonus or an extra for the month is added on top, role by role.
The month a worker joined in pays each role for the days from their first
on: ``base * days_active // days_in_month``.

A role's cost for the month is split between the residents who share it: all
of them (``equal``), or only those who opted in (``opt_in``). The paise that do
not divide go one each to the residents in resident-ID order, so the shares
always add up to the cost exactly. Each resident's bill for the month is the sum
of their shares of every role.

All money is integer paise. Nothing here reads a database or mutates its input.
"""
from __future__ import annotations

from typing import Mapping, Sequence

SPLIT_METHODS = ("equal", "opt_in")
EXCESS_LEAVE_ACTIONS = ("deduct", "waive")


class HouseHelpCalculationError(ValueError):
    """Raised when salary or split inputs are invalid."""


def _whole(value: object, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise HouseHelpCalculationError(
            f"{label} must be a whole number of at least {minimum}.")
    return value


def calculate_excess_leaves(days_absent: int, allowed_leaves: int) -> int:
    """The days a worker was absent beyond their allowance."""
    _whole(days_absent, "days_absent")
    _whole(allowed_leaves, "allowed_leaves")
    return max(0, days_absent - allowed_leaves)


def _month_days(days_in_month: int) -> int:
    days = _whole(days_in_month, "days_in_month", minimum=28)
    if days > 31:
        raise HouseHelpCalculationError("days_in_month must be from 28 to 31.")
    return days


def prorate_salary(monthly_salary_paise: int, days_active: int, days_in_month: int) -> int:
    """A role's pay for the days of the month its worker was there --
    ``monthly_salary_paise * days_active // days_in_month``, floored to the
    paisa -- or the whole salary for the whole month."""
    base = _whole(monthly_salary_paise, "monthly_salary_paise", minimum=1)
    days = _month_days(days_in_month)
    active = _whole(days_active, "days_active", minimum=1)
    if active > days:
        raise HouseHelpCalculationError(
            f"days_active cannot be more than the {days} days in the month.")
    return base * active // days


def calculate_role_salary(base_salary_paise: int, excess_days: int, days_in_month: int,
                          excess_leave_action: str = "deduct", bonus_or_extra_paise: int = 0,
                          manual_deduction_paise: int | None = None,
                          days_active: int | None = None) -> tuple[int, int]:
    """``(final_salary_paise, deduction_paise)`` for one role in one month.

    ``base_salary_paise`` is the role's salary for a whole month. A worker
    who joined during it is paid for ``days_active`` of its days
    (:func:`prorate_salary`). ``excess_days`` are the days they were absent
    beyond their allowance (:func:`calculate_excess_leaves`); each deducts a
    day's pay, ``base_salary_paise // days_in_month``. ``manual_deduction_paise``
    replaces that figure when the household agreed another; ``'waive'``
    deducts nothing either way. The bonus is added after the deduction, which
    never takes the salary below zero.
    """
    base = _whole(base_salary_paise, "base_salary_paise", minimum=1)
    days = _month_days(days_in_month)
    active = days if days_active is None else _whole(days_active, "days_active", minimum=1)
    earned = prorate_salary(base, active, days)
    excess = _whole(excess_days, "excess_days")
    if excess > active:
        raise HouseHelpCalculationError(
            f"excess_days cannot be more than the {active} days worked in the month.")
    if excess_leave_action not in EXCESS_LEAVE_ACTIONS:
        raise HouseHelpCalculationError(
            f"excess_leave_action must be one of {', '.join(EXCESS_LEAVE_ACTIONS)}.")
    bonus = _whole(bonus_or_extra_paise, "bonus_or_extra_paise")
    if excess_leave_action == "waive":
        deduction = 0
    elif manual_deduction_paise is not None:
        deduction = _whole(manual_deduction_paise, "manual_deduction_paise")
        if deduction > earned:
            raise HouseHelpCalculationError(
                "manual_deduction_paise cannot be more than the salary for the month.")
    else:
        deduction = (base // days) * excess
    return earned - deduction + bonus, deduction


def allocate_role_cost(net_salary_paise: int, split_method: str, participants: Sequence[str],
                       all_residents: Sequence[str]) -> dict[str, int]:
    """Each sharer's part of a role's cost: every resident for ``'equal'``, the
    opted-in ``participants`` for ``'opt_in'``. The remainder paise go one each in
    resident-ID order, so the parts add up to ``net_salary_paise`` exactly."""
    total = _whole(net_salary_paise, "net_salary_paise")
    if split_method not in SPLIT_METHODS:
        raise HouseHelpCalculationError(
            f"split_method must be one of {', '.join(SPLIT_METHODS)}.")
    everyone = set(all_residents)
    strangers = sorted(set(participants) - everyone)
    if strangers:
        raise HouseHelpCalculationError(
            f"Not residents sharing this month: {', '.join(strangers)}.")
    sharers = sorted(everyone if split_method == "equal" else set(participants))
    if not sharers:
        if total:
            raise HouseHelpCalculationError("Nobody shares this role, so its cost has no payer.")
        return {}
    base, remainder = divmod(total, len(sharers))
    return {rid: base + (1 if index < remainder else 0) for index, rid in enumerate(sharers)}


def consolidate_resident_shares(role_allocations: Sequence[Mapping[str, int]],
                                all_residents: Sequence[str]) -> dict[str, int]:
    """Each resident's bill for the month: the sum of their parts of every role,
    ₹0 for a resident who shares none of them."""
    totals = {rid: 0 for rid in sorted(set(all_residents))}
    for allocation in role_allocations:
        for rid, paise in allocation.items():
            if rid not in totals:
                raise HouseHelpCalculationError(f"{rid} is not a resident sharing this month.")
            totals[rid] += _whole(paise, f"share for {rid}")
    return totals

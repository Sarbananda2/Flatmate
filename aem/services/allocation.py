"""Allocation planning: turn an amount plus a method into resident shares.

Nothing here does arithmetic of its own. It validates operator input, calls the
engine's split functions, and returns a complete map plus a basis record so the
UI can show *why* each resident owes what it says, before anything is saved.

Two conventions matter:

* The returned map always names **every** resident of the apartment. A resident
  who does not take part in this expense gets an explicit ``0`` rather than
  being absent, so an allocation can never be silently incomplete. Zero rows
  are not written to the database, but the zero is stated in the plan and shown
  in the preview.
* The payer is passed to the engine so that unavoidable remainder paise go to
  non-payers first. This is the household's agreed rounding rule.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from ..engine.ledger import custom_allocation, equal_split, weighted_split
from ..errors import ValidationError
from ..money import ensure_paise

PLANNABLE_METHODS = ("equal", "weighted", "custom")


@dataclass(frozen=True)
class AllocationPlan:
    amount_paise: int
    method: str
    shares_paise: dict[str, int]
    participants: list[str]
    basis: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def total_paise(self) -> int:
        return sum(self.shares_paise.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "amount_paise": self.amount_paise,
            "method": self.method,
            "shares_paise": dict(self.shares_paise),
            "participants": list(self.participants),
            "total_paise": self.total_paise,
            "basis": dict(self.basis),
            "warnings": list(self.warnings),
        }


def _check_participants(participants: Sequence[str] | None,
                        all_residents: Sequence[str]) -> list[str]:
    known = set(all_residents)
    if participants is None:
        chosen = list(all_residents)
    else:
        if not isinstance(participants, Sequence) or isinstance(participants, (str, bytes)):
            raise ValidationError("participants must be a list of resident IDs.",
                                  details={"field": "participants"})
        chosen = [str(p) for p in participants]
    if len(set(chosen)) != len(chosen):
        raise ValidationError("participants must be unique.",
                              details={"field": "participants"})
    unknown = sorted(set(chosen) - known)
    if unknown:
        raise ValidationError(
            "participants must all be residents of this apartment.",
            details={"field": "participants", "unknown_resident_ids": unknown})
    if not chosen:
        raise ValidationError("At least one participant is required.",
                              details={"field": "participants"})
    return sorted(chosen)


def plan_allocation(
    *,
    amount_paise: int,
    method: str,
    all_residents: Sequence[str],
    participants: Sequence[str] | None = None,
    weights: Mapping[str, Any] | None = None,
    shares_paise: Mapping[str, Any] | None = None,
    payer_resident_id: str | None = None,
) -> AllocationPlan:
    """Build a complete, validated allocation over every apartment resident."""
    ensure_paise(amount_paise, "amount_paise")
    if method not in PLANNABLE_METHODS:
        raise ValidationError(
            f"Unsupported allocation method {method!r}.",
            details={"field": "method", "allowed": list(PLANNABLE_METHODS)})
    residents = sorted(set(all_residents))
    if not residents:
        raise ValidationError("No residents are registered for this apartment.",
                              code="no_residents")
    if payer_resident_id is not None and payer_resident_id not in residents:
        raise ValidationError("The payer must be a resident of this apartment.",
                              details={"field": "payer_resident_id",
                                       "value": payer_resident_id})

    warnings: list[str] = []
    basis: dict[str, Any] = {"method": method, "payer_resident_id": payer_resident_id}

    if method == "custom":
        if not isinstance(shares_paise, Mapping) or not shares_paise:
            raise ValidationError(
                "Custom allocation requires an explicit amount for each participant.",
                details={"field": "shares_paise"})
        chosen = _check_participants(list(shares_paise.keys()), residents)
        cleaned = {rid: ensure_paise(shares_paise[rid], f"share for {rid}") for rid in chosen}
        total = sum(cleaned.values())
        if total != amount_paise:
            raise ValidationError(
                "Custom shares must sum exactly to the expense amount.",
                code="allocation_total_mismatch",
                details={"expected_paise": amount_paise, "provided_paise": total,
                         "difference_paise": total - amount_paise})
        computed = custom_allocation(amount_paise, cleaned)
        basis["explicit_shares_paise"] = dict(cleaned)
    elif method == "equal":
        chosen = _check_participants(participants, residents)
        computed = equal_split(amount_paise, chosen, payer_resident_id)
        basis["participant_count"] = len(chosen)
        basis["base_share_paise"] = amount_paise // len(chosen)
        basis["remainder_paise"] = amount_paise % len(chosen)
        basis["rounding_rule"] = (
            "remainder paise go to non-payers first, in resident-ID order")
    else:  # weighted
        if not isinstance(weights, Mapping) or not weights:
            raise ValidationError(
                "Weighted allocation requires a weight for each participant.",
                details={"field": "weights"})
        chosen = _check_participants(list(weights.keys()), residents)
        cleaned_weights: dict[str, int] = {}
        for rid in chosen:
            value = weights[rid]
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValidationError(
                    f"Weight for {rid} must be a non-negative whole number.",
                    details={"field": "weights", "resident_id": rid})
            if value < 0:
                raise ValidationError(f"Weight for {rid} cannot be negative.",
                                      details={"field": "weights", "resident_id": rid})
            cleaned_weights[rid] = value
        if sum(cleaned_weights.values()) <= 0:
            raise ValidationError("At least one weight must be greater than zero.",
                                  details={"field": "weights"})
        computed = weighted_split(amount_paise, cleaned_weights)
        basis["weights"] = dict(cleaned_weights)
        basis["weight_total"] = sum(cleaned_weights.values())
        basis["rounding_rule"] = (
            "largest-remainder method, ties broken by resident ID; the payer "
            "rounding preference applies to equal splits only")
        if payer_resident_id in cleaned_weights:
            warnings.append(
                "Weighted splits use largest-remainder rounding. The payer "
                "rounding preference is defined for equal splits only, so the "
                "payer may receive a remainder paisa here.")

    shares = {rid: 0 for rid in residents}
    shares.update(computed)
    if sum(shares.values()) != amount_paise:
        # Unreachable while the engine holds its contract; kept as a hard stop so
        # a future change can never persist an unbalanced allocation.
        raise ValidationError(
            "Calculated shares do not sum to the expense amount.",
            code="allocation_total_mismatch",
            details={"expected_paise": amount_paise, "calculated_paise": sum(shares.values())})

    excluded = [rid for rid in residents if rid not in chosen]
    if excluded:
        basis["excluded_residents"] = excluded
    return AllocationPlan(amount_paise=amount_paise, method=method, shares_paise=shares,
                          participants=chosen, basis=basis, warnings=warnings)

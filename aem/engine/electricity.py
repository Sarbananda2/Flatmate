"""Standalone electricity bill allocation engine.

The engine has no database or ledger side effects. It takes a bill, resident
identifiers, and smart-plug kWh readings and returns validated paise allocations.

Input contract (all money values are integer paise):
{
  "residents": ["alice", "bob"],
  "smart_plug_readings": [
    {"resident_id": "alice", "kwh": "42.5"},
    {"resident_id": "bob", "kwh": "31.0"}
  ],
  "bill": {
    "total_amount_paise": 735000,
    "components": [
      {"id": "energy", "name": "Energy charges", "amount_paise": 650000,
       "allocation_method": "usage"},
      {"id": "fixed", "name": "Fixed charges", "amount_paise": 85000,
       "allocation_method": "equal"}
    ]
  },
  "payer_resident_id": "alice"
}

If components are omitted, the entire bill is treated as one usage-based
component. Supported per-component allocation_method values:
- usage: proportional to each resident's total smart-plug kWh.
- equal: equal split.
- custom: use allocations_paise or weights keyed by resident ID.

A component may override the default with allocation_method. For configurable
exceptions, use component-level methods or custom weights/amounts. Guest kWh
must be included in the host resident's smart-plug reading by the caller.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence
import json


class ElectricityCalculationError(ValueError):
    """Raised when bill inputs or allocation rules are invalid."""


@dataclass(frozen=True)
class ResidentAllocation:
    resident_id: str
    amount_paise: int


@dataclass(frozen=True)
class ComponentResult:
    component_id: str
    name: str
    amount_paise: int
    allocation_method: str
    allocations: tuple[ResidentAllocation, ...]


@dataclass(frozen=True)
class ElectricityCalculationResult:
    total_amount_paise: int
    components: tuple[ComponentResult, ...]
    allocations: tuple[ResidentAllocation, ...]
    breakdown: Mapping[str, Any]
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {
            "total_amount_paise": self.total_amount_paise,
            "components": [
                {
                    "component_id": c.component_id,
                    "name": c.name,
                    "amount_paise": c.amount_paise,
                    "allocation_method": c.allocation_method,
                    "allocations": [asdict(a) for a in c.allocations],
                }
                for c in self.components
            ],
            "allocations": [asdict(a) for a in self.allocations],
            "breakdown": dict(self.breakdown),
            "warnings": list(self.warnings),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


def _money(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ElectricityCalculationError(f"{label} must be an integer number of paise.")
    if value < 0:
        raise ElectricityCalculationError(f"{label} cannot be negative.")
    return value


def _decimal_kwh(value: Any, label: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ElectricityCalculationError(f"{label} must be a valid non-negative kWh value.")
    if not number.is_finite() or number < 0:
        raise ElectricityCalculationError(f"{label} must be a valid non-negative kWh value.")
    return number


def _round_weighted(total: int, weights: Mapping[str, Decimal],
                    resident_ids: Sequence[str], payer_resident_id: str | None) -> dict[str, int]:
    """Largest-remainder allocation; when tied, payer is deliberately last."""
    weight_sum = sum(weights.values(), Decimal(0))
    if total == 0:
        return {rid: 0 for rid in resident_ids}
    if weight_sum <= 0:
        raise ElectricityCalculationError("Usage/weights must sum to more than zero for a positive charge.")
    exact = {rid: Decimal(total) * weights[rid] / weight_sum for rid in resident_ids}
    result = {rid: int(exact[rid]) for rid in resident_ids}  # floor, never float
    remaining = total - sum(result.values())
    order = sorted(
        resident_ids,
        key=lambda rid: (
            rid == payer_resident_id,  # payer loses tie-breaks on remainder paise
            -(exact[rid] - Decimal(result[rid])),
            rid,
        ),
    )
    for rid in order[:remaining]:
        result[rid] += 1
    return result


def _allocate_component(component: Mapping[str, Any], resident_ids: Sequence[str],
                        usage: Mapping[str, Decimal], payer_resident_id: str | None,
                        default_method: str) -> tuple[str, dict[str, int]]:
    amount = _money(component.get("amount_paise"), f"Component {component.get('id', '?')} amount_paise")
    method = component.get("allocation_method", default_method)
    if method not in {"usage", "equal", "custom"}:
        raise ElectricityCalculationError(
            f"Unsupported allocation_method {method!r}; use 'usage', 'equal', or 'custom'."
        )

    if method == "usage":
        weights = dict(usage)
        # A *positive* charge with no recorded usage is rejected rather than
        # invented. A zero charge allocates zero to everyone, which is a real
        # answer and needs no readings.
        if amount > 0 and sum(weights.values(), Decimal(0)) == 0:
            raise ElectricityCalculationError(
                f"Component {component.get('id', '?')} uses usage allocation but total smart-plug usage is zero."
            )
        return method, _round_weighted(amount, weights, resident_ids, payer_resident_id)

    if method == "equal":
        weights = {rid: Decimal(1) for rid in resident_ids}
        return method, _round_weighted(amount, weights, resident_ids, payer_resident_id)

    custom_amounts = component.get("allocations_paise")
    custom_weights = component.get("weights")
    if custom_amounts is not None and custom_weights is not None:
        raise ElectricityCalculationError("Custom allocation must provide either allocations_paise or weights, not both.")
    if custom_amounts is not None:
        if not isinstance(custom_amounts, Mapping):
            raise ElectricityCalculationError("allocations_paise must be an object keyed by resident ID.")
        if set(custom_amounts) != set(resident_ids):
            raise ElectricityCalculationError("allocations_paise must contain exactly one entry per resident.")
        result = {rid: _money(custom_amounts[rid], f"Custom allocation for {rid}") for rid in resident_ids}
        if sum(result.values()) != amount:
            raise ElectricityCalculationError(
                f"Custom allocations for component {component.get('id', '?')} must sum to its amount."
            )
        return method, result

    if custom_weights is not None:
        if not isinstance(custom_weights, Mapping) or set(custom_weights) != set(resident_ids):
            raise ElectricityCalculationError("weights must contain exactly one entry per resident.")
        weights = {rid: _decimal_kwh(custom_weights[rid], f"Custom weight for {rid}") for rid in resident_ids}
        return method, _round_weighted(amount, weights, resident_ids, payer_resident_id)

    raise ElectricityCalculationError("Custom allocation requires allocations_paise or weights.")


def calculate_electricity(input_data: Mapping[str, Any]) -> ElectricityCalculationResult:
    """Calculate a household electricity bill and validate all allocation totals.

    Bill component amounts must add up exactly to total_amount_paise. The
    engine does not infer tariffs or reconstruct the bill from kWh; the utility
    bill is authoritative. Smart-plug readings are allocation weights only.
    """
    if not isinstance(input_data, Mapping):
        raise ElectricityCalculationError("input_data must be an object.")
    resident_values = input_data.get("residents")
    if not isinstance(resident_values, Sequence) or isinstance(resident_values, (str, bytes)):
        raise ElectricityCalculationError("residents must be a list of resident IDs.")
    resident_ids = list(resident_values)
    if not resident_ids or any(not isinstance(r, str) or not r.strip() for r in resident_ids):
        raise ElectricityCalculationError("At least one non-empty resident ID is required.")
    if len(set(resident_ids)) != len(resident_ids):
        raise ElectricityCalculationError("Resident IDs must be unique.")
    resident_ids = sorted(resident_ids)

    payer = input_data.get("payer_resident_id")
    if payer is not None and payer not in resident_ids:
        raise ElectricityCalculationError("payer_resident_id must reference a known resident.")

    readings = input_data.get("smart_plug_readings", [])
    if not isinstance(readings, Sequence) or isinstance(readings, (str, bytes)):
        raise ElectricityCalculationError("smart_plug_readings must be a list.")
    usage = {rid: Decimal(0) for rid in resident_ids}
    for index, reading in enumerate(readings):
        if not isinstance(reading, Mapping):
            raise ElectricityCalculationError(f"Smart-plug reading {index} must be an object.")
        rid = reading.get("resident_id")
        if rid not in usage:
            raise ElectricityCalculationError(f"Smart-plug reading {index} references an unknown resident.")
        kwh = _decimal_kwh(reading.get("kwh"), f"Smart-plug reading {index} kwh")
        usage[rid] += kwh

    bill = input_data.get("bill")
    if not isinstance(bill, Mapping):
        raise ElectricityCalculationError("bill must be an object.")
    total = _money(bill.get("total_amount_paise"), "bill.total_amount_paise")
    components_input = bill.get("components")
    if components_input is None:
        components_input = [{
            "id": "bill-total",
            "name": "Total electricity bill",
            "amount_paise": total,
            "allocation_method": bill.get("default_allocation_method", "usage"),
        }]
    if not isinstance(components_input, Sequence) or isinstance(components_input, (str, bytes)) or not components_input:
        raise ElectricityCalculationError("bill.components must be a non-empty list when supplied.")

    component_ids: set[str] = set()
    component_results: list[ComponentResult] = []
    resident_totals = {rid: 0 for rid in resident_ids}
    component_sum = 0
    default_method = bill.get("default_allocation_method", "usage")
    for index, component in enumerate(components_input):
        if not isinstance(component, Mapping):
            raise ElectricityCalculationError(f"Bill component {index} must be an object.")
        cid = component.get("id")
        name = component.get("name", str(cid or f"Component {index + 1}"))
        if not isinstance(cid, str) or not cid.strip():
            raise ElectricityCalculationError(f"Bill component {index} requires a non-empty id.")
        if cid in component_ids:
            raise ElectricityCalculationError(f"Duplicate bill component id: {cid}.")
        component_ids.add(cid)
        if not isinstance(name, str) or not name.strip():
            raise ElectricityCalculationError(f"Bill component {cid} requires a non-empty name.")
        amount = _money(component.get("amount_paise"), f"Component {cid} amount_paise")
        method, allocations = _allocate_component(component, resident_ids, usage, payer, default_method)
        if sum(allocations.values()) != amount:
            raise ElectricityCalculationError(f"Allocation total mismatch for component {cid}.")
        for rid, value in allocations.items():
            resident_totals[rid] += value
        component_sum += amount
        component_results.append(ComponentResult(
            component_id=cid,
            name=name,
            amount_paise=amount,
            allocation_method=method,
            allocations=tuple(ResidentAllocation(rid, allocations[rid]) for rid in resident_ids),
        ))

    if component_sum != total:
        raise ElectricityCalculationError(
            f"Bill components total {component_sum} paise, but bill total is {total} paise."
        )
    if sum(resident_totals.values()) != total:
        raise ElectricityCalculationError("Final resident allocations do not equal the bill total.")

    return ElectricityCalculationResult(
        total_amount_paise=total,
        components=tuple(component_results),
        allocations=tuple(ResidentAllocation(rid, resident_totals[rid]) for rid in resident_ids),
        breakdown={
            "allocation_basis": "actual bill amounts; smart-plug kWh used as allocation weights",
            "usage_kwh_by_resident": {rid: str(usage[rid]) for rid in resident_ids},
            "payer_resident_id": payer,
            "component_count": len(component_results),
        },
        warnings=(),
    )

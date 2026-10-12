"""Pure accounting engines.

These modules are the source of truth for every financial calculation. They
have no database, network, or UI dependency, they never use floating point for
money, and they never mutate state. Services persist their output; they do not
re-implement it.
"""
from .electricity import (
    ComponentResult,
    ElectricityCalculationError,
    ElectricityCalculationResult,
    ResidentAllocation,
    calculate_electricity,
)
from .ledger import (
    Charge,
    PaymentAllocationResult,
    PaymentApplication,
    PeriodRule,
    Statement,
    StatementStatus,
    apply_payment_oldest_first,
    close_expired_statement,
    custom_allocation,
    equal_split,
    finalize_statement,
    host_guest_electricity,
    net_settlement_transfers,
    pooled_net_positions,
    revise_statement,
    weighted_split,
)

__all__ = [
    "Charge", "PaymentAllocationResult", "PaymentApplication", "PeriodRule",
    "Statement", "StatementStatus", "apply_payment_oldest_first",
    "close_expired_statement", "custom_allocation", "equal_split",
    "finalize_statement", "host_guest_electricity", "net_settlement_transfers",
    "pooled_net_positions", "revise_statement", "weighted_split",
    "ComponentResult", "ElectricityCalculationError",
    "ElectricityCalculationResult", "ResidentAllocation", "calculate_electricity",
]

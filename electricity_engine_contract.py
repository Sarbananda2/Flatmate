"""Compatibility interface for the standalone electricity calculation engine."""
from electricity_engine import (
    ComponentResult,
    ElectricityCalculationError,
    ElectricityCalculationResult,
    ResidentAllocation,
    calculate_electricity,
)

__all__ = [
    "ComponentResult",
    "ElectricityCalculationError",
    "ElectricityCalculationResult",
    "ResidentAllocation",
    "calculate_electricity",
]

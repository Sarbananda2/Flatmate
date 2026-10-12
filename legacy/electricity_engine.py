"""Compatibility import path for the standalone electricity engine.

The engine moved to ``aem.engine.electricity``. It is re-exported here
unchanged so the terminal wizard and the original tests keep working.
"""
import _root  # noqa: F401  (makes ``aem`` importable)

from aem.engine.electricity import (  # noqa: F401
    ComponentResult,
    ElectricityCalculationError,
    ElectricityCalculationResult,
    ResidentAllocation,
    calculate_electricity,
)

__all__ = [
    "ComponentResult", "ElectricityCalculationError",
    "ElectricityCalculationResult", "ResidentAllocation", "calculate_electricity",
]

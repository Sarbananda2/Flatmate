"""Base class for repositories."""
from __future__ import annotations

from typing import Any, Iterable

from ..session import Session


class Repository:
    """Owns the SQL for one area of the application.

    A repository's methods are named for what the application needs
    (``open_charges_for``, ``append_revision``), take and return plain values,
    and contain no business rules: validation and decisions stay in services.
    """

    def __init__(self, session: Session) -> None:
        self._s = session

    @staticmethod
    def _placeholders(values: Iterable[Any]) -> str:
        """``%s,%s,%s`` for an ``IN (...)`` list. The list must not be empty."""
        return ",".join("%s" for _ in values)

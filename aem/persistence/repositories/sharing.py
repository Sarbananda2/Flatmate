"""Read model for shareable settlement summaries."""
from __future__ import annotations

from typing import Any

from .base import Repository


class SharingRepository(Repository):
    def resident_upi_ids(self, apartment_id: str) -> list[dict[str, Any]]:
        """``resident_id`` and ``upi_id`` (which may be empty) for every resident."""
        return self._s.fetch_all(
            "SELECT resident_id, upi_id FROM resident WHERE apartment_id=%s",
            (apartment_id,))

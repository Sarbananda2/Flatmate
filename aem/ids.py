"""Record identifiers and timestamps.

These are application values, not database ones: an ID is minted and a
timestamp is taken by the service that creates a record, and both are stored as
text exactly as produced here.
"""
from __future__ import annotations

import uuid
from datetime import datetime


def new_id(prefix: str) -> str:
    """Opaque, sortable-enough identifier. Stable across restarts."""
    return f"{prefix}_{uuid.uuid4().hex}"


def now_timestamp(value: datetime | None = None) -> str:
    """Timezone-aware ISO-8601 timestamp to the second."""
    return (value or datetime.now().astimezone()).isoformat(timespec="seconds")

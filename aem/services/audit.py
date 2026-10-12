"""Append-only audit trail.

Every mutating service writes here, inside the same transaction as the change
itself, so the trail cannot drift from the data. The table is protected by
triggers against UPDATE and DELETE.
"""
from __future__ import annotations

import json
from typing import Any

from ..ids import new_id, now_timestamp
from ..persistence import Database, UnitOfWork

ACTOR_DEFAULT = "operator"


def _render(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (str, int)):
        return str(value)
    return json.dumps(value, sort_keys=True, default=str)


def record(
    uow: UnitOfWork,
    *,
    entity_type: str,
    entity_id: str,
    action: str,
    actor: str = ACTOR_DEFAULT,
    apartment_id: str | None = None,
    field_name: str | None = None,
    old_value: Any = None,
    new_value: Any = None,
    reason: str | None = None,
    created_at: str | None = None,
) -> str:
    audit_id = new_id("audit")
    uow.audit.append(
        audit_id=audit_id, apartment_id=apartment_id, entity_type=entity_type,
        entity_id=entity_id, action=action, field_name=field_name,
        old_value=_render(old_value), new_value=_render(new_value), reason=reason,
        actor=(actor or ACTOR_DEFAULT).strip() or ACTOR_DEFAULT,
        created_at=created_at or now_timestamp())
    return audit_id


def record_field_changes(
    uow: UnitOfWork,
    *,
    entity_type: str,
    entity_id: str,
    before: dict[str, Any],
    after: dict[str, Any],
    fields: list[str],
    actor: str = ACTOR_DEFAULT,
    apartment_id: str | None = None,
    reason: str | None = None,
) -> list[str]:
    """Write one audit row per field that actually changed."""
    written: list[str] = []
    for field in fields:
        old, new = before.get(field), after.get(field)
        if old == new:
            continue
        written.append(record(
            uow, entity_type=entity_type, entity_id=entity_id, action="update",
            actor=actor, apartment_id=apartment_id, field_name=field,
            old_value=old, new_value=new, reason=reason))
    return written


def history(
    uow: UnitOfWork,
    *,
    entity_type: str | None = None,
    entity_id: str | None = None,
    apartment_id: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[dict[str, Any]]:
    return uow.audit.history(entity_type=entity_type, entity_id=entity_id,
                             apartment_id=apartment_id, limit=limit, offset=offset)


def count(uow: UnitOfWork, *, apartment_id: str | None = None,
          entity_type: str | None = None, entity_id: str | None = None) -> int:
    return uow.audit.count(entity_type=entity_type, entity_id=entity_id,
                           apartment_id=apartment_id)


def read_history(db: Database, *, apartment_id: str | None = None,
                 entity_type: str | None = None, entity_id: str | None = None,
                 limit: int = 200, offset: int = 0) -> dict[str, Any]:
    """One page of the trail and the total it was drawn from, read together."""
    with db.read() as uow:
        items = history(uow, entity_type=entity_type, entity_id=entity_id,
                        apartment_id=apartment_id, limit=limit, offset=offset)
        total = count(uow, apartment_id=apartment_id, entity_type=entity_type,
                      entity_id=entity_id)
    return {"items": items, "total": total}

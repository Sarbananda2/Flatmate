"""Append-only audit trail.

Every mutating service writes here, inside the same transaction as the change
itself, so the trail cannot drift from the data. The table is protected by
triggers against UPDATE and DELETE.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..db import fetch_all, new_id, now_timestamp

ACTOR_DEFAULT = "operator"


def _render(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (str, int)):
        return str(value)
    return json.dumps(value, sort_keys=True, default=str)


def record(
    conn: sqlite3.Connection,
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
    conn.execute(
        """INSERT INTO audit_log
           (audit_id, apartment_id, entity_type, entity_id, action, field_name,
            old_value, new_value, reason, actor, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (audit_id, apartment_id, entity_type, entity_id, action, field_name,
         _render(old_value), _render(new_value), reason,
         (actor or ACTOR_DEFAULT).strip() or ACTOR_DEFAULT,
         created_at or now_timestamp()),
    )
    return audit_id


def record_field_changes(
    conn: sqlite3.Connection,
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
            conn, entity_type=entity_type, entity_id=entity_id, action="update",
            actor=actor, apartment_id=apartment_id, field_name=field,
            old_value=old, new_value=new, reason=reason))
    return written


def history(
    conn: sqlite3.Connection,
    *,
    entity_type: str | None = None,
    entity_id: str | None = None,
    apartment_id: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if entity_type:
        clauses.append("entity_type = ?")
        params.append(entity_type)
    if entity_id:
        clauses.append("entity_id = ?")
        params.append(entity_id)
    if apartment_id:
        clauses.append("(apartment_id = ? OR apartment_id IS NULL)")
        params.append(apartment_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.extend([int(limit), int(offset)])
    return fetch_all(
        conn,
        f"SELECT * FROM audit_log {where} ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?",
        params,
    )


def count(conn: sqlite3.Connection, *, apartment_id: str | None = None,
          entity_type: str | None = None, entity_id: str | None = None) -> int:
    clauses: list[str] = []
    params: list[Any] = []
    if entity_type:
        clauses.append("entity_type = ?")
        params.append(entity_type)
    if entity_id:
        clauses.append("entity_id = ?")
        params.append(entity_id)
    if apartment_id:
        clauses.append("(apartment_id = ? OR apartment_id IS NULL)")
        params.append(apartment_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return int(conn.execute(f"SELECT COUNT(*) FROM audit_log {where}", tuple(params)).fetchone()[0])

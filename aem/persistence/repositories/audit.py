"""The append-only audit trail."""
from __future__ import annotations

from typing import Any

from .base import Repository


class AuditRepository(Repository):
    def append(self, *, audit_id: str, apartment_id: str | None, entity_type: str,
               entity_id: str, action: str, field_name: str | None, old_value: str | None,
               new_value: str | None, reason: str | None, actor: str,
               created_at: str) -> None:
        self._s.execute(
            """INSERT INTO audit_log
               (audit_id, apartment_id, entity_type, entity_id, action, field_name,
                old_value, new_value, reason, actor, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (audit_id, apartment_id, entity_type, entity_id, action, field_name,
             old_value, new_value, reason, actor, created_at))

    @staticmethod
    def _filter(entity_type: str | None, entity_id: str | None,
                apartment_id: str | None) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if entity_type:
            clauses.append("entity_type = %s")
            params.append(entity_type)
        if entity_id:
            clauses.append("entity_id = %s")
            params.append(entity_id)
        if apartment_id:
            clauses.append("(apartment_id = %s OR apartment_id IS NULL)")
            params.append(apartment_id)
        return (f"WHERE {' AND '.join(clauses)}" if clauses else ""), params

    def history(self, *, entity_type: str | None = None, entity_id: str | None = None,
                apartment_id: str | None = None, limit: int = 200,
                offset: int = 0) -> list[dict[str, Any]]:
        """Newest first."""
        where, params = self._filter(entity_type, entity_id, apartment_id)
        params.extend([int(limit), int(offset)])
        return self._s.fetch_all(
            f"SELECT * FROM audit_log {where} "
            "ORDER BY created_at DESC, row_seq DESC LIMIT %s OFFSET %s", params)

    def count(self, *, entity_type: str | None = None, entity_id: str | None = None,
              apartment_id: str | None = None) -> int:
        where, params = self._filter(entity_type, entity_id, apartment_id)
        return int(self._s.scalar(f"SELECT COUNT(*) FROM audit_log {where}", params, 0))

"""Health and database status."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ... import __version__
from ...db import connect
from ...migrations import current_version, discover
from ...schemas import HealthResponse
from ...services import apartments
from ..deps import AppSettings, DbPath

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Liveness and database status")
def health(db_path: DbPath, settings: AppSettings) -> dict[str, Any]:
    """Report whether the app can serve requests and whether the database is current."""
    exists = db_path.exists()
    checks: dict[str, Any] = {}
    apartment_count = 0
    schema_version = 0
    default_apartment = None
    try:
        if exists:
            schema_version = current_version(db_path)
            with connect(db_path, read_only=True) as conn:
                apartment_count = int(conn.execute(
                    "SELECT COUNT(*) FROM apartment").fetchone()[0])
                checks["foreign_keys"] = bool(
                    conn.execute("PRAGMA foreign_keys").fetchone()[0])
                checks["integrity"] = conn.execute(
                    "PRAGMA quick_check(1)").fetchone()[0]
            default_apartment = apartments.default_apartment_id(db_path)
        checks["database_readable"] = exists
    except Exception as exc:  # pragma: no cover - reported, not raised
        checks["database_error"] = str(exc)

    latest = max((m.version for m in discover()), default=0)
    checks["schema_up_to_date"] = schema_version == latest
    checks["latest_schema_version"] = latest
    status = "ok" if exists and checks.get("schema_up_to_date") else "degraded"
    return {
        "status": status,
        "version": __version__,
        "schema_version": schema_version,
        "database_path": str(db_path),
        "database_exists": exists,
        "apartment_count": apartment_count,
        "default_apartment_id": default_apartment,
        "settings": settings.describe(),
        "checks": checks,
    }

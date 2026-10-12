"""Health and database status."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ... import __version__
from ...schemas import HealthResponse
from ...services import apartments
from ..deps import AppSettings, Db

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Liveness and database status")
def health(db: Db, settings: AppSettings) -> dict[str, Any]:
    """Report whether the app can serve requests and whether the database is current.

    ``database_path`` keeps its name from when the database was a file; it now
    holds the PostgreSQL target as ``postgresql://user@host:port/name``, with
    no password in it.
    """
    report = db.status()
    reachable = bool(report["reachable"])
    schema_version = int(report["schema_version"])
    latest = int(report["latest_schema_version"])
    checks: dict[str, Any] = {"database_readable": reachable}
    apartment_count = 0
    default_apartment = None
    if report["error"]:
        checks["database_error"] = report["error"]
    if reachable:
        # PostgreSQL always enforces foreign keys; what can be wrong is a
        # constraint that was added without validating existing rows.
        checks["foreign_keys"] = True
        checks["integrity"] = ("ok" if not report["invalid_constraints"]
                               else f"{report['invalid_constraints']} constraint(s) not validated")
        if report["disabled_triggers"]:
            checks["integrity"] = f"{report['disabled_triggers']} integrity trigger(s) disabled"
        checks["database_role"] = report["role"]
        checks["database_role_is_privileged"] = report["role_is_privileged"]
        # The app should serve as a role that owns nothing and cannot change
        # the schema; the migration role does that, separately.
        checks["database_role_owns_tables"] = bool(report["role_owns_tables"])
        checks["database_role_can_change_schema"] = report["role_can_change_schema"]
        checks["trigger_guard_installed"] = report["trigger_guard_installed"]
        checks["server_version"] = report["server_version"]
        checks["collation"] = report["collation"]
        if schema_version:
            try:
                apartment_count = apartments.count_apartments(db)
                default_apartment = apartments.default_apartment_id(db)
            except Exception as exc:  # pragma: no cover - reported, not raised
                checks["database_error"] = str(exc)
    checks["schema_up_to_date"] = schema_version == latest
    checks["latest_schema_version"] = latest
    status = "ok" if reachable and checks["schema_up_to_date"] else "degraded"
    return {
        "status": status,
        "version": __version__,
        "schema_version": schema_version,
        "database_backend": "postgresql",
        "database_path": report["target"],
        "database_exists": reachable,
        "apartment_count": apartment_count,
        "default_apartment_id": default_apartment,
        "settings": settings.describe(),
        "checks": checks,
    }

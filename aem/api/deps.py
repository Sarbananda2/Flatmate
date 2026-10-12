"""Request-scoped dependencies and error translation."""
from __future__ import annotations

import csv
import io
import logging
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Query, Request
from fastapi.responses import Response

from ..config import Settings
from ..errors import AemError, NotFoundError
from ..persistence import Database
from ..services import apartments

logger = logging.getLogger("aem.api")


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_database(request: Request) -> Database:
    """The database handle services are given. It exposes units of work, never
    a connection or SQL, so a route cannot reach the database except through a
    service."""
    return request.app.state.database


Db = Annotated[Database, Depends(get_database)]
AppSettings = Annotated[Settings, Depends(get_settings)]


def resolve_apartment_id(
    db: Db,
    apartment_id: Annotated[str | None, Query(
        description="Omit to use the default apartment.")] = None,
) -> str:
    """The apartment every request works against.

    A single-operator household normally has exactly one, so the query
    parameter is optional and falls back to the configured default.
    """
    if apartment_id:
        return apartment_id
    resolved = apartments.default_apartment_id(db)
    if not resolved:
        raise NotFoundError(
            "No apartment has been set up yet. Create one first.",
            code="no_apartment")
    return resolved


ApartmentId = Annotated[str, Depends(resolve_apartment_id)]


def to_http(exc: AemError) -> HTTPException:
    return HTTPException(status_code=exc.http_status, detail={"error": exc.to_dict()})


def csv_response(rows: list[dict[str, Any]], filename: str, *,
                 columns: list[str] | None = None) -> Response:
    """Return rows as a downloadable CSV with a safe filename."""
    from ..services.reports import to_csv

    safe = "".join(ch for ch in filename if ch.isalnum() or ch in "-_.") or "export"
    if not safe.endswith(".csv"):
        safe += ".csv"
    body = to_csv(rows, columns=columns)
    if not body:
        body = "no_rows\n"
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{safe}"',
                 "Cache-Control": "no-store"},
    )

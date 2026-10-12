"""FastAPI application: startup, shutdown, error handling, and static serving.

Startup
    Resolves settings, runs any pending migrations (so the database is always at
    the schema the code expects, and existing data is preserved), and closes any
    statement whose correction window elapsed while the app was not running.

While running
    A background task closes statements whose correction window has elapsed,
    once an hour by default, so a statement closes on time even when nobody
    restarts the app or presses the button.

Shutdown
    There is no connection pool to drain: every unit of work opens and closes its
    own SQLite connection inside a context manager, so a shutdown can never leave
    a transaction open. The shutdown path performs a WAL checkpoint so the
    database file on disk is complete and safe to copy for a backup.

Errors
    Every expected error is an :class:`aem.errors.AemError` and is rendered as
    ``{"error": {"code", "message", "details"}}`` with a matching HTTP status.
    Request validation failures use the same envelope, so the frontend has one
    error shape to handle.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from .config import Settings, get_settings
from .db import connect
from .errors import AemError
from .migrations import current_version, migrate
from .services import statements

logger = logging.getLogger("aem")

DESCRIPTION = """
Local-first household expense accounting for a shared apartment.

**Money** is integer paise everywhere. No floating-point arithmetic is used in
the accounting path, and every money field is named `*_paise`.

**Accounting model.** Each resident has an account, plus a synthetic `__pool__`
account for the collection pool. A positive position means the account is owed
money; a negative position means it owes money. All positions sum to zero.

**Three distinct facts** about every expense, kept separate so nothing is counted
twice: the household incurred a cost; each resident owes a share of it; somebody
funded it. A payment -- into the pool, to a provider, or between residents -- is
never a household expense.

**Security.** There is no authentication. This app is meant to run on the
operator's own machine, bound to localhost. Do not expose it to a network.
"""


def _checkpoint(db_path: Path) -> None:
    """Flush the WAL into the main database file so a file copy is a valid backup."""
    try:
        if db_path.exists():
            with connect(db_path) as conn:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.DatabaseError as exc:  # pragma: no cover - logged, not fatal
        logger.warning("Could not checkpoint the database on shutdown: %s", exc)


async def _close_expired_periodically(db_path: Path, interval_seconds: float) -> None:
    """Close statements past their correction window, for as long as the app runs.

    The work is one short SQLite transaction, run off the event loop. A failed
    pass is logged and the loop carries on: a statement that could not be closed
    this hour is simply picked up by the next pass.
    """
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            closed = await asyncio.to_thread(
                statements.close_expired_statements, db_path, actor="scheduler")
            if closed:
                logger.info("Closed %d statement(s) past their correction window: %s",
                            len(closed), closed)
        except (AemError, sqlite3.Error) as exc:
            logger.warning("Could not close expired statements: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = app.state.settings
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s")
    logger.info("Apartment Expense Manager %s starting", __version__)
    logger.info("Database: %s", settings.database_path)

    if settings.auto_migrate:
        for line in migrate(settings.database_path):
            logger.info("migration: %s", line)
    app.state.schema_version = current_version(settings.database_path)
    logger.info("Schema version: %s", app.state.schema_version)

    if settings.auto_close_expired_statements:
        try:
            closed = statements.close_expired_statements(settings.database_path,
                                                          actor="startup")
            if closed:
                logger.info("Closed %d statement(s) whose correction window elapsed "
                            "while the app was not running: %s", len(closed), closed)
        except AemError as exc:  # pragma: no cover - logged, not fatal
            logger.warning("Could not close expired statements at startup: %s", exc)

    if not settings.describe()["binds_loopback_only"]:
        logger.warning(
            "The server is configured to bind %s, which is not loopback. This "
            "application has no authentication; anyone who can reach this port can "
            "read and change the household's accounts.", settings.host)

    closer: asyncio.Task[None] | None = None
    if settings.auto_close_expired_statements and settings.auto_close_interval_minutes > 0:
        closer = asyncio.create_task(_close_expired_periodically(
            settings.database_path, settings.auto_close_interval_minutes * 60))

    try:
        yield
    finally:
        if closer:
            closer.cancel()
            try:
                await closer
            except asyncio.CancelledError:
                pass
        logger.info("Shutting down; checkpointing the database")
        _checkpoint(settings.database_path)


def _error_response(status_code: int, code: str, message: str,
                    details: dict[str, Any] | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "details": details or {}}})


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    app = FastAPI(
        title="Apartment Expense Manager",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
        openapi_tags=[
            {"name": "health", "description": "Liveness and database status."},
            {"name": "setup", "description": "Apartments and operator settings."},
            {"name": "dashboard", "description": "Current balances and month totals."},
            {"name": "residents", "description": "Residents and their financial history."},
            {"name": "expense definitions",
             "description": "Categories, period rules, and recurring generation."},
            {"name": "expenses", "description": "General expenses and allocations."},
            {"name": "electricity", "description": "Utility bills over the electricity engine."},
            {"name": "payments",
             "description": "Payments, credits, provider advances, adjustments."},
            {"name": "rent",
             "description": "The master lease, monthly rent cycles, and HRA receipts."},
            {"name": "statements", "description": "Draft, finalize, correct, close."},
            {"name": "reconciliation",
             "description": "Full cross-month reconciliation and validation."},
            {"name": "transactions", "description": "The searchable ledger."},
            {"name": "audit", "description": "Append-only change history."},
            {"name": "reports", "description": "Reports and CSV exports."},
        ],
    )
    app.state.settings = resolved
    app.state.schema_version = 0

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type"],
    )

    @app.exception_handler(AemError)
    async def handle_aem_error(request: Request, exc: AemError) -> JSONResponse:
        logger.info("%s %s -> %s: %s", request.method, request.url.path, exc.code, exc.message)
        return _error_response(exc.http_status, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request,
                                      exc: RequestValidationError) -> JSONResponse:
        fields = [
            {"field": ".".join(str(part) for part in error.get("loc", ())[1:]) or "body",
             "message": error.get("msg", "Invalid value")}
            for error in exc.errors()
        ]
        return _error_response(422, "request_validation_error",
                               "The request could not be accepted as written.",
                               {"fields": fields})

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(request: Request,
                                    exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail
        if isinstance(detail, dict) and "error" in detail:
            return JSONResponse(status_code=exc.status_code, content=detail)
        return _error_response(exc.status_code, f"http_{exc.status_code}", str(detail))

    @app.exception_handler(sqlite3.Error)
    async def handle_sqlite_error(request: Request, exc: sqlite3.Error) -> JSONResponse:
        message = str(exc)
        logger.error("Database error on %s %s: %s", request.method, request.url.path, message)
        for marker, code, status in (
            ("closed_statement", "closed_statement", 409),
            ("locked_statement", "locked_statement", 409),
            ("append_only", "append_only_record", 409),
            ("overapplied_payment", "payment_overapplied", 409),
            ("overapplied_charge", "charge_overapplied", 409),
        ):
            if marker in message:
                return _error_response(status, code, message.split(": ", 1)[-1])
        return _error_response(500, "database_error",
                               "The database refused the change and nothing was saved.",
                               {"detail": message})

    from .api.routers import ALL_ROUTERS

    for router in ALL_ROUTERS:
        app.include_router(router)

    # The built frontend, when it exists. Unknown non-API paths fall through to
    # index.html so the client-side router can handle them; nothing outside this
    # directory is ever served.
    static_dir = resolved.project_root / "frontend" / "dist"
    if static_dir.is_dir():
        assets = static_dir / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/", include_in_schema=False)
        async def index() -> FileResponse:
            return FileResponse(static_dir / "index.html")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str) -> Any:
            if full_path.startswith(("api/", "docs", "openapi.json", "health", "redoc")):
                return _error_response(404, "not_found", "No such endpoint.")
            candidate = (static_dir / full_path).resolve()
            if candidate.is_file() and candidate.is_relative_to(static_dir.resolve()):
                return FileResponse(candidate)
            return FileResponse(static_dir / "index.html")
    else:
        @app.get("/", include_in_schema=False)
        async def no_frontend() -> JSONResponse:
            return JSONResponse({
                "application": "Apartment Expense Manager",
                "version": __version__,
                "message": ("The frontend has not been built yet. Run 'npm install && "
                            "npm run build' in ./frontend, or 'npm run dev' for the dev "
                            "server on http://127.0.0.1:5173."),
                "api_docs": "/docs",
                "health": "/health",
            })

    return app


app = create_app()

"""FastAPI application: startup, shutdown, error handling, and static serving.

Startup
    Resolves settings, connects to the configured PostgreSQL database, checks
    that its schema is the one this code expects, and closes any statement
    whose correction window elapsed while the app was not running.

    The app does not migrate. Changing the schema takes the role that owns it,
    and the serving process connects as a different role that owns nothing:
    migrations are a separate step (``scripts/manage.py init``), which the
    launch scripts run before starting the app.

    The app only connects. It never starts or stops a database server: the
    development launcher manages the local cluster, and a deployment points
    this app at an endpoint that is managed elsewhere.

While running
    A background task closes statements whose correction window has elapsed,
    once an hour by default, so a statement closes on time even when nobody
    restarts the app or presses the button.

Shutdown
    Every unit of work is a context manager that commits or rolls back before
    it returns, so a shutdown can never leave a transaction open. The shutdown
    path closes the connection pool; the database server keeps running.

Errors
    Every expected error is an :class:`aem.errors.AemError` and is rendered as
    ``{"error": {"code", "message", "details"}}`` with a matching HTTP status.
    Request validation failures use the same envelope, so the frontend has one
    error shape to handle.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from .config import Settings, get_settings
from .errors import AemError
from .persistence import Database, PersistenceError
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


async def _close_expired_periodically(db: Database, interval_seconds: float) -> None:
    """Close statements past their correction window, for as long as the app runs.

    The work is one short transaction, run off the event loop. A failed
    pass is logged and the loop carries on: a statement that could not be closed
    this hour is simply picked up by the next pass.
    """
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            closed = await asyncio.to_thread(
                statements.close_expired_statements, db, actor="scheduler")
            if closed:
                logger.info("Closed %d statement(s) past their correction window: %s",
                            len(closed), closed)
        except (AemError, PersistenceError) as exc:
            logger.warning("Could not close expired statements: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = app.state.settings
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s")
    logger.info("Apartment Expense Manager %s starting", __version__)
    database: Database = app.state.database
    logger.info("Database: %s", database.describe())

    connection = database.status()
    app.state.schema_version = connection["schema_version"]
    schema_current = (connection["reachable"]
                      and connection["schema_version"] == connection["latest_schema_version"])
    if not connection["reachable"]:
        logger.error("The database could not be reached: %s", connection["error"])
    elif not schema_current:
        logger.error(
            "The database schema is at version %s; this code needs %s. The app does not "
            "migrate. Run 'python scripts/manage.py init' as the migration role, then "
            "restart.", connection["schema_version"], connection["latest_schema_version"])
    else:
        logger.info("Schema version: %s", app.state.schema_version)

    if settings.auto_close_expired_statements and schema_current:
        try:
            closed = statements.close_expired_statements(database, actor="startup")
            if closed:
                logger.info("Closed %d statement(s) whose correction window elapsed "
                            "while the app was not running: %s", len(closed), closed)
        except AemError as exc:  # pragma: no cover - logged, not fatal
            logger.warning("Could not close expired statements at startup: %s", exc)

    if connection.get("role_is_privileged"):
        logger.warning(
            "The application is connected to PostgreSQL as %s, which is a superuser (or "
            "has REPLICATION or BYPASSRLS). Such a role can switch off the schema's "
            "integrity triggers. Serve as an ordinary role that owns nothing.",
            connection.get("role"))
    if connection.get("role_owns_tables") or connection.get("role_can_change_schema"):
        logger.warning(
            "The application is connected to PostgreSQL as %s, which owns tables or can "
            "change the schema. Serve as the runtime role and keep the owning role for "
            "'manage.py init', 'import-sqlite' and 'restore'.", connection.get("role"))

    if not settings.describe()["binds_loopback_only"]:
        logger.warning(
            "The server is configured to bind %s, which is not loopback. This "
            "application has no authentication; anyone who can reach this port can "
            "read and change the household's accounts.", settings.host)

    closer: asyncio.Task[None] | None = None
    if (settings.auto_close_expired_statements and schema_current
            and settings.auto_close_interval_minutes > 0):
        closer = asyncio.create_task(_close_expired_periodically(
            database, settings.auto_close_interval_minutes * 60))

    try:
        yield
    finally:
        if closer:
            closer.cancel()
            try:
                await closer
            except asyncio.CancelledError:
                pass
        logger.info("Shutting down; closing database connections")
        database.close()


def _error_response(status_code: int, code: str, message: str,
                    details: dict[str, Any] | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "details": details or {}}})


def create_app(settings: Settings | None = None,
               database: Database | None = None) -> FastAPI:
    """Build the application.

    ``database`` is where the data lives; when omitted it is the PostgreSQL
    database named by the settings. Nothing connects until the app starts.
    """
    resolved = settings or get_settings()
    if database is None:
        from .persistence.postgres import PostgresDatabase
        database = PostgresDatabase(resolved.database, log_sql=resolved.log_sql)
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
    app.state.database = database
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

    @app.exception_handler(PersistenceError)
    async def handle_persistence_error(request: Request,
                                       exc: PersistenceError) -> JSONResponse:
        """The database refused something the services did not anticipate.

        A schema guard (closed statement, append-only table, over-application)
        arrives with its own code and a 409; anything else is a 500 whose
        detail is the database's own message.
        """
        logger.error("Database error on %s %s: %s %s", request.method, request.url.path,
                     exc.message, exc.details.get("detail", ""))
        return _error_response(exc.http_status, exc.code, exc.message, exc.details)

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

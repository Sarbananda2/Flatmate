"""First-run setup: apartments and operator settings."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Path, Query

from ...errors import AemError
from ...schemas import (
    ApartmentCreate,
    ApartmentResponse,
    ApartmentUpdate,
    SetupApartmentRequest,
    SetupCategoriesRequest,
    SetupFinishRequest,
    SetupResidentsRequest,
)
from ...services import apartments, definitions
from ...services import setup as setup_service
from ..deps import AppSettings, Db, ApartmentId, to_http

router = APIRouter(prefix="/api", tags=["setup"])


@router.get("/apartments", summary="List apartments")
def list_apartments(db: Db) -> dict[str, Any]:
    items = apartments.list_apartments(db)
    return {"items": items, "total": len(items),
            "default_apartment_id": apartments.default_apartment_id(db)}


@router.post("/apartments", status_code=201, response_model=ApartmentResponse,
             summary="Create an apartment")
def create_apartment(payload: ApartmentCreate, db: Db,
                     settings: AppSettings) -> dict[str, Any]:
    try:
        return apartments.create_apartment(
            db, actor=settings.operator_name, **payload.model_dump())
    except AemError as exc:
        raise to_http(exc) from exc


@router.get("/apartments/{apartment_id}", response_model=ApartmentResponse,
            summary="Read one apartment")
def read_apartment(db: Db, apartment_id: str = Path()) -> dict[str, Any]:
    try:
        return apartments.read_apartment(db, apartment_id)
    except AemError as exc:
        raise to_http(exc) from exc


@router.patch("/apartments/{apartment_id}", response_model=ApartmentResponse,
              summary="Update an apartment")
def update_apartment(payload: ApartmentUpdate, db: Db, settings: AppSettings,
                     apartment_id: str = Path()) -> dict[str, Any]:
    try:
        return apartments.update_apartment(
            db, apartment_id, actor=settings.operator_name,
            **payload.model_dump(exclude_unset=True))
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/apartments/{apartment_id}/starter-definitions", status_code=201,
             summary="Add a conventional starter set of expense definitions")
def seed_definitions(db: Db, settings: AppSettings,
                     apartment_id: str = Path()) -> dict[str, Any]:
    """Create ordinary, fully editable definitions. Nothing here is hardcoded
    behaviour: these are rows the operator can rename, re-rule, or deactivate."""
    try:
        created = definitions.seed_starter_definitions(
            db, apartment_id, actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc
    return {"created": created, "created_count": len(created)}


@router.get("/settings", summary="Operator settings")
def read_settings(db: Db, settings: AppSettings) -> dict[str, Any]:
    return {
        "default_apartment_id": apartments.default_apartment_id(db),
        "server": settings.describe(),
        "security_note": (
            "This application has no authentication and is intended to run on the "
            "operator's own machine, bound to localhost. Do not expose it to a network."),
    }


@router.put("/settings/default-apartment/{apartment_id}", summary="Set the default apartment")
def set_default_apartment(db: Db, settings: AppSettings,
                          apartment_id: str = Path()) -> dict[str, Any]:
    try:
        apartments.read_apartment(db, apartment_id)
        apartments.set_setting(db, apartments.DEFAULT_APARTMENT_SETTING, apartment_id,
                               actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc
    return {"default_apartment_id": apartment_id}


# --------------------------------------------------------------------------- #
# Guided setup
#
# Each step commits as it completes, and the resume point is derived from what
# exists on the server rather than from anything the browser remembered. That
# is what makes closing the tab halfway through harmless.
# --------------------------------------------------------------------------- #

@router.get("/setup/state", tags=["guided setup"],
            summary="Where setup has got to, and where to resume")
def setup_state(db: Db,
                apartment_id: Annotated[str | None, Query()] = None) -> dict[str, Any]:
    """Read from real records: an apartment row, resident rows, definition rows.

    There is no separate progress flag that could drift out of step with the
    data, so this is correct after a reload, a cleared browser, or a different
    machine.
    """
    try:
        return setup_service.setup_state(db, apartment_id)
    except AemError as exc:
        raise to_http(exc) from exc


@router.get("/setup/catalogue", tags=["guided setup"],
            summary="The essentials the guided setup offers")
def setup_catalogue() -> dict[str, Any]:
    """Suggestions only. Each becomes an ordinary editable definition row; the
    application has no special knowledge of any of these names."""
    items = setup_service.catalogue()
    return {"items": items, "total": len(items),
            "suggested_count": sum(1 for item in items if item["suggested"])}


@router.post("/setup/apartment", status_code=201, tags=["guided setup"],
             summary="Step 1 — create the apartment")
def setup_apartment(payload: SetupApartmentRequest, db: Db,
                    settings: AppSettings) -> dict[str, Any]:
    """The identifier is derived from the name, so the operator never types one."""
    try:
        result = setup_service.create_apartment_step(
            db, actor=settings.operator_name, **payload.model_dump(exclude_none=True))
        return {**result, "state": setup_service.setup_state(db, result["apartment_id"])}
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/setup/residents", status_code=201, tags=["guided setup"],
             summary="Step 2 — add everyone at once")
def setup_residents(payload: SetupResidentsRequest, db: Db,
                    apartment_id: ApartmentId, settings: AppSettings) -> dict[str, Any]:
    """One transaction for the whole list. Re-submitting is harmless: a name that
    already exists is skipped rather than refused."""
    try:
        result = setup_service.create_residents_step(
            db, apartment_id=apartment_id, names=payload.names,
            actor=settings.operator_name)
        return {**result, "state": setup_service.setup_state(db, apartment_id)}
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/setup/categories", status_code=201, tags=["guided setup"],
             summary="Step 3 — add the chosen categories at once")
def setup_categories(payload: SetupCategoriesRequest, db: Db,
                     apartment_id: ApartmentId, settings: AppSettings) -> dict[str, Any]:
    """One transaction for the whole list. A category the operator typed goes
    through exactly the same path as one picked from the catalogue."""
    try:
        result = setup_service.create_categories_step(
            db, apartment_id=apartment_id,
            items=[item.model_dump() for item in payload.items],
            actor=settings.operator_name)
        return {**result, "state": setup_service.setup_state(db, apartment_id)}
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/setup/finish", tags=["guided setup"],
             summary="Step 4 — mark setup done, optionally opening the first statement")
def setup_finish(payload: SetupFinishRequest, db: Db,
                 settings: AppSettings,
                 apartment_id: Annotated[str | None, Query()] = None) -> dict[str, Any]:
    """Marking it done only stops the application prompting. Nothing is locked:
    residents and categories stay editable and the guided setup can be reopened."""
    try:
        return setup_service.finish_setup(
            db, apartment_id=apartment_id,
            create_statement_for=payload.create_statement_for,
            actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/setup/reopen", tags=["guided setup"],
             summary="Prompt for the guided setup again")
def setup_reopen(db: Db, settings: AppSettings) -> dict[str, Any]:
    try:
        return setup_service.reopen_setup(db, actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc

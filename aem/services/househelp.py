"""House help and cooks: the household's workers, their monthly salaries, and paying them.

A worker -- a maid, a cook, a cleaner -- does one role or several, each on its
own monthly salary and leave allowance, and each shared its own way: by every
resident (``equal``) or only by those who opted in (``opt_in``). The arithmetic
is :mod:`aem.engine.househelp`'s.

**A month is saved from its attendance.** Saving it records each role's days
absent, whether leave beyond the allowance is deducted or waived, and any bonus
or extra, and works out each role's salary for the month from the role as it
then stands. The roles saved are the month's: one left out of a later save is
taken off it. A role given a worker joins this month at once, once the month
has been saved; one set up by a month's end and not on it otherwise shows with
it, unsaved and on nobody's bill, until the month is saved again. The month is
billed as one expense under the household's house help definition -- a
component per role, a charge per resident -- split between the residents living
in the flat and anyone already on the bill, so a resident who moves out keeps
the month they were in. **Nothing prorates**, as
everywhere in the household.

The month is paid in two steps, as every household service's is (see
:mod:`aem.services.service_bills`): residents pay their shares into the House
Kitty, and each worker is paid from it, in one payout or several -- or by a
resident from their own account, who pays their own share through the kitty
and is paid back the rest. Each payout names the worker it paid.

While the month's statement is a draft it can be saved again: charges change in
place, and what each resident paid moves to their new share (see
:mod:`aem.services.bill_shares`). A worker's pay never goes below what they have
been paid for the month (``househelp_worker_already_paid``): payouts are money
that has left. Once the statement is finalized the month is frozen.
"""
from __future__ import annotations

import calendar
import json
import logging
from typing import Any, Mapping, Sequence

from ..engine import (
    HouseHelpCalculationError,
    allocate_role_cost,
    calculate_excess_leaves,
    calculate_role_salary,
    consolidate_resident_shares,
    prorate_salary,
)
from ..errors import AemError, ConflictError, NotFoundError, ValidationError
from ..ids import new_id, now_timestamp
from ..money import ensure_paise, format_inr
from ..periods import month_label, normalize_month, parse_iso_date
from ..persistence import Database, UnitOfWork
from . import (
    audit,
    bill_shares,
    definitions,
    deletions,
    expenses,
    payments,
    resident_payouts,
    service_bills,
)
from .common import (
    get_apartment,
    get_resident,
    list_residents,
    optional_text,
    require_text,
    resident_ids,
)
from .residents import _upi_id

SERVICE_TYPE = "domestic_help"
DEFAULT_NAME = "Household Staff"
#: What each charge's allocation basis says the bill is.
SOURCE = "househelp"
SPLIT_METHODS = ("equal", "opt_in")
EXCESS_LEAVE_ACTIONS = ("deduct", "waive")
DEFAULT_ALLOWED_LEAVES = 2
#: Who saved a month that was saved as it was first looked at.
AUTO_SAVE_ACTOR = "househelp_auto_save"

logger = logging.getLogger(__name__)
#: Where a payout to a worker came from: the kitty, or a resident's own account.
PAYOUT_MODES = ("pool", "resident_advance")


def _today() -> str:
    return now_timestamp()[:10]


def _current_month() -> str:
    return _today()[:7]


def _days_in_month(month: str) -> int:
    year, number = (int(part) for part in month.split("-"))
    return calendar.monthrange(year, number)[1]


def _days_active(joined_date: str | None, month: str) -> int:
    """The days of ``month`` a worker who joined on ``joined_date`` was there:
    none before the month they joined, the days from that day on in it, and
    every day after."""
    days = _days_in_month(month)
    joined = joined_date or "0000-00-00"
    if joined[:7] > month:
        return 0
    if joined[:7] == month:
        return days - int(joined[8:10]) + 1
    return days


def _names(uow: UnitOfWork, apartment_id: str) -> dict[str, str]:
    return {row["resident_id"]: row["display_name"]
            for row in list_residents(uow, apartment_id)}


# ----------------------------------------------------------- the service --

def _service_and_config(uow: UnitOfWork, apartment_id: str
                        ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    service = uow.furniture.live_service(apartment_id, SERVICE_TYPE)
    config = uow.househelp.config(service["service_id"]) if service else None
    return service, config


def _require_service(uow: UnitOfWork, apartment_id: str
                     ) -> tuple[dict[str, Any], dict[str, Any]]:
    service, config = _service_and_config(uow, apartment_id)
    if service is None or config is None:
        raise ConflictError("Add a worker to set up house help first.",
                            code="househelp_not_configured")
    return service, config


def _ensure_definition(db: Database, apartment_id: str, actor: str) -> str:
    """The household's house help definition: the service's, the household's own
    'House help', or one created for it."""
    with db.read() as uow:
        get_apartment(uow, apartment_id)
        _, config = _service_and_config(uow, apartment_id)
        definition_id = ((config or {}).get("expense_definition_id")
                         or uow.househelp.find_definition_id(apartment_id))
        id_taken = definition_id is None and uow.househelp.definition_id_taken("house-help")
    if definition_id is None:
        definition_id = definitions.create_expense_definition(
            db, apartment_id=apartment_id,
            definition_id=new_id("house-help") if id_taken else "house-help",
            name="House help", category="Household help", period_rule="current_month",
            default_allocation_method="custom", is_recurring=False, recurrence="none",
            sort_order=30, notes="Created by House Help. Each month's bill is saved from "
                                 "its attendance.", actor=actor)["expense_definition_id"]
    return definition_id


def _ensure_service_in(uow: UnitOfWork, apartment_id: str, definition_id: str,
                       timestamp: str, actor: str) -> tuple[dict[str, Any], dict[str, Any]]:
    service, config = _service_and_config(uow, apartment_id)
    if service is None:
        service_id = new_id("svc")
        uow.furniture.insert_service(
            service_id=service_id, apartment_id=apartment_id, service_type=SERVICE_TYPE,
            display_name=DEFAULT_NAME, provider_name=DEFAULT_NAME, billing_day_of_month=None,
            created_at=timestamp)
        audit.record(uow, entity_type="household_service", entity_id=service_id,
                     action="create", actor=actor, apartment_id=apartment_id,
                     new_value={"service_type": SERVICE_TYPE, "display_name": DEFAULT_NAME,
                                "expense_definition_id": definition_id})
        service = uow.furniture.service_in_apartment(service_id, apartment_id)
    if config is None:
        uow.househelp.insert_config(service_id=service["service_id"], apartment_id=apartment_id,
                                    expense_definition_id=definition_id, created_at=timestamp)
        config = uow.househelp.config(service["service_id"])
    return service, config


def ensure_service(db: Database, apartment_id: str, *,
                   actor: str = "operator") -> dict[str, Any]:
    """The household's house help service, set up if it has none: a
    'domestic_help' service, and the definition its months are billed under."""
    definition_id = _ensure_definition(db, apartment_id, actor)
    with db.transaction() as uow:
        return _public_service(*_ensure_service_in(uow, apartment_id, definition_id,
                                                   now_timestamp(), actor))


def _public_service(service: Mapping[str, Any] | None,
                    config: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if service is None:
        return None
    return {**{key: service[key] for key in (
        "service_id", "service_type", "display_name", "status", "created_at", "updated_at")},
        "expense_definition_id": config["expense_definition_id"] if config else None}


# ------------------------------------------------------------- the workers --

def _public_role(role: Mapping[str, Any], opted: Sequence[str],
                 names: Mapping[str, str]) -> dict[str, Any]:
    return {
        **{key: role[key] for key in (
            "role_id", "worker_id", "worker_name", "role_name", "monthly_salary_paise",
            "split_method", "created_at")},
        "participant_ids": list(opted),
        "participants": [{"resident_id": rid, "display_name": names.get(rid, rid)}
                         for rid in sorted(opted, key=lambda r: (names.get(r, r), r))],
    }


def _public_worker(worker: Mapping[str, Any], roles: Sequence[Mapping[str, Any]]
                   ) -> dict[str, Any]:
    return {**{key: worker[key] for key in (
        "worker_id", "name", "phone", "upi_id", "notes", "is_active",
        "allowed_leaves_per_month", "joined_date", "created_at", "updated_at")},
        "roles": [role for role in roles if role["worker_id"] == worker["worker_id"]]}


def _public_roles(uow: UnitOfWork, apartment_id: str) -> list[dict[str, Any]]:
    names = _names(uow, apartment_id)
    opted = uow.househelp.participants(apartment_id)
    return [_public_role(role, opted.get(role["role_id"], []), names)
            for role in uow.househelp.roles(apartment_id)]


def _workers(uow: UnitOfWork, apartment_id: str, *,
             include_inactive: bool = True) -> list[dict[str, Any]]:
    roles = _public_roles(uow, apartment_id)
    return [_public_worker(worker, roles) for worker in uow.househelp.workers(apartment_id)
            if include_inactive or worker["is_active"]]


def _staff(uow: UnitOfWork, apartment_id: str, *,
           include_inactive: bool = True) -> dict[str, Any]:
    return {"items": _workers(uow, apartment_id, include_inactive=include_inactive),
            "unassigned_roles": [role for role in _public_roles(uow, apartment_id)
                                 if role["worker_id"] is None]}


def _require_worker(uow: UnitOfWork, apartment_id: str, worker_id: Any) -> dict[str, Any]:
    worker = uow.househelp.worker(worker_id) if isinstance(worker_id, str) else None
    if worker is None or worker["apartment_id"] != apartment_id:
        raise NotFoundError("No such worker.", code="househelp_worker_not_found",
                            details={"worker_id": worker_id})
    return worker


def _clean_worker(payload: Mapping[str, Any], *, creating: bool) -> dict[str, Any]:
    """The worker's fields sent, checked. Creating, the name and the day they
    joined are required."""
    cleaned: dict[str, Any] = {}
    if creating or "name" in payload:
        cleaned["name"] = require_text(payload.get("name"), "name", max_length=100)
    if creating or "joined_date" in payload:
        cleaned["joined_date"] = parse_iso_date(payload.get("joined_date"),
                                                "joined_date").isoformat()
    if "phone" in payload:
        cleaned["phone"] = optional_text(payload["phone"], "phone", max_length=50)
    if "upi_id" in payload:
        cleaned["upi_id"] = _upi_id(payload["upi_id"])
    if "notes" in payload:
        cleaned["notes"] = optional_text(payload["notes"], "notes", max_length=2000)
    if "is_active" in payload:
        if not isinstance(payload["is_active"], bool):
            raise ValidationError("is_active must be true or false.",
                                  details={"field": "is_active"})
        cleaned["is_active"] = payload["is_active"]
    if creating or "allowed_leaves_per_month" in payload:
        cleaned["allowed_leaves_per_month"] = _allowed_leaves(
            payload.get("allowed_leaves_per_month", DEFAULT_ALLOWED_LEAVES))
    return cleaned


def _allowed_leaves(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 31:
        raise ValidationError("allowed_leaves_per_month must be a number of days from 0 to 31.",
                              details={"field": "allowed_leaves_per_month"})
    return value


def create_worker(db: Database, apartment_id: str, payload: Mapping[str, Any], *,
                  actor: str = "operator") -> dict[str, Any]:
    """Add a worker: ``name`` and ``joined_date`` -- the day they joined, from
    which they are paid -- and optionally ``phone``, ``upi_id``, ``notes``,
    ``is_active`` (true unless sent) and ``allowed_leaves_per_month`` -- the days
    off a month, across every role they do, before pay is deducted (2). The
    household's house help is set up with its first worker."""
    cleaned = {"phone": None, "upi_id": None, "notes": None, "is_active": True,
               **_clean_worker(payload, creating=True)}
    definition_id = _ensure_definition(db, apartment_id, actor)
    timestamp = now_timestamp()
    with db.transaction() as uow:
        _ensure_service_in(uow, apartment_id, definition_id, timestamp, actor)
        worker_id = new_id("hhworker")
        uow.househelp.insert_worker(worker_id=worker_id, apartment_id=apartment_id,
                                    created_at=timestamp, **cleaned)
        audit.record(uow, entity_type="househelp_worker", entity_id=worker_id,
                     action="create", actor=actor, apartment_id=apartment_id,
                     new_value=cleaned)
        return next(worker for worker in _workers(uow, apartment_id)
                    if worker["worker_id"] == worker_id)


def update_worker(db: Database, apartment_id: str, worker_id: str,
                  payload: Mapping[str, Any], *, actor: str = "operator") -> dict[str, Any]:
    """Change a worker's details. Only the fields sent change; a null clears an
    optional one. A worker who has left is made inactive: their roles join no
    month saved after, and every month already saved keeps them. A new leave
    allowance or joining day, like a role's new salary, reaches a month when
    it is saved."""
    cleaned = _clean_worker(payload, creating=False)
    timestamp = now_timestamp()
    with db.transaction() as uow:
        get_apartment(uow, apartment_id)
        before = _require_worker(uow, apartment_id, worker_id)
        changed = {key: value for key, value in cleaned.items() if before[key] != value}
        if changed:
            uow.househelp.update_worker(worker_id, updated_at=timestamp, **changed)
            audit.record(uow, entity_type="househelp_worker", entity_id=worker_id,
                         action="update", actor=actor, apartment_id=apartment_id,
                         old_value={key: before[key] for key in changed}, new_value=changed)
        return next(worker for worker in _workers(uow, apartment_id)
                    if worker["worker_id"] == worker_id)


def list_workers(db: Database, apartment_id: str, *,
                 include_inactive: bool = True) -> list[dict[str, Any]]:
    """Every worker with their roles and who shares each, the active first."""
    with db.read() as uow:
        get_apartment(uow, apartment_id)
        return _workers(uow, apartment_id, include_inactive=include_inactive)


def list_staff(db: Database, apartment_id: str, *,
               include_inactive: bool = True) -> dict[str, Any]:
    """``items``: every worker with their roles, as :func:`list_workers`; and
    ``unassigned_roles``: the roles not yet given a worker."""
    with db.read() as uow:
        get_apartment(uow, apartment_id)
        return _staff(uow, apartment_id, include_inactive=include_inactive)


# --------------------------------------------------------------- the roles --

def _require_role(uow: UnitOfWork, apartment_id: str, role_id: Any) -> dict[str, Any]:
    role = uow.househelp.role(role_id) if isinstance(role_id, str) else None
    if role is None or role["apartment_id"] != apartment_id:
        raise NotFoundError("No such role.", code="househelp_role_not_found",
                            details={"role_id": role_id})
    return role


def _clean_role(payload: Mapping[str, Any], *, creating: bool) -> dict[str, Any]:
    """The role's fields sent, checked. Creating, the name and salary are
    required; the split is equal unless sent. Leave is the worker's."""
    cleaned: dict[str, Any] = {}
    if creating or "role_name" in payload:
        cleaned["role_name"] = require_text(payload.get("role_name"), "role_name",
                                            max_length=100)
    if creating or "monthly_salary_paise" in payload:
        cleaned["monthly_salary_paise"] = ensure_paise(
            payload.get("monthly_salary_paise"), "monthly_salary_paise", allow_zero=False)
    if creating or "split_method" in payload:
        method = payload.get("split_method", "equal")
        if method not in SPLIT_METHODS:
            raise ValidationError("Unsupported split_method.",
                                  details={"field": "split_method",
                                           "allowed": list(SPLIT_METHODS)})
        cleaned["split_method"] = method
    return cleaned


def _clean_participants(uow: UnitOfWork, apartment_id: str, value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)) or not all(isinstance(rid, str) for rid in value):
        raise ValidationError("participant_ids must be a list of resident IDs.",
                              details={"field": "participant_ids"})
    known = set(resident_ids(uow, apartment_id))
    strangers = sorted(set(value) - known)
    if strangers:
        raise ValidationError("Each participant must be a resident of this flat.",
                              code="househelp_participant_not_resident",
                              details={"field": "participant_ids", "resident_ids": strangers})
    return sorted(set(value))


def _role_view(uow: UnitOfWork, apartment_id: str, role_id: str) -> dict[str, Any]:
    return _public_role(uow.househelp.role(role_id),
                        uow.househelp.participants(apartment_id).get(role_id, []),
                        _names(uow, apartment_id))


def create_role(db: Database, apartment_id: str, payload: Mapping[str, Any], *,
                actor: str = "operator") -> dict[str, Any]:
    """Add a role: ``role_name``, ``monthly_salary_paise``, and optionally
    ``worker_id`` -- who does it -- ``split_method`` ('equal') and
    ``participant_ids`` -- who shares an opt-in role. The role joins the months
    saved from now on, once it has a worker: a role with none joins no month, as
    there is nobody to pay. Given a worker, it joins this month at once if the
    month has been saved (:func:`_join_this_month`)."""
    cleaned = _clean_role(payload, creating=True)
    definition_id = _ensure_definition(db, apartment_id, actor)
    timestamp = now_timestamp()
    with db.transaction() as uow:
        _ensure_service_in(uow, apartment_id, definition_id, timestamp, actor)
        worker_id = (None if payload.get("worker_id") is None else
                     _require_worker(uow, apartment_id, payload["worker_id"])["worker_id"])
        opted = (_clean_participants(uow, apartment_id, payload["participant_ids"])
                 if payload.get("participant_ids") is not None else [])
        role_id = new_id("hhrole")
        uow.househelp.insert_role(role_id=role_id, worker_id=worker_id,
                                  apartment_id=apartment_id, created_at=timestamp, **cleaned)
        uow.househelp.replace_participants(role_id, apartment_id, opted)
        audit.record(uow, entity_type="househelp_role", entity_id=role_id, action="create",
                     actor=actor, apartment_id=apartment_id,
                     new_value={"worker_id": worker_id, **cleaned, "participant_ids": opted})
        view = _role_view(uow, apartment_id, role_id)
    if worker_id is not None:
        _join_this_month(db, apartment_id, role_id)
    return view


def update_role(db: Database, apartment_id: str, role_id: str, payload: Mapping[str, Any], *,
                actor: str = "operator") -> dict[str, Any]:
    """Change a role's name, salary or split, or the worker who
    does it -- ``worker_id`` null for nobody yet. Only the fields sent change. A
    month already saved keeps the role as it was saved; the change reaches it
    when it is saved again.

    A role moves to another worker, or back to nobody, only while no month has
    been saved with it (``househelp_role_has_months``): each saved month's
    salary was the worker's, and may have been paid to them. Give the new
    worker a role of their own. Given a worker, a role joins this month at once
    if the month has been saved (:func:`_join_this_month`)."""
    cleaned = _clean_role(payload, creating=False)
    with db.transaction() as uow:
        get_apartment(uow, apartment_id)
        before = _require_role(uow, apartment_id, role_id)
        changed = {key: value for key, value in cleaned.items() if before[key] != value}
        if "worker_id" in payload and payload["worker_id"] != before["worker_id"]:
            worker = (None if payload["worker_id"] is None
                      else _require_worker(uow, apartment_id, payload["worker_id"]))
            months = uow.househelp.months_for_role(role_id)
            if months:
                instead = (f" Give {worker['name']} a role of their own instead."
                           if worker else "")
                raise ConflictError(
                    f"{before['role_name']} is saved on {month_label(months[-1])} as "
                    f"{before['worker_name']}'s, so it stays theirs.{instead}",
                    code="househelp_role_has_months",
                    details={"role_id": role_id, "settlement_months": months})
            changed["worker_id"] = worker["worker_id"] if worker else None
        if changed:
            uow.househelp.update_role(role_id, **changed)
            audit.record(uow, entity_type="househelp_role", entity_id=role_id,
                         action="update", actor=actor, apartment_id=apartment_id,
                         old_value={key: before[key] for key in changed}, new_value=changed)
        view = _role_view(uow, apartment_id, role_id)
    if changed.get("worker_id"):
        _join_this_month(db, apartment_id, role_id)
    return view


def set_role_participants(db: Database, apartment_id: str, role_id: str,
                          participant_ids: Sequence[str], *,
                          actor: str = "operator") -> dict[str, Any]:
    """Who shares an opt-in role, replacing whoever did. An equal role is shared
    by every resident whoever is listed. As with :func:`update_role`, a month
    already saved keeps its split until it is saved again."""
    with db.transaction() as uow:
        get_apartment(uow, apartment_id)
        _require_role(uow, apartment_id, role_id)
        opted = _clean_participants(uow, apartment_id, participant_ids)
        before = uow.househelp.participants(apartment_id).get(role_id, [])
        if opted != before:
            uow.househelp.replace_participants(role_id, apartment_id, opted)
            audit.record(uow, entity_type="househelp_role", entity_id=role_id,
                         action="update", actor=actor, apartment_id=apartment_id,
                         field_name="participant_ids", old_value=before, new_value=opted)
        return _role_view(uow, apartment_id, role_id)


def delete_role(db: Database, apartment_id: str, role_id: str, *,
                actor: str = "operator") -> dict[str, Any]:
    """Delete a role added by mistake, or one the worker no longer does.

    It comes off every month it was saved in while that month is open, and
    those months' bills are written again without it. A role on a month whose
    statement has been finalized stays on the books
    (``househelp_role_on_finalized_month``): make the worker inactive instead.
    Returns the staff as they then stand, as :func:`list_staff`, with the
    workers under ``workers``."""
    timestamp = now_timestamp()
    with db.transaction() as uow:
        get_apartment(uow, apartment_id)
        role = _require_role(uow, apartment_id, role_id)
        months = uow.househelp.months_for_role(role_id)
        frozen = [month for month in months
                  if not deletions.month_is_open(uow, apartment_id, month)]
        if frozen:
            raise ConflictError(
                f"{role['role_name']} is on the {month_label(frozen[-1])} bill, whose statement "
                f"has been finalized, so it stays on the household's books. Make "
                f"{role['worker_name']} inactive instead.",
                code="househelp_role_on_finalized_month",
                details={"role_id": role_id, "settlement_months": frozen})
        if months:
            service, config = _require_service(uow, apartment_id)
            for month in months:
                uow.househelp.delete_record(role_id, month)
                _write_bill(uow, apartment_id, service, config, month, timestamp, actor)
        uow.househelp.delete_role(role_id)
        audit.record(uow, entity_type="househelp_role", entity_id=role_id, action="delete",
                     actor=actor, apartment_id=apartment_id,
                     old_value={key: role[key] for key in (
                         "worker_id", "role_name", "monthly_salary_paise", "split_method")},
                     new_value={"settlement_months_rewritten": months})
        staff = _staff(uow, apartment_id)
        return {"workers": staff["items"], "unassigned_roles": staff["unassigned_roles"]}


# -------------------------------------------------------- the month's bill --

def _stored_basis(allocations: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    for row in allocations:
        if row.get("allocation_basis_json"):
            try:
                return json.loads(row["allocation_basis_json"])
            except ValueError:
                return {}
    return {}


def _month_expense(uow: UnitOfWork, config: Mapping[str, Any] | None, apartment_id: str,
                   month: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """``(the month's bill, a house help expense recorded for it some other way)``."""
    definition_id = (config or {}).get("expense_definition_id")
    if not definition_id:
        return None, None
    foreign = None
    for expense in uow.furniture.expenses_for_month(apartment_id, definition_id, month):
        basis = _stored_basis(uow.furniture.allocations_for_expense(expense["expense_id"]))
        if basis.get("source") == SOURCE:
            return expense, None
        foreign = foreign or expense
    return None, foreign


def _month_residents(uow: UnitOfWork, apartment_id: str,
                     allocations: Sequence[Mapping[str, Any]]) -> list[str]:
    """Who the month is split between: everyone living in the flat, and whoever
    the bill already charges -- a resident who moved out keeps the month they
    were in."""
    everyone = set(resident_ids(uow, apartment_id))
    basis = _stored_basis(allocations)
    on_bill = set(basis.get("participants")
                  or [row["resident_id"] for row in allocations if row["amount_paise"]])
    return sorted(set(resident_ids(uow, apartment_id, active_only=True)) | (on_bill & everyone))


def _salary(role: Mapping[str, Any], entry: Mapping[str, Any], month: str) -> dict[str, Any]:
    """A role's month: its worker's attendance as entered, and the salary the
    role comes to -- for the days from the day its worker joined, less each of
    their days beyond their allowance at the role's daily rate."""
    days = _days_in_month(month)
    active = _days_active(role["worker_joined_date"], month)
    if entry["days_absent"] > active:
        raise ValidationError(
            f"{role['worker_name']} joined on {role['worker_joined_date']}, so was due "
            f"{active} days of {month_label(month)}: they cannot have been absent for "
            f"{entry['days_absent']}.", code="househelp_absent_before_joining",
            details={"role_id": role["role_id"], "days_active": active})
    excess = calculate_excess_leaves(entry["days_absent"], role["allowed_leaves_per_month"])
    try:
        final, deduction = calculate_role_salary(
            role["monthly_salary_paise"], excess, days, entry["excess_leave_action"],
            entry["bonus_or_extra_paise"], entry.get("manual_deduction_paise"),
            days_active=active)
    except HouseHelpCalculationError as exc:
        raise ValidationError(f"{role['role_name']} ({role['worker_name']}): {exc}",
                              code="househelp_salary_invalid",
                              details={"role_id": role["role_id"]}) from exc
    return {
        "role_id": role["role_id"], "role_name": role["role_name"],
        "worker_id": role["worker_id"], "worker_name": role["worker_name"],
        "split_method": role["split_method"],
        "base_salary_paise": prorate_salary(role["monthly_salary_paise"], active, days),
        # Set only for the month the worker joined in, if not on its first day.
        "pro_rated_days": active if active < days else None,
        "allowed_leaves_per_month": role["allowed_leaves_per_month"],
        "days_absent": entry["days_absent"],
        "excess_leaves": excess,
        "excess_leave_action": entry["excess_leave_action"],
        "deduction_paise": deduction,
        "bonus_or_extra_paise": entry["bonus_or_extra_paise"],
        "extra_notes": entry.get("extra_notes"),
        "final_salary_paise": final,
    }


def _saved_role(record: Mapping[str, Any]) -> dict[str, Any]:
    """A role's month as saved. Its base salary is what it was saved at, for
    the days its worker was there; the leave allowance is its worker's."""
    days = _days_in_month(record["settlement_month"])
    active = _days_active(record["worker_joined_date"], record["settlement_month"])
    return {
        "role_id": record["role_id"], "role_name": record["role_name"],
        "worker_id": record["worker_id"], "worker_name": record["worker_name"],
        "split_method": record["split_method"],
        "base_salary_paise": (record["final_salary_paise"] + record["deduction_paise"]
                              - record["bonus_or_extra_paise"]),
        "pro_rated_days": active if 0 < active < days else None,
        "allowed_leaves_per_month": record["allowed_leaves_per_month"],
        "days_absent": record["days_absent"],
        "excess_leaves": calculate_excess_leaves(record["days_absent"],
                                                 record["allowed_leaves_per_month"]),
        "excess_leave_action": record["excess_leave_action"],
        "deduction_paise": record["deduction_paise"],
        "bonus_or_extra_paise": record["bonus_or_extra_paise"],
        "extra_notes": record["extra_notes"],
        "final_salary_paise": record["final_salary_paise"],
    }


def _split(uow: UnitOfWork, apartment_id: str, roles: Sequence[Mapping[str, Any]],
           sharing: Sequence[str]) -> dict[str, Any]:
    """Each role's cost split between who shares it, and each resident's bill.

    ``problem`` is why a role cannot be split -- nobody to share it -- and that
    role is then left without shares."""
    opted = uow.househelp.participants(apartment_id)
    split_roles, parts, problem = [], [], None
    for role in roles:
        sharers = (list(sharing) if role["split_method"] == "equal"
                   else sorted(set(opted.get(role["role_id"], [])) & set(sharing)))
        try:
            shares = allocate_role_cost(role["final_salary_paise"], role["split_method"],
                                        sharers, sharing)
        except HouseHelpCalculationError:
            shares = {}
            if problem is None:
                what = f"{role['role_name']} ({role['worker_name']})"
                problem = ValidationError(
                    f"Nobody lives in the flat, so there is nobody to share {what}."
                    if role["split_method"] == "equal" else
                    f"Nobody living in the flat has opted in to {what}, so there is nobody "
                    "to share its cost. Choose who shares it first.",
                    code="househelp_role_has_no_participants",
                    details={"role_id": role["role_id"]})
        parts.append(shares)
        split_roles.append({**role, "sharers": sorted(shares), "shares": shares})
    dues: dict[str, int] = {}
    for role in roles:
        dues[role["worker_id"]] = dues.get(role["worker_id"], 0) + role["final_salary_paise"]
    return {"roles": split_roles, "participants": list(sharing),
            "shares": consolidate_resident_shares(parts, sharing),
            "total_paise": sum(role["final_salary_paise"] for role in roles),
            "dues": dues, "problem": problem}


def _bill_basis(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Kept with each charge, so the month reads as it was split."""
    return {"source": SOURCE, "method": "custom", "participants": list(plan["participants"]),
            "roles": [{key: role[key] for key in (
                "role_id", "role_name", "worker_id", "worker_name", "split_method",
                "final_salary_paise", "shares")} for role in plan["roles"]]}


def _bill_components(plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [{"component_id": new_id("component"),
             "name": f"{role['role_name']} ({role['worker_name']})"[:200],
             "amount_paise": role["final_salary_paise"]}
            for role in plan["roles"] if role["final_salary_paise"]]


def _bill_payees(plan: Mapping[str, Any]) -> str:
    return ", ".join(dict.fromkeys(role["worker_name"] for role in plan["roles"]))[:200]


def _bill_notes(plan: Mapping[str, Any]) -> str:
    roles = "; ".join(f"{role['role_name']} ({role['worker_name']}) "
                      f"{format_inr(role['final_salary_paise'])}" for role in plan["roles"])
    return f"{roles}. Split between {len(plan['participants'])}."[:2000]


def _has_payments(uow: UnitOfWork, expense_id: str,
                  allocations: Sequence[Mapping[str, Any]]) -> bool:
    return bool(sum(row["settled_paise"] for row in allocations)
                or uow.furniture.service_disbursements(expense_id)
                or uow.furniture.linked_advance_total_paise(expense_id)
                or uow.payments.kitty_payments_for_bill(expense_id))


def _paid_to_workers(tranches: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    paid: dict[str, int] = {}
    for tranche in tranches:
        if tranche.get("worker_id"):
            paid[tranche["worker_id"]] = paid.get(tranche["worker_id"], 0) + tranche["amount_paise"]
    return paid


def _write_bill(uow: UnitOfWork, apartment_id: str, service: Mapping[str, Any],
                config: Mapping[str, Any], month: str, timestamp: str, actor: str) -> None:
    """Bill the month from its saved records, or bring its bill to them.

    Charges change in place, and what each resident paid -- into the kitty, or to
    a worker -- follows their new share. Refused when a role cannot be split,
    when a worker would be owed less than they have been paid, or when the kitty
    has paid somebody back more than it would then owe them. A month left with
    nothing to bill, and nothing paid towards its bill, has none."""
    expense, foreign = _month_expense(uow, config, apartment_id, month)
    if foreign is not None:
        raise ConflictError(
            f"{month_label(month)}'s house help is already recorded as an expense entered by "
            "hand. Correct that expense instead.", code="househelp_bill_recorded_elsewhere",
            details={"expense_id": foreign["expense_id"]})
    allocations = uow.furniture.allocations_for_expense(expense["expense_id"]) if expense else []
    roles = [_saved_role(record) for record in uow.househelp.records_for_month(apartment_id, month)]
    plan = _split(uow, apartment_id, roles, _month_residents(uow, apartment_id, allocations))
    if plan["problem"] is not None:
        raise plan["problem"]
    name = month_label(month)
    statement = uow.furniture.statement_for_month(apartment_id, month)

    if expense is None:
        if not plan["total_paise"]:
            return
        expense_id = new_id("househelp")
        shares = plan["shares"]
        expenses._create_expense(
            uow, apartment_id=apartment_id, expense_id=expense_id,
            description=f"{service['display_name']} for {name}",
            amount_paise=plan["total_paise"], expense_date=f"{month}-01",
            allocation_method="custom",
            allocations_paise={rid: shares.get(rid, 0) for rid in resident_ids(uow, apartment_id)},
            allocation_basis=_bill_basis(plan), provider_name=_bill_payees(plan),
            expense_definition_id=config["expense_definition_id"],
            statement_id=statement["statement_id"] if statement else None,
            period_rule="current_month", service_period=month, settlement_period=month,
            notes=_bill_notes(plan), created_at=timestamp, actor=actor)
        uow.househelp.replace_components(expense_id, _bill_components(plan))
        audit.record(uow, entity_type="household_service", entity_id=service["service_id"],
                     action="generate", actor=actor, apartment_id=apartment_id,
                     new_value={"settlement_month": month, "expense_id": expense_id,
                                "total_paise": plan["total_paise"],
                                "shares": {rid: paise for rid, paise in shares.items() if paise}})
        return

    expense_id = expense["expense_id"]
    tranches = uow.furniture.service_disbursements(expense_id)
    paid = _paid_to_workers(tranches)
    over = [(worker_id, amount) for worker_id, amount in sorted(paid.items())
            if amount > plan["dues"].get(worker_id, 0)]
    if over:
        worker_id, amount = over[0]
        worker = uow.househelp.worker(worker_id)
        raise ConflictError(
            f"{worker['name']} has already been paid {format_inr(amount)} for {name}, more than "
            f"the {format_inr(plan['dues'].get(worker_id, 0))} this would bring their pay to. "
            "Delete the payout first, or add the difference as a bonus.",
            code="househelp_worker_already_paid",
            details={"worker_id": worker_id, "paid_paise": amount,
                     "due_paise": plan["dues"].get(worker_id, 0)})
    if not plan["total_paise"]:
        if _has_payments(uow, expense_id, allocations):
            raise ConflictError(
                f"Payments have already been recorded for the {name} house help bill, so it "
                "cannot be taken away.", code="househelp_bill_has_payments",
                details={"settlement_month": month, "expense_id": expense_id})
        uow.expenses.delete_allocations(expense_id)
        uow.expenses.delete_components(expense_id)
        uow.expenses.delete_expense(expense_id)
        audit.record(uow, entity_type="expense", entity_id=expense_id, action="delete",
                     actor=actor, apartment_id=apartment_id,
                     reason="No house help salary is due for the month",
                     old_value={"settlement_month": month,
                                "amount_paise": expense["amount_paise"]})
        return

    shares = {rid: paise for rid, paise in plan["shares"].items() if paise}
    new_basis = _bill_basis(plan)
    basis_json = json.dumps(new_basis, sort_keys=True, default=str)
    current = {row["resident_id"]: row for row in allocations}
    components = [(row["name"], row["amount_paise"])
                  for row in uow.expenses.components(expense_id)]
    if (expense["amount_paise"] == plan["total_paise"] and _stored_basis(allocations) == new_basis
            and components == [(row["name"], row["amount_paise"])
                               for row in _bill_components(plan)]
            and {rid: row["amount_paise"] for rid, row in current.items()
                 if row["amount_paise"]} == shares):
        return
    rebalance = bill_shares.plan(
        uow, scope="service", apartment_id=apartment_id, expense_id=expense_id,
        shares=shares, charged=list(current), tranches=tranches, owed=plan["dues"],
        names=_names(uow, apartment_id), what=f"the {name} house help bill")
    if rebalance.problem is not None:
        raise rebalance.problem
    uow.furniture.update_bill(expense_id, amount_paise=plan["total_paise"],
                              notes=_bill_notes(plan), provider_name=_bill_payees(plan),
                              updated_at=timestamp)
    uow.househelp.replace_components(expense_id, _bill_components(plan))

    def set_charge(rid: str, amount: int) -> None:
        row = current.get(rid)
        if row is not None and amount:
            uow.furniture.update_allocation(row["allocation_id"], amount_paise=amount,
                                            allocation_basis_json=basis_json)
        elif row is not None:
            uow.furniture.delete_allocation(row["allocation_id"])
        elif amount:
            uow.expenses.insert_allocation(
                allocation_id=new_id("allocation"), expense_id=expense_id, component_id=None,
                resident_id=rid, amount_paise=amount, allocation_basis_json=basis_json)

    bill_shares.apply(
        uow, rebalance, set_charge=set_charge,
        payee=lambda group: group["tranches"][0]["provider_name"],
        share_note=f"{service['display_name']} for {name}",
        advance_note=(f"Extra paid for {service['display_name']} ({name}) to be reimbursed "
                      "via House Kitty"),
        timestamp=timestamp, actor=actor)
    # Every charge reads the month as it now stands, a charge left as it was too.
    for row in uow.furniture.allocations_for_expense(expense_id):
        uow.furniture.update_allocation(row["allocation_id"], amount_paise=row["amount_paise"],
                                        allocation_basis_json=basis_json)
    audit.record(uow, entity_type="expense", entity_id=expense_id, action="sync",
                 actor=actor, apartment_id=apartment_id,
                 old_value={"amount_paise": expense["amount_paise"],
                            "shares": {rid: row["amount_paise"] for rid, row in current.items()}},
                 new_value={"amount_paise": plan["total_paise"], "shares": shares})


def _clean_records(records_payload: Any, days: int) -> list[dict[str, Any]]:
    """The month's attendance as sent: a record per role on the month."""
    if not isinstance(records_payload, (list, tuple)):
        raise ValidationError("records must be a list, one record per role on the month.",
                              details={"field": "records"})
    cleaned, seen = [], set()
    for index, entry in enumerate(records_payload):
        if not isinstance(entry, Mapping):
            raise ValidationError("Each record must give a role's attendance.",
                                  details={"field": f"records[{index}]"})
        role_id = require_text(entry.get("role_id"), f"records[{index}].role_id", max_length=64)
        if role_id in seen:
            raise ValidationError("Each role is saved once a month.",
                                  code="househelp_duplicate_role",
                                  details={"field": f"records[{index}].role_id",
                                           "role_id": role_id})
        seen.add(role_id)
        days_absent = entry.get("days_absent", 0)
        if (isinstance(days_absent, bool) or not isinstance(days_absent, int)
                or not 0 <= days_absent <= days):
            raise ValidationError(f"days_absent must be a number of days from 0 to {days}.",
                                  details={"field": f"records[{index}].days_absent"})
        action = entry.get("excess_leave_action") or "deduct"
        if action not in EXCESS_LEAVE_ACTIONS:
            raise ValidationError("Unsupported excess_leave_action.",
                                  details={"field": f"records[{index}].excess_leave_action",
                                           "allowed": list(EXCESS_LEAVE_ACTIONS)})
        manual = entry.get("deduction_paise")
        cleaned.append({
            "role_id": role_id, "days_absent": days_absent, "excess_leave_action": action,
            "bonus_or_extra_paise": ensure_paise(entry.get("bonus_or_extra_paise", 0),
                                                 f"records[{index}].bonus_or_extra_paise"),
            "manual_deduction_paise": (None if manual is None else
                                       ensure_paise(manual, f"records[{index}].deduction_paise")),
            "extra_notes": optional_text(entry.get("extra_notes"),
                                         f"records[{index}].extra_notes", max_length=500),
        })
    return cleaned


def _role_with_worker(uow: UnitOfWork, apartment_id: str, role_id: str) -> dict[str, Any]:
    role = _require_role(uow, apartment_id, role_id)
    if role["worker_id"] is None:
        raise ConflictError(
            f"{role['role_name']} has no helper yet, so there is nobody to pay for it. Give it "
            "a helper on the Staff tab first.", code="househelp_role_has_no_worker",
            details={"role_id": role_id})
    return role


def _one_attendance_each(roles: Sequence[tuple[Mapping[str, Any], Mapping[str, Any]]]) -> None:
    """A worker away is away from every role they do: their roles on the month
    must give the same days absent, and the same answer to leave beyond the
    allowance."""
    seen: dict[str, tuple[Mapping[str, Any], Mapping[str, Any]]] = {}
    for role, entry in roles:
        first = seen.setdefault(role["worker_id"], (role, entry))
        if any(entry[key] != first[1][key] for key in ("days_absent", "excess_leave_action")):
            raise ValidationError(
                f"{role['worker_name']}'s days absent are theirs, not each role's: give "
                f"{first[0]['role_name']} and {role['role_name']} the same days absent, and "
                "deduct or waive the leave beyond the allowance for both.",
                code="househelp_worker_attendance_differs",
                details={"worker_id": role["worker_id"],
                         "role_ids": [first[0]["role_id"], role["role_id"]]})


def save_monthly_attendance(db: Database, apartment_id: str, month: str,
                            records_payload: Sequence[Mapping[str, Any]], *,
                            actor: str = "operator",
                            only_if_unsaved: bool = False) -> dict[str, Any]:
    """Save a month's attendance, and bill it.

    ``records_payload`` is the month's roles, a record each: ``role_id``,
    ``days_absent`` (0) and ``excess_leave_action`` ('deduct' or 'waive') --
    the worker's, the same on each of their roles
    (``househelp_worker_attendance_differs``) -- ``bonus_or_extra_paise`` (0),
    ``extra_notes``, and ``deduction_paise`` to deduct an agreed sum for the
    excess leave instead of the pro-rata days. Each role's salary is worked out
    from the role and its worker's leave allowance as they stand. A role saved on the
    month before and left out now is taken off it; an empty list leaves the
    month with no bill, as long as nothing has been paid towards it. A role
    with no worker cannot be on a month (``househelp_role_has_no_worker``).

    Refused once the month's statement is finalized
    (``househelp_month_finalized``), and for a month that has not begun. With
    ``only_if_unsaved``, a month someone has saved already is left as it is.
    Returns the month's overview."""
    month = normalize_month(month, "settlement_month")
    days = _days_in_month(month)
    entries = _clean_records(records_payload, days)
    definition_id = _ensure_definition(db, apartment_id, actor)
    timestamp = now_timestamp()
    with db.transaction() as uow:
        service, config = _ensure_service_in(uow, apartment_id, definition_id, timestamp, actor)
        statement = uow.furniture.statement_for_month(apartment_id, month)
        if statement is not None and statement["status"] != "draft":
            raise ConflictError(
                f"The {month_label(month)} statement has been finalized, so its house help can "
                "no longer change.", code="househelp_month_finalized",
                details={"settlement_month": month, "status": statement["status"]})
        if month > _current_month():
            raise ConflictError(
                f"{month_label(month)} has not begun, so there is no attendance to save yet.",
                code="househelp_month_not_started", details={"settlement_month": month})
        before = {row["role_id"]: row
                  for row in uow.househelp.records_for_month(apartment_id, month)}
        if only_if_unsaved and before:
            # Saved by someone else in the meantime: theirs stands.
            return _overview(uow, apartment_id, month)
        roles = [(_role_with_worker(uow, apartment_id, entry["role_id"]), entry)
                 for entry in entries]
        _one_attendance_each(roles)
        for role, _ in roles:
            if not _days_active(role["worker_joined_date"], month):
                raise ConflictError(
                    f"{role['worker_name']} joined on {role['worker_joined_date']}, after "
                    f"{month_label(month)}, so {role['role_name']} is not on it.",
                    code="househelp_worker_not_joined",
                    details={"role_id": role["role_id"], "settlement_month": month})
        saved = [_salary(role, entry, month) for role, entry in roles]
        for role_id in sorted(set(before) - {role["role_id"] for role in saved}):
            uow.househelp.delete_record(role_id, month)
        for role in saved:
            uow.househelp.upsert_record(
                record_id=(before.get(role["role_id"]) or {}).get("record_id")
                or new_id("hhrecord"),
                role_id=role["role_id"], settlement_month=month, apartment_id=apartment_id,
                **{key: role[key] for key in (
                    "days_absent", "excess_leave_action", "deduction_paise",
                    "bonus_or_extra_paise", "extra_notes", "final_salary_paise")})
        _write_bill(uow, apartment_id, service, config, month, timestamp, actor)
        audit.record(uow, entity_type="household_service", entity_id=service["service_id"],
                     action="attendance", actor=actor, apartment_id=apartment_id,
                     old_value={role_id: {key: row[key] for key in (
                         "days_absent", "excess_leave_action", "deduction_paise",
                         "bonus_or_extra_paise", "final_salary_paise")}
                         for role_id, row in before.items()},
                     new_value={"settlement_month": month, "records": {
                         role["role_id"]: {key: role[key] for key in (
                             "days_absent", "excess_leave_action", "deduction_paise",
                             "bonus_or_extra_paise", "final_salary_paise")}
                         for role in saved}})
        return _overview(uow, apartment_id, month)


def _join_this_month(db: Database, apartment_id: str, role_id: str) -> None:
    """Put a role just given a worker on this month and bill it, if the month
    has been saved and can be saved again: at its worker's attendance as saved
    -- none, for a worker not on the month yet -- with no bonus, at its salary
    for the days its worker was there.

    A month nobody has saved takes the role when it is first looked at
    (:func:`get_month_overview`); a month gone by is left as it was saved. A
    month that cannot take it -- finalized, recorded by hand, a worker who joins
    later, a split nobody can share, a worker already paid more than the month
    would owe them -- is left as it is, and nothing is written: the role shows
    with the month, unsaved, until the month is saved with it."""
    month = _current_month()
    timestamp = now_timestamp()
    try:
        with db.transaction() as uow:
            service, config = _service_and_config(uow, apartment_id)
            statement = uow.furniture.statement_for_month(apartment_id, month)
            records = uow.househelp.records_for_month(apartment_id, month)
            role = uow.househelp.role(role_id)
            if (service is None or config is None or not records or role is None
                    or (statement is not None and statement["status"] != "draft")
                    or any(record["role_id"] == role_id for record in records)
                    or role["worker_id"] is None or not role["worker_is_active"]):
                return
            active = _days_active(role["worker_joined_date"], month)
            if not active:
                return
            # A worker's attendance is the same on each of their roles.
            absent, action = next(((record["days_absent"], record["excess_leave_action"])
                                   for record in records
                                   if record["worker_id"] == role["worker_id"]), (0, "deduct"))
            salary = _salary(role, {"days_absent": min(absent, active),
                                    "excess_leave_action": action,
                                    "bonus_or_extra_paise": 0}, month)
            figures = {key: salary[key] for key in (
                "days_absent", "excess_leave_action", "deduction_paise",
                "bonus_or_extra_paise", "final_salary_paise")}
            uow.househelp.upsert_record(
                record_id=new_id("hhrecord"), role_id=role_id, settlement_month=month,
                apartment_id=apartment_id, extra_notes=None, **figures)
            _write_bill(uow, apartment_id, service, config, month, timestamp, AUTO_SAVE_ACTOR)
            audit.record(uow, entity_type="household_service", entity_id=service["service_id"],
                         action="attendance", actor=AUTO_SAVE_ACTOR, apartment_id=apartment_id,
                         new_value={"settlement_month": month, "records": {role_id: figures}})
    except AemError as exc:
        logger.warning("House help role %s did not join %s: %s", role_id, month, exc.message)


# ----------------------------------------------------------------- overview --

def get_month_overview(db: Database, apartment_id: str, month: str) -> dict[str, Any]:
    """A month's house help: each role's attendance and salary, what each
    worker is owed and has been paid, and each resident's share with what it is
    made of -- and where paying it stands, into the kitty and out to the workers.

    A month that can be billed and has roles to pay is saved as it is first
    looked at, with nobody absent and no bonus: every active worker's roles
    that were set up by then, at their full salary. It is billed like any
    other month, and changed by saving it again. A month that cannot be --
    not begun, finalized, recorded by hand, or with an opt-in role nobody has
    opted in to -- is a preview of that, and nothing is written."""
    month = normalize_month(month, "month")
    with db.read() as uow:
        get_apartment(uow, apartment_id)
        overview = _overview(uow, apartment_id, month)
        defaults = _defaults_to_save(uow, apartment_id, month, overview)
    if not defaults:
        return overview
    try:
        return save_monthly_attendance(db, apartment_id, month, defaults,
                                       actor=AUTO_SAVE_ACTOR, only_if_unsaved=True)
    except AemError as exc:
        # Left as a preview: whoever saves it will be told why.
        logger.warning("House help for %s was not saved by default: %s", month, exc.message)
        return overview


def _defaults_to_save(uow: UnitOfWork, apartment_id: str, month: str,
                      overview: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The month's attendance to save by default, or none: each role an active
    worker who had joined had by the month's end, nobody absent, for a month
    nobody has saved that can be billed as it stands. A role set up after the
    month is left out, whatever day its worker joined: a month gone by is saved
    by hand."""
    if (overview["saved"] or not overview["can_save"] or overview["expense"] is not None
            or overview["blocked_reason"]):
        return []
    return [{"role_id": role["role_id"], "days_absent": 0, "excess_leave_action": "deduct",
             "bonus_or_extra_paise": 0}
            for role in uow.househelp.roles(apartment_id)
            if role["worker_is_active"] and role["created_at"][:7] <= month
            and _days_active(role["worker_joined_date"], month)]


def _payouts(uow: UnitOfWork, apartment_id: str, tranches: Sequence[Mapping[str, Any]],
             names: Mapping[str, str]) -> list[dict[str, Any]]:
    """Each payout to a worker as it was made: by a resident -- whatever went
    through the kitty with it -- or by the kitty itself, oldest first."""
    active = {row["payment_id"]: row for row in uow.payments.active_payments(apartment_id)}
    by_resident, by_kitty = resident_payouts.group_payouts("service", tranches, active)
    made = []
    for group in by_resident:
        first = group["tranches"][0]
        made.append({
            "payout_id": first["disbursement_id"], "worker_id": first.get("worker_id"),
            "amount_paise": group["amount_paise"], "paid_on": group["disbursed_at"],
            "reference": group["reference"], "paid_by": "resident",
            "resident_id": group["resident_id"],
            "resident_name": names.get(group["resident_id"], group["resident_name"]),
            "note": group["note"], "seq": group["seq"]})
    for tranche in by_kitty:
        made.append({
            "payout_id": tranche["disbursement_id"], "worker_id": tranche.get("worker_id"),
            "amount_paise": tranche["amount_paise"], "paid_on": tranche["disbursed_at"],
            "reference": tranche["disbursement_reference"], "paid_by": "kitty",
            "resident_id": None, "resident_name": None, "note": tranche.get("note"),
            "seq": tranche["seq"]})
    made.sort(key=lambda row: (row["paid_on"], row["seq"]))
    for row in made:
        del row["seq"]
    return made


def _overview(uow: UnitOfWork, apartment_id: str, month: str) -> dict[str, Any]:
    service, config = _service_and_config(uow, apartment_id)
    names = _names(uow, apartment_id)
    statement = uow.furniture.statement_for_month(apartment_id, month)
    expense, foreign = _month_expense(uow, config, apartment_id, month)
    allocations = uow.furniture.allocations_for_expense(expense["expense_id"]) if expense else []
    sharing = _month_residents(uow, apartment_id, allocations)
    days = _days_in_month(month)
    records = uow.househelp.records_for_month(apartment_id, month)
    saved = bool(records)
    live = statement is None or statement["status"] == "draft"
    can_save = live and foreign is None and month <= _current_month()
    on_month = {record["role_id"] for record in records}
    # A worker's attendance is the same on each of their roles, so a role of
    # theirs not on the month yet starts from what was saved for them.
    attendance = {record["worker_id"]: (record["days_absent"], record["excess_leave_action"])
                  for record in records}
    all_roles = uow.househelp.roles(apartment_id)
    set_up = {role["role_id"]: role["created_at"][:7] for role in all_roles}
    open_roles = []
    for role in all_roles:
        active = _days_active(role["worker_joined_date"], month)
        if not role["worker_is_active"] or role["role_id"] in on_month or not active:
            continue
        absent, action = attendance.get(role["worker_id"], (0, "deduct"))
        open_roles.append(_salary(role, {"days_absent": min(absent, active),
                                         "excess_leave_action": action,
                                         "bonus_or_extra_paise": 0}, month))
    roles = [_saved_role(record) for record in records] if saved else open_roles
    plan = _split(uow, apartment_id, roles, sharing)
    if expense is not None:
        # As billed: a month keeps the split it was saved with.
        billed = {role["role_id"]: role for role in _stored_basis(allocations).get("roles", [])}
        for role in plan["roles"]:
            shares = {rid: int(paise)
                      for rid, paise in billed.get(role["role_id"], {}).get("shares", {}).items()}
            role.update(sharers=sorted(shares), shares=shares)
    for role in plan["roles"]:
        role["saved"] = saved
    # A saved month that can be saved again lists, among its roles, each of an
    # active worker's set up by the month's end and not on it yet -- one that
    # could not join it as it was set up (_join_this_month), or set up before
    # roles did. Each is unsaved, at its salary now with its own split, and on
    # nobody's bill until the month is saved again, which adds it. A role set
    # up after the month is not on it: a month gone by is not billed for it.
    joining = [role for role in open_roles if set_up[role["role_id"]] <= month] \
        if saved and can_save else []
    joining_plan = _split(uow, apartment_id, joining, sharing)
    for role in joining_plan["roles"]:
        role["saved"] = False
    problem = (plan["problem"] if expense is None else None) or joining_plan["problem"]

    funding = service_bills.funding(uow, apartment_id, expense) if expense else None
    owing = {entry["recipient_resident_id"]: entry["pending_reimbursement_paise"]
             for entry in (funding["reimbursements"] if funding else [])}
    charged = ({rid: row["share_paise"] for rid, row in funding["charges"].items()}
               if funding else {rid: paise for rid, paise in plan["shares"].items() if paise})
    shares = []
    for rid in sorted(set(sharing) | set(charged), key=lambda r: (names.get(r, r), r)):
        owed = charged.get(rid, 0)
        charge = funding["charges"].get(rid) if funding else None
        paid = charge["settled_paise"] if charge else 0
        advanced = funding["advanced"].get(rid, 0) if funding else 0
        covered = min(owed, paid + advanced)
        status = service_bills.share_status(owed, paid, advanced) if funding else None
        shares.append({
            "resident_id": rid, "display_name": names.get(rid, rid),
            # What they are charged: share = paid + still owed, always.
            "total_paise": owed,
            "paid_paise": covered,
            "settled_paise": paid,
            "outstanding_paise": owed - covered if funding else 0,
            "status": status,
            "paid_date": (funding["advanced_on"][rid] if status == "funded_payout"
                          else charge["last_payment_date"] if paid and charge else None),
            "payments": funding["paid_in"].get(rid, []) if funding else [],
            "breakdown": [{"role_id": role["role_id"], "role_name": role["role_name"],
                           "worker_id": role["worker_id"], "worker_name": role["worker_name"],
                           "share_paise": role["shares"][rid]}
                          for role in plan["roles"] if role["shares"].get(rid)],
            # Above zero the kitty owes them; below, they owe the kitty.
            "net_balance_paise": owing.get(rid, 0) - (owed - covered if funding else 0),
        })

    tranches = funding["tranches"] if funding else []
    paid = _paid_to_workers(tranches)
    payouts = _payouts(uow, apartment_id, tranches, names) if funding else []
    # A resident's share paid by paying a worker themselves goes with that payout.
    share_of = {tranche["disbursement_id"][len(resident_payouts.SHARE_PREFIX["service"]):]:
                tranche["disbursement_id"] for tranche in tranches
                if tranche["disbursement_id"].startswith(resident_payouts.SHARE_PREFIX["service"])}
    for share in shares:
        share["payments"] = [{**payment, "payout_id": share_of.get(payment["payment_id"])}
                             for payment in share["payments"]]
    workers = []
    for worker in uow.househelp.workers(apartment_id):
        worker_id = worker["worker_id"]
        own = [role for role in plan["roles"] if role["worker_id"] == worker_id]
        if not own and worker_id not in paid:
            continue
        due = sum(role["final_salary_paise"] for role in own)
        to_worker = paid.get(worker_id, 0)
        workers.append({
            "worker_id": worker_id, "name": worker["name"], "phone": worker["phone"],
            "upi_id": worker["upi_id"], "is_active": worker["is_active"],
            "role_ids": [role["role_id"] for role in own],
            "total_due_paise": due,
            "paid_to_worker_paise": to_worker,
            "still_owed_paise": max(0, due - to_worker) if expense else 0,
            # As a provider's payout reads: null until the month is billed.
            "payout_status": (None if expense is None
                              else "disbursed" if to_worker >= due
                              else "partially_disbursed" if to_worker else "pending"),
            "payouts": [row for row in payouts if row["worker_id"] == worker_id],
        })

    result: dict[str, Any] = {
        "settlement_month": month,
        "days_in_month": days,
        "service": _public_service(service, config),
        "active_worker_count": sum(1 for worker in uow.househelp.workers(apartment_id)
                                   if worker["is_active"]),
        "saved": saved,
        # 'live': billed, and can be saved again until its statement is
        # finalized; 'frozen': the month's snapshot; 'not_billed': nothing
        # billed yet; 'upcoming': not begun; 'recorded_elsewhere': entered by hand.
        "bill_state": ("recorded_elsewhere" if foreign is not None
                       else ("live" if live else "frozen") if expense
                       else "upcoming" if month > _current_month() else "not_billed"),
        "can_save": can_save,
        "statement_status": statement["status"] if statement else None,
        "transactions_open": deletions.month_is_open(uow, apartment_id, month),
        "participants": [{"resident_id": rid, "display_name": names.get(rid, rid)}
                         for rid in sorted(sharing, key=lambda r: (names.get(r, r), r))],
        "roles": plan["roles"] + joining_plan["roles"],
        # Roles not yet given a worker, which join no month until they are.
        "unassigned_roles": [{key: role[key] for key in (
            "role_id", "role_name", "monthly_salary_paise")}
            for role in all_roles if role["worker_id"] is None],
        "total_paise": expense["amount_paise"] if expense else plan["total_paise"],
        # Why the month cannot be billed as it stands, or saved again with the
        # roles joining it. A billed month keeps the split it was saved with.
        "blocked_reason": problem.message if problem else None,
        "workers": workers,
        "shares": shares,
        "expense": expense,
        "foreign_expense": foreign,
    }
    if funding is not None:
        result.update({key: funding[key] for key in (
            "collected_paise", "collected_count", "payout_status", "disbursed_paise",
            "remaining_disbursement_paise", "reimbursement", "reimbursements")})
    else:
        result.update(collected_paise=0, collected_count=0, payout_status="pending",
                      disbursed_paise=0, remaining_disbursement_paise=0, reimbursement=None,
                      reimbursements=[])
    return result


# ----------------------------------------------------------- paying it -- #

def _require_bill(uow: UnitOfWork, apartment_id: str, config: Mapping[str, Any],
                  month: str) -> dict[str, Any]:
    expense, _ = _month_expense(uow, config, apartment_id, month)
    if expense is None:
        raise ConflictError(f"{month_label(month)}'s house help is not saved yet.",
                            code="househelp_bill_not_recorded",
                            details={"settlement_month": month})
    return expense


def record_payment(db: Database, apartment_id: str, payload: Mapping[str, Any], *,
                   actor: str = "operator") -> dict[str, Any]:
    """Record a resident paying their share of a month's house help into the kitty.

    ``payload``: ``settlement_month``, ``resident_id``, ``amount_paise``,
    ``payment_date``, and optionally ``payment_mode``, ``reference`` and ``note``.
    The payment settles their share of this bill first, up to what is still
    owed on it; anything beyond goes to their other charges, oldest first, and
    what is left after that stays a credit. A share already paid, or covered by
    their paying a worker, is refused."""
    month = normalize_month(payload.get("settlement_month"), "settlement_month")
    resident_id = require_text(payload.get("resident_id"), "resident_id", max_length=64)
    amount = ensure_paise(payload.get("amount_paise"), "amount_paise", allow_zero=False)
    payment_date = parse_iso_date(payload.get("payment_date"), "payment_date").isoformat()
    timestamp = now_timestamp()
    with db.transaction() as uow:
        get_apartment(uow, apartment_id)
        name = get_resident(uow, resident_id, apartment_id)["display_name"]
        service, config = _require_service(uow, apartment_id)
        expense = _require_bill(uow, apartment_id, config, month)
        charges = uow.furniture.resident_charges_for_expense(expense["expense_id"], resident_id)
        if not charges:
            raise ValidationError(
                f"{name} has no share of the {month_label(month)} house help.",
                code="not_a_househelp_participant",
                details={"field": "resident_id", "resident_id": resident_id})
        target = next((charge for charge in charges if charge["outstanding_paise"] > 0), None)
        if target is None:
            raise ConflictError(
                f"{name} has already paid their share of the {month_label(month)} house help.",
                code="househelp_share_already_paid", details={"resident_id": resident_id})
        advanced = sum(tranche["amount_paise"]
                       for tranche in uow.furniture.service_disbursements(expense["expense_id"])
                       if tranche["funding_resident_id"] == resident_id)
        if advanced and advanced >= sum(charge["outstanding_paise"] for charge in charges):
            raise ConflictError(
                f"{name} paid a worker for {month_label(month)}, which already covers their "
                "share; the kitty owes them the rest.",
                code="househelp_share_funded_by_payout", details={"resident_id": resident_id})
        payments._record_payment(
            uow, apartment_id=apartment_id, payment_id=new_id("payment"),
            payer_resident_id=resident_id, amount_paise=amount, payment_date=payment_date,
            payment_kind="to_pool", payee_or_provider=None,
            reference=optional_text(payload.get("reference"), "reference", max_length=200),
            note=(optional_text(payload.get("note"), "note")
                  or f"{service['display_name']} for {month_label(month)}"),
            timestamp=timestamp, expense_id=expense["expense_id"], actor=actor,
            target_allocation_id=target["allocation_id"],
            payment_mode=payload.get("payment_mode"))
        return _overview(uow, apartment_id, month)


def disburse_worker_payout(db: Database, apartment_id: str, payload: Mapping[str, Any], *,
                           actor: str = "operator") -> dict[str, Any]:
    """Record paying a worker their salary for a month.

    ``payload``: ``settlement_month``, ``worker_id``, ``disbursement_mode``,
    ``reference``, ``disbursed_date``, and optionally ``amount_paise`` (all the
    worker is still owed for the month unless given), ``funding_resident_id``
    and ``notes``.

    ``pool``
        The kitty paid them, out of what residents paid in.

    ``resident_advance``
        A resident paid them from their own account. What covers their own open
        share goes through the kitty, as everyone else's does: a payment into the
        kitty settling their share, and a ``pool`` payout of it. Only what they
        paid beyond their share is an advance -- a ``provider_advance`` linked to
        the bill -- which the kitty pays back to them (``reimbursements``).

    A worker is paid in tranches, each with its own date and reference, never
    adding up to more than their salary for the month."""
    month = normalize_month(payload.get("settlement_month"), "settlement_month")
    mode = payload.get("disbursement_mode")
    if mode not in PAYOUT_MODES:
        raise ValidationError("Unsupported disbursement_mode.",
                              details={"field": "disbursement_mode",
                                       "allowed": list(PAYOUT_MODES)})
    reference = require_text(payload.get("reference"), "reference", max_length=200)
    disbursed_date = parse_iso_date(payload.get("disbursed_date"), "disbursed_date").isoformat()
    note = optional_text(payload.get("notes"), "notes", max_length=500)
    amount_paise = payload.get("amount_paise")
    if amount_paise is not None:
        amount_paise = ensure_paise(amount_paise, "amount_paise", allow_zero=False)
    funding_resident_id = payload.get("funding_resident_id")
    if mode == "pool" and funding_resident_id:
        raise ValidationError("A payout from the kitty has no funding resident.",
                              details={"field": "funding_resident_id"})
    if mode == "resident_advance" and not funding_resident_id:
        raise ValidationError("Choose the resident who paid the worker.",
                              details={"field": "funding_resident_id"})
    timestamp = now_timestamp()
    with db.transaction() as uow:
        get_apartment(uow, apartment_id)
        service, config = _require_service(uow, apartment_id)
        worker = _require_worker(uow, apartment_id, payload.get("worker_id"))
        worker_id, worker_name = worker["worker_id"], worker["name"]
        expense = _require_bill(uow, apartment_id, config, month)
        expense_id = expense["expense_id"]
        due = sum(record["final_salary_paise"]
                  for record in uow.househelp.records_for_month(apartment_id, month)
                  if record["worker_id"] == worker_id)
        if not due:
            raise ConflictError(
                f"{worker_name} has no salary on the {month_label(month)} house help bill.",
                code="househelp_worker_not_on_month",
                details={"worker_id": worker_id, "settlement_month": month})
        tranches = uow.furniture.service_disbursements(expense_id)
        remaining = due - _paid_to_workers(tranches).get(worker_id, 0)
        if remaining <= 0:
            raise ConflictError(
                f"{worker_name} has already been paid their {month_label(month)} salary in full.",
                code="househelp_worker_already_paid",
                details={"worker_id": worker_id, "settlement_month": month})
        amount = remaining if amount_paise is None else amount_paise
        if amount > remaining:
            raise ValidationError(
                f"That is more than the {format_inr(remaining)} still owed to {worker_name} for "
                f"{month_label(month)}.", code="tranche_exceeds_remaining",
                details={"field": "amount_paise", "amount_paise": amount,
                         "remaining_paise": remaining})
        advanced = sum(tranche["amount_paise"] for tranche in tranches
                       if tranche["disbursed_by"] == "resident_advance")
        external = uow.furniture.linked_advance_total_paise(expense_id) - advanced
        if external > 0 or expense["payer_resident_id"]:
            raise ConflictError(
                f"The {month_label(month)} house help bill already records a resident as having "
                "paid it, so a payout cannot also be recorded here. Correct the expense, or the "
                "payment linked to it, first.", code="househelp_already_funded",
                details={"expense_id": expense_id,
                         "payer_resident_id": expense["payer_resident_id"],
                         "linked_advance_paise": external})

        written: list[dict[str, Any]] = []
        if mode == "pool":
            written.append(dict(disbursement_id=new_id("svcdisb"), amount_paise=amount,
                                disbursed_by="pool", funding_resident_id=None,
                                funding_payment_id=None, note=note))
        else:
            name = get_resident(uow, funding_resident_id, apartment_id)["display_name"]
            open_charge = next((charge for charge in uow.furniture.resident_charges_for_expense(
                expense_id, funding_resident_id) if charge["outstanding_paise"] > 0), None)
            share_paise = min(amount, open_charge["outstanding_paise"]) if open_charge else 0
            extra_paise = amount - share_paise
            if share_paise:
                # Their own share goes through the kitty, as everyone else's does.
                share_payment_id = new_id("payment")
                payments._record_payment(
                    uow, apartment_id=apartment_id, payment_id=share_payment_id,
                    payer_resident_id=funding_resident_id, amount_paise=share_paise,
                    payment_date=disbursed_date, payment_kind="to_pool",
                    payee_or_provider=None, reference=reference,
                    note=f"{service['display_name']} for {month_label(month)}",
                    timestamp=timestamp, expense_id=expense_id, actor=actor,
                    target_allocation_id=open_charge["allocation_id"])
                written.append(dict(disbursement_id=service_bills.share_payout_id(share_payment_id),
                                    amount_paise=share_paise, disbursed_by="pool",
                                    funding_resident_id=None, funding_payment_id=None,
                                    note=f"{name}'s allotted share via House Kitty"))
            if extra_paise:
                # Only what they paid beyond their share is an advance, which the
                # kitty pays back out of what the others pay in.
                funding_payment_id = new_id("househelp-payout")
                payments._record_payment(
                    uow, apartment_id=apartment_id, payment_id=funding_payment_id,
                    payer_resident_id=funding_resident_id, amount_paise=extra_paise,
                    payment_date=disbursed_date, payment_kind="provider_advance",
                    payee_or_provider=worker_name, reference=reference,
                    note=(f"Extra paid for {service['display_name']} ({month_label(month)}) "
                          "to be reimbursed via House Kitty"),
                    timestamp=timestamp, expense_id=expense_id, actor=actor)
                written.append(dict(disbursement_id=new_id("svcdisb"), amount_paise=extra_paise,
                                    disbursed_by="resident_advance",
                                    funding_resident_id=funding_resident_id,
                                    funding_payment_id=funding_payment_id,
                                    note=note or (f"Extra above {name}'s share "
                                                  "(reimbursable via House Kitty)")))
        for tranche in written:
            uow.furniture.insert_service_disbursement(
                service_id=service["service_id"], expense_id=expense_id,
                apartment_id=apartment_id, settlement_month=month, disbursed_at=disbursed_date,
                disbursement_reference=reference, created_at=timestamp, worker_id=worker_id,
                **tranche)
        audit.record(uow, entity_type="household_service", entity_id=service["service_id"],
                     action="disburse", actor=actor, apartment_id=apartment_id,
                     new_value={"settlement_month": month, "expense_id": expense_id,
                                "worker_id": worker_id, "amount_paise": amount,
                                "due_paise": due, "remaining_paise": remaining - amount,
                                "disbursed_by": mode, "disbursed_at": disbursed_date,
                                "disbursement_reference": reference,
                                "funding_resident_id": funding_resident_id, "note": note,
                                "tranches": [{key: tranche[key] for key in (
                                    "disbursement_id", "amount_paise", "disbursed_by",
                                    "funding_payment_id")} for tranche in written]})
        return _overview(uow, apartment_id, month)


def reimburse_from_kitty(db: Database, apartment_id: str, payload: Mapping[str, Any], *,
                         actor: str = "operator") -> dict[str, Any]:
    """Pay back, from the kitty, a resident who paid a worker more than their share.

    ``payload``: ``settlement_month``, ``recipient_resident_id``, ``amount_paise``,
    ``payment_date``, and optionally ``payment_mode``, ``reference`` and
    ``notes``. It is a ``refund`` out of what the kitty collected for the bill:
    never more than it holds for it, nor more than they are still owed. Part can
    wait until the others have paid in."""
    month = normalize_month(payload.get("settlement_month"), "settlement_month")
    recipient = require_text(payload.get("recipient_resident_id"), "recipient_resident_id",
                             max_length=64)
    amount = ensure_paise(payload.get("amount_paise"), "amount_paise", allow_zero=False)
    payment_date = parse_iso_date(payload.get("payment_date"), "payment_date").isoformat()
    timestamp = now_timestamp()
    with db.transaction() as uow:
        get_apartment(uow, apartment_id)
        service, config = _require_service(uow, apartment_id)
        expense = _require_bill(uow, apartment_id, config, month)
        owing = service_bills.funding(uow, apartment_id, expense)["reimbursements"]
        owed = next((entry for entry in owing
                     if entry["recipient_resident_id"] == recipient), None)
        if owed is None:
            raise NotFoundError(
                f"The kitty owes nobody by that name for the {month_label(month)} house help.",
                code="househelp_no_reimbursement",
                details={"field": "recipient_resident_id", "settlement_month": month})
        name = owed["recipient_name"]
        if amount > owed["pending_reimbursement_paise"]:
            raise ValidationError(
                f"That is more than the {format_inr(owed['pending_reimbursement_paise'])} "
                f"still owed to {name} for {month_label(month)}.",
                code="exceeds_pending_reimbursement",
                details={"field": "amount_paise", "amount_paise": amount,
                         "pending_reimbursement_paise": owed["pending_reimbursement_paise"]})
        if amount > owed["kitty_cash_available_paise"]:
            raise ValidationError(
                f"The kitty holds only {format_inr(owed['kitty_cash_available_paise'])} from "
                f"flatmates for this bill that has not already gone to the workers or back to "
                f"{name}.", code="exceeds_kitty_cash",
                details={"field": "amount_paise", "amount_paise": amount,
                         "kitty_cash_available_paise": owed["kitty_cash_available_paise"]})
        payment = payments._record_payment(
            uow, apartment_id=apartment_id,
            payment_id=new_id(service_bills.reimbursement_id_base(expense["expense_id"])),
            payer_resident_id=recipient, amount_paise=amount, payment_date=payment_date,
            payment_kind="refund", payee_or_provider=None,
            reference=optional_text(payload.get("reference"), "reference", max_length=200),
            note=(optional_text(payload.get("notes"), "notes", max_length=500)
                  or f"Reimbursement for {service['display_name']} for {month_label(month)}, "
                     "paid to the household's workers on its behalf"),
            timestamp=timestamp, expense_id=None, actor=actor,
            payment_mode=payload.get("payment_mode"))
        audit.record(uow, entity_type="household_service", entity_id=service["service_id"],
                     action="reimburse", actor=actor, apartment_id=apartment_id,
                     new_value={"settlement_month": month, "expense_id": expense["expense_id"],
                                "recipient_resident_id": recipient, "amount_paise": amount,
                                "payment_id": payment["payment_id"],
                                "payment_date": payment_date})
        return _overview(uow, apartment_id, month)


# ------------------------------------------------- deleting a mistake -- #

def _is_househelp_bill(uow: UnitOfWork, config: Mapping[str, Any], apartment_id: str,
                       expense_id: str | None) -> bool:
    if not expense_id:
        return False
    expense = uow.expenses.get(expense_id)
    return bool(expense and expense.get("apartment_id") == apartment_id
                and expense["expense_definition_id"] == config["expense_definition_id"]
                and _stored_basis(uow.furniture.allocations_for_expense(expense_id))
                .get("source") == SOURCE)


def delete_payment(db: Database, apartment_id: str, payment_id: str, *,
                   actor: str = "operator") -> dict[str, Any]:
    """Delete, while its month is open, a resident's payment into the kitty
    towards a month's house help, or the kitty's reimbursement for one --
    recorded by mistake. A payment the kitty paid out to a worker as part of a
    resident's payout deletes that payout. Returns the overview of that month.
    See :mod:`aem.services.deletions`."""
    with db.transaction() as uow:
        get_apartment(uow, apartment_id)
        service, config = _require_service(uow, apartment_id)
        payment = uow.payments.get(payment_id)
        payout = uow.payments.service_payout_of(payment_id)
        if payment is None:
            bills: list[str | None] = []
        elif payout is not None:
            bills = [payout["expense_id"]]
        elif payment["payment_kind"] == "refund" and "-reimbursement_" in payment_id:
            bills = [payment_id.split("-reimbursement_")[0]]
        else:
            bills = [payment["expense_id"], *(row["expense_id"] for row in
                                              uow.payments.applied_by_expense(payment_id))]
        if not any(_is_househelp_bill(uow, config, apartment_id, bill) for bill in bills):
            raise NotFoundError("That payment is not towards house help.",
                                code="payment_not_found", details={"payment_id": payment_id})
        deleted = deletions.delete_payment_in(uow, apartment_id=apartment_id, scope="service",
                                              payment_id=payment_id, actor=actor)
        return {**_overview(uow, apartment_id, deleted["settlement_month"]), "deleted": deleted}


def delete_disbursement(db: Database, apartment_id: str, disbursement_id: str, *,
                        settlement_month: str | None = None,
                        actor: str = "operator") -> dict[str, Any]:
    """Delete, while its month is open, a payout to a worker recorded by mistake,
    with whatever was recorded with it: a resident's payout is deleted whole.
    With ``settlement_month``, only a payout for that month. Returns the overview
    of the month. See :mod:`aem.services.deletions`."""
    month = normalize_month(settlement_month, "settlement_month") if settlement_month else None
    with db.transaction() as uow:
        get_apartment(uow, apartment_id)
        service, _ = _require_service(uow, apartment_id)
        tranche = uow.furniture.service_disbursement(disbursement_id)
        if tranche is None or tranche["service_id"] != service["service_id"]:
            raise NotFoundError("No such payout to a worker.", code="payout_not_found",
                                details={"disbursement_id": disbursement_id})
        deleted = deletions.delete_payout_in(uow, apartment_id=apartment_id, scope="service",
                                             disbursement_id=disbursement_id,
                                             settlement_month=month, actor=actor)
        return {**_overview(uow, apartment_id, deleted["settlement_month"]), "deleted": deleted}

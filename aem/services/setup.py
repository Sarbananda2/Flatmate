"""Guided first-run setup.

The design problem this solves is not "collect some fields" -- it is "survive
the operator closing the tab halfway through".

So each step **commits to the database as it completes**, and the resume point
is derived from what actually exists on the server, never from browser storage:

    no apartment            -> step 1, apartment
    apartment, no residents -> step 2, residents
    residents, no categories-> step 3, categories
    all three present       -> step 4, finish

Close the tab after step 2 and the apartment and residents are really saved;
reopening lands on step 3 with nothing to redo. Clear the browser's storage,
switch machines, or open a private window and the resume point is still
correct, because it was never stored in the browser in the first place.

Identifiers are derived from the names the operator types, so they never have
to invent one. ``Flat 402, Lakeview`` becomes ``flat-402-lakeview``; a clash
gets a numeric suffix.
"""
from __future__ import annotations

import re
import sqlite3
import unicodedata
from typing import Any, Iterable, Mapping, Sequence

from .. import db
from ..db import connect, now_timestamp, transaction
from ..errors import ConflictError, ValidationError
from ..money import ensure_paise
from ..periods import PERIOD_RULES, normalize_month
from . import apartments, audit
from .common import ALLOCATION_METHODS, RECURRENCES, get_apartment, require_text
from .definitions import ESSENTIALS_CATALOGUE

SETUP_COMPLETED_SETTING = "setup_completed_at"

STEP_APARTMENT = "apartment"
STEP_RESIDENTS = "residents"
STEP_CATEGORIES = "categories"
STEP_FINISH = "finish"
STEP_DONE = "done"

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(value: str, *, fallback: str = "item", max_length: int = 48) -> str:
    """Turn a typed name into a usable identifier.

    The operator never types an ID. This is deliberately lossy and readable
    rather than clever -- it only has to be stable, unique, and recognisable in
    an export.
    """
    # Fold accents to their base letters first, so "Société" becomes "societe"
    # rather than "soci-t-".
    folded = unicodedata.normalize("NFKD", (value or "").strip().lower())
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    slug = _SLUG_STRIP.sub("-", folded).strip("-")
    slug = slug[:max_length].strip("-")
    return slug or fallback


def _unique_id(conn: sqlite3.Connection, table: str, column: str, candidate: str) -> str:
    """`candidate`, or `candidate-2`, `candidate-3`, ... if it is already taken."""
    taken = {row[0] for row in conn.execute(f"SELECT {column} FROM {table}").fetchall()}
    if candidate not in taken:
        return candidate
    for suffix in range(2, 1000):
        attempt = f"{candidate}-{suffix}"
        if attempt not in taken:
            return attempt
    raise ConflictError("Could not derive a free identifier; please set one by hand.",
                        code="identifier_exhausted", details={"candidate": candidate})


# --------------------------------------------------------------------------- #
# Progress
# --------------------------------------------------------------------------- #

def setup_state(db_path, apartment_id: str | None = None) -> dict[str, Any]:
    """Where setup has got to, and therefore where to resume.

    Everything here is read from real records. There is no separate "wizard
    progress" row that could drift out of step with the data.
    """
    resolved = apartment_id or apartments.default_apartment_id(db_path)
    with connect(db_path, read_only=True) as conn:
        apartment = None
        if resolved:
            row = db.fetch_one(conn, "SELECT * FROM apartment WHERE apartment_id=?",
                               (resolved,))
            apartment = row
        apartment_count = int(db.scalar(conn, "SELECT COUNT(*) FROM apartment", default=0))
        resident_count = active_residents = definition_count = statement_count = 0
        residents: list[dict[str, Any]] = []
        definitions_preview: list[dict[str, Any]] = []
        if apartment:
            residents = db.fetch_all(
                conn, "SELECT resident_id, display_name, is_active FROM resident "
                      "WHERE apartment_id=? ORDER BY display_name", (resolved,))
            resident_count = len(residents)
            active_residents = sum(1 for r in residents if r["is_active"])
            definitions_preview = db.fetch_all(
                conn, "SELECT expense_definition_id, name, category, period_rule "
                      "FROM expense_definition WHERE apartment_id=? AND is_active=1 "
                      "ORDER BY sort_order, name", (resolved,))
            definition_count = len(definitions_preview)
            statement_count = int(db.scalar(
                conn, "SELECT COUNT(*) FROM monthly_statement WHERE apartment_id=?",
                (resolved,), default=0))
        completed_at = db.scalar(
            conn, "SELECT setting_value FROM app_setting WHERE setting_key=?",
            (SETUP_COMPLETED_SETTING,))

    if not apartment:
        next_step = STEP_APARTMENT
    elif resident_count == 0:
        next_step = STEP_RESIDENTS
    elif definition_count == 0:
        next_step = STEP_CATEGORIES
    elif not completed_at:
        next_step = STEP_FINISH
    else:
        next_step = STEP_DONE

    current_month = now_timestamp()[:7]
    return {
        "next_step": next_step,
        "is_complete": next_step == STEP_DONE,
        "completed_at": completed_at,
        "apartment_count": apartment_count,
        "apartment": apartment,
        "apartment_id": apartment["apartment_id"] if apartment else None,
        "resident_count": resident_count,
        "active_resident_count": active_residents,
        "residents": residents,
        "definition_count": definition_count,
        "definitions": definitions_preview,
        "statement_count": statement_count,
        "current_month": current_month,
        "steps": [
            {"key": STEP_APARTMENT, "label": "Your apartment", "done": bool(apartment),
             "summary": apartment["name"] if apartment else None},
            {"key": STEP_RESIDENTS, "label": "Who lives here",
             "done": resident_count > 0,
             "summary": ", ".join(r["display_name"] for r in residents) or None},
            {"key": STEP_CATEGORIES, "label": "What you pay for",
             "done": definition_count > 0,
             "summary": (f"{definition_count} categor"
                         f"{'y' if definition_count == 1 else 'ies'}")
                        if definition_count else None},
            {"key": STEP_FINISH, "label": "Ready", "done": bool(completed_at),
             "summary": None},
        ],
    }


def catalogue() -> list[dict[str, Any]]:
    """The essentials offered by the guided setup.

    Suggestions only. Each one becomes an ordinary editable definition row; the
    application has no special knowledge of any of these names.
    """
    return [dict(entry) for entry in ESSENTIALS_CATALOGUE]


# --------------------------------------------------------------------------- #
# Steps. Each commits, so closing the tab after one never loses it.
# --------------------------------------------------------------------------- #

def create_apartment_step(db_path, *, name: str, currency_code: str = "INR",
                          timezone: str = "Asia/Kolkata",
                          apartment_id: str | None = None,
                          actor: str = "operator") -> dict[str, Any]:
    """Step 1. The operator types a name; the identifier is derived from it."""
    name = require_text(name, "name", max_length=200)
    with connect(db_path, read_only=True) as conn:
        resolved_id = (slugify(apartment_id, fallback="home", max_length=64)
                       if apartment_id else slugify(name, fallback="home", max_length=64))
        resolved_id = _unique_id(conn, "apartment", "apartment_id", resolved_id)
    created = apartments.create_apartment(
        db_path, apartment_id=resolved_id, name=name, currency_code=currency_code,
        timezone=timezone, actor=actor)
    return {"apartment": created, "apartment_id": resolved_id}


def create_residents_step(db_path, *, apartment_id: str, names: Sequence[str],
                          actor: str = "operator") -> dict[str, Any]:
    """Step 2. Everyone at once, in one transaction.

    Blank rows are ignored so the operator can leave spare boxes empty. A name
    that already exists is skipped rather than refused, which is what makes
    re-submitting the step after a reload harmless.
    """
    cleaned = [str(name).strip() for name in names if str(name or "").strip()]
    if not cleaned:
        raise ValidationError("Add at least one resident.", code="no_residents",
                              details={"field": "names"})
    if len({name.casefold() for name in cleaned}) != len(cleaned):
        raise ValidationError("Two residents have the same name. Make them distinct so "
                              "their balances can be told apart.",
                              code="duplicate_resident_name", details={"field": "names"})

    timestamp = now_timestamp()
    created: list[dict[str, Any]] = []
    skipped: list[str] = []
    with transaction(db_path) as conn:
        get_apartment(conn, apartment_id)
        existing = {row["display_name"].casefold(): row["resident_id"] for row in
                    conn.execute("SELECT resident_id, display_name FROM resident "
                                 "WHERE apartment_id=?", (apartment_id,)).fetchall()}
        for name in cleaned:
            if name.casefold() in existing:
                skipped.append(name)
                continue
            resident_id = _unique_id(conn, "resident", "resident_id", slugify(name, fallback="resident"))
            conn.execute(
                """INSERT INTO resident
                   (resident_id, apartment_id, display_name, is_active, created_at, updated_at)
                   VALUES (?,?,?,1,?,?)""",
                (resident_id, apartment_id, name, timestamp, timestamp))
            audit.record(conn, entity_type="resident", entity_id=resident_id,
                         action="create", actor=actor, apartment_id=apartment_id,
                         reason="guided setup",
                         new_value={"display_name": name, "is_active": True})
            created.append({"resident_id": resident_id, "display_name": name})
    return {"created": created, "created_count": len(created),
            "skipped": skipped, "skipped_count": len(skipped)}


def create_categories_step(db_path, *, apartment_id: str,
                           items: Sequence[Mapping[str, Any]],
                           actor: str = "operator") -> dict[str, Any]:
    """Step 3. Every chosen category at once, in one transaction.

    Each item carries its own rule, so a custom category the operator typed is
    handled by exactly the same path as one picked from the catalogue. A
    category that already exists is skipped, which keeps a re-submit harmless.
    """
    if not items:
        raise ValidationError("Choose at least one thing the household pays for.",
                              code="no_categories", details={"field": "items"})
    timestamp = now_timestamp()
    created: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    with transaction(db_path) as conn:
        get_apartment(conn, apartment_id)
        existing_names = {row["name"].casefold() for row in conn.execute(
            "SELECT name FROM expense_definition WHERE apartment_id=?",
            (apartment_id,)).fetchall()}
        for index, item in enumerate(items):
            name = require_text(item.get("name"), f"items[{index}].name", max_length=200)
            if name.casefold() in existing_names:
                skipped.append({"name": name, "reason": "already exists"})
                continue
            category = require_text(item.get("category") or "Household",
                                    f"items[{index}].category", max_length=120)
            period_rule = item.get("period_rule") or "current_month"
            if period_rule not in PERIOD_RULES:
                raise ValidationError(f"Unsupported period_rule: {period_rule}",
                                      details={"field": f"items[{index}].period_rule",
                                               "allowed": list(PERIOD_RULES)})
            method = item.get("default_allocation_method") or "equal"
            if method not in ALLOCATION_METHODS:
                raise ValidationError(f"Unsupported allocation method: {method}",
                                      details={"field": f"items[{index}]"
                                                        ".default_allocation_method"})
            recurrence = item.get("recurrence") or "monthly"
            if recurrence not in RECURRENCES:
                raise ValidationError(f"Unsupported recurrence: {recurrence}",
                                      details={"field": f"items[{index}].recurrence"})
            amount = item.get("default_amount_paise")
            if amount is not None:
                ensure_paise(amount, f"items[{index}].default_amount_paise")

            definition_id = _unique_id(
                conn, "expense_definition", "expense_definition_id",
                slugify(item.get("definition_id") or name, fallback="category", max_length=64))
            conn.execute(
                """INSERT INTO expense_definition
                   (expense_definition_id, apartment_id, name, category, period_rule,
                    default_allocation_method, is_recurring, is_active, recurrence,
                    default_amount_paise, sort_order, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,1,?,?,?,?,?)""",
                (definition_id, apartment_id, name, category, period_rule, method,
                 0 if recurrence == "none" else 1, recurrence, amount,
                 int(item.get("sort_order") or (index + 1) * 10), timestamp, timestamp))
            audit.record(conn, entity_type="expense_definition", entity_id=definition_id,
                         action="create", actor=actor, apartment_id=apartment_id,
                         reason="guided setup",
                         new_value={"name": name, "category": category,
                                    "period_rule": period_rule,
                                    "recurrence": recurrence,
                                    "default_amount_paise": amount})
            existing_names.add(name.casefold())
            created.append({"expense_definition_id": definition_id, "name": name,
                            "period_rule": period_rule,
                            "default_amount_paise": amount})
    return {"created": created, "created_count": len(created),
            "skipped": skipped, "skipped_count": len(skipped)}


def finish_setup(db_path, *, apartment_id: str | None = None,
                 create_statement_for: str | None = None,
                 actor: str = "operator") -> dict[str, Any]:
    """Step 4. Mark setup done, and optionally open the first statement.

    Marking it done only stops the application prompting. It does not lock
    anything: residents and categories stay editable, and the guided setup can
    be reopened at any time to add more.
    """
    resolved = apartment_id or apartments.default_apartment_id(db_path)
    statement_id = None
    if create_statement_for and resolved:
        from . import statements  # local import: statements has no business here otherwise

        month = normalize_month(create_statement_for, "create_statement_for")
        try:
            statement_id = statements.create_monthly_statement(
                db_path, apartment_id=resolved, settlement_month=month, actor=actor)
        except ConflictError as exc:
            # A statement for the month already exists; that is a fine outcome.
            statement_id = exc.details.get("statement_id")

    completed_at = now_timestamp()
    apartments.set_setting(db_path, SETUP_COMPLETED_SETTING, completed_at, actor=actor)
    state = setup_state(db_path, resolved)
    return {**state, "statement_id": statement_id, "completed_at": completed_at}


def reopen_setup(db_path, *, actor: str = "operator") -> dict[str, Any]:
    """Clear the completion marker so the guided setup prompts again."""
    with transaction(db_path) as conn:
        conn.execute("DELETE FROM app_setting WHERE setting_key=?",
                     (SETUP_COMPLETED_SETTING,))
        audit.record(conn, entity_type="app_setting", entity_id=SETUP_COMPLETED_SETTING,
                     action="delete", actor=actor, reason="guided setup reopened")
    return setup_state(db_path)

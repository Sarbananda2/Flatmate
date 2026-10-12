"""Apartment records and operator settings."""
from __future__ import annotations

import sqlite3
from typing import Any

from .. import db
from ..db import connect, now_timestamp, transaction
from ..errors import ConflictError, ValidationError
from . import audit
from .common import apartment_exists, get_apartment, optional_text, require_text

DEFAULT_APARTMENT_SETTING = "default_apartment_id"


def create_apartment(db_path, *, apartment_id: str, name: str,
                     currency_code: str = "INR", timezone: str = "Asia/Kolkata",
                     actor: str = "operator", created_at: str | None = None) -> dict[str, Any]:
    apartment_id = require_text(apartment_id, "apartment_id", max_length=64)
    name = require_text(name, "name", max_length=200)
    currency_code = require_text(currency_code, "currency_code", max_length=3).upper()
    timezone = require_text(timezone, "timezone", max_length=64)
    timestamp = created_at or now_timestamp()
    with transaction(db_path) as conn:
        if apartment_exists(conn, apartment_id):
            raise ConflictError("An apartment with this ID already exists.",
                                code="apartment_exists",
                                details={"apartment_id": apartment_id})
        conn.execute(
            """INSERT INTO apartment (apartment_id, name, currency_code, timezone, created_at)
               VALUES (?,?,?,?,?)""",
            (apartment_id, name, currency_code, timezone, timestamp))
        audit.record(conn, entity_type="apartment", entity_id=apartment_id,
                     action="create", actor=actor, apartment_id=apartment_id,
                     new_value={"name": name, "currency_code": currency_code,
                                "timezone": timezone})
        if _get_setting(conn, DEFAULT_APARTMENT_SETTING) is None:
            _set_setting(conn, DEFAULT_APARTMENT_SETTING, apartment_id)
        return get_apartment(conn, apartment_id)


def update_apartment(db_path, apartment_id: str, *, name: str | None = None,
                     currency_code: str | None = None, timezone: str | None = None,
                     actor: str = "operator") -> dict[str, Any]:
    with transaction(db_path) as conn:
        before = get_apartment(conn, apartment_id)
        after = dict(before)
        if name is not None:
            after["name"] = require_text(name, "name", max_length=200)
        if currency_code is not None:
            after["currency_code"] = require_text(currency_code, "currency_code", max_length=3).upper()
        if timezone is not None:
            after["timezone"] = require_text(timezone, "timezone", max_length=64)
        conn.execute(
            "UPDATE apartment SET name=?, currency_code=?, timezone=? WHERE apartment_id=?",
            (after["name"], after["currency_code"], after["timezone"], apartment_id))
        audit.record_field_changes(
            conn, entity_type="apartment", entity_id=apartment_id, before=before,
            after=after, fields=["name", "currency_code", "timezone"],
            actor=actor, apartment_id=apartment_id)
        return get_apartment(conn, apartment_id)


def list_apartments(db_path) -> list[dict[str, Any]]:
    with connect(db_path, read_only=True) as conn:
        return db.fetch_all(conn, "SELECT * FROM apartment ORDER BY name, apartment_id")


def read_apartment(db_path, apartment_id: str) -> dict[str, Any]:
    with connect(db_path, read_only=True) as conn:
        return get_apartment(conn, apartment_id)


def _get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT setting_value FROM app_setting WHERE setting_key=?",
                       (key,)).fetchone()
    return row[0] if row else None


def _set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """INSERT INTO app_setting (setting_key, setting_value, updated_at) VALUES (?,?,?)
           ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value,
                                                 updated_at=excluded.updated_at""",
        (key, value, now_timestamp()))


def get_setting(db_path, key: str) -> str | None:
    with connect(db_path, read_only=True) as conn:
        return _get_setting(conn, key)


def set_setting(db_path, key: str, value: str, *, actor: str = "operator") -> None:
    key = require_text(key, "setting_key", max_length=64)
    value = require_text(value, "setting_value", max_length=500)
    with transaction(db_path) as conn:
        old = _get_setting(conn, key)
        _set_setting(conn, key, value)
        audit.record(conn, entity_type="app_setting", entity_id=key, action="update",
                     actor=actor, field_name=key, old_value=old, new_value=value)


def default_apartment_id(db_path) -> str | None:
    """The apartment the UI opens on.

    Falls back to the only apartment on record, so a fresh database that was set
    up through the CLI still works without touching settings.
    """
    with connect(db_path, read_only=True) as conn:
        configured = _get_setting(conn, DEFAULT_APARTMENT_SETTING)
        if configured and apartment_exists(conn, configured):
            return configured
        rows = conn.execute("SELECT apartment_id FROM apartment ORDER BY created_at, apartment_id").fetchall()
        return rows[0][0] if rows else None

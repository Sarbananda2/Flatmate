"""Apartment records and operator settings."""
from __future__ import annotations

from typing import Any

from ..errors import ConflictError, ValidationError
from ..ids import now_timestamp
from ..persistence import Database, UnitOfWork
from . import audit
from .common import apartment_exists, get_apartment, optional_text, require_text

DEFAULT_APARTMENT_SETTING = "default_apartment_id"


def create_apartment(db: Database, *, apartment_id: str, name: str,
                     currency_code: str = "INR", timezone: str = "Asia/Kolkata",
                     actor: str = "operator", created_at: str | None = None) -> dict[str, Any]:
    apartment_id = require_text(apartment_id, "apartment_id", max_length=64)
    name = require_text(name, "name", max_length=200)
    currency_code = require_text(currency_code, "currency_code", max_length=3).upper()
    timezone = require_text(timezone, "timezone", max_length=64)
    timestamp = created_at or now_timestamp()
    with db.transaction() as uow:
        if apartment_exists(uow, apartment_id):
            raise ConflictError("An apartment with this ID already exists.",
                                code="apartment_exists",
                                details={"apartment_id": apartment_id})
        uow.apartments.insert(apartment_id=apartment_id, name=name,
                              currency_code=currency_code, timezone=timezone,
                              created_at=timestamp)
        audit.record(uow, entity_type="apartment", entity_id=apartment_id,
                     action="create", actor=actor, apartment_id=apartment_id,
                     new_value={"name": name, "currency_code": currency_code,
                                "timezone": timezone})
        if _get_setting(uow, DEFAULT_APARTMENT_SETTING) is None:
            _set_setting(uow, DEFAULT_APARTMENT_SETTING, apartment_id)
        return get_apartment(uow, apartment_id)


def update_apartment(db: Database, apartment_id: str, *, name: str | None = None,
                     currency_code: str | None = None, timezone: str | None = None,
                     actor: str = "operator") -> dict[str, Any]:
    with db.transaction() as uow:
        before = get_apartment(uow, apartment_id)
        after = dict(before)
        if name is not None:
            after["name"] = require_text(name, "name", max_length=200)
        if currency_code is not None:
            after["currency_code"] = require_text(currency_code, "currency_code", max_length=3).upper()
        if timezone is not None:
            after["timezone"] = require_text(timezone, "timezone", max_length=64)
        uow.apartments.update(apartment_id, name=after["name"],
                              currency_code=after["currency_code"],
                              timezone=after["timezone"])
        audit.record_field_changes(
            uow, entity_type="apartment", entity_id=apartment_id, before=before,
            after=after, fields=["name", "currency_code", "timezone"],
            actor=actor, apartment_id=apartment_id)
        return get_apartment(uow, apartment_id)


def list_apartments(db: Database) -> list[dict[str, Any]]:
    with db.read() as uow:
        return uow.apartments.list_all()


def count_apartments(db: Database) -> int:
    with db.read() as uow:
        return uow.apartments.count()


def read_apartment(db: Database, apartment_id: str) -> dict[str, Any]:
    with db.read() as uow:
        return get_apartment(uow, apartment_id)


def _get_setting(uow: UnitOfWork, key: str) -> str | None:
    return uow.apartments.get_setting(key)


def _set_setting(uow: UnitOfWork, key: str, value: str) -> None:
    uow.apartments.set_setting(key, value, updated_at=now_timestamp())


def get_setting(db: Database, key: str) -> str | None:
    with db.read() as uow:
        return _get_setting(uow, key)


def set_setting(db: Database, key: str, value: str, *, actor: str = "operator") -> None:
    key = require_text(key, "setting_key", max_length=64)
    value = require_text(value, "setting_value", max_length=500)
    with db.transaction() as uow:
        old = _get_setting(uow, key)
        _set_setting(uow, key, value)
        audit.record(uow, entity_type="app_setting", entity_id=key, action="update",
                     actor=actor, field_name=key, old_value=old, new_value=value)


def default_apartment_id(db: Database) -> str | None:
    """The apartment the UI opens on.

    Falls back to the only apartment on record, so a fresh database that was set
    up through the CLI still works without touching settings.
    """
    with db.read() as uow:
        configured = _get_setting(uow, DEFAULT_APARTMENT_SETTING)
        if configured and apartment_exists(uow, configured):
            return configured
        return uow.apartments.first_apartment_id()

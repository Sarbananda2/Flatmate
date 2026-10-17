"""Apartment records and operator settings."""
from __future__ import annotations

import re
from typing import Any, Mapping

from ..errors import ConflictError, ValidationError
from ..ids import now_timestamp
from ..persistence import Database, UnitOfWork
from . import audit
from .common import (
    DEFAULT_POOL_NAME,
    apartment_exists,
    get_apartment,
    optional_text,
    require_text,
)
from .property import bank_account, ifsc_code, upi_id

DEFAULT_APARTMENT_SETTING = "default_apartment_id"

# The postal address, part by part, in Indian postal order, with each part's
# longest allowed length. The PIN code follows them, after a dash.
ADDRESS_FIELDS = (("flat_number", 50), ("tower_block", 50), ("society_name", 150),
                  ("locality_street", 200), ("city", 100), ("state", 100))
PINCODE_RE = re.compile(r"^[1-9][0-9]{5}$")
# The household pool, the House Kitty: where residents send their shares and
# settlements. Its money is held in a real person's account -- the account
# holder's -- whose bank account, IFSC and UPI ID follow the same rules as the
# owner contact's.
POOL_FIELDS = ("pool_name", "pool_account_holder", "pool_bank_account", "pool_bank_ifsc",
               "pool_upi_id")
EDITABLE_FIELDS = ["name", "currency_code", "timezone",
                   *(field for field, _ in ADDRESS_FIELDS), "pincode", *POOL_FIELDS]


def premises_address(apartment: Mapping[str, Any]) -> str:
    """The apartment's address on one line, as HRA rent receipts print it.

    The parts that are set, in postal order -- "1805, CG6, Supertech Capetown,
    Sector 74, Noida, Uttar Pradesh - 201301". An apartment with no address
    parts recorded falls back to its name, which until migration 0014 was where
    the address was written.
    """
    line = ", ".join(apartment[field] for field, _ in ADDRESS_FIELDS if apartment.get(field))
    pincode = apartment.get("pincode")
    if pincode:
        line = f"{line} - {pincode}" if line else pincode
    return line or apartment["name"]


def _present(apartment: dict[str, Any]) -> dict[str, Any]:
    return {**apartment, "premises_address": premises_address(apartment)}


def _pincode(value: Any) -> str | None:
    pincode = optional_text(value, "pincode", max_length=50)
    if pincode is not None and not PINCODE_RE.match(pincode):
        raise ValidationError("The PIN code must be 6 digits and cannot start with 0.",
                              code="invalid_pincode", details={"field": "pincode"})
    return pincode


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
        return _present(get_apartment(uow, apartment_id))


def update_apartment(db: Database, apartment_id: str, *, actor: str = "operator",
                     **changes: Any) -> dict[str, Any]:
    """Change the apartment's name, settings, address or household pool.

    ``name``, ``currency_code`` and ``timezone`` are kept when left out or
    given as ``None``. An address part that is given is set, and ``None`` or an
    empty string clears it. So do the pool's account holder, bank account, IFSC
    and UPI ID; the pool's name is kept when left out, and ``None`` or an empty
    string puts back the default, "House Kitty".
    """
    unknown = sorted(set(changes) - set(EDITABLE_FIELDS))
    if unknown:
        raise ValidationError(f"Unsupported apartment fields: {', '.join(unknown)}.",
                              details={"unsupported_fields": unknown,
                                       "editable_fields": EDITABLE_FIELDS})
    with db.transaction() as uow:
        before = get_apartment(uow, apartment_id)
        after = dict(before)
        if changes.get("name") is not None:
            after["name"] = require_text(changes["name"], "name", max_length=200)
        if changes.get("currency_code") is not None:
            after["currency_code"] = require_text(changes["currency_code"], "currency_code",
                                                  max_length=3).upper()
        if changes.get("timezone") is not None:
            after["timezone"] = require_text(changes["timezone"], "timezone", max_length=64)
        for field, max_length in ADDRESS_FIELDS:
            if field in changes:
                after[field] = optional_text(changes[field], field, max_length=max_length)
        if "pincode" in changes:
            after["pincode"] = _pincode(changes["pincode"])
        if "pool_name" in changes:
            after["pool_name"] = (optional_text(changes["pool_name"], "pool_name",
                                                max_length=200) or DEFAULT_POOL_NAME)
        if "pool_account_holder" in changes:
            after["pool_account_holder"] = optional_text(
                changes["pool_account_holder"], "pool_account_holder", max_length=200)
        if "pool_bank_account" in changes:
            after["pool_bank_account"] = bank_account(changes["pool_bank_account"],
                                                      "pool_bank_account")
        if "pool_bank_ifsc" in changes:
            after["pool_bank_ifsc"] = ifsc_code(changes["pool_bank_ifsc"], "pool_bank_ifsc")
        if "pool_upi_id" in changes:
            after["pool_upi_id"] = upi_id(changes["pool_upi_id"], "pool_upi_id")
        uow.apartments.update(apartment_id, **{field: after[field] for field in EDITABLE_FIELDS})
        audit.record_field_changes(
            uow, entity_type="apartment", entity_id=apartment_id, before=before,
            after=after, fields=EDITABLE_FIELDS, actor=actor, apartment_id=apartment_id)
        return _present(get_apartment(uow, apartment_id))


def list_apartments(db: Database) -> list[dict[str, Any]]:
    with db.read() as uow:
        return [_present(apartment) for apartment in uow.apartments.list_all()]


def count_apartments(db: Database) -> int:
    with db.read() as uow:
        return uow.apartments.count()


def read_apartment(db: Database, apartment_id: str) -> dict[str, Any]:
    with db.read() as uow:
        return _present(get_apartment(uow, apartment_id))


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

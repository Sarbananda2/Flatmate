"""Resident records.

A resident is never deleted and never loses history. Moving out sets
``is_active = 0`` and ``active_until``; every expense allocation, payment,
credit, and adjustment stays exactly where it was, and the resident keeps
appearing in reconciliation for as long as they have a non-zero position.

**Nothing prorates.** The household decided on 2026-10-11 that a partial month
does not reduce anybody's share, for any category. ``active_from`` and
``active_until`` are recorded for reference and never alter an allocation. What
the operator still controls, expense by expense, is who takes part at all.
"""
from __future__ import annotations

import re
from typing import Any

from ..errors import ConflictError, ValidationError
from ..ids import now_timestamp
from ..periods import parse_iso_date
from ..persistence import Database, UnitOfWork
from ..persistence.repositories.residents import DOSSIER_COLUMNS
from . import audit
from .common import (
    get_apartment,
    get_resident,
    list_residents,
    optional_text,
    require_text,
)

#: The dossier: work, medical safety, tenancy terms, vehicle and building
#: access, and identity verification. None of it is read by the ledger.
DOSSIER_FIELDS = list(DOSSIER_COLUMNS)

EDITABLE_FIELDS = ["display_name", "active_from", "active_until", "is_active",
                   "email", "phone", "notes", "upi_id", "emergency_contact_name",
                   "emergency_contact_phone", "emergency_contact_relation",
                   *DOSSIER_FIELDS]

#: Dossier fields that are one of a fixed set of values.
_DOSSIER_CHOICES: dict[str, tuple[str, ...]] = {
    "work_mode": ("wfh", "hybrid", "onsite"),
    "blood_group": ("A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"),
    "vehicle_type": ("none", "two_wheeler", "four_wheeler", "both"),
    "id_document_type": ("aadhaar", "passport", "voter_id", "driving_license", "other"),
    "police_verification_status": ("pending", "submitted", "verified", "exempt"),
    "society_noc_status": ("pending", "approved", "not_applicable"),
}
#: Statuses that always have a value; a new resident starts at ``pending``.
_ALWAYS_SET = {"police_verification_status", "society_noc_status"}
#: Free-text dossier fields, with the longest value each will hold.
_DOSSIER_TEXT = {
    "profession": 200, "employer": 200, "secondary_emergency_name": 200,
    "secondary_emergency_phone": 50, "secondary_emergency_relation": 100,
    "keys_issued_notes": 500, "vehicle_reg_no": 50, "parking_slot": 100,
    "society_rfid_tag": 100, "id_document_number": 100, "home_city": 100, "home_state": 100,
}
#: Never written to the audit log in full: an identity document's number.
#: The log records that it changed, and its last four characters.
_AUDIT_MASKED = ("id_document_number",)

#: Who to call for a resident in an emergency. Optional free text, with the
#: longest value each will hold.
EMERGENCY_CONTACT_FIELDS = (("emergency_contact_name", 200), ("emergency_contact_phone", 50),
                            ("emergency_contact_relation", 100))

_UPI_ID = re.compile(r"^[A-Za-z0-9._-]{2,256}@[A-Za-z0-9.-]{2,64}$")


def _upi_id(value: Any) -> str | None:
    """A UPI ID is ``handle@provider``. It is only ever put into a payment link."""
    text = optional_text(value, "upi_id", max_length=320)
    if text and not _UPI_ID.match(text):
        raise ValidationError("A UPI ID looks like name@bank.", details={"field": "upi_id"})
    return text


def _dossier_value(field: str, value: Any) -> Any:
    """One dossier field, checked and cleaned as it is to be stored."""
    if field in _DOSSIER_CHOICES:
        allowed = _DOSSIER_CHOICES[field]
        text = optional_text(value, field, max_length=50)
        if text is None:
            if field in _ALWAYS_SET:
                raise ValidationError(f"{field} must be one of {', '.join(allowed)}.",
                                      details={"field": field, "allowed": list(allowed)})
            return None
        if text not in allowed:
            raise ValidationError(f"{field} must be one of {', '.join(allowed)}.",
                                  details={"field": field, "allowed": list(allowed)})
        return text
    if field == "notice_period_days":
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValidationError("notice_period_days must be a whole number of days, "
                                  "zero or more.", details={"field": field})
        return value
    if field == "lock_in_until":
        text = optional_text(value, field, max_length=10)
        if text:
            parse_iso_date(text, field)
        return text
    if field == "is_lease_signatory":
        return 1 if value else 0
    text = optional_text(value, field, max_length=_DOSSIER_TEXT[field])
    if field == "vehicle_reg_no" and text:
        text = text.upper()
    return text


def _dossier_changes(changes: dict[str, Any]) -> dict[str, Any]:
    unknown = sorted(set(changes) - set(DOSSIER_FIELDS))
    if unknown:
        raise ValidationError(f"Unsupported resident fields: {', '.join(unknown)}.",
                              details={"unsupported_fields": unknown,
                                       "editable_fields": EDITABLE_FIELDS})
    return {field: _dossier_value(field, value) for field, value in changes.items()}


def _masked(value: str | None) -> str | None:
    """What the audit log keeps of an identity number: its last four characters."""
    if not value:
        return value
    return f"•••• {value[-4:]}" if len(value) > 4 else "••••"


def _validate_dates(active_from: str | None, active_until: str | None) -> None:
    if active_from:
        parse_iso_date(active_from, "active_from")
    if active_until:
        parse_iso_date(active_until, "active_until")
    if active_from and active_until and active_until < active_from:
        raise ValidationError("active_until cannot be before active_from.",
                              details={"field": "active_until"})


def create_resident(db: Database, *, apartment_id: str, resident_id: str, display_name: str,
                    active_from: str | None = None, active_until: str | None = None,
                    is_active: bool = True, email: str | None = None,
                    phone: str | None = None, notes: str | None = None,
                    upi_id: str | None = None,
                    emergency_contact_name: str | None = None,
                    emergency_contact_phone: str | None = None,
                    emergency_contact_relation: str | None = None,
                    actor: str = "operator", **dossier: Any) -> dict[str, Any]:
    """A new resident. Dossier fields left out, or given as None, start empty
    (the two verification statuses start at ``pending``)."""
    resident_id = require_text(resident_id, "resident_id", max_length=64)
    display_name = require_text(display_name, "display_name", max_length=200)
    active_from = optional_text(active_from, "active_from", max_length=10)
    active_until = optional_text(active_until, "active_until", max_length=10)
    _validate_dates(active_from, active_until)
    upi_id = _upi_id(upi_id)
    given = {"emergency_contact_name": emergency_contact_name,
             "emergency_contact_phone": emergency_contact_phone,
             "emergency_contact_relation": emergency_contact_relation}
    emergency = {field: optional_text(given[field], field, max_length=max_length)
                 for field, max_length in EMERGENCY_CONTACT_FIELDS}
    dossier_values = _dossier_changes(
        {field: value for field, value in dossier.items() if value is not None})
    timestamp = now_timestamp()
    with db.transaction() as uow:
        get_apartment(uow, apartment_id)
        if uow.residents.exists(resident_id):
            raise ConflictError("A resident with this ID already exists.",
                                code="resident_exists",
                                details={"resident_id": resident_id})
        uow.residents.insert(
            resident_id=resident_id, apartment_id=apartment_id, display_name=display_name,
            active_from=active_from, active_until=active_until,
            is_active=1 if is_active else 0,
            email=optional_text(email, "email", max_length=200),
            phone=optional_text(phone, "phone", max_length=50),
            notes=optional_text(notes, "notes"), upi_id=upi_id,
            created_at=timestamp, updated_at=timestamp, dossier=dossier_values, **emergency)
        audit.record(uow, entity_type="resident", entity_id=resident_id, action="create",
                     actor=actor, apartment_id=apartment_id,
                     new_value={"display_name": display_name, "is_active": is_active,
                                "active_from": active_from})
        return get_resident(uow, resident_id)


def update_resident(db: Database, resident_id: str, *, apartment_id: str | None = None,
                    actor: str = "operator", reason: str | None = None,
                    **changes: Any) -> dict[str, Any]:
    """Update editable resident fields. Financial history is never touched."""
    with db.transaction() as uow:
        return update_resident_in(uow, resident_id, apartment_id=apartment_id, actor=actor,
                                  reason=reason, **changes)


def update_resident_in(uow: UnitOfWork, resident_id: str, *, apartment_id: str | None = None,
                       actor: str = "operator", reason: str | None = None,
                       **changes: Any) -> dict[str, Any]:
    """``update_resident`` inside the caller's transaction."""
    unknown = sorted(set(changes) - set(EDITABLE_FIELDS))
    if unknown:
        raise ValidationError(f"Unsupported resident fields: {', '.join(unknown)}.",
                              details={"unsupported_fields": unknown,
                                       "editable_fields": EDITABLE_FIELDS})
    before = get_resident(uow, resident_id, apartment_id)
    after = dict(before)
    if "display_name" in changes:
        after["display_name"] = require_text(changes["display_name"], "display_name",
                                             max_length=200)
    for field, max_length in (("active_from", 10), ("active_until", 10),
                              ("email", 200), ("phone", 50), ("notes", 2000),
                              *EMERGENCY_CONTACT_FIELDS):
        if field in changes:
            after[field] = optional_text(changes[field], field, max_length=max_length)
    if "upi_id" in changes:
        after["upi_id"] = _upi_id(changes["upi_id"])
    if "is_active" in changes:
        after["is_active"] = 1 if changes["is_active"] else 0
    after.update(_dossier_changes(
        {field: value for field, value in changes.items() if field in DOSSIER_FIELDS}))
    _validate_dates(after.get("active_from"), after.get("active_until"))
    after["updated_at"] = now_timestamp()
    uow.residents.update(
        resident_id, display_name=after["display_name"],
        active_from=after.get("active_from"), active_until=after.get("active_until"),
        is_active=after["is_active"], email=after.get("email"),
        phone=after.get("phone"), notes=after.get("notes"),
        upi_id=after.get("upi_id"),
        emergency_contact_name=after.get("emergency_contact_name"),
        emergency_contact_phone=after.get("emergency_contact_phone"),
        emergency_contact_relation=after.get("emergency_contact_relation"),
        updated_at=after["updated_at"],
        dossier={field: after.get(field) for field in DOSSIER_FIELDS})
    audit.record_field_changes(
        uow, entity_type="resident", entity_id=resident_id, before=before, after=after,
        fields=[field for field in EDITABLE_FIELDS if field not in _AUDIT_MASKED],
        actor=actor, apartment_id=before["apartment_id"], reason=reason)
    for field in _AUDIT_MASKED:
        if before.get(field) != after.get(field):
            audit.record(uow, entity_type="resident", entity_id=resident_id, action="update",
                         actor=actor, apartment_id=before["apartment_id"], field_name=field,
                         old_value=_masked(before.get(field)),
                         new_value=_masked(after.get(field)), reason=reason)
    return get_resident(uow, resident_id)


def deactivate_resident(db: Database, resident_id: str, *, active_until: str | None = None,
                        apartment_id: str | None = None, reason: str | None = None,
                        actor: str = "operator") -> dict[str, Any]:
    """Mark a resident as moved out. Nothing financial is deleted or recalculated.

    Their share of a part-month is **not** prorated -- that is the household's
    rule, not an omission. The move-out date is recorded for reference, and
    allocations stay exactly as the operator entered them. To leave somebody out
    of a particular expense, exclude them from its participants; they then get
    an explicit zero share.
    """
    return update_resident(
        db, resident_id, apartment_id=apartment_id, actor=actor,
        reason=reason or "resident deactivated",
        is_active=False,
        active_until=active_until or now_timestamp()[:10])


def reactivate_resident(db: Database, resident_id: str, *, apartment_id: str | None = None,
                        actor: str = "operator") -> dict[str, Any]:
    return update_resident(db, resident_id, apartment_id=apartment_id, actor=actor,
                           reason="resident reactivated", is_active=True, active_until=None)


def read_residents(db: Database, apartment_id: str, *, active_only: bool = False) -> list[dict[str, Any]]:
    with db.read() as uow:
        get_apartment(uow, apartment_id)
        return list_residents(uow, apartment_id, active_only=active_only)


def read_resident(db: Database, resident_id: str, apartment_id: str | None = None) -> dict[str, Any]:
    with db.read() as uow:
        return get_resident(uow, resident_id, apartment_id)

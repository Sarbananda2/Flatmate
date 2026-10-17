"""The household's contacts and its document vault.

Contacts are the people the flat deals with -- the owner, the broker, the
caretaker, the society manager, emergency utility lines -- with how to reach
them. The owner is the lease's landlord: there is one owner contact at most,
and its name and contact, tax and bank details are kept identical to the
lease's ``landlord_*`` columns in both directions, in the same transaction as
the change that prompted it. A few fields belong to one role only: an owner's PAN and bank details,
a broker's agency and fee, the responsibilities of whoever keeps the building
running. Those are refused for a role they do not belong to, and dropped when
a contact's role changes.

Documents are kept in the database, so a database backup holds every
agreement as well as the ledger. Each file is hashed with SHA-256 as it comes
in; the hash is stored beside it, written to the audit trail on upload and on
deletion, and checked again whenever the file is read back. A listing never
carries file contents.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import PurePath
from typing import Any, Mapping

from ..errors import ConflictError, NotFoundError, ReconciliationError, ValidationError
from ..ids import new_id, now_timestamp
from ..money import ensure_paise, format_inr
from ..persistence import Database, UnitOfWork
from ..persistence.repositories.property import CONTACT_COLUMNS
from . import audit, names
from .common import get_apartment, get_resident, optional_text, require_text

CONTACT_ENTITY = "household_contact"
DOCUMENT_ENTITY = "household_document"

#: A contact's name, in parts; ``name`` is composed from them.
NAME_PARTS = ("first_name", "middle_name", "last_name")
ROLES = ("owner", "broker", "caretaker", "society_manager", "emergency_utility")
FEE_STATUSES = ("paid", "pending", "na")
CATEGORIES = ("lease_agreement", "society_noc", "police_verification",
              "inventory_handover", "other")

# Fields that only make sense for some roles.
ROLE_FIELDS: dict[str, tuple[str, ...]] = {
    "pan": ("owner",),
    "bank_account_no": ("owner",),
    "bank_ifsc": ("owner",),
    "beneficiary_name": ("owner",),
    "upi_id": ("owner", "broker", "caretaker", "society_manager"),
    "agency_name": ("broker",),
    "fee_amount_paise": ("broker",),
    "fee_status": ("broker",),
    "responsibilities": ("caretaker", "society_manager", "emergency_utility"),
}

MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
# What the vault accepts, by extension, and the type each is served as.
DOCUMENT_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
}
# Types a browser may show in place rather than download.
INLINE_TYPES = {"application/pdf", "image/png", "image/jpeg", "image/webp", "text/plain"}

_PHONE = re.compile(r"^\+?[0-9 ()-]+$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PAN = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
# A payout beneficiary is a person or a business: letters, digits and spaces,
# with . , ' & - for initials, firms and names like D'Souza. Nothing a query or
# a page could read as code: no ; " / * < > or control characters, and no "--".
_BENEFICIARY_NAME = re.compile(r"^(?!.*--)[A-Za-z0-9 .,'&-]{1,200}$")
_IFSC = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
_ACCOUNT = re.compile(r"^[0-9]{9,18}$")
_UPI = re.compile(r"^[A-Za-z0-9._-]{2,256}@[A-Za-z0-9.-]{2,64}$")


# ---------------------------------------------------------------- contacts --

def phone_number(value: Any, field: str = "phone") -> str | None:
    """7 to 15 digits, with spaces, dashes, brackets and a leading + allowed."""
    text = optional_text(value, field, max_length=30)
    if text is None:
        return None
    digits = re.sub(r"\D", "", text)
    if not _PHONE.match(text) or not 7 <= len(digits) <= 15:
        raise ValidationError("Enter a phone number of 7 to 15 digits.",
                              details={"field": field})
    return text


def _pattern(value: Any, field: str, pattern: re.Pattern[str], message: str, *,
             upper: bool = False, max_length: int = 320) -> str | None:
    text = optional_text(value, field, max_length=max_length)
    if text is None:
        return None
    if upper:
        text = text.upper().replace(" ", "")
    if not pattern.match(text):
        raise ValidationError(message, details={"field": field})
    return text


def email_address(value: Any, field: str = "email") -> str | None:
    return _pattern(value, field, _EMAIL, "Enter a valid email address.", max_length=254)


def pan_number(value: Any, field: str = "pan") -> str | None:
    return _pattern(value, field, _PAN,
                    "A PAN is five letters, four digits and a letter, e.g. ABCDE1234F.",
                    upper=True, max_length=12)


def bank_account(value: Any, field: str = "bank_account_no") -> str | None:
    return _pattern(value, field, _ACCOUNT, "A bank account number is 9 to 18 digits.",
                    max_length=18)


def ifsc_code(value: Any, field: str = "bank_ifsc") -> str | None:
    return _pattern(value, field, _IFSC,
                    "An IFSC is four letters, a zero, then six characters, e.g. HDFC0001234.",
                    upper=True, max_length=13)


def upi_id(value: Any, field: str = "upi_id") -> str | None:
    return _pattern(value, field, _UPI, "A UPI ID looks like name@bank.")


def beneficiary_name(value: Any, owner_name: str | None,
                     field: str = "beneficiary_name") -> str | None:
    """Whose name the owner's payout account is in, when it is not the owner's
    own. Naming nobody, or the owner themself, is the owner's own account:
    None, whatever a client sent. Any other name must look like a name."""
    name = optional_text(value, field, max_length=200)
    if not name:
        return None
    if owner_name and name.casefold() == owner_name.strip().casefold():
        return None
    if not _BENEFICIARY_NAME.match(name):
        raise ValidationError("Beneficiary name contains invalid characters.",
                              details={"field": field})
    return name


def _clean_contact(fields: Mapping[str, Any]) -> dict[str, Any]:
    """Every contact column, validated, with the inapplicable ones refused."""
    role = fields.get("role")
    if role not in ROLES:
        raise ValidationError(f"role must be one of: {', '.join(ROLES)}.",
                              details={"field": "role"})
    for field, roles in ROLE_FIELDS.items():
        if fields.get(field) not in (None, "") and role not in roles:
            raise ValidationError(f"{field} does not apply to a {role.replace('_', ' ')}.",
                                  details={"field": field})
    fee = fields.get("fee_amount_paise")
    if fee is not None:
        ensure_paise(fee, "fee_amount_paise")
    fee_status = fields.get("fee_status") or None
    if fee_status is not None and fee_status not in FEE_STATUSES:
        raise ValidationError(f"fee_status must be one of: {', '.join(FEE_STATUSES)}.",
                              details={"field": "fee_status"})
    if role == "broker" and fee is not None and fee_status is None:
        fee_status = "pending"
    name = names.resolve_name(fields, None, parts=NAME_PARTS, whole="name")
    return {
        "role": role,
        **name,
        "phone": phone_number(fields.get("phone")),
        "email": email_address(fields.get("email")),
        "pan": pan_number(fields.get("pan")),
        "bank_account_no": bank_account(fields.get("bank_account_no")),
        "bank_ifsc": ifsc_code(fields.get("bank_ifsc")),
        "upi_id": upi_id(fields.get("upi_id")),
        "beneficiary_name": beneficiary_name(fields.get("beneficiary_name"), name["name"]),
        "agency_name": optional_text(fields.get("agency_name"), "agency_name",
                                     max_length=200),
        "fee_amount_paise": fee,
        "fee_status": fee_status,
        "responsibilities": optional_text(fields.get("responsibilities"),
                                          "responsibilities", max_length=500),
        "notes": optional_text(fields.get("notes"), "notes"),
    }


def _present_contact(row: Mapping[str, Any]) -> dict[str, Any]:
    out = {key: row[key] for key in ("contact_id", "apartment_id", *CONTACT_COLUMNS,
                                     "created_at", "updated_at")}
    out["fee_display"] = (format_inr(row["fee_amount_paise"])
                          if row["fee_amount_paise"] is not None else None)
    return out


def _contact(uow: UnitOfWork, contact_id: str, apartment_id: str) -> dict[str, Any]:
    row = uow.property.contact(contact_id)
    if row is None or row["apartment_id"] != apartment_id:
        raise NotFoundError("Contact not found.", details={"contact_id": contact_id})
    return row


def _unknown(fields: Mapping[str, Any]) -> None:
    unknown = sorted(set(fields) - set(CONTACT_COLUMNS))
    if unknown:
        raise ValidationError(f"Unsupported contact fields: {', '.join(unknown)}.",
                              details={"unsupported_fields": unknown})


# ------------------------------------------------- the owner and the lease --

#: The owner contact's fields and the lease columns that hold the same thing.
OWNER_LEASE_FIELDS = {
    "name": "landlord_name",
    "first_name": "landlord_first_name",
    "middle_name": "landlord_middle_name",
    "last_name": "landlord_last_name",
    "phone": "landlord_phone",
    "email": "landlord_email",
    "pan": "landlord_pan",
    "bank_account_no": "landlord_bank_account",
    "bank_ifsc": "landlord_bank_ifsc",
    "upi_id": "landlord_upi_id",
    "beneficiary_name": "payout_beneficiary_name",
}
SYNC_FROM_OWNER = "synced from the owner contact"
SYNC_FROM_LEASE = "synced from the lease"


def _refuse_second_owner(uow: UnitOfWork, apartment_id: str,
                         contact_id: str | None = None) -> None:
    """The lease has one landlord, so the household has one owner contact."""
    owner = uow.property.owner_contact(apartment_id)
    if owner and owner["contact_id"] != contact_id:
        raise ConflictError(
            f"{owner['name']} is already on file as the property owner. Edit that contact "
            "instead.", code="owner_exists",
            details={"field": "role", "contact_id": owner["contact_id"]})


def _sync_lease_from_owner(uow: UnitOfWork, apartment_id: str, owner: Mapping[str, Any],
                           actor: str) -> None:
    """Copy the owner contact's details onto the lease, if there is a lease."""
    lease = uow.rent.active_lease(apartment_id)
    if lease is None:
        return
    changes = {column: owner[field] for field, column in OWNER_LEASE_FIELDS.items()
               if lease[column] != owner[field]}
    if not changes:
        return
    uow.rent.update_lease(lease["config_id"], updated_at=now_timestamp(), **changes)
    audit.record_field_changes(
        uow, entity_type="rent_lease_config", entity_id=lease["config_id"], before=lease,
        after={**lease, **changes}, fields=list(changes), actor=actor,
        apartment_id=apartment_id, reason=SYNC_FROM_OWNER)


def sync_owner_from_lease_in(uow: UnitOfWork, apartment_id: str,
                             lease: Mapping[str, Any], *,
                             actor: str = "operator") -> dict[str, Any]:
    """Make the owner contact match the lease's landlord, inside the caller's
    transaction: update the owner contact, or create one if there is none.
    ``lease`` holds already-validated ``landlord_*`` values."""
    landlord = {field: lease.get(column) for field, column in OWNER_LEASE_FIELDS.items()}
    owner = uow.property.owner_contact(apartment_id)
    if owner is None:
        contact_id = new_id("contact")
        fields = {column: None for column in CONTACT_COLUMNS}
        fields.update(role="owner", **landlord)
        uow.property.insert_contact(contact_id=contact_id, apartment_id=apartment_id,
                                    created_at=now_timestamp(), **fields)
        audit.record(uow, entity_type=CONTACT_ENTITY, entity_id=contact_id, action="create",
                     actor=actor, apartment_id=apartment_id, new_value=fields,
                     reason=SYNC_FROM_LEASE)
        return _present_contact(uow.property.contact(contact_id))
    changed = {field: value for field, value in landlord.items() if owner[field] != value}
    if changed:
        uow.property.update_contact(
            owner["contact_id"], updated_at=now_timestamp(),
            **{column: changed.get(column, owner[column]) for column in CONTACT_COLUMNS})
        audit.record(uow, entity_type=CONTACT_ENTITY, entity_id=owner["contact_id"],
                     action="update", actor=actor, apartment_id=apartment_id,
                     old_value={field: owner[field] for field in changed},
                     new_value=changed, reason=SYNC_FROM_LEASE)
    return _present_contact(uow.property.contact(owner["contact_id"]))


def list_contacts(db: Database, apartment_id: str) -> dict[str, Any]:
    with db.read() as uow:
        get_apartment(uow, apartment_id)
        items = [_present_contact(row) for row in uow.property.contacts(apartment_id)]
    return {"items": items, "total": len(items),
            "roles_on_file": sorted({item["role"] for item in items}, key=ROLES.index)}


def create_contact(db: Database, apartment_id: str, fields: Mapping[str, Any], *,
                   actor: str = "operator") -> dict[str, Any]:
    _unknown(fields)
    clean = _clean_contact(fields)
    with db.transaction() as uow:
        get_apartment(uow, apartment_id)
        if clean["role"] == "owner":
            _refuse_second_owner(uow, apartment_id)
        contact_id = new_id("contact")
        uow.property.insert_contact(contact_id=contact_id, apartment_id=apartment_id,
                                    created_at=now_timestamp(), **clean)
        audit.record(uow, entity_type=CONTACT_ENTITY, entity_id=contact_id, action="create",
                     actor=actor, apartment_id=apartment_id, new_value=clean)
        if clean["role"] == "owner":
            _sync_lease_from_owner(uow, apartment_id, clean, actor)
        return _present_contact(uow.property.contact(contact_id))


def update_contact(db: Database, apartment_id: str, contact_id: str,
                   changes: Mapping[str, Any], *, actor: str = "operator") -> dict[str, Any]:
    """Change only the fields sent. A new role drops the fields it does not use."""
    _unknown(changes)
    with db.transaction() as uow:
        before = _contact(uow, contact_id, apartment_id)
        merged = {column: before[column] for column in CONTACT_COLUMNS}
        merged.update(changes)
        # The name follows what was sent: its parts, or a whole name to split.
        merged.update(names.resolve_name(changes, before, parts=NAME_PARTS, whole="name"))
        role = merged["role"]
        if "role" in changes and changes["role"] != before["role"]:
            for field, roles in ROLE_FIELDS.items():
                if role not in roles and field not in changes:
                    merged[field] = None
        clean = _clean_contact(merged)
        if clean["role"] == "owner" and before["role"] != "owner":
            _refuse_second_owner(uow, apartment_id, contact_id)
        old = {column: before[column] for column in CONTACT_COLUMNS}
        if clean == old:
            return _present_contact(before)
        uow.property.update_contact(contact_id, updated_at=now_timestamp(), **clean)
        audit.record(uow, entity_type=CONTACT_ENTITY, entity_id=contact_id, action="update",
                     actor=actor, apartment_id=apartment_id,
                     old_value={k: v for k, v in old.items() if clean[k] != v},
                     new_value={k: v for k, v in clean.items() if old[k] != v})
        if clean["role"] == "owner":
            _sync_lease_from_owner(uow, apartment_id, clean, actor)
        return _present_contact(uow.property.contact(contact_id))


def delete_contact(db: Database, apartment_id: str, contact_id: str, *,
                   actor: str = "operator") -> None:
    with db.transaction() as uow:
        before = _contact(uow, contact_id, apartment_id)
        uow.property.delete_contact(contact_id)
        audit.record(uow, entity_type=CONTACT_ENTITY, entity_id=contact_id, action="delete",
                     actor=actor, apartment_id=apartment_id,
                     old_value={column: before[column] for column in CONTACT_COLUMNS})


# --------------------------------------------------------------- documents --

def _filename(value: Any) -> str:
    """The file's own name, without any path a browser sent along with it."""
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("The file needs a name.", details={"field": "file"})
    name = PurePath(value.replace("\\", "/")).name.strip()
    name = re.sub(r"[\x00-\x1f\x7f]", "", name)
    if not name or name in (".", ".."):
        raise ValidationError("The file needs a name.", details={"field": "file"})
    return name[:200]


def _document(uow: UnitOfWork, doc_id: str, apartment_id: str) -> dict[str, Any]:
    row = uow.property.document(doc_id)
    if row is None or row["apartment_id"] != apartment_id:
        raise NotFoundError("Document not found.", details={"doc_id": doc_id})
    return row


def list_documents(db: Database, apartment_id: str) -> dict[str, Any]:
    """Every document's details, newest first, without the files themselves."""
    with db.read() as uow:
        get_apartment(uow, apartment_id)
        items = uow.property.documents(apartment_id)
    return {"items": items, "total": len(items),
            "total_bytes": sum(item["size_bytes"] for item in items),
            "categories_on_file": sorted({item["category"] for item in items},
                                         key=CATEGORIES.index)}


def upload_document(db: Database, apartment_id: str, *, content: bytes, filename: str,
                    title: str, category: str, notes: str | None = None,
                    uploaded_by_resident_id: str | None = None,
                    actor: str = "operator") -> dict[str, Any]:
    """Store one file with its SHA-256. The file type is decided by its extension."""
    name = _filename(filename)
    content_type = DOCUMENT_TYPES.get(PurePath(name).suffix.lower())
    if content_type is None:
        raise ValidationError(
            "That kind of file is not accepted. Upload a PDF, an image (PNG, JPEG, WebP), "
            "a Word document or a text file.", code="unsupported_file_type",
            details={"field": "file", "accepted": sorted(DOCUMENT_TYPES)})
    if not isinstance(content, (bytes, bytearray)) or not content:
        raise ValidationError("The file is empty.", details={"field": "file"})
    if len(content) > MAX_DOCUMENT_BYTES:
        raise ValidationError(
            f"The file is larger than the {MAX_DOCUMENT_BYTES // (1024 * 1024)} MB limit.",
            code="file_too_large", details={"field": "file", "size_bytes": len(content),
                                            "limit_bytes": MAX_DOCUMENT_BYTES})
    if category not in CATEGORIES:
        raise ValidationError(f"category must be one of: {', '.join(CATEGORIES)}.",
                              details={"field": "category"})
    clean_title = require_text(title, "title", max_length=200)
    clean_notes = optional_text(notes, "notes")
    digest = hashlib.sha256(content).hexdigest()

    with db.transaction() as uow:
        get_apartment(uow, apartment_id)
        uploader = uploaded_by_resident_id or None
        if uploader:
            get_resident(uow, uploader, apartment_id)
        doc_id = new_id("doc")
        uow.property.insert_document(
            doc_id=doc_id, apartment_id=apartment_id, title=clean_title, category=category,
            filename=name, content_type=content_type, size_bytes=len(content),
            sha256=digest, content=bytes(content), uploaded_by_resident_id=uploader,
            notes=clean_notes, uploaded_at=now_timestamp())
        # What was stored must be what arrived, byte for byte.
        stored = uow.property.document_content(doc_id)
        if stored is None or hashlib.sha256(stored).hexdigest() != digest:
            raise ReconciliationError("The stored document does not match the upload.",
                                      code="document_hash_mismatch")
        audit.record(uow, entity_type=DOCUMENT_ENTITY, entity_id=doc_id, action="upload",
                     actor=actor, apartment_id=apartment_id,
                     new_value={"title": clean_title, "category": category,
                                "filename": name, "size_bytes": len(content),
                                "sha256": digest})
        return uow.property.document(doc_id)


def get_document_content(db: Database, apartment_id: str, doc_id: str) -> dict[str, Any]:
    """A document's bytes and how to serve them, after checking its hash."""
    with db.read() as uow:
        meta = _document(uow, doc_id, apartment_id)
        content = uow.property.document_content(doc_id)
    if content is None or hashlib.sha256(content).hexdigest() != meta["sha256"]:
        raise ReconciliationError(
            "This document no longer matches the SHA-256 recorded when it was uploaded.",
            code="document_hash_mismatch", details={"doc_id": doc_id})
    return {**meta, "content": content, "inline_allowed": meta["content_type"] in INLINE_TYPES}


def delete_document(db: Database, apartment_id: str, doc_id: str, *,
                    actor: str = "operator", reason: str | None = None) -> None:
    with db.transaction() as uow:
        meta = _document(uow, doc_id, apartment_id)
        uow.property.delete_document(doc_id)
        audit.record(uow, entity_type=DOCUMENT_ENTITY, entity_id=doc_id, action="delete",
                     actor=actor, apartment_id=apartment_id, reason=reason,
                     old_value={key: meta[key] for key in ("title", "category", "filename",
                                                           "size_bytes", "sha256")})


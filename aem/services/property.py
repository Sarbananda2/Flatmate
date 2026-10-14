"""The household's contacts and its document vault.

Contacts are the people the flat deals with -- the owner, the broker, the
caretaker, the society manager, emergency utility lines -- with how to reach
them. A few fields belong to one role only: an owner's PAN and bank details,
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

from ..errors import NotFoundError, ReconciliationError, ValidationError
from ..ids import new_id, now_timestamp
from ..money import ensure_paise, format_inr
from ..persistence import Database, UnitOfWork
from ..persistence.repositories.property import CONTACT_COLUMNS
from . import audit
from .common import get_apartment, get_resident, optional_text, require_text

CONTACT_ENTITY = "household_contact"
DOCUMENT_ENTITY = "household_document"

ROLES = ("owner", "broker", "caretaker", "society_manager", "emergency_utility")
FEE_STATUSES = ("paid", "pending", "na")
CATEGORIES = ("lease_agreement", "society_noc", "police_verification",
              "inventory_handover", "other")

# Fields that only make sense for some roles.
ROLE_FIELDS: dict[str, tuple[str, ...]] = {
    "pan": ("owner",),
    "bank_account_no": ("owner",),
    "bank_ifsc": ("owner",),
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
_IFSC = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
_ACCOUNT = re.compile(r"^[0-9]{9,18}$")
_UPI = re.compile(r"^[A-Za-z0-9._-]{2,256}@[A-Za-z0-9.-]{2,64}$")


# ---------------------------------------------------------------- contacts --

def _phone(value: Any) -> str | None:
    text = optional_text(value, "phone", max_length=30)
    if text is None:
        return None
    digits = re.sub(r"\D", "", text)
    if not _PHONE.match(text) or not 7 <= len(digits) <= 15:
        raise ValidationError("Enter a phone number of 7 to 15 digits.",
                              details={"field": "phone"})
    return text


def _pattern(value: Any, field: str, pattern: re.Pattern[str], message: str, *,
             upper: bool = False, max_length: int = 320) -> str | None:
    text = optional_text(value, field, max_length=max_length)
    if text is None:
        return None
    if upper:
        text = text.upper()
    if not pattern.match(text):
        raise ValidationError(message, details={"field": field})
    return text


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
    return {
        "role": role,
        "name": require_text(fields.get("name"), "name", max_length=200),
        "phone": _phone(fields.get("phone")),
        "email": _pattern(fields.get("email"), "email", _EMAIL,
                          "Enter a valid email address."),
        "pan": _pattern(fields.get("pan"), "pan", _PAN,
                        "A PAN is five letters, four digits and a letter, e.g. ABCDE1234F.",
                        upper=True, max_length=10),
        "bank_account_no": _pattern(fields.get("bank_account_no"), "bank_account_no",
                                    _ACCOUNT, "A bank account number is 9 to 18 digits.",
                                    max_length=18),
        "bank_ifsc": _pattern(fields.get("bank_ifsc"), "bank_ifsc", _IFSC,
                              "An IFSC is four letters, a zero, then six characters, "
                              "e.g. HDFC0001234.", upper=True, max_length=11),
        "upi_id": _pattern(fields.get("upi_id"), "upi_id", _UPI,
                           "A UPI ID looks like name@bank."),
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
        contact_id = new_id("contact")
        uow.property.insert_contact(contact_id=contact_id, apartment_id=apartment_id,
                                    created_at=now_timestamp(), **clean)
        audit.record(uow, entity_type=CONTACT_ENTITY, entity_id=contact_id, action="create",
                     actor=actor, apartment_id=apartment_id, new_value=clean)
        return _present_contact(uow.property.contact(contact_id))


def update_contact(db: Database, apartment_id: str, contact_id: str,
                   changes: Mapping[str, Any], *, actor: str = "operator") -> dict[str, Any]:
    """Change only the fields sent. A new role drops the fields it does not use."""
    _unknown(changes)
    with db.transaction() as uow:
        before = _contact(uow, contact_id, apartment_id)
        merged = {column: before[column] for column in CONTACT_COLUMNS}
        merged.update(changes)
        role = merged["role"]
        if "role" in changes and changes["role"] != before["role"]:
            for field, roles in ROLE_FIELDS.items():
                if role not in roles and field not in changes:
                    merged[field] = None
        clean = _clean_contact(merged)
        old = {column: before[column] for column in CONTACT_COLUMNS}
        if clean == old:
            return _present_contact(before)
        uow.property.update_contact(contact_id, updated_at=now_timestamp(), **clean)
        audit.record(uow, entity_type=CONTACT_ENTITY, entity_id=contact_id, action="update",
                     actor=actor, apartment_id=apartment_id,
                     old_value={k: v for k, v in old.items() if clean[k] != v},
                     new_value={k: v for k, v in clean.items() if old[k] != v})
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


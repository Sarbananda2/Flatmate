"""Household & Property: the flat's contacts and its document vault.

The rooms live in their own router, under the same ``/api/property`` prefix.
"""
from __future__ import annotations

from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Path, Query, UploadFile
from fastapi.responses import Response

from ...errors import AemError
from ...schemas import ContactCreate, ContactResponse, ContactUpdate, DocumentMetadataResponse
from ...services import property as household
from ..deps import ApartmentId, AppSettings, Db, to_http

router = APIRouter(prefix="/api/property", tags=["household & property"])


# ---------------------------------------------------------------- contacts --

@router.get("/contacts", summary="List the household's contacts")
def list_contacts(db: Db, apartment_id: ApartmentId) -> dict[str, Any]:
    try:
        return household.list_contacts(db, apartment_id)
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/contacts", status_code=201, response_model=ContactResponse,
             summary="Add a contact")
def create_contact(payload: ContactCreate, db: Db, apartment_id: ApartmentId,
                   settings: AppSettings) -> dict[str, Any]:
    """Role-specific details (an owner's PAN and bank, a broker's agency and fee,
    a caretaker's responsibilities) are refused for any other role."""
    try:
        return household.create_contact(db, apartment_id,
                                        payload.model_dump(exclude_unset=True),
                                        actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc


@router.patch("/contacts/{contact_id}", response_model=ContactResponse,
              summary="Change a contact")
def update_contact(payload: ContactUpdate, db: Db, apartment_id: ApartmentId,
                   settings: AppSettings, contact_id: str = Path()) -> dict[str, Any]:
    """Only the fields sent are changed; send null to clear one."""
    try:
        return household.update_contact(db, apartment_id, contact_id,
                                        payload.model_dump(exclude_unset=True),
                                        actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc


@router.delete("/contacts/{contact_id}", status_code=204, summary="Remove a contact")
def delete_contact(db: Db, apartment_id: ApartmentId, settings: AppSettings,
                   contact_id: str = Path()) -> None:
    try:
        household.delete_contact(db, apartment_id, contact_id, actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc


# --------------------------------------------------------------- documents --

@router.get("/documents", summary="List the stored documents")
def list_documents(db: Db, apartment_id: ApartmentId) -> dict[str, Any]:
    """Each document's title, category, file details and SHA-256 -- never the
    file itself, so the list stays quick however large the files are."""
    try:
        return household.list_documents(db, apartment_id)
    except AemError as exc:
        raise to_http(exc) from exc


@router.post("/documents", status_code=201, response_model=DocumentMetadataResponse,
             summary="Upload a document")
def upload_document(
    db: Db, apartment_id: ApartmentId, settings: AppSettings,
    file: Annotated[UploadFile, File(
        description="PDF, PNG, JPEG, WebP, Word or text. 10 MB at most.")],
    title: Annotated[str, Form(max_length=200)],
    category: Annotated[str, Form()],
    notes: Annotated[str | None, Form(max_length=2000)] = None,
    uploaded_by_resident_id: Annotated[str | None, Form(max_length=64)] = None,
) -> dict[str, Any]:
    """Multipart upload. The file is hashed with SHA-256 and stored in the
    database, so a database backup carries it too."""
    try:
        # Read one byte past the limit: enough to tell an oversized file apart
        # without holding all of a very large one in memory.
        content = file.file.read(household.MAX_DOCUMENT_BYTES + 1)
        return household.upload_document(
            db, apartment_id, content=content, filename=file.filename or "",
            title=title, category=category, notes=notes,
            uploaded_by_resident_id=uploaded_by_resident_id, actor=settings.operator_name)
    except AemError as exc:
        raise to_http(exc) from exc
    finally:
        file.file.close()


@router.get("/documents/{doc_id}/download", summary="Download a document",
            response_class=Response)
def download_document(db: Db, apartment_id: ApartmentId, doc_id: str = Path(),
                      inline: Annotated[bool, Query(
                          description="Show a PDF, image or text file in the browser "
                                      "instead of downloading it.")] = False) -> Response:
    """The file exactly as uploaded, after its SHA-256 is checked again."""
    try:
        document = household.get_document_content(db, apartment_id, doc_id)
    except AemError as exc:
        raise to_http(exc) from exc
    disposition = "inline" if inline and document["inline_allowed"] else "attachment"
    ascii_name = document["filename"].encode("ascii", "replace").decode().replace('"', "'")
    return Response(
        content=document["content"],
        media_type=document["content_type"],
        headers={
            "Content-Disposition": (f'{disposition}; filename="{ascii_name}"; '
                                    f"filename*=UTF-8''{quote(document['filename'])}"),
            "ETag": f'"{document["sha256"]}"',
            "X-Content-Sha256": document["sha256"],
            "X-Content-Type-Options": "nosniff",
            # A stored file is shown, never run.
            "Content-Security-Policy": "sandbox; default-src 'none'; img-src 'self'; "
                                       "style-src 'unsafe-inline'",
            "Cache-Control": "private, no-store",
        })


@router.delete("/documents/{doc_id}", status_code=204, summary="Delete a document")
def delete_document(db: Db, apartment_id: ApartmentId, settings: AppSettings,
                    doc_id: str = Path(),
                    reason: Annotated[str | None, Query(max_length=2000)] = None) -> None:
    """The file is removed for good. Its title, size and SHA-256 stay in the
    audit trail."""
    try:
        household.delete_document(db, apartment_id, doc_id, actor=settings.operator_name,
                                  reason=reason)
    except AemError as exc:
        raise to_http(exc) from exc



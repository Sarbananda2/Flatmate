"""The household's contacts and its document vault."""
from __future__ import annotations

from typing import Any

from .base import Repository

CONTACT_COLUMNS = (
    "role", "name", "phone", "email", "pan", "bank_account_no", "bank_ifsc", "upi_id",
    "agency_name", "fee_amount_paise", "fee_status", "responsibilities", "notes",
)
# Everything about a document except its bytes, so a listing stays light.
_DOCUMENT_METADATA = (
    "doc_id, apartment_id, title, category, filename, content_type, size_bytes, sha256, "
    "uploaded_by_resident_id, notes, uploaded_at")


class PropertyRepository(Repository):
    # -- contacts --------------------------------------------------------------

    def contacts(self, apartment_id: str) -> list[dict[str, Any]]:
        return self._s.fetch_all(
            "SELECT * FROM household_contact WHERE apartment_id = %s ORDER BY row_seq",
            (apartment_id,))

    def contact(self, contact_id: str) -> dict[str, Any] | None:
        return self._s.fetch_one(
            "SELECT * FROM household_contact WHERE contact_id = %s", (contact_id,))

    def insert_contact(self, *, contact_id: str, apartment_id: str, created_at: str,
                       **fields: Any) -> None:
        columns = ("contact_id", "apartment_id", *CONTACT_COLUMNS, "created_at", "updated_at")
        values = {"contact_id": contact_id, "apartment_id": apartment_id,
                  "created_at": created_at, "updated_at": created_at, **fields}
        self._s.execute(
            f"INSERT INTO household_contact ({', '.join(columns)}) "
            f"VALUES ({', '.join('%s' for _ in columns)})",
            tuple(values[column] for column in columns))

    def update_contact(self, contact_id: str, *, updated_at: str, **fields: Any) -> None:
        assignments = ", ".join(f"{column} = %s" for column in CONTACT_COLUMNS)
        self._s.execute(
            f"UPDATE household_contact SET {assignments}, updated_at = %s "
            "WHERE contact_id = %s",
            (*(fields[column] for column in CONTACT_COLUMNS), updated_at, contact_id))

    def delete_contact(self, contact_id: str) -> None:
        self._s.execute("DELETE FROM household_contact WHERE contact_id = %s", (contact_id,))

    # -- documents -------------------------------------------------------------

    def documents(self, apartment_id: str) -> list[dict[str, Any]]:
        """Newest first, without the file contents."""
        return self._s.fetch_all(
            f"SELECT {_DOCUMENT_METADATA} FROM household_document "
            "WHERE apartment_id = %s ORDER BY row_seq DESC", (apartment_id,))

    def document(self, doc_id: str) -> dict[str, Any] | None:
        """One document's metadata, without its contents."""
        return self._s.fetch_one(
            f"SELECT {_DOCUMENT_METADATA} FROM household_document WHERE doc_id = %s",
            (doc_id,))

    def document_content(self, doc_id: str) -> bytes | None:
        row = self._s.fetch_one(
            "SELECT content FROM household_document WHERE doc_id = %s", (doc_id,))
        return bytes(row["content"]) if row else None

    def insert_document(self, *, doc_id: str, apartment_id: str, title: str, category: str,
                        filename: str, content_type: str, size_bytes: int, sha256: str,
                        content: bytes, uploaded_by_resident_id: str | None,
                        notes: str | None, uploaded_at: str) -> None:
        self._s.execute(
            """INSERT INTO household_document (doc_id, apartment_id, title, category,
                   filename, content_type, size_bytes, sha256, content,
                   uploaded_by_resident_id, notes, uploaded_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (doc_id, apartment_id, title, category, filename, content_type, size_bytes,
             sha256, content, uploaded_by_resident_id, notes, uploaded_at))

    def delete_document(self, doc_id: str) -> None:
        self._s.execute("DELETE FROM household_document WHERE doc_id = %s", (doc_id,))

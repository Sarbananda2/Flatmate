"""Residents."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .base import Repository

#: The resident's dossier: work, medical safety, tenancy terms, vehicle and
#: building access, and identity verification. Each is optional or has a
#: database default, and none of it is read by the ledger.
DOSSIER_COLUMNS = (
    "profession", "employer", "work_mode", "blood_group",
    "secondary_emergency_name", "secondary_emergency_phone", "secondary_emergency_relation",
    "notice_period_days", "lock_in_until", "is_lease_signatory", "keys_issued_notes",
    "vehicle_type", "vehicle_reg_no", "parking_slot", "society_rfid_tag",
    "id_document_type", "id_document_number", "home_city", "home_state",
    "police_verification_status", "society_noc_status",
)


def _dossier_columns(dossier: Mapping[str, Any]) -> list[str]:
    """The dossier columns given, in table order. Anything else is refused, so
    no caller-supplied name ever reaches the SQL."""
    unknown = set(dossier) - set(DOSSIER_COLUMNS)
    if unknown:
        raise ValueError(f"Not resident dossier columns: {sorted(unknown)}")
    return [column for column in DOSSIER_COLUMNS if column in dossier]


class ResidentsRepository(Repository):
    def get(self, resident_id: str) -> dict[str, Any] | None:
        return self._s.fetch_one("SELECT * FROM resident WHERE resident_id = %s",
                                 (resident_id,))

    def list_for_apartment(self, apartment_id: str, *,
                           active_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM resident WHERE apartment_id = %s"
        if active_only:
            sql += " AND is_active = 1"
        sql += " ORDER BY display_name, resident_id"
        return self._s.fetch_all(sql, (apartment_id,))

    def ids_for_apartment(self, apartment_id: str, *, active_only: bool = False) -> list[str]:
        """Resident IDs, in no particular order."""
        sql = "SELECT resident_id FROM resident WHERE apartment_id = %s"
        if active_only:
            sql += " AND is_active = 1"
        return [row["resident_id"] for row in self._s.fetch_all(sql, (apartment_id,))]

    def exists(self, resident_id: str) -> bool:
        return self._s.fetch_one("SELECT 1 AS present FROM resident WHERE resident_id=%s",
                                 (resident_id,)) is not None

    def insert(self, *, resident_id: str, apartment_id: str, display_name: str,
               active_from: str | None, active_until: str | None, is_active: int,
               email: str | None, phone: str | None, notes: str | None,
               upi_id: str | None, created_at: str, updated_at: str,
               emergency_contact_name: str | None = None,
               emergency_contact_phone: str | None = None,
               emergency_contact_relation: str | None = None,
               dossier: Mapping[str, Any] | None = None) -> None:
        """A dossier column left out of ``dossier`` takes its database default."""
        dossier = dossier or {}
        extra = _dossier_columns(dossier)
        columns = ", ".join(["resident_id", "apartment_id", "display_name", "active_from",
                             "active_until", "is_active", "email", "phone", "notes",
                             "upi_id", "emergency_contact_name", "emergency_contact_phone",
                             "emergency_contact_relation", "created_at", "updated_at",
                             *extra])
        values = (resident_id, apartment_id, display_name, active_from, active_until,
                  is_active, email, phone, notes, upi_id, emergency_contact_name,
                  emergency_contact_phone, emergency_contact_relation, created_at, updated_at,
                  *(dossier[column] for column in extra))
        self._s.execute(
            f"INSERT INTO resident ({columns}) VALUES ({', '.join(['%s'] * len(values))})",
            values)

    def update(self, resident_id: str, *, display_name: str, active_from: str | None,
               active_until: str | None, is_active: int, email: str | None,
               phone: str | None, notes: str | None, upi_id: str | None,
               emergency_contact_name: str | None,
               emergency_contact_phone: str | None,
               emergency_contact_relation: str | None,
               updated_at: str,
               dossier: Mapping[str, Any] | None = None) -> None:
        """Every column is written as given; ``dossier`` carries the dossier
        columns to write with them."""
        dossier = dossier or {}
        extra = _dossier_columns(dossier)
        sets = "".join(f", {column}=%s" for column in extra)
        self._s.execute(
            f"""UPDATE resident SET display_name=%s, active_from=%s, active_until=%s,
                    is_active=%s, email=%s, phone=%s, notes=%s, upi_id=%s,
                    emergency_contact_name=%s, emergency_contact_phone=%s,
                    emergency_contact_relation=%s, updated_at=%s{sets}
                WHERE resident_id=%s""",
            (display_name, active_from, active_until, is_active, email, phone, notes,
             upi_id, emergency_contact_name, emergency_contact_phone,
             emergency_contact_relation, updated_at,
             *(dossier[column] for column in extra), resident_id))

"""The master lease, rent cycles, and rent receipts."""
from __future__ import annotations

from typing import Any

from .base import Repository

#: Columns of ``rent_lease_config`` a service may set, besides its keys and
#: timestamps. Column names reach the SQL text, so they are checked against this.
_LEASE_COLUMNS = frozenset({
    "landlord_name", "landlord_first_name", "landlord_middle_name", "landlord_last_name",
    "landlord_phone", "landlord_email", "landlord_pan",
    "landlord_bank_account", "landlord_bank_ifsc", "landlord_upi_id",
    "payout_beneficiary_name", "monthly_rent_paise", "due_day_of_month",
    "requires_landlord_receipt", "expense_definition_id", "lease_start_date", "lease_end_date", "duration_months",
    "notice_period_days", "lock_in_months", "security_deposit_paise", "escalation_type",
    "escalation_percent", "renewal_rent_paise", "lease_status", "notice_served_date",
    "target_handover_date", "rent_split_mode",
})


def _lease_columns(fields: dict[str, Any]) -> list[str]:
    unknown = sorted(set(fields) - _LEASE_COLUMNS)
    if unknown:
        raise ValueError(f"Not rent_lease_config columns: {', '.join(unknown)}")
    return list(fields)


class RentRepository(Repository):
    # ------------------------------------------------------------ the lease --

    def active_lease(self, apartment_id: str) -> dict[str, Any] | None:
        return self._s.fetch_one(
            "SELECT * FROM rent_lease_config WHERE apartment_id=%s AND is_active=1",
            (apartment_id,))

    def insert_lease(self, *, config_id: str, apartment_id: str, created_at: str,
                     updated_at: str, **fields: Any) -> None:
        """A new, active lease. ``fields`` are the lease's own columns."""
        columns = ["config_id", "apartment_id", *_lease_columns(fields), "created_at",
                   "updated_at", "is_active"]
        self._s.execute(
            f"INSERT INTO rent_lease_config ({', '.join(columns)}) "
            f"VALUES ({', '.join('%s' for _ in columns)})",
            (config_id, apartment_id, *fields.values(), created_at, updated_at, 1))

    def update_lease(self, config_id: str, *, updated_at: str, **fields: Any) -> int:
        """Set the given lease columns, in the order given."""
        columns = _lease_columns(fields)
        return self._s.execute(
            f"UPDATE rent_lease_config SET {', '.join(f'{c}=%s' for c in columns)}, "
            "updated_at=%s WHERE config_id=%s",
            (*fields.values(), updated_at, config_id))

    def completed_term_count(self, apartment_id: str) -> int:
        return int(self._s.scalar(
            "SELECT COUNT(*) FROM rent_lease_term WHERE apartment_id=%s",
            (apartment_id,), 0))

    def insert_lease_term(self, *, term_id: str, apartment_id: str, term_number: int,
                          start_date: str, end_date: str, monthly_rent_paise: int,
                          escalation_applied_percent: float, archived_at: str,
                          created_at: str) -> None:
        self._s.execute(
            """INSERT INTO rent_lease_term
               (term_id, apartment_id, term_number, start_date, end_date, monthly_rent_paise,
                escalation_applied_percent, archived_at, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (term_id, apartment_id, term_number, start_date, end_date, monthly_rent_paise,
             escalation_applied_percent, archived_at, created_at))

    def lease_terms(self, apartment_id: str) -> list[dict[str, Any]]:
        """Completed terms, oldest first."""
        return self._s.fetch_all(
            "SELECT * FROM rent_lease_term WHERE apartment_id=%s ORDER BY term_number",
            (apartment_id,))

    def backdate_lease_terms(self, apartment_id: str, *, archived_at: str,
                             created_at: str) -> int:
        """Set the timestamps on an apartment's archived terms.

        Only the sample-data seeder uses this, to date a completed term to the
        day it was actually renewed.
        """
        return self._s.execute(
            "UPDATE rent_lease_term SET archived_at=%s, created_at=%s WHERE apartment_id=%s",
            (archived_at, created_at, apartment_id))

    # ------------------------------------------- the rent expense definition --

    def definition_name(self, definition_id: str | None) -> str | None:
        return self._s.scalar(
            "SELECT name FROM expense_definition WHERE expense_definition_id=%s",
            (definition_id,))

    def definition_recurrence(self, definition_id: str | None) -> str | None:
        return self._s.scalar(
            "SELECT recurrence FROM expense_definition WHERE expense_definition_id=%s",
            (definition_id,))

    def definition_owner(self, definition_id: str) -> dict[str, Any] | None:
        """``apartment_id`` and ``is_active`` of a definition."""
        return self._s.fetch_one(
            "SELECT apartment_id, is_active FROM expense_definition "
            "WHERE expense_definition_id=%s", (definition_id,))

    def definition_id_taken(self, definition_id: str) -> bool:
        return self._s.fetch_one(
            "SELECT 1 AS present FROM expense_definition WHERE expense_definition_id=%s",
            (definition_id,)) is not None

    def find_rent_definition_id(self, apartment_id: str, category: str) -> str | None:
        """The active definition in `category` that is called, or has the ID, 'rent'."""
        return self._s.scalar(
            """SELECT expense_definition_id FROM expense_definition
                WHERE apartment_id=%s AND is_active=1 AND lower(category)=lower(%s)
                  AND (lower(expense_definition_id)='rent' OR lower(trim(name))='rent')
                ORDER BY sort_order, expense_definition_id LIMIT 1""",
            (apartment_id, category))

    # ------------------------------------------------------------ the cycle --

    def cycle_for_month(self, apartment_id: str,
                        settlement_month: str) -> dict[str, Any] | None:
        return self._s.fetch_one(
            "SELECT * FROM rent_cycle WHERE apartment_id=%s AND settlement_month=%s",
            (apartment_id, settlement_month))

    def disbursements_for_apartment(self, apartment_id: str) -> list[dict[str, Any]]:
        """Every payment made to the landlord, in the order paid, with the name of
        the resident who advanced it, if one did."""
        return self._s.fetch_all(
            """SELECT d.*, d.row_seq AS seq, r.display_name AS funding_resident_name
                 FROM rent_disbursement d
                 LEFT JOIN resident r ON r.resident_id = d.funding_resident_id
                WHERE d.apartment_id = %s
                ORDER BY d.disbursed_at, d.row_seq""",
            (apartment_id,))

    def disbursements_for_cycle(self, cycle_id: str) -> list[dict[str, Any]]:
        """The tranches a month's rent has been paid to the landlord in, oldest first."""
        return self._s.fetch_all(
            """SELECT d.*, d.row_seq AS seq, r.display_name AS funding_resident_name
                 FROM rent_disbursement d
                 LEFT JOIN resident r ON r.resident_id = d.funding_resident_id
                WHERE d.cycle_id = %s
                ORDER BY d.disbursed_at, d.row_seq""",
            (cycle_id,))

    def insert_disbursement(self, *, disbursement_id: str, cycle_id: str, apartment_id: str,
                            settlement_month: str, amount_paise: int, disbursed_at: str,
                            disbursement_reference: str, disbursed_by: str,
                            funding_resident_id: str | None, funding_payment_id: str | None,
                            note: str | None, created_at: str) -> None:
        self._s.execute(
            """INSERT INTO rent_disbursement
               (disbursement_id, cycle_id, apartment_id, settlement_month, amount_paise,
                disbursed_at, disbursement_reference, disbursed_by, funding_resident_id,
                funding_payment_id, note, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (disbursement_id, cycle_id, apartment_id, settlement_month, amount_paise,
             disbursed_at, disbursement_reference, disbursed_by, funding_resident_id,
             funding_payment_id, note, created_at))

    def insert_pending_cycle(self, *, cycle_id: str, apartment_id: str,
                             settlement_month: str, expense_id: str, total_rent_paise: int,
                             participant_count: int, created_at: str) -> None:
        self._s.execute(
            """INSERT INTO rent_cycle
               (cycle_id, apartment_id, settlement_month, expense_id, total_rent_paise,
                participant_count, disbursement_status, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,'pending',%s)""",
            (cycle_id, apartment_id, settlement_month, expense_id, total_rent_paise,
             participant_count, created_at))

    def update_cycle_disbursement_state(self, cycle_id: str, *, status: str,
                                        last_disbursed_at: str, last_reference: str,
                                        total_rent_paise: int, participant_count: int) -> int:
        """The month's state after a tranche: its status, the latest tranche's date
        and reference, and the rent and headcount the tranches were paid against."""
        return self._s.execute(
            """UPDATE rent_cycle SET disbursement_status=%s, disbursed_at=%s,
                      disbursement_reference=%s, total_rent_paise=%s, participant_count=%s
                WHERE cycle_id=%s""",
            (status, last_disbursed_at, last_reference, total_rent_paise, participant_count,
             cycle_id))

    def statement_for_month(self, apartment_id: str,
                            settlement_month: str) -> dict[str, Any] | None:
        """``statement_id``, ``settlement_month`` and ``status`` of the month's statement."""
        return self._s.fetch_one(
            "SELECT statement_id, settlement_month, status FROM monthly_statement "
            "WHERE apartment_id=%s AND settlement_month=%s", (apartment_id, settlement_month))

    def statement_brief(self, statement_id: str) -> dict[str, Any] | None:
        """``statement_id``, ``settlement_month`` and ``status`` of one statement."""
        return self._s.fetch_one(
            "SELECT statement_id, settlement_month, status FROM monthly_statement "
            "WHERE statement_id=%s", (statement_id,))

    def resident_names(self, apartment_id: str) -> list[dict[str, Any]]:
        """``resident_id`` and ``display_name`` of every resident, in no particular order."""
        return self._s.fetch_all(
            "SELECT resident_id, display_name FROM resident WHERE apartment_id=%s",
            (apartment_id,))

    # -------------------------------------------- the rent expense's charges --

    def charges_for_expense(self, expense_id: str) -> list[dict[str, Any]]:
        """Each resident's rent charge for one expense, with what has been paid off."""
        return self._s.fetch_all(
            """SELECT a.resident_id, r.display_name,
                      SUM(a.amount_paise)::BIGINT AS share_paise,
                      COALESCE(SUM((SELECT SUM(pa.amount_paise) FROM payment_application pa
                                     WHERE pa.allocation_id = a.allocation_id)), 0)::BIGINT
                          AS settled_paise,
                      (SELECT MAX(p.payment_date) FROM payment_application pa
                         JOIN payment p ON p.payment_id = pa.payment_id
                         JOIN allocation a2 ON a2.allocation_id = pa.allocation_id
                        WHERE a2.expense_id = a.expense_id
                          AND a2.resident_id = a.resident_id) AS last_payment_date
                 FROM allocation a JOIN resident r ON r.resident_id = a.resident_id
                WHERE a.expense_id = %s
                GROUP BY a.expense_id, a.resident_id, r.display_name
                ORDER BY r.display_name, a.resident_id""", (expense_id,))

    def payments_for_rent_expense(self, expense_id: str) -> list[dict[str, Any]]:
        """Each payment that settled part of a resident's rent charge, oldest first,
        with what it put towards the charge -- not the whole payment, whose excess
        may have gone to other charges. A reversed payment's applications are
        removed with it, so these always add up to the charge's settled amount."""
        return self._s.fetch_all(
            """SELECT a.resident_id, p.payment_id, p.payment_date,
                      SUM(pa.amount_paise)::BIGINT AS amount_paise,
                      p.payment_mode, p.reference, p.note, p.created_at
                 FROM payment_application pa
                 JOIN payment p ON p.payment_id = pa.payment_id
                 JOIN allocation a ON a.allocation_id = pa.allocation_id
                WHERE a.expense_id = %s AND p.status = 'active'
                GROUP BY a.resident_id, p.payment_id, p.payment_date, p.payment_mode,
                         p.reference, p.note, p.created_at
                ORDER BY p.payment_date, p.created_at, p.payment_id""", (expense_id,))

    def resident_charges_for_expense(self, expense_id: str,
                                     resident_id: str) -> list[dict[str, Any]]:
        """One resident's allocations on an expense, oldest first, with what is still owed."""
        return self._s.fetch_all(
            """SELECT a.allocation_id,
                      (a.amount_paise - COALESCE((SELECT SUM(pa.amount_paise)
                                                    FROM payment_application pa
                                                   WHERE pa.allocation_id = a.allocation_id), 0)
                      )::BIGINT AS outstanding_paise
                 FROM allocation a
                WHERE a.expense_id = %s AND a.resident_id = %s ORDER BY a.row_seq""",
            (expense_id, resident_id))

    def expenses_for_definition_month(self, apartment_id: str, definition_id: str,
                                      settlement_month: str) -> list[dict[str, Any]]:
        """Expenses recorded under a definition for a settlement month, oldest first."""
        return self._s.fetch_all(
            """SELECT * FROM expense_instance
                WHERE apartment_id=%s AND expense_definition_id=%s AND settlement_period=%s
                ORDER BY row_seq""", (apartment_id, definition_id, settlement_month))

    def linked_advance_total_paise(self, expense_id: str) -> int:
        """Active provider advances linked to an expense."""
        return int(self._s.scalar(
            """SELECT COALESCE(SUM(amount_paise), 0)::BIGINT FROM payment
                WHERE expense_id=%s AND payment_kind='provider_advance'
                  AND status='active'""", (expense_id,), 0))

    def set_expense_payer(self, expense_id: str, *, payer_resident_id: str | None,
                          updated_at: str) -> int:
        return self._s.execute(
            "UPDATE expense_instance SET payer_resident_id=%s, updated_at=%s "
            "WHERE expense_id=%s", (payer_resident_id, updated_at, expense_id))

    # ------------------------------------------------------------- receipts --

    def insert_receipt(self, *, receipt_id: str, cycle_id: str, resident_id: str,
                       settlement_month: str, amount_paise: int, landlord_name: str,
                       landlord_pan: str, verification_hash: str, created_at: str) -> None:
        self._s.execute(
            """INSERT INTO rent_receipt
               (receipt_id, cycle_id, resident_id, settlement_month, amount_paise,
                landlord_name, landlord_pan, verification_hash, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (receipt_id, cycle_id, resident_id, settlement_month, amount_paise,
             landlord_name, landlord_pan, verification_hash, created_at))

    def receipts_for_cycle(self, cycle_id: str) -> list[dict[str, Any]]:
        """In resident-ID order."""
        return self._s.fetch_all(
            "SELECT * FROM rent_receipt WHERE cycle_id=%s ORDER BY resident_id", (cycle_id,))

    def landlord_receipt_summary(self, cycle_id: str) -> dict[str, Any] | None:
        """The attached file's description, without its bytes."""
        return self._s.fetch_one(
            "SELECT filename, content_type, size_bytes, sha256, uploaded_at "
            "FROM rent_landlord_receipt WHERE cycle_id=%s", (cycle_id,))

    def landlord_receipt_identity(self, cycle_id: str) -> dict[str, Any] | None:
        """``filename``, ``sha256`` and ``uploaded_at`` of the attached file."""
        return self._s.fetch_one(
            "SELECT filename, sha256, uploaded_at FROM rent_landlord_receipt "
            "WHERE cycle_id=%s", (cycle_id,))

    def delete_landlord_receipt(self, cycle_id: str) -> int:
        return self._s.execute("DELETE FROM rent_landlord_receipt WHERE cycle_id=%s",
                               (cycle_id,))

    def insert_landlord_receipt(self, *, cycle_id: str, filename: str, content_type: str,
                                size_bytes: int, sha256: str, content: bytes,
                                uploaded_at: str) -> None:
        self._s.execute(
            """INSERT INTO rent_landlord_receipt
               (cycle_id, filename, content_type, size_bytes, sha256, content, uploaded_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (cycle_id, filename, content_type, size_bytes, sha256, bytes(content),
             uploaded_at))

    def landlord_receipt_for_month(self, apartment_id: str,
                                   settlement_month: str) -> dict[str, Any] | None:
        """The attached file with its bytes (``content`` is ``bytes``)."""
        row = self._s.fetch_one(
            """SELECT lr.* FROM rent_landlord_receipt lr
                 JOIN rent_cycle c ON c.cycle_id = lr.cycle_id
                WHERE c.apartment_id=%s AND c.settlement_month=%s""",
            (apartment_id, settlement_month))
        if row is not None and row.get("content") is not None:
            row["content"] = bytes(row["content"])
        return row

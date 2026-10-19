"""The resident dossier: work, medical safety, the terms they moved in on,
their vehicle and building access, and identity verification.

None of it is accounting. These tests pin what is stored, what is refused --
by the service and by the database itself -- and that an identity document's
number is never written to the audit log in full.
"""
from __future__ import annotations

import pytest

from aem.errors import ValidationError
from aem.services import residents

from .conftest import APARTMENT
from .support import DatabaseRefusal, connect

ALWAYS_SET = {"police_verification_status", "society_noc_status", "is_lease_signatory"}


def _rows(db, sql, params=()):
    with connect(db, read_only=True) as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def test_a_new_resident_starts_with_an_empty_dossier_and_pending_checks(apartment):
    alice = residents.read_resident(apartment, "alice")
    assert (alice["police_verification_status"], alice["society_noc_status"],
            alice["is_lease_signatory"]) == ("pending", "pending", 0)
    assert {field: alice[field] for field in residents.DOSSIER_FIELDS
            if field not in ALWAYS_SET} == dict.fromkeys(
        set(residents.DOSSIER_FIELDS) - ALWAYS_SET)


def test_a_resident_is_created_with_their_dossier_cleaned(apartment):
    dora = residents.create_resident(
        apartment, apartment_id=APARTMENT, resident_id="dora", display_name="Dora",
        profession="  Data analyst ", work_mode="hybrid", blood_group="O+",
        vehicle_type="four_wheeler", vehicle_reg_no="dl 3c ab 1234",
        is_lease_signatory=True, notice_period_days=30, lock_in_until="2026-12-31",
        police_verification_status="submitted", id_document_type="aadhaar")
    assert (dora["profession"], dora["work_mode"], dora["blood_group"]) == (
        "Data analyst", "hybrid", "O+")
    assert dora["vehicle_reg_no"] == "DL 3C AB 1234"
    assert (dora["is_lease_signatory"], dora["notice_period_days"], dora["lock_in_until"]) == (
        1, 30, "2026-12-31")
    # Left out, a status starts where every new resident's does.
    assert (dora["police_verification_status"], dora["society_noc_status"]) == (
        "submitted", "pending")


def test_each_card_changes_only_its_own_fields(apartment):
    residents.update_resident(apartment, "alice", profession="Designer", employer="Acme",
                              blood_group="B+", phone="+91 98765 43210")
    after = residents.update_resident(apartment, "alice", parking_slot="B-12 (Stilt)",
                                      society_rfid_tag="#RF-8821")
    assert (after["profession"], after["employer"], after["blood_group"], after["phone"]) == (
        "Designer", "Acme", "B+", "+91 98765 43210")
    assert (after["parking_slot"], after["society_rfid_tag"]) == ("B-12 (Stilt)", "#RF-8821")

    # An emptied field is stored as nothing, not as an empty string.
    cleared = residents.update_resident(apartment, "alice", employer="  ", work_mode=None,
                                        is_lease_signatory=False)
    assert (cleared["employer"], cleared["work_mode"], cleared["is_lease_signatory"]) == (
        None, None, 0)
    assert cleared["profession"] == "Designer"


@pytest.mark.parametrize("field, value", [
    ("blood_group", "Z+"), ("work_mode", "remote"), ("vehicle_type", "bicycle"),
    ("id_document_type", "pan"), ("police_verification_status", "done"),
    ("society_noc_status", "rejected"), ("lock_in_until", "2026-02-30"),
    ("notice_period_days", -1), ("notice_period_days", "30"), ("notice_period_days", True),
    # The two verification statuses always have a value.
    ("police_verification_status", None), ("society_noc_status", ""),
    ("parking_slot", "x" * 101), ("id_document_number", "9" * 101),
])
def test_an_invalid_dossier_value_is_refused_and_nothing_changes(apartment, field, value):
    before = residents.read_resident(apartment, "alice")
    with pytest.raises(ValidationError):
        residents.update_resident(apartment, "alice", **{field: value})
    assert residents.read_resident(apartment, "alice") == before


@pytest.mark.parametrize("column, value", [
    ("blood_group", "Z+"), ("work_mode", "remote"), ("vehicle_type", "bus"),
    ("id_document_type", "pan"), ("police_verification_status", "done"),
    ("police_verification_status", None), ("society_noc_status", "rejected"),
    ("is_lease_signatory", 2), ("notice_period_days", -5),
])
def test_the_database_itself_refuses_values_outside_each_set(apartment, column, value):
    with connect(apartment) as conn, pytest.raises(DatabaseRefusal):
        conn.execute(f"UPDATE resident SET {column} = %s WHERE resident_id = 'alice'", (value,))


def test_an_identity_number_is_never_written_to_the_audit_log_in_full(apartment):
    residents.update_resident(apartment, "alice", id_document_type="aadhaar",
                              id_document_number="1234 5678 4821")
    residents.update_resident(apartment, "alice", id_document_number="9876 5432 1099")
    assert residents.read_resident(apartment, "alice")["id_document_number"] == "9876 5432 1099"

    # Each change is on record, by its last four characters only.
    rows = _rows(apartment, """SELECT old_value, new_value FROM audit_log
                                WHERE entity_type = 'resident' AND entity_id = 'alice'
                                  AND field_name = 'id_document_number' ORDER BY row_seq""")
    assert [(row["old_value"], row["new_value"]) for row in rows] == [
        (None, "•••• 4821"), ("•••• 4821", "•••• 1099")]
    everything = " ".join(row["line"] for row in _rows(
        apartment, "SELECT a::text AS line FROM audit_log a"))
    assert "5678" not in everything and "5432" not in everything
    # The document's type is not sensitive, and is audited as any other field.
    assert _rows(apartment, """SELECT new_value FROM audit_log
                                WHERE entity_id = 'alice' AND field_name = 'id_document_type'""") == [
        {"new_value": "aadhaar"}]


def test_the_dossier_over_http(client):
    client.post("/api/apartments", json={"apartment_id": APARTMENT, "name": "Flat"})
    created = client.post("/api/residents", json={
        "resident_id": "bob", "display_name": "Bob", "blood_group": "AB-",
        "vehicle_type": "two_wheeler", "vehicle_reg_no": "ka 05 mn 4321"})
    assert created.status_code == 201, created.text
    assert (created.json()["blood_group"], created.json()["vehicle_reg_no"]) == (
        "AB-", "KA 05 MN 4321")

    # A scoped save sends only its own fields; the rest are left as they were.
    updated = client.patch("/api/residents/bob", json={
        "police_verification_status": "verified", "society_noc_status": "approved",
        "home_city": "Kolkata", "home_state": "West Bengal"}).json()
    assert (updated["police_verification_status"], updated["society_noc_status"],
            updated["home_city"], updated["blood_group"]) == (
        "verified", "approved", "Kolkata", "AB-")
    assert client.get("/api/residents/bob").json()["resident"]["home_state"] == "West Bengal"

    for body in ({"blood_group": "O"}, {"work_mode": "remote"}, {"notice_period_days": -1},
                 {"lock_in_until": "31-12-2026"}, {"society_noc_status": "rejected"}):
        assert client.patch("/api/residents/bob", json=body).status_code == 422, body

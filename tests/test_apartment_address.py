"""The apartment's postal address, part by part (migration 0014).

The parts are optional and edited like any other apartment field; a PIN code is
six digits not starting with 0, checked by the API, the service and the
database alike. ``premises_address`` puts them on one line for HRA receipts.
"""
from __future__ import annotations

import psycopg
import pytest

from aem.errors import ValidationError
from aem.services import apartments
from tests.conftest import APARTMENT
from tests.support import connect

CAPETOWN = {
    "flat_number": "1805", "tower_block": "CG6", "society_name": "Supertech Capetown",
    "locality_street": "Sector 74", "city": "Noida", "state": "Uttar Pradesh",
    "pincode": "201301",
}


def _audited_fields(db) -> set[str]:
    with connect(db, read_only=True) as conn:
        rows = conn.execute(
            "SELECT field_name FROM audit_log WHERE entity_type='apartment' AND action='update'"
        ).fetchall()
    return {row["field_name"] for row in rows}


def test_a_new_apartment_has_no_address_and_prints_its_name(db):
    created = apartments.create_apartment(db, apartment_id=APARTMENT, name="Flat 402")
    assert all(created[field] is None for field in CAPETOWN)
    assert created["premises_address"] == "Flat 402"


def test_the_address_parts_are_saved_and_audited(db):
    apartments.create_apartment(db, apartment_id=APARTMENT, name="Flat 1805")
    saved = apartments.update_apartment(db, APARTMENT, **CAPETOWN)
    assert {field: saved[field] for field in CAPETOWN} == CAPETOWN
    assert saved["name"] == "Flat 1805", "the name is left alone"
    assert saved["premises_address"] == (
        "1805, CG6, Supertech Capetown, Sector 74, Noida, Uttar Pradesh - 201301")
    assert set(CAPETOWN) <= _audited_fields(db)


def test_a_part_left_out_is_kept_and_one_sent_empty_is_cleared(db):
    apartments.create_apartment(db, apartment_id=APARTMENT, name="Flat 1805")
    apartments.update_apartment(db, APARTMENT, **CAPETOWN)
    saved = apartments.update_apartment(db, APARTMENT, tower_block=None, locality_street="  ")
    assert saved["tower_block"] is None and saved["locality_street"] is None
    assert saved["society_name"] == "Supertech Capetown"
    assert saved["premises_address"] == (
        "1805, Supertech Capetown, Noida, Uttar Pradesh - 201301")


def test_premises_address_skips_missing_parts():
    base = {"name": "Flat 9", **{field: None for field in CAPETOWN}}
    assert apartments.premises_address({**base, "pincode": "560001"}) == "560001"
    assert apartments.premises_address({**base, "city": "Pune", "state": "Maharashtra"}) == (
        "Pune, Maharashtra")


@pytest.mark.parametrize("pincode", ["012345", "20130", "2013011", "20130A", "201 301"])
def test_an_invalid_pin_code_is_refused_and_nothing_changes(db, pincode):
    apartments.create_apartment(db, apartment_id=APARTMENT, name="Flat 1805")
    with pytest.raises(ValidationError) as refused:
        apartments.update_apartment(db, APARTMENT, city="Noida", pincode=pincode)
    assert refused.value.code == "invalid_pincode"
    assert apartments.read_apartment(db, APARTMENT)["city"] is None


def test_an_unknown_field_is_refused(db):
    apartments.create_apartment(db, apartment_id=APARTMENT, name="Flat 1805")
    with pytest.raises(ValidationError):
        apartments.update_apartment(db, APARTMENT, landmark="Near the metro")


def test_the_database_itself_refuses_a_bad_pin_code(db):
    apartments.create_apartment(db, apartment_id=APARTMENT, name="Flat 1805")
    with connect(db) as conn, pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("UPDATE apartment SET pincode='012345' WHERE apartment_id=%s",
                     (APARTMENT,))


def test_the_api_edits_the_address_and_reports_the_premises(client):
    client.post("/api/apartments", json={"apartment_id": APARTMENT, "name": "Flat"})
    response = client.patch(f"/api/apartments/{APARTMENT}", json=CAPETOWN)
    assert response.status_code == 200
    body = response.json()
    assert {field: body[field] for field in CAPETOWN} == CAPETOWN
    assert body["premises_address"].endswith("Uttar Pradesh - 201301")
    listed = client.get("/api/apartments").json()["items"][0]
    assert listed["pincode"] == "201301" and listed["premises_address"] == body["premises_address"]
    assert client.get(f"/api/apartments/{APARTMENT}").json()["city"] == "Noida"

    cleared = client.patch(f"/api/apartments/{APARTMENT}", json={"pincode": None})
    assert cleared.status_code == 200 and cleared.json()["pincode"] is None


@pytest.mark.parametrize("pincode", ["012345", "20130", ""])
def test_the_api_refuses_an_invalid_pin_code(client, pincode):
    client.post("/api/apartments", json={"apartment_id": APARTMENT, "name": "Flat"})
    response = client.patch(f"/api/apartments/{APARTMENT}", json={"pincode": pincode})
    assert response.status_code == 422
    assert client.get(f"/api/apartments/{APARTMENT}").json()["pincode"] is None


def test_migration_0014_fills_in_the_household_flat_only(database_factory):
    db = database_factory(migrated=False)
    db.migrate(target_version=13)
    with connect(db, as_owner=True) as conn:
        for apartment_id, name in (("flat-1805-cg6-supertech-capetown",
                                    "Flat 1805, CG6, Supertech Capetown"),
                                   ("other", "Flat 12, Supertech Capetown")):
            conn.execute(
                "INSERT INTO apartment (apartment_id, name, created_at) VALUES (%s,%s,%s)",
                (apartment_id, name, "2026-10-11T20:23:03+05:30"))
    db.migrate()
    household = apartments.read_apartment(db, "flat-1805-cg6-supertech-capetown")
    assert {field: household[field] for field in CAPETOWN} == CAPETOWN
    other = apartments.read_apartment(db, "other")
    assert all(other[field] is None for field in CAPETOWN)

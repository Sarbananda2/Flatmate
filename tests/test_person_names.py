"""People's names in parts: first, middle and last (migration 0017).

A contact's name and the lease's landlord are collected as a first name, an
optional middle name and an optional last name. These tests pin that the parts
are what is stored, that the whole name is composed from them on every write so
everything reading one name keeps working, that a client sending only the whole
name has it split the same way the migration split the names already on file,
and that the owner contact and the lease keep the same parts.
"""
from __future__ import annotations

import pytest

from aem.errors import ValidationError
from aem.services import apartments, names, rent
from aem.services import property as household

from .conftest import APARTMENT
from .support import connect

RENT = 2_850_000


def _parts(record, prefix=""):
    return tuple(record[f"{prefix}{part}_name"] for part in ("first", "middle", "last"))


def _audit(db, entity_type):
    """The entity type's audit entries, oldest first."""
    with connect(db, read_only=True) as conn:
        return [dict(row) for row in conn.execute(
            "SELECT action, field_name, new_value FROM audit_log WHERE entity_type=%s "
            "ORDER BY row_seq", (entity_type,)).fetchall()]


# ------------------------------------------------------------ the helpers --

@pytest.mark.parametrize("whole, parts", [
    ("Rajesh Kumar Sharma", ("Rajesh", "Kumar", "Sharma")),
    ("Kumar Yadav", ("Kumar", None, "Yadav")),
    ("Mohan", ("Mohan", None, None)),
    ("  Venkata  Sai\tKrishna   Rao ", ("Venkata", "Sai Krishna", "Rao")),
    ("", ("", None, None)),
])
def test_a_whole_name_splits_into_first_middle_and_last(whole, parts):
    assert names.split_full_name(whole) == parts
    # Composing the parts gives the name back, its spacing tidied.
    assert names.compose_full_name(*parts) == " ".join(whole.split())


def test_composing_skips_the_parts_that_are_not_there():
    assert names.compose_full_name(" Rajesh ", "  ", "Sharma") == "Rajesh Sharma"
    assert names.compose_full_name("Mohan", None, None) == "Mohan"


# -------------------------------------------------------------- contacts --

def test_a_contact_is_named_in_parts_and_the_whole_is_composed(apartment):
    owner = household.create_contact(apartment, APARTMENT, {
        "role": "owner", "first_name": " Rajesh ", "middle_name": "Kumar",
        "last_name": "Sharma "})
    assert _parts(owner) == ("Rajesh", "Kumar", "Sharma")
    assert owner["name"] == "Rajesh Kumar Sharma"
    [listed] = household.list_contacts(apartment, APARTMENT)["items"]
    assert (listed["name"], _parts(listed)) == (owner["name"], _parts(owner))


def test_a_mononym_needs_only_a_first_name(apartment):
    caretaker = household.create_contact(apartment, APARTMENT, {
        "role": "caretaker", "first_name": "Mohan", "middle_name": "", "last_name": None})
    assert (caretaker["name"], _parts(caretaker)) == ("Mohan", ("Mohan", None, None))


def test_a_whole_name_sent_alone_is_split_into_parts(apartment):
    owner = household.create_contact(apartment, APARTMENT, {
        "role": "owner", "name": "Rajesh Kumar Sharma"})
    assert _parts(owner) == ("Rajesh", "Kumar", "Sharma")


def test_parts_sent_with_a_whole_name_win(apartment):
    broker = household.create_contact(apartment, APARTMENT, {
        "role": "broker", "name": "Somebody Else", "first_name": "Ravi", "last_name": "Rao"})
    assert (broker["name"], _parts(broker)) == ("Ravi Rao", ("Ravi", None, "Rao"))


@pytest.mark.parametrize("fields, field", [
    ({}, "first_name"),
    ({"first_name": "  "}, "first_name"),
    ({"first_name": None, "last_name": "Sharma"}, "first_name"),
    ({"first_name": "R" * 101}, "first_name"),
    ({"first_name": "Rajesh", "middle_name": "K" * 101}, "middle_name"),
    ({"first_name": "Rajesh", "last_name": "S" * 101}, "last_name"),
    ({"first_name": "R" * 100, "middle_name": "K" * 100, "last_name": "S"}, "name"),
    ({"name": "  "}, "name"),
])
def test_a_contact_without_a_first_name_is_refused(apartment, fields, field):
    with pytest.raises(ValidationError) as caught:
        household.create_contact(apartment, APARTMENT, {"role": "broker", **fields})
    assert caught.value.details["field"] == field
    assert household.list_contacts(apartment, APARTMENT)["total"] == 0


def test_changing_one_part_keeps_the_others_and_recomposes_the_name(apartment):
    owner = household.create_contact(apartment, APARTMENT, {
        "role": "owner", "first_name": "Rajesh", "middle_name": "Kumar", "last_name": "Sharma"})
    changed = household.update_contact(apartment, APARTMENT, owner["contact_id"],
                                       {"middle_name": None})
    assert (changed["name"], _parts(changed)) == ("Rajesh Sharma", ("Rajesh", None, "Sharma"))
    changed = household.update_contact(apartment, APARTMENT, owner["contact_id"],
                                       {"last_name": "Verma"})
    assert changed["name"] == "Rajesh Verma"
    # The audit trail has the part that changed and the whole it made.
    updates = [row for row in _audit(apartment, "household_contact") if row["action"] == "update"]
    latest = updates[-1]
    assert '"last_name": "Verma"' in latest["new_value"]
    assert '"name": "Rajesh Verma"' in latest["new_value"]


def test_an_update_leaving_the_name_out_keeps_it(apartment):
    owner = household.create_contact(apartment, APARTMENT, {
        "role": "owner", "first_name": "Rajesh", "middle_name": "Kumar", "last_name": "Sharma"})
    changed = household.update_contact(apartment, APARTMENT, owner["contact_id"],
                                       {"phone": "9876543210"})
    assert (changed["name"], _parts(changed)) == (owner["name"], _parts(owner))


def test_an_update_sending_a_whole_name_splits_it(apartment):
    owner = household.create_contact(apartment, APARTMENT,
                                     {"role": "owner", "first_name": "Kumar"})
    changed = household.update_contact(apartment, APARTMENT, owner["contact_id"],
                                       {"name": "Kumar S. Yadav"})
    assert _parts(changed) == ("Kumar", "S.", "Yadav")


# ------------------------------------------------------- the lease's landlord --

def test_the_landlord_is_named_in_parts_on_the_lease_and_the_owner(apartment):
    config = rent.save_lease_config(apartment, APARTMENT, {
        "landlord_first_name": "Rajesh", "landlord_middle_name": "Kumar",
        "landlord_last_name": "Sharma", "monthly_rent_paise": RENT})
    assert config["landlord_name"] == "Rajesh Kumar Sharma"
    assert _parts(config, "landlord_") == ("Rajesh", "Kumar", "Sharma")
    [owner] = household.list_contacts(apartment, APARTMENT)["items"]
    assert (owner["name"], _parts(owner)) == ("Rajesh Kumar Sharma", ("Rajesh", "Kumar", "Sharma"))


def test_a_lease_saved_with_a_whole_landlord_name_has_it_split(apartment):
    config = rent.save_lease_config(apartment, APARTMENT, {
        "landlord_name": "Kumar Yadav", "monthly_rent_paise": RENT})
    assert _parts(config, "landlord_") == ("Kumar", None, "Yadav")


def test_a_lease_save_leaving_the_name_out_keeps_it(apartment):
    rent.save_lease_config(apartment, APARTMENT, {
        "landlord_first_name": "Rajesh", "landlord_middle_name": "Kumar",
        "landlord_last_name": "Sharma", "monthly_rent_paise": RENT})
    saved = rent.save_lease_config(apartment, APARTMENT, {"notice_period_days": 45})
    assert (saved["landlord_name"], _parts(saved, "landlord_")) == (
        "Rajesh Kumar Sharma", ("Rajesh", "Kumar", "Sharma"))


def test_changing_one_part_of_the_landlords_name_reaches_the_owner(apartment):
    rent.save_lease_config(apartment, APARTMENT, {
        "landlord_first_name": "Rajesh", "landlord_middle_name": "Kumar",
        "landlord_last_name": "Sharma", "monthly_rent_paise": RENT})
    saved = rent.save_lease_config(apartment, APARTMENT, {"landlord_middle_name": None})
    assert saved["landlord_name"] == "Rajesh Sharma"
    [owner] = household.list_contacts(apartment, APARTMENT)["items"]
    assert (owner["name"], owner["middle_name"]) == ("Rajesh Sharma", None)
    changed = {row["field_name"] for row in _audit(apartment, "rent_lease_config")
               if row["action"] == "update"}
    assert {"landlord_name", "landlord_middle_name"} <= changed


def test_the_owner_contacts_parts_are_the_leases(apartment):
    rent.save_lease_config(apartment, APARTMENT, {
        "landlord_name": "Kumar Yadav", "monthly_rent_paise": RENT})
    [owner] = household.list_contacts(apartment, APARTMENT)["items"]
    household.update_contact(apartment, APARTMENT, owner["contact_id"], {
        "first_name": "Kumar", "middle_name": "S.", "last_name": "Yadav"})
    config = rent.get_lease_config(apartment, APARTMENT)
    assert (config["landlord_name"], _parts(config, "landlord_")) == (
        "Kumar S. Yadav", ("Kumar", "S.", "Yadav"))


@pytest.mark.parametrize("landlord", [{}, {"landlord_first_name": " "}])
def test_a_new_lease_needs_the_landlords_first_name(apartment, landlord):
    with pytest.raises(ValidationError) as caught:
        rent.save_lease_config(apartment, APARTMENT, {**landlord, "monthly_rent_paise": RENT})
    assert caught.value.details["field"] == "landlord_first_name"
    assert rent.get_lease_config(apartment, APARTMENT) is None


def test_a_lease_stored_without_parts_has_them_split_on_its_next_save(apartment):
    rent.save_lease_config(apartment, APARTMENT, {
        "landlord_name": "Rajesh Kumar Sharma", "monthly_rent_paise": RENT})
    with connect(apartment) as conn:
        conn.execute("UPDATE rent_lease_config SET landlord_first_name=NULL, "
                     "landlord_middle_name=NULL, landlord_last_name=NULL")
    saved = rent.save_lease_config(apartment, APARTMENT, {"notice_period_days": 60})
    assert _parts(saved, "landlord_") == ("Rajesh", "Kumar", "Sharma")


# ------------------------------------------------------------------- the API --

def test_the_contact_endpoints_take_and_return_the_parts(client):
    client.post("/api/apartments", json={"apartment_id": APARTMENT, "name": "Flat 402"})
    created = client.post("/api/property/contacts", json={
        "role": "owner", "first_name": "Rajesh", "middle_name": "Kumar", "last_name": "Sharma"})
    assert created.status_code == 201
    body = created.json()
    assert (body["name"], _parts(body)) == ("Rajesh Kumar Sharma", ("Rajesh", "Kumar", "Sharma"))
    # An older client sending the whole name still works.
    legacy = client.post("/api/property/contacts", json={"role": "broker", "name": "Ravi Rao"})
    assert legacy.status_code == 201 and _parts(legacy.json()) == ("Ravi", None, "Rao")
    assert client.post("/api/property/contacts", json={"role": "caretaker"}).status_code == 422
    changed = client.patch(f"/api/property/contacts/{body['contact_id']}",
                           json={"middle_name": None})
    assert changed.status_code == 200 and changed.json()["name"] == "Rajesh Sharma"


def test_an_owner_is_paid_into_their_own_account_or_a_named_beneficiarys(client):
    client.post("/api/apartments", json={"apartment_id": APARTMENT, "name": "Flat 402"})
    created = client.post("/api/property/contacts", json={
        "role": "owner", "first_name": "Rajesh", "last_name": "Sharma",
        "beneficiary_name": "Anita Sharma"})
    assert created.status_code == 201
    owner = created.json()
    assert (owner["name"], owner["beneficiary_name"]) == ("Rajesh Sharma", "Anita Sharma")
    # Back to the owner's own account: no beneficiary, and the name as it was.
    same = client.patch(f"/api/property/contacts/{owner['contact_id']}",
                        json={"beneficiary_name": None})
    assert same.status_code == 200
    assert (same.json()["name"], same.json()["beneficiary_name"]) == ("Rajesh Sharma", None)


def test_the_lease_endpoint_takes_and_returns_the_landlords_parts(client):
    client.post("/api/apartments", json={"apartment_id": APARTMENT, "name": "Flat 402"})
    saved = client.post("/api/rent/config", json={
        "landlord_first_name": "Rajesh", "landlord_last_name": "Sharma",
        "monthly_rent_paise": RENT})
    assert saved.status_code == 200
    assert saved.json()["landlord_name"] == "Rajesh Sharma"
    assert _parts(client.get("/api/rent/config").json()["config"], "landlord_") == (
        "Rajesh", None, "Sharma")


# ------------------------------------------------------------- the migration --

def test_migration_0017_splits_the_names_on_file(database_factory):
    db = database_factory(migrated=False)
    db.migrate(target_version=16)
    on_file = ["Rajesh Kumar Sharma", "Mohan", "  Kumar   S.  Yadav ", "Venkata Sai Krishna Rao"]
    with connect(db, as_owner=True) as conn:
        conn.execute("INSERT INTO apartment (apartment_id, name, created_at) VALUES (%s,%s,%s)",
                     (APARTMENT, "Flat 402", "2026-10-01T10:00:00+05:30"))
        for number, name in enumerate(on_file):
            conn.execute(
                "INSERT INTO household_contact (contact_id, apartment_id, role, name, "
                "created_at, updated_at) VALUES (%s,%s,'caretaker',%s,'then','then')",
                (f"contact-{number}", APARTMENT, name))
        conn.execute(
            "INSERT INTO rent_lease_config (config_id, apartment_id, landlord_name, "
            "monthly_rent_paise, created_at, updated_at) VALUES ('lease-1',%s,%s,%s,%s,%s)",
            (APARTMENT, "Anita Rao", RENT, "then", "then"))
    db.migrate()
    with connect(db, read_only=True) as conn:
        contacts = {row["name"]: row for row in conn.execute(
            "SELECT name, first_name, middle_name, last_name FROM household_contact")}
        lease = conn.execute("SELECT * FROM rent_lease_config").fetchone()
    # Split as the application splits a whole name it is sent; the name itself is kept.
    for name in on_file:
        stored = (contacts[name]["first_name"], contacts[name]["middle_name"],
                  contacts[name]["last_name"])
        assert stored == names.split_full_name(name), name
    assert _parts(lease, "landlord_") == ("Anita", None, "Rao")
    assert lease["landlord_name"] == "Anita Rao"
    assert apartments.read_apartment(db, APARTMENT)["name"] == "Flat 402"

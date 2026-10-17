"""The House Kitty and whose account holds it (migration 0018).

The household pool is one accounting entity, the House Kitty, on every screen
and statement. Its money sits in a real person's bank account or UPI ID, and
that person's name -- what a payer's banking app shows -- is the apartment's
``pool_account_holder``. These tests pin that the account holder is saved,
cleared and checked like the pool's other details, that the passbook carries it,
and that the migration kept a pool name that had been changed from the defaults
as the account holder.
"""
from __future__ import annotations

import pytest

from aem.errors import ValidationError
from aem.services import apartments, pool_statements

from .conftest import APARTMENT
from .support import DatabaseRefusal, connect

HOLDER = "Sarbananda Taw"


def test_a_new_household_has_a_house_kitty_with_no_account_holder(apartment):
    saved = apartments.read_apartment(apartment, APARTMENT)
    assert (saved["pool_name"], saved["pool_account_holder"]) == ("House Kitty", None)


def test_the_account_holder_is_saved_trimmed_and_audited(apartment):
    saved = apartments.update_apartment(apartment, APARTMENT, pool_account_holder=f"  {HOLDER} ")
    assert saved["pool_account_holder"] == HOLDER
    assert saved["pool_name"] == "House Kitty", "the kitty keeps its name"
    with connect(apartment, read_only=True) as conn:
        [entry] = conn.execute(
            "SELECT old_value, new_value FROM audit_log WHERE entity_type='apartment' "
            "AND action='update' AND field_name='pool_account_holder'").fetchall()
    assert (entry["old_value"], entry["new_value"]) == (None, HOLDER)


def test_the_account_holder_is_kept_when_left_out_and_cleared_when_empty(apartment):
    apartments.update_apartment(apartment, APARTMENT, pool_account_holder=HOLDER)
    kept = apartments.update_apartment(apartment, APARTMENT, pool_upi_id="kitty@okaxis")
    assert kept["pool_account_holder"] == HOLDER
    for empty in (None, "", "   "):
        apartments.update_apartment(apartment, APARTMENT, pool_account_holder=HOLDER)
        cleared = apartments.update_apartment(apartment, APARTMENT, pool_account_holder=empty)
        assert cleared["pool_account_holder"] is None


def test_an_account_holder_too_long_is_refused_and_nothing_changes(apartment):
    with pytest.raises(ValidationError) as refused:
        apartments.update_apartment(apartment, APARTMENT, pool_account_holder="x" * 201,
                                    pool_upi_id="kitty@okaxis")
    assert refused.value.details["field"] == "pool_account_holder"
    saved = apartments.read_apartment(apartment, APARTMENT)
    assert (saved["pool_account_holder"], saved["pool_upi_id"]) == (None, None)


def test_the_database_itself_refuses_a_blank_account_holder(apartment):
    with connect(apartment) as conn, pytest.raises(DatabaseRefusal):
        conn.execute("UPDATE apartment SET pool_account_holder='   '")


def test_the_passbook_names_the_kittys_account_holder(apartment):
    apartments.update_apartment(apartment, APARTMENT, pool_account_holder=HOLDER)
    account = pool_statements.get_payment_pools_statement(
        apartment, APARTMENT)["pool_statement"]["account"]
    assert account == {"instance_id": "__pool__", "name": "House Kitty",
                       "account_holder": HOLDER}


def test_the_apartment_endpoint_saves_and_refuses_the_account_holder(client):
    client.post("/api/apartments", json={"apartment_id": APARTMENT, "name": "Flat 402"})
    saved = client.patch(f"/api/apartments/{APARTMENT}", json={
        "pool_name": "House Kitty", "pool_account_holder": HOLDER,
        "pool_bank_account": "123456789012", "pool_bank_ifsc": "sbin0005678",
        "pool_upi_id": "sarbananda@okicici"})
    assert saved.status_code == 200
    body = saved.json()
    assert (body["pool_name"], body["pool_account_holder"], body["pool_bank_ifsc"]) == (
        "House Kitty", HOLDER, "SBIN0005678")
    assert client.patch(f"/api/apartments/{APARTMENT}",
                        json={"pool_account_holder": "x" * 201}).status_code == 422
    assert client.get(f"/api/apartments/{APARTMENT}").json()["pool_account_holder"] == HOLDER


def test_migration_0018_keeps_a_changed_pool_name_as_the_account_holder(database_factory):
    db = database_factory(migrated=False)
    db.migrate(target_version=17)
    with connect(db, as_owner=True) as conn:
        for apartment_id, pool_name in (("default", "Household Pool"),
                                        ("kitty", "House Kitty"),
                                        ("held", HOLDER)):
            conn.execute(
                "INSERT INTO apartment (apartment_id, name, created_at, pool_name) "
                "VALUES (%s,%s,'then',%s)", (apartment_id, apartment_id, pool_name))
    db.migrate()
    held = {apartment_id: (apartments.read_apartment(db, apartment_id)["pool_name"],
                           apartments.read_apartment(db, apartment_id)["pool_account_holder"])
            for apartment_id in ("default", "kitty", "held")}
    assert held == {"default": ("House Kitty", None), "kitty": ("House Kitty", None),
                    "held": ("House Kitty", HOLDER)}
    # A household added afterwards starts as the House Kitty too.
    with connect(db, as_owner=True) as conn:
        conn.execute("INSERT INTO apartment (apartment_id, name, created_at) "
                     "VALUES ('new','new','then')")
    assert apartments.read_apartment(db, "new")["pool_name"] == "House Kitty"

"""The household's rooms.

These tests pin the inventory's rules: a room number is unique in the flat, a
room holds at most one resident and a resident holds at most one room, only an
active resident is housed, every change is audited, and a move-out clearance
frees the leaver's room in the same transaction as everything else.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from aem.errors import ConflictError, NotFoundError, ValidationError
from aem.services import clearance, residents, resident_statements, rooms

from .conftest import APARTMENT
from .support import DatabaseRefusal, connect

TODAY = date.today().isoformat()
TOMORROW = (date.today() + timedelta(days=1)).isoformat()


def _rows(db, sql, params=()):
    with connect(db, read_only=True) as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _add(db, number, **fields):
    return rooms.create_room(db, APARTMENT, {"room_number": number, **fields})


@pytest.fixture()
def flat(apartment):
    """Room 1 with a washroom and balcony for Alice, Room 2 with a washroom for
    Bob, and a plain Room 3 standing vacant."""
    _add(apartment, "Room 1", has_attached_washroom=True, has_balcony=True,
         assigned_resident_id="alice", effective_date="2026-08-01")
    _add(apartment, "Room 2", has_attached_washroom=True, assigned_resident_id="bob",
         effective_date="2026-08-01")
    _add(apartment, "Room 3")
    return apartment


def _room(db, number):
    return next(room for room in rooms.list_rooms(db, APARTMENT)["items"]
                if room["room_number"] == number)


# ---------------------------------------------------------------- inventory --

def test_rooms_are_listed_in_order_with_their_occupants_and_counts(flat):
    listing = rooms.list_rooms(flat, APARTMENT)
    assert [(r["room_number"], r["status"], r["assigned_display_name"])
            for r in listing["items"]] == [
        ("Room 1", "occupied", "Alice"), ("Room 2", "occupied", "Bob"),
        ("Room 3", "vacant", None)]
    assert listing["summary"] == {"total_rooms": 3, "occupied_rooms": 2, "vacant_rooms": 1,
                                  "with_attached_washroom": 2, "with_balcony": 1,
                                  "rooms_with_base_rent": 0, "total_base_rent_paise": 0,
                                  "linked_slots": 0}
    first = listing["items"][0]
    assert (first["has_attached_washroom"], first["has_balcony"],
            first["assigned_since"]) == (True, True, "2026-08-01")


def test_a_new_room_defaults_to_no_amenities_and_vacant(apartment):
    room = _add(apartment, "  Room 9 ")
    assert room["room_number"] == "Room 9"
    assert (room["has_attached_washroom"], room["has_balcony"]) == (False, False)
    assert (room["status"], room["assigned_resident_id"], room["assigned_since"]) == (
        "vacant", None, None)


def test_an_occupant_given_at_creation_moves_in_today_by_default(apartment):
    assert _add(apartment, "Room 1", assigned_resident_id="chris")["assigned_since"] == TODAY


@pytest.mark.parametrize("number", ["Room 1", "room 1", " ROOM 1 "])
def test_room_numbers_are_unique_in_the_flat_ignoring_case(flat, number):
    with pytest.raises(ConflictError) as caught:
        _add(flat, number)
    assert caught.value.code == "room_number_taken"


def test_the_database_refuses_a_duplicate_room_number(flat):
    with pytest.raises(DatabaseRefusal):
        with connect(flat) as conn:
            conn.execute(
                "INSERT INTO household_room (room_id, apartment_id, room_number, created_at, "
                "updated_at) VALUES ('dup', %s, 'Room 1', 'now', 'now')", (APARTMENT,))


@pytest.mark.parametrize("payload, field", [
    ({"room_number": ""}, "room_number"),
    ({"room_number": "   "}, "room_number"),
    ({"room_number": "R" * 51}, "room_number"),
    ({"room_number": "Room 4", "has_balcony": "yes"}, "has_balcony"),
    ({"room_number": "Room 4", "assigned_resident_id": "chris",
      "effective_date": TOMORROW}, "effective_date"),
    ({"room_number": "Room 4", "assigned_resident_id": "chris",
      "effective_date": "1999-12-31"}, "effective_date"),
])
def test_an_invalid_room_is_refused_and_writes_nothing(flat, payload, field):
    with pytest.raises(ValidationError) as caught:
        rooms.create_room(flat, APARTMENT, payload)
    assert caught.value.details.get("field") == field
    assert rooms.list_rooms(flat, APARTMENT)["total"] == 3


def test_editing_changes_only_what_was_sent(flat):
    room_id = _room(flat, "Room 3")["room_id"]
    updated = rooms.update_room(flat, APARTMENT, room_id, {"has_balcony": True})
    assert (updated["room_number"], updated["has_balcony"],
            updated["has_attached_washroom"]) == ("Room 3", True, False)
    renamed = rooms.update_room(flat, APARTMENT, room_id, {"room_number": "Room 3A"})
    assert renamed["room_number"] == "Room 3A" and renamed["has_balcony"] is True
    with pytest.raises(ConflictError):
        rooms.update_room(flat, APARTMENT, room_id, {"room_number": "room 2"})
    # Renaming a room to its own number, in another case, is not a clash.
    assert rooms.update_room(flat, APARTMENT, room_id,
                             {"room_number": "ROOM 3A"})["room_number"] == "ROOM 3A"


def test_editing_can_assign_and_vacate(flat):
    room_id = _room(flat, "Room 3")["room_id"]
    assigned = rooms.update_room(flat, APARTMENT, room_id,
                                 {"assigned_resident_id": "chris",
                                  "effective_date": "2026-09-01"})
    assert (assigned["assigned_resident_id"], assigned["assigned_since"]) == (
        "chris", "2026-09-01")
    vacated = rooms.update_room(flat, APARTMENT, room_id, {"assigned_resident_id": None})
    assert (vacated["status"], vacated["assigned_since"]) == ("vacant", None)


# ---------------------------------------------------------------- occupancy --

def test_a_resident_is_assigned_to_a_vacant_room(flat):
    room = rooms.assign_resident(flat, APARTMENT, _room(flat, "Room 3")["room_id"], "chris",
                                 effective_date="2026-10-01")
    assert (room["assigned_display_name"], room["assigned_since"], room["status"]) == (
        "Chris", "2026-10-01", "occupied")
    assert rooms.room_for_resident(flat, APARTMENT, "chris")["room_number"] == "Room 3"


def test_assigning_to_an_occupied_room_swaps_its_occupant_out(flat):
    room_id = _room(flat, "Room 1")["room_id"]
    room = rooms.assign_resident(flat, APARTMENT, room_id, "chris", effective_date=TODAY)
    assert room["assigned_resident_id"] == "chris"
    assert rooms.room_for_resident(flat, APARTMENT, "alice") is None
    actions = [row["action"] for row in _rows(
        flat, "SELECT action FROM audit_log WHERE entity_id=%s ORDER BY row_seq", (room_id,))]
    assert actions == ["create", "swap_occupant"]


def test_a_resident_cannot_occupy_two_rooms(flat):
    with pytest.raises(ConflictError) as caught:
        rooms.assign_resident(flat, APARTMENT, _room(flat, "Room 3")["room_id"], "alice",
                              effective_date=TODAY)
    assert caught.value.code == "resident_has_room"
    assert "Alice already occupies Room 1" in str(caught.value)
    assert _room(flat, "Room 1")["assigned_resident_id"] == "alice"
    assert _room(flat, "Room 3")["status"] == "vacant"


def test_the_database_refuses_a_resident_in_two_rooms(flat):
    with pytest.raises(DatabaseRefusal):
        with connect(flat) as conn:
            conn.execute("UPDATE household_room SET assigned_resident_id='alice', "
                         "assigned_since='2026-10-01' WHERE room_number='Room 3'")


def test_reassigning_the_same_occupant_is_refused(flat):
    with pytest.raises(ConflictError) as caught:
        rooms.assign_resident(flat, APARTMENT, _room(flat, "Room 1")["room_id"], "alice",
                              effective_date=TODAY)
    assert caught.value.code == "already_assigned"


def test_a_resident_who_has_moved_out_cannot_be_housed(flat):
    residents.deactivate_resident(flat, "chris", active_until="2026-09-30")
    with pytest.raises(ValidationError) as caught:
        rooms.assign_resident(flat, APARTMENT, _room(flat, "Room 3")["room_id"], "chris",
                              effective_date=TODAY)
    assert caught.value.code == "resident_inactive"


@pytest.mark.parametrize("when", [TOMORROW, "1999-01-01", "2026-13-01", "soon"])
def test_a_move_in_date_must_be_real_and_not_in_the_future(flat, when):
    with pytest.raises(ValidationError):
        rooms.assign_resident(flat, APARTMENT, _room(flat, "Room 3")["room_id"], "chris",
                              effective_date=when)
    assert _room(flat, "Room 3")["status"] == "vacant"


def test_vacating_frees_the_room_and_is_audited_with_its_reason(flat):
    room_id = _room(flat, "Room 2")["room_id"]
    room = rooms.vacate_room(flat, APARTMENT, room_id, reason="Moving rooms")
    assert (room["status"], room["assigned_resident_id"], room["assigned_since"]) == (
        "vacant", None, None)
    entry = _rows(flat, "SELECT * FROM audit_log WHERE entity_type='household_room' "
                        "AND action='vacate'")[0]
    assert entry["entity_id"] == room_id and entry["reason"] == "Moving rooms"
    with pytest.raises(ConflictError):
        rooms.vacate_room(flat, APARTMENT, room_id)


# ------------------------------------------------------------------ removal --

def test_only_a_vacant_room_can_be_removed(flat):
    with pytest.raises(ConflictError) as caught:
        rooms.delete_room(flat, APARTMENT, _room(flat, "Room 1")["room_id"])
    assert caught.value.code == "room_occupied"
    room_id = _room(flat, "Room 3")["room_id"]
    rooms.delete_room(flat, APARTMENT, room_id)
    assert [r["room_number"] for r in rooms.list_rooms(flat, APARTMENT)["items"]] == [
        "Room 1", "Room 2"]
    assert _rows(flat, "SELECT action FROM audit_log WHERE entity_id=%s ORDER BY row_seq",
                 (room_id,))[-1]["action"] == "delete"
    with pytest.raises(NotFoundError):
        rooms.delete_room(flat, APARTMENT, room_id)


def test_another_apartments_room_is_not_found(flat):
    from aem.services import apartments
    apartments.create_apartment(flat, apartment_id="other", name="Other flat")
    with pytest.raises(NotFoundError):
        rooms.vacate_room(flat, "other", _room(flat, "Room 1")["room_id"])


# ---------------------------------------------------------- move-out clearance --

def test_a_move_out_clearance_frees_the_leavers_room(flat):
    clearance.execute_clearance(flat, APARTMENT, "bob", {
        "move_out_date": TODAY, "settlement_date": TODAY,
        "settlement_path": "standalone_clearance", "deductions_paise": 0})
    assert _room(flat, "Room 2")["status"] == "vacant"
    assert _room(flat, "Room 1")["assigned_resident_id"] == "alice"
    entry = _rows(flat, "SELECT * FROM audit_log WHERE entity_type='household_room' "
                        "AND action='vacate'")[0]
    assert entry["reason"] == "exit clearance"


def test_a_refused_clearance_leaves_the_room_occupied(flat):
    with pytest.raises(ValidationError):
        clearance.execute_clearance(flat, APARTMENT, "bob", {
            "move_out_date": TOMORROW, "settlement_date": TODAY,
            "settlement_path": "standalone_clearance", "deductions_paise": 0})
    assert _room(flat, "Room 2")["assigned_resident_id"] == "bob"


def test_a_resident_without_a_room_can_still_be_cleared(flat):
    clearance.execute_clearance(flat, APARTMENT, "chris", {
        "move_out_date": TODAY, "settlement_date": TODAY,
        "settlement_path": "standalone_clearance", "deductions_paise": 0})
    assert rooms.list_rooms(flat, APARTMENT)["summary"]["occupied_rooms"] == 2


def test_a_statement_names_the_room_its_resident_lives_in(flat):
    statement = resident_statements.get_resident_monthly_statement(
        flat, APARTMENT, "bob", TODAY[:7])
    assert statement["room_label"] == "Room 2"
    assert "👤 Resident: Bob (Room 2)" in statement["whatsapp_text"]


# ----------------------------------------------------------------------- API --

def test_the_rooms_api_round_trip(client):
    client.post("/api/apartments", json={"apartment_id": APARTMENT, "name": "Flat"})
    client.post("/api/residents", json={"resident_id": "alice", "display_name": "Alice"})
    created = client.post("/api/property/rooms", json={
        "room_number": "Room 1", "has_attached_washroom": True})
    assert created.status_code == 201, created.text
    room_id = created.json()["room_id"]
    assert client.post("/api/property/rooms", json={"room_number": "room 1"}).status_code == 409
    assert client.post("/api/property/rooms", json={"room_number": ""}).status_code == 422
    assert client.post("/api/property/rooms",
                       json={"room_number": "Room 2", "extra": 1}).status_code == 422

    assigned = client.post(f"/api/property/rooms/{room_id}/assign",
                           json={"resident_id": "alice", "effective_date": TODAY})
    assert assigned.status_code == 200 and assigned.json()["status"] == "occupied"
    assert client.post(f"/api/property/rooms/{room_id}/assign",
                       json={"resident_id": "alice"}).status_code == 422
    assert client.delete(f"/api/property/rooms/{room_id}").status_code == 409

    patched = client.patch(f"/api/property/rooms/{room_id}", json={"has_balcony": True})
    assert patched.json()["has_balcony"] is True
    assert patched.json()["assigned_resident_id"] == "alice"

    vacated = client.post(f"/api/property/rooms/{room_id}/vacate")
    assert vacated.status_code == 200 and vacated.json()["status"] == "vacant"
    listing = client.get("/api/property/rooms").json()
    assert listing["summary"]["vacant_rooms"] == 1
    assert client.delete(f"/api/property/rooms/{room_id}").status_code == 204
    assert client.get("/api/property/rooms").json()["total"] == 0
    assert client.post("/api/property/rooms/nope/vacate").status_code == 404


# ------------------------------------------------------- the live seed script --

def _seed_module():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parent.parent / "scripts" / "seed_live_rooms.py"
    spec = importlib.util.spec_from_file_location("seed_live_rooms", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def flatmates(apartment):
    from aem.services import rent
    for rid, name in (("rohan", "Rohan Das"), ("arnab", "Arnab Deka"),
                      ("sarbananda", "Sarbananda Taw")):
        residents.create_resident(apartment, apartment_id=APARTMENT, resident_id=rid,
                                  display_name=name, active_from="2026-08-01")
    rent.save_lease_config(apartment, APARTMENT, {
        "landlord_name": "Kumar Yadav", "monthly_rent_paise": 2_850_000})
    # The owner contact the lease created is removed, to show the script adds it.
    from aem.services import property as household
    for contact in household.list_contacts(apartment, APARTMENT)["items"]:
        household.delete_contact(apartment, APARTMENT, contact["contact_id"])
    return apartment


def test_the_seed_script_houses_everyone_and_is_safe_to_run_again(flatmates):
    from aem.services import property as household
    seed = _seed_module()
    assert any("would add Room 1" in line
               for line in seed.seed(flatmates, APARTMENT, dry_run=True))
    assert rooms.list_rooms(flatmates, APARTMENT)["total"] == 0

    seed.seed(flatmates, APARTMENT, dry_run=False)
    assert [(r["room_number"], r["assigned_resident_id"], r["assigned_since"])
            for r in rooms.list_rooms(flatmates, APARTMENT)["items"]] == [
        ("Room 1", "rohan", "2026-08-01"), ("Room 2", "arnab", "2026-08-01"),
        ("Room 3", "sarbananda", "2026-08-01")]
    [owner] = household.list_contacts(flatmates, APARTMENT)["items"]
    assert (owner["role"], owner["name"]) == ("owner", "Kumar Yadav")

    audit_rows = len(_rows(flatmates, "SELECT 1 FROM audit_log"))
    again = seed.seed(flatmates, APARTMENT, dry_run=False)
    assert "Room 1 already has Rohan Das" in again
    assert len(_rows(flatmates, "SELECT 1 FROM audit_log")) == audit_rows


def test_the_seed_script_refuses_to_reshuffle_an_occupied_room(flatmates):
    seed = _seed_module()
    _add(flatmates, "Room 1", assigned_resident_id="alice")
    with pytest.raises(seed.PlanError, match="Room 1 is occupied by Alice"):
        seed.seed(flatmates, APARTMENT, dry_run=False)
    assert rooms.list_rooms(flatmates, APARTMENT)["total"] == 1


def test_a_deposit_slot_is_linked_to_its_room(flatmates):
    room = _add(flatmates, "Room 9")
    with connect(flatmates) as conn:
        conn.execute(
            "INSERT INTO household_deposit_slot (slot_id, apartment_id, slot_number, "
            "slot_label, target_deposit_paise, created_at, updated_at) "
            "VALUES ('slot-9', %s, 9, 'Room 9', 100, 'now', 'now')", (APARTMENT,))
    assert rooms.link_slot_to_room(flatmates, APARTMENT, "slot-9", room["room_id"]) is True
    assert rooms.link_slot_to_room(flatmates, APARTMENT, "slot-9", room["room_id"]) is False
    assert _rows(flatmates, "SELECT room_id FROM household_deposit_slot")[0]["room_id"] == \
        room["room_id"]


# --------------------------------------------------------- base rent and slots --

def _slot(db, number, *, room_id=None):
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO household_deposit_slot (slot_id, apartment_id, slot_number, "
            "slot_label, target_deposit_paise, funded_amount_paise, status, room_id, "
            "created_at, updated_at) VALUES (%s, %s, %s, %s, 5000000, 0, "
            "'vacant', %s, 'now', 'now')",
            (f"slot-{number}", APARTMENT, number, f"Room {number}", room_id))
    return f"slot-{number}"


def test_a_room_carries_a_base_rent(flat):
    room = _add(flat, "Room 4", base_rent_paise=1_200_000)
    assert room["base_rent_paise"] == 1_200_000
    changed = rooms.update_room(flat, APARTMENT, room["room_id"], {"base_rent_paise": 1_250_000})
    assert changed["base_rent_paise"] == 1_250_000
    cleared = rooms.update_room(flat, APARTMENT, room["room_id"], {"base_rent_paise": None})
    assert cleared["base_rent_paise"] is None
    rooms.update_room(flat, APARTMENT, _room(flat, "Room 1")["room_id"],
                      {"base_rent_paise": 1_000_000})
    summary = rooms.list_rooms(flat, APARTMENT)["summary"]
    assert (summary["rooms_with_base_rent"], summary["total_base_rent_paise"]) == (1, 1_000_000)


@pytest.mark.parametrize("rent", [-1, 12.5, True, "1000"])
def test_a_base_rent_must_be_whole_non_negative_paise(flat, rent):
    with pytest.raises(ValidationError):
        _add(flat, "Room 4", base_rent_paise=rent)


def test_a_room_lists_the_deposit_slot_that_backs_it(flat):
    room_id = _room(flat, "Room 1")["room_id"]
    slot_id = _slot(flat, 1)
    linked = rooms.update_room(flat, APARTMENT, room_id, {"linked_slot_id": slot_id})
    assert linked["linked_deposit_slot"] == {
        "slot_id": slot_id, "slot_number": 1, "slot_label": "Room 1",
        "target_deposit_paise": 5_000_000, "funded_amount_paise": 0, "status": "vacant"}
    assert rooms.list_rooms(flat, APARTMENT)["summary"]["linked_slots"] == 1
    unlinked = rooms.update_room(flat, APARTMENT, room_id, {"linked_slot_id": None})
    assert unlinked["linked_deposit_slot"] is None


def test_a_new_room_can_be_linked_to_a_slot_as_it_is_added(flat):
    slot_id = _slot(flat, 4)
    room = _add(flat, "Room 4", linked_slot_id=slot_id)
    assert room["linked_deposit_slot"]["slot_id"] == slot_id


def test_a_room_is_backed_by_one_slot_at_most(flat):
    room_id = _room(flat, "Room 1")["room_id"]
    _slot(flat, 1, room_id=room_id)
    second = _slot(flat, 2)
    with pytest.raises(ConflictError) as caught:
        rooms.link_slot_to_room(flat, APARTMENT, second, room_id)
    assert caught.value.code == "room_has_slot"
    # Choosing another slot for the room from the room's side moves the link.
    moved = rooms.update_room(flat, APARTMENT, room_id, {"linked_slot_id": second})
    assert moved["linked_deposit_slot"]["slot_id"] == second
    assert _rows(flat, "SELECT room_id FROM household_deposit_slot WHERE slot_id='slot-1'"
                 )[0]["room_id"] is None


def test_the_database_refuses_two_slots_for_one_room(flat):
    room_id = _room(flat, "Room 1")["room_id"]
    _slot(flat, 1, room_id=room_id)
    with pytest.raises(DatabaseRefusal):
        _slot(flat, 2, room_id=room_id)


def test_a_refused_link_to_an_unknown_slot_changes_nothing(flat):
    room_id = _room(flat, "Room 1")["room_id"]
    with pytest.raises(NotFoundError):
        rooms.update_room(flat, APARTMENT, room_id,
                          {"linked_slot_id": "nope", "base_rent_paise": 5})
    assert _room(flat, "Room 1")["base_rent_paise"] is None

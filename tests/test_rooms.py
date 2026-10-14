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
                                  "with_attached_washroom": 2, "with_balcony": 1}
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

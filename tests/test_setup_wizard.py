"""The guided setup.

The behaviour that matters most here is not the happy path -- it is what
happens when the operator closes the tab halfway through. Every step commits,
and the resume point is derived from real records, so these tests check that
by throwing away the client entirely between steps.
"""
from __future__ import annotations

import pytest

from aem.db import connect
from aem.errors import ValidationError
from aem.services import setup as setup_service
from aem.services.definitions import ESSENTIALS_CATALOGUE, STARTER_DEFINITIONS
from tests.conftest import APARTMENT


# --------------------------------------------------------------------------- #
# The catalogue is a suggestion list, not built-in behaviour
# --------------------------------------------------------------------------- #

def test_the_catalogue_offers_the_essentials_with_sensible_period_rules():
    by_id = {entry["definition_id"]: entry for entry in ESSENTIALS_CATALOGUE}
    # The ones a shared flat almost always pays are pre-ticked.
    for key in ("rent", "electricity", "water", "cooking_gas", "maid", "maintenance",
                "broadband", "groceries"):
        assert by_id[key]["suggested"] is True, key
    # Rent is for the month it covers; the electricity bill is for the month before.
    assert by_id["rent"]["period_rule"] == "current_month"
    assert by_id["electricity"]["period_rule"] == "previous_month"
    assert by_id["groceries"]["period_rule"] == "previous_month"
    # A gas cylinder is bought when it runs out, not on a schedule.
    assert by_id["cooking_gas"]["period_rule"] == "transaction_date"
    assert by_id["cooking_gas"]["recurrence"] == "none"
    # Every entry explains itself to the operator.
    assert all(entry["hint"].strip() for entry in ESSENTIALS_CATALOGUE)


def test_the_starter_set_is_exactly_the_suggested_subset():
    assert {d["definition_id"] for d in STARTER_DEFINITIONS} == {
        entry["definition_id"] for entry in ESSENTIALS_CATALOGUE if entry["suggested"]}


def test_a_catalogue_entry_becomes_an_ordinary_editable_record(apartment, client):
    """Nothing created here is special to the application."""
    setup_service.create_categories_step(
        apartment, apartment_id=APARTMENT,
        items=[{"name": "Rent", "category": "Housing", "period_rule": "current_month"}])
    from aem.services import definitions

    definitions.update_expense_definition(
        apartment, "rent", name="Houseboat mooring", period_rule="transaction_date",
        reason="we live on a boat now")
    updated = definitions.read_expense_definition(apartment, "rent")
    assert updated["name"] == "Houseboat mooring"
    assert updated["period_rule"] == "transaction_date"
    definitions.deactivate_expense_definition(apartment, "rent")
    assert definitions.read_expense_definition(apartment, "rent")["is_active"] is False


# --------------------------------------------------------------------------- #
# Identifiers are derived, so the operator never types one
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("typed, expected", [
    ("Flat 402, Lakeview", "flat-402-lakeview"),
    ("  Alice  ", "alice"),
    ("Rohit & Sameer", "rohit-sameer"),
    ("Société Maintenance", "societe-maintenance"),
    ("Zoë", "zoe"),
])
def test_identifiers_are_derived_from_what_was_typed(typed, expected):
    assert setup_service.slugify(typed) == expected


def test_an_unusable_name_falls_back_rather_than_producing_an_empty_id():
    assert setup_service.slugify("###", fallback="resident") == "resident"
    assert setup_service.slugify("日本", fallback="category") == "category"


def test_a_clashing_derived_identifier_gets_a_suffix(db):
    setup_service.create_apartment_step(db, name="Home")
    second = setup_service.create_apartment_step(db, name="Home")
    assert second["apartment_id"] == "home-2"


def test_two_residents_with_similar_names_get_distinct_identifiers(apartment):
    result = setup_service.create_residents_step(
        apartment, apartment_id=APARTMENT, names=["Alice B", "Alice-B"])
    ids = [row["resident_id"] for row in result["created"]]
    assert ids == ["alice-b", "alice-b-2"]
    assert len(set(ids)) == 2


# --------------------------------------------------------------------------- #
# Progress is read from real records
# --------------------------------------------------------------------------- #

def test_an_empty_database_resumes_at_the_first_step(db):
    state = setup_service.setup_state(db)
    assert state["next_step"] == "apartment"
    assert state["is_complete"] is False
    assert [step["done"] for step in state["steps"]] == [False, False, False, False]


def test_each_step_moves_the_resume_point_forward(db):
    assert setup_service.setup_state(db)["next_step"] == "apartment"

    created = setup_service.create_apartment_step(db, name="Flat 402")
    assert setup_service.setup_state(db)["next_step"] == "residents"

    setup_service.create_residents_step(db, apartment_id=created["apartment_id"],
                                        names=["Alice", "Bob"])
    assert setup_service.setup_state(db)["next_step"] == "categories"

    setup_service.create_categories_step(
        db, apartment_id=created["apartment_id"],
        items=[{"name": "Rent", "category": "Housing"}])
    assert setup_service.setup_state(db)["next_step"] == "finish"

    setup_service.finish_setup(db, apartment_id=created["apartment_id"])
    final = setup_service.setup_state(db)
    assert final["next_step"] == "done"
    assert final["is_complete"] is True
    assert [step["done"] for step in final["steps"]] == [True, True, True, True]


def test_the_steps_summarise_what_is_already_saved(db):
    created = setup_service.create_apartment_step(db, name="Flat 402")
    setup_service.create_residents_step(db, apartment_id=created["apartment_id"],
                                        names=["Alice", "Bob"])
    steps = {step["key"]: step for step in setup_service.setup_state(db)["steps"]}
    assert steps["apartment"]["summary"] == "Flat 402"
    assert steps["residents"]["summary"] == "Alice, Bob"
    assert steps["categories"]["summary"] is None


# --------------------------------------------------------------------------- #
# Closing the tab halfway through
# --------------------------------------------------------------------------- #

def test_closing_the_tab_after_a_step_loses_nothing(db):
    """Simulated by discarding all client state and re-reading from the server."""
    created = setup_service.create_apartment_step(db, name="Flat 402")
    setup_service.create_residents_step(db, apartment_id=created["apartment_id"],
                                        names=["Alice", "Bob", "Chris"])

    # ... the operator closes the tab here. Nothing in the browser survives.
    resumed = setup_service.setup_state(db)
    assert resumed["next_step"] == "categories"
    assert resumed["apartment"]["name"] == "Flat 402"
    assert [r["display_name"] for r in resumed["residents"]] == ["Alice", "Bob", "Chris"]


def test_resuming_works_from_a_completely_fresh_process(db):
    """Cleared browser storage, a different machine: the server still knows."""
    created = setup_service.create_apartment_step(db, name="Flat 402")
    setup_service.create_residents_step(db, apartment_id=created["apartment_id"],
                                        names=["Alice"])
    # Re-import the module to prove nothing is cached in memory either.
    import importlib

    import aem.services.setup as fresh
    importlib.reload(fresh)
    assert fresh.setup_state(db)["next_step"] == "categories"


def test_re_submitting_the_resident_step_creates_no_duplicates(apartment):
    first = setup_service.create_residents_step(
        apartment, apartment_id=APARTMENT, names=["Dana", "Eve"])
    assert first["created_count"] == 2
    second = setup_service.create_residents_step(
        apartment, apartment_id=APARTMENT, names=["Dana", "Eve"])
    assert (second["created_count"], second["skipped_count"]) == (0, 2)
    with connect(apartment, read_only=True) as conn:
        names = [row[0] for row in conn.execute(
            "SELECT display_name FROM resident WHERE apartment_id=? AND display_name "
            "IN ('Dana','Eve')", (APARTMENT,)).fetchall()]
    assert sorted(names) == ["Dana", "Eve"]


def test_re_submitting_the_category_step_creates_no_duplicates(apartment):
    items = [{"name": "Rent", "category": "Housing"},
             {"name": "Broadband", "category": "Utilities"}]
    first = setup_service.create_categories_step(apartment, apartment_id=APARTMENT,
                                                 items=items)
    assert first["created_count"] == 2
    second = setup_service.create_categories_step(apartment, apartment_id=APARTMENT,
                                                  items=items)
    assert (second["created_count"], second["skipped_count"]) == (0, 2)


def test_a_half_finished_step_writes_nothing(apartment):
    """One bad entry rolls the whole batch back, so a retry starts clean."""
    with pytest.raises(ValidationError):
        setup_service.create_categories_step(
            apartment, apartment_id=APARTMENT,
            items=[{"name": "Rent", "category": "Housing"},
                   {"name": "Broken", "period_rule": "every_other_tuesday"}])
    with connect(apartment, read_only=True) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM expense_definition WHERE apartment_id=?",
            (APARTMENT,)).fetchone()[0] == 0


# --------------------------------------------------------------------------- #
# The steps themselves
# --------------------------------------------------------------------------- #

def test_blank_resident_rows_are_ignored(apartment):
    """So spare input boxes can simply be left empty."""
    result = setup_service.create_residents_step(
        apartment, apartment_id=APARTMENT, names=["Dana", "", "   ", "Eve", ""])
    assert [row["display_name"] for row in result["created"]] == ["Dana", "Eve"]


def test_an_entirely_blank_resident_step_is_refused(apartment):
    with pytest.raises(ValidationError, match="at least one resident"):
        setup_service.create_residents_step(apartment, apartment_id=APARTMENT,
                                            names=["", "  "])


def test_two_residents_with_the_same_name_are_refused(apartment):
    with pytest.raises(ValidationError, match="same name"):
        setup_service.create_residents_step(apartment, apartment_id=APARTMENT,
                                            names=["Dana", "dana"])


def test_a_category_the_operator_typed_goes_through_the_same_path(apartment):
    result = setup_service.create_categories_step(
        apartment, apartment_id=APARTMENT,
        items=[{"name": "Terrace garden", "category": "Household",
                "period_rule": "transaction_date", "recurrence": "none"}])
    created = result["created"][0]
    assert created["expense_definition_id"] == "terrace-garden"
    from aem.services import definitions

    saved = definitions.read_expense_definition(apartment, "terrace-garden")
    assert saved["period_rule"] == "transaction_date"
    assert saved["recurrence"] == "none"
    assert saved["is_active"] is True


def test_an_optional_amount_is_stored_as_integer_paise(apartment):
    setup_service.create_categories_step(
        apartment, apartment_id=APARTMENT,
        items=[{"name": "Rent", "category": "Housing", "default_amount_paise": 4_500_000},
               {"name": "Water", "category": "Utilities"}])
    from aem.services import definitions

    assert definitions.read_expense_definition(apartment, "rent")["default_amount_paise"] == 4_500_000
    assert definitions.read_expense_definition(apartment, "water")["default_amount_paise"] is None


def test_a_float_amount_is_refused(apartment):
    with pytest.raises(ValidationError, match="integer number of paise"):
        setup_service.create_categories_step(
            apartment, apartment_id=APARTMENT,
            items=[{"name": "Rent", "default_amount_paise": 45000.5}])


def test_finishing_can_open_the_first_statement(db):
    created = setup_service.create_apartment_step(db, name="Flat 402")
    setup_service.create_residents_step(db, apartment_id=created["apartment_id"],
                                        names=["Alice"])
    setup_service.create_categories_step(db, apartment_id=created["apartment_id"],
                                         items=[{"name": "Rent"}])
    result = setup_service.finish_setup(db, apartment_id=created["apartment_id"],
                                        create_statement_for="2026-11")
    assert result["statement_id"]
    assert result["is_complete"] is True
    from aem.services import statements

    assert statements.read_statement(db, result["statement_id"])["status"] == "draft"


def test_finishing_twice_does_not_create_a_second_statement(db):
    created = setup_service.create_apartment_step(db, name="Flat 402")
    setup_service.create_residents_step(db, apartment_id=created["apartment_id"],
                                        names=["Alice"])
    setup_service.create_categories_step(db, apartment_id=created["apartment_id"],
                                         items=[{"name": "Rent"}])
    first = setup_service.finish_setup(db, apartment_id=created["apartment_id"],
                                       create_statement_for="2026-11")
    second = setup_service.finish_setup(db, apartment_id=created["apartment_id"],
                                        create_statement_for="2026-11")
    assert second["statement_id"] == first["statement_id"]
    assert second["statement_count"] == 1


def test_setup_can_be_reopened(db):
    created = setup_service.create_apartment_step(db, name="Flat 402")
    setup_service.create_residents_step(db, apartment_id=created["apartment_id"],
                                        names=["Alice"])
    setup_service.create_categories_step(db, apartment_id=created["apartment_id"],
                                         items=[{"name": "Rent"}])
    setup_service.finish_setup(db, apartment_id=created["apartment_id"])
    assert setup_service.setup_state(db)["is_complete"] is True
    setup_service.reopen_setup(db)
    assert setup_service.setup_state(db)["is_complete"] is False
    assert setup_service.setup_state(db)["next_step"] == "finish"


def test_everything_the_wizard_created_is_audited(db):
    created = setup_service.create_apartment_step(db, name="Flat 402")
    setup_service.create_residents_step(db, apartment_id=created["apartment_id"],
                                        names=["Alice", "Bob"])
    setup_service.create_categories_step(db, apartment_id=created["apartment_id"],
                                         items=[{"name": "Rent"}])
    with connect(db, read_only=True) as conn:
        rows = conn.execute(
            "SELECT entity_type, reason FROM audit_log WHERE reason='guided setup'"
        ).fetchall()
    kinds = {row[0] for row in rows}
    assert {"resident", "expense_definition"} <= kinds


# --------------------------------------------------------------------------- #
# Over HTTP
# --------------------------------------------------------------------------- #

def test_the_whole_wizard_over_http_with_an_interruption(client):
    state = client.get("/api/setup/state").json()
    assert state["next_step"] == "apartment"

    catalogue = client.get("/api/setup/catalogue").json()
    assert catalogue["total"] == len(ESSENTIALS_CATALOGUE)
    assert catalogue["suggested_count"] == len(STARTER_DEFINITIONS)

    step1 = client.post("/api/setup/apartment", json={"name": "Flat 402, Lakeview"})
    assert step1.status_code == 201
    assert step1.json()["apartment_id"] == "flat-402-lakeview"

    # The operator closes the tab. A brand-new request still resumes correctly.
    assert client.get("/api/setup/state").json()["next_step"] == "residents"

    step2 = client.post("/api/setup/residents",
                        json={"names": ["Alice", "", "Bob", "Chris"]})
    assert step2.status_code == 201
    assert step2.json()["created_count"] == 3
    assert step2.json()["state"]["next_step"] == "categories"

    picked = [{k: v for k, v in entry.items() if k not in ("suggested", "hint")}
              for entry in catalogue["items"] if entry["suggested"]]
    picked[0]["default_amount_paise"] = 4_500_000
    picked.append({"name": "Terrace garden", "category": "Household",
                   "period_rule": "transaction_date", "recurrence": "none"})
    step3 = client.post("/api/setup/categories", json={"items": picked})
    assert step3.status_code == 201
    assert step3.json()["created_count"] == len(picked)
    assert step3.json()["state"]["next_step"] == "finish"

    step4 = client.post("/api/setup/finish", json={"create_statement_for": "2026-11"})
    assert step4.status_code == 200
    assert step4.json()["is_complete"] is True

    # The application is immediately usable.
    dashboard = client.get("/api/dashboard", params={"settlement_period": "2026-11"}).json()
    assert dashboard["resident_count"] == 3
    assert dashboard["definition_count"] == len(picked)
    assert dashboard["statement_status"] == "draft"
    recurring = client.get("/api/recurring/preview",
                           params={"settlement_period": "2026-11"}).json()
    assert recurring["eligible_count"] == 1          # only rent has an amount
    assert recurring["eligible_total_paise"] == 4_500_000


def test_the_wizard_refuses_a_blank_apartment_name(client):
    response = client.post("/api/setup/apartment", json={"name": "   "})
    assert response.status_code == 422


def test_setup_state_is_safe_to_poll_before_anything_exists(client):
    for _ in range(3):
        body = client.get("/api/setup/state").json()
        assert body["next_step"] == "apartment"
        assert body["apartment"] is None
        assert body["residents"] == []

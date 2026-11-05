"""House help and cooks: salaries worked out from attendance, split by role, paid through the kitty.

These tests pin what reaches the ledger: each role's salary for the month --
the worker's leave beyond their allowance deducted from each of their roles
pro rata or waived, a bonus added -- split
to the paisa between every resident or only those who opted in, one worker
doing several roles, each worker paid from the kitty or by a resident who is
paid back the rest, and a month saved again moving what was paid to the new
shares until its statement is finalized.
"""
from __future__ import annotations

import pytest

from aem.engine import (
    HouseHelpCalculationError,
    allocate_role_cost,
    calculate_excess_leaves,
    calculate_role_salary,
    consolidate_resident_shares,
    prorate_salary,
)
from aem.errors import ConflictError, NotFoundError, ValidationError
from aem.services import apartments, definitions, househelp, reconciliation, statements
from aem.services import validation

from .conftest import APARTMENT
from .support import connect

MONTH = "2026-11"   # 30 days


@pytest.fixture()
def clock(monkeypatch):
    """It is 10 November 2026."""
    now = {"now": "2026-11-10T09:00:00+05:30"}
    monkeypatch.setattr(househelp, "now_timestamp", lambda value=None: now["now"])
    monkeypatch.setattr(househelp, "_today", lambda: now["now"][:10])
    return now


def _rows(db, sql, params=()):
    with connect(db, read_only=True) as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _worker(db, name="Sunita", **fields):
    return househelp.create_worker(db, APARTMENT, {
        "name": name, "joined_date": "2026-01-01", **fields})["worker_id"]


def _role(db, worker_id, role_name, salary, split="equal", **fields):
    return househelp.create_role(db, APARTMENT, {
        "worker_id": worker_id, "role_name": role_name, "monthly_salary_paise": salary,
        "split_method": split, **fields})["role_id"]


def _save(db, *records, month=MONTH):
    return househelp.save_monthly_attendance(
        db, APARTMENT, month, [{"role_id": role_id, **fields} for role_id, fields in records])


def _month(db, month=MONTH):
    return househelp.get_month_overview(db, APARTMENT, month)


def _shares(overview):
    return {row["resident_id"]: row["total_paise"] for row in overview["shares"]}


def _standing(db, month=MONTH):
    return {row["resident_id"]: (row["total_paise"], row["paid_paise"],
                                 row["outstanding_paise"], row["status"])
            for row in _month(db, month)["shares"]}


def _workers(overview):
    return {row["name"]: (row["total_due_paise"], row["paid_to_worker_paise"],
                          row["still_owed_paise"], row["payout_status"])
            for row in overview["workers"]}


def _pay_in(db, resident, paise, month=MONTH):
    return househelp.record_payment(db, APARTMENT, {
        "settlement_month": month, "resident_id": resident, "amount_paise": paise,
        "payment_date": f"{month}-08", "payment_mode": "upi", "reference": "UPI-1"})


def _pay_worker(db, worker_id, mode="pool", month=MONTH, **details):
    return househelp.disburse_worker_payout(db, APARTMENT, {
        "settlement_month": month, "worker_id": worker_id, "disbursement_mode": mode,
        "reference": "UPI-SAL-11", "disbursed_date": f"{month}-07", **details})


def _owed(db, month=MONTH):
    return [(row["recipient_resident_id"], row["net_reimbursement_owed_paise"],
             row["pending_reimbursement_paise"], row["can_reimburse"])
            for row in _month(db, month)["reimbursements"]]


def _clean(db):
    assert validation.validate(db, APARTMENT)["issues"] == []
    net = reconciliation.reconcile(db, apartment_id=APARTMENT)["net_positions_paise_by_account"]
    assert sum(net.values()) == 0


# ---------------------------------------------------------------- the engine --

def test_leave_beyond_the_allowance_costs_the_days_pro_rata_pay():
    assert (calculate_excess_leaves(5, 2), calculate_excess_leaves(2, 2),
            calculate_excess_leaves(0, 2)) == (3, 0, 0)
    # ₹3,000 over 30 days is ₹100 a day; three days beyond the worker's two allowed.
    assert calculate_role_salary(300_000, 3, 30) == (270_000, 30_000)
    assert calculate_role_salary(300_000, 3, 30, "waive") == (300_000, 0)
    assert calculate_role_salary(300_000, 0, 30) == (300_000, 0)
    # The same three days cost a ₹4,500 role ₹150 each.
    assert calculate_role_salary(450_000, 3, 30) == (405_000, 45_000)
    # A bonus is added after the deduction; an agreed deduction replaces the days'.
    assert calculate_role_salary(300_000, 3, 30, "deduct", 50_000) == (320_000, 30_000)
    assert calculate_role_salary(300_000, 3, 30, "deduct", 0, 25_000) == (275_000, 25_000)
    assert calculate_role_salary(300_000, 3, 30, "waive", 0, 25_000) == (300_000, 0)
    # The day's pay is floored to the paisa: ₹5,000 over 31 days is ₹161.29.
    assert calculate_role_salary(500_000, 2, 31) == (467_742, 32_258)
    # Joined on the 15th of a 30-day month: 16 days' pay; a day off still costs a whole day's.
    assert prorate_salary(300_000, 16, 30) == 160_000
    assert prorate_salary(500_000, 17, 31) == 274_193
    assert calculate_role_salary(300_000, 0, 30, days_active=16) == (160_000, 0)
    assert calculate_role_salary(300_000, 1, 30, days_active=16) == (150_000, 10_000)
    for bad in [dict(days_active=0), dict(days_active=31), dict(excess_days=17)]:
        with pytest.raises(HouseHelpCalculationError):
            calculate_role_salary(**{"base_salary_paise": 300_000, "excess_days": 0,
                                     "days_in_month": 30, "days_active": 16, **bad})
    for bad in [dict(excess_days=31), dict(excess_days=-1), dict(excess_leave_action="halve"),
                dict(manual_deduction_paise=300_001), dict(base_salary_paise=0),
                dict(days_in_month=27), dict(bonus_or_extra_paise=-1)]:
        args = {"base_salary_paise": 300_000, "excess_days": 0, "days_in_month": 30, **bad}
        with pytest.raises(HouseHelpCalculationError):
            calculate_role_salary(**args)


def test_a_roles_cost_is_split_to_the_paisa_between_who_shares_it():
    everyone = ["chris", "alice", "bob"]
    # The paise over go one each in resident-ID order.
    assert allocate_role_cost(100_001, "equal", [], everyone) == {
        "alice": 33_334, "bob": 33_334, "chris": 33_333}
    assert allocate_role_cost(450_001, "opt_in", ["chris", "alice"], everyone) == {
        "alice": 225_001, "chris": 225_000}
    assert allocate_role_cost(0, "opt_in", [], everyone) == {}
    with pytest.raises(HouseHelpCalculationError):
        allocate_role_cost(100_000, "opt_in", [], everyone)
    with pytest.raises(HouseHelpCalculationError):
        allocate_role_cost(100_000, "opt_in", ["dave"], everyone)
    with pytest.raises(HouseHelpCalculationError):
        allocate_role_cost(100_000, "by_weight", [], everyone)


def test_each_residents_bill_is_their_parts_of_every_role():
    everyone = ["alice", "bob", "chris"]
    cleaning = allocate_role_cost(300_000, "equal", [], everyone)
    cooking = allocate_role_cost(450_000, "opt_in", ["alice", "bob"], everyone)
    assert consolidate_resident_shares([cleaning, cooking], everyone) == {
        "alice": 325_000, "bob": 325_000, "chris": 100_000}
    assert consolidate_resident_shares([], everyone) == {"alice": 0, "bob": 0, "chris": 0}
    with pytest.raises(HouseHelpCalculationError):
        consolidate_resident_shares([{"dave": 1}], everyone)


# ------------------------------------------------------- workers and roles --

def test_a_worker_and_their_roles_are_set_up(apartment, clock):
    worker = househelp.create_worker(apartment, APARTMENT, {
        "name": "Sunita", "phone": "98100 12345", "upi_id": "sunita@okaxis",
        "allowed_leaves_per_month": 4, "joined_date": "2026-03-01"})
    assert (worker["name"], worker["phone"], worker["upi_id"], worker["is_active"],
            worker["allowed_leaves_per_month"], worker["joined_date"], worker["roles"]) == (
        "Sunita", "98100 12345", "sunita@okaxis", True, 4, "2026-03-01", [])
    # The household's house help is set up with its first worker.
    service = _rows(apartment, "SELECT * FROM household_service")
    assert [(row["service_type"], row["display_name"]) for row in service] == [
        ("domestic_help", "House Help")]
    definition = _rows(apartment, "SELECT d.* FROM expense_definition d JOIN "
                                  "househelp_service_config c USING (expense_definition_id)")[0]
    assert (definition["name"], definition["category"], definition["is_recurring"]) == (
        "House help", "Household help", 0)

    cleaning = _role(apartment, worker["worker_id"], "Cleaning", 300_000)
    cooking = househelp.create_role(apartment, APARTMENT, {
        "worker_id": worker["worker_id"], "role_name": "Cooking",
        "monthly_salary_paise": 450_000, "split_method": "opt_in",
        "participant_ids": ["bob", "alice"]})
    assert (cooking["participant_ids"], [row["display_name"] for row in cooking["participants"]],
            "allowed_leaves_per_month" in cooking) == (["alice", "bob"], ["Alice", "Bob"], False)
    _worker(apartment, "Ramesh")
    workers = househelp.list_workers(apartment, APARTMENT)
    assert [(row["name"], row["allowed_leaves_per_month"],
             [role["role_name"] for role in row["roles"]]) for row in workers] == [
        ("Ramesh", 2, []), ("Sunita", 4, ["Cleaning", "Cooking"])]
    assert len(_rows(apartment, "SELECT 1 FROM household_service")) == 1

    # Only what is sent changes; a worker who has left is inactive.
    left = househelp.update_worker(apartment, APARTMENT, worker["worker_id"],
                                   {"is_active": False, "phone": None,
                                    "allowed_leaves_per_month": 3})
    assert (left["is_active"], left["phone"], left["upi_id"],
            left["allowed_leaves_per_month"]) == (False, None, "sunita@okaxis", 3)
    assert [row["name"] for row in househelp.list_workers(apartment, APARTMENT,
                                                          include_inactive=False)] == ["Ramesh"]
    raised = househelp.update_role(apartment, APARTMENT, cleaning,
                                   {"monthly_salary_paise": 320_000})
    assert (raised["role_name"], raised["monthly_salary_paise"]) == ("Cleaning", 320_000)
    assert househelp.set_role_participants(apartment, APARTMENT, cooking["role_id"],
                                           ["chris"])["participant_ids"] == ["chris"]

    for bad in [{"name": " "}, {"upi_id": "not-a-upi"}, {"is_active": "yes"},
                {"allowed_leaves_per_month": -1}, {"allowed_leaves_per_month": 32},
                {"joined_date": None}, {"joined_date": "2026-02-30"}]:
        with pytest.raises(ValidationError):
            househelp.create_worker(apartment, APARTMENT,
                                    {"name": "Geeta", "joined_date": "2026-01-01", **bad})
    for bad in [{"monthly_salary_paise": 0}, {"split_method": "weighted"}, {"role_name": ""}]:
        with pytest.raises(ValidationError):
            househelp.create_role(apartment, APARTMENT, {
                "worker_id": worker["worker_id"], "role_name": "Ironing",
                "monthly_salary_paise": 80_000, **bad})
    with pytest.raises(ValidationError) as stranger:
        househelp.set_role_participants(apartment, APARTMENT, cleaning, ["zed"])
    assert stranger.value.code == "househelp_participant_not_resident"
    with pytest.raises(NotFoundError):
        _role(apartment, "hhworker_nobody", "Ironing", 80_000)


def test_a_role_waits_for_its_helper(apartment, clock):
    # Cooking is set up before anyone is hired to do it.
    cooking = househelp.create_role(apartment, APARTMENT, {
        "role_name": "Cooking", "monthly_salary_paise": 450_000})
    assert (cooking["worker_id"], cooking["worker_name"]) == (None, None)
    assert househelp.list_staff(apartment, APARTMENT) == {
        "items": [], "unassigned_roles": [cooking]}
    sunita = _worker(apartment)
    cleaning = _role(apartment, sunita, "Cleaning", 300_000)
    staff = househelp.list_staff(apartment, APARTMENT)
    assert ([(row["name"], [role["role_name"] for role in row["roles"]])
             for row in staff["items"]],
            [role["role_name"] for role in staff["unassigned_roles"]]) == (
        [("Sunita", ["Cleaning"])], ["Cooking"])

    # Nobody to pay: it joins no month.
    preview = _month(apartment)
    assert ([role["role_name"] for role in preview["roles"]], preview["total_paise"],
            preview["unassigned_roles"]) == (
        ["Cleaning"], 300_000,
        [{"role_id": cooking["role_id"], "role_name": "Cooking",
          "monthly_salary_paise": 450_000}])
    with pytest.raises(ConflictError) as nobody:
        _save(apartment, (cleaning, {}), (cooking["role_id"], {}))
    assert nobody.value.code == "househelp_role_has_no_worker"
    assert _save(apartment, (cleaning, {}))["roles_not_on_month"] == []

    # Given a helper, it can join the month; until it has, it can go back to nobody.
    given = househelp.update_role(apartment, APARTMENT, cooking["role_id"],
                                  {"worker_id": sunita})
    assert given["worker_name"] == "Sunita"
    month = _month(apartment)
    assert ([role["role_name"] for role in month["roles_not_on_month"]],
            month["unassigned_roles"]) == (["Cooking"], [])
    back = househelp.update_role(apartment, APARTMENT, cooking["role_id"], {"worker_id": None})
    assert (back["worker_id"], back["worker_name"]) == (None, None)
    with pytest.raises(ConflictError) as saved:
        househelp.update_role(apartment, APARTMENT, cleaning, {"worker_id": None})
    assert saved.value.code == "househelp_role_has_months"
    left = househelp.delete_role(apartment, APARTMENT, cooking["role_id"])
    assert (len(left["workers"]), left["unassigned_roles"]) == (1, [])


def test_the_households_own_house_help_definition_is_used(apartment, clock):
    definitions.create_expense_definition(
        apartment, apartment_id=APARTMENT, definition_id="maid", name="House help",
        category="Household help", period_rule="current_month", recurrence="monthly")
    _worker(apartment)
    assert househelp.ensure_service(apartment, APARTMENT)["expense_definition_id"] == "maid"
    assert len(_rows(apartment, "SELECT 1 FROM expense_definition "
                                "WHERE category='Household help'")) == 1


# ---------------------------------------------------------- the month's bill --

def test_one_worker_with_two_roles_each_split_its_own_way(apartment, clock):
    sunita = _worker(apartment)
    cleaning = _role(apartment, sunita, "Cleaning", 300_000)
    cooking = _role(apartment, sunita, "Cooking", 450_000, "opt_in",
                    participant_ids=["alice", "bob"])
    month = _save(apartment, (cleaning, {}), (cooking, {}))

    # All three share the cleaning; Alice and Bob the cooking.
    assert (month["bill_state"], month["saved"], month["total_paise"]) == ("live", True, 750_000)
    assert _shares(month) == {"alice": 325_000, "bob": 325_000, "chris": 100_000}
    assert [(row["role_name"], row["final_salary_paise"], row["shares"])
            for row in month["roles"]] == [
        ("Cleaning", 300_000, {"alice": 100_000, "bob": 100_000, "chris": 100_000}),
        ("Cooking", 450_000, {"alice": 225_000, "bob": 225_000})]
    alice = next(row for row in month["shares"] if row["resident_id"] == "alice")
    assert [(row["role_name"], row["worker_name"], row["share_paise"])
            for row in alice["breakdown"]] == [("Cleaning", "Sunita", 100_000),
                                               ("Cooking", "Sunita", 225_000)]
    # One worker, one salary: both roles, paid as one.
    assert _workers(month) == {"Sunita": (750_000, 0, 750_000, "pending")}

    expense_id = month["expense"]["expense_id"]
    assert [(row["name"], row["amount_paise"]) for row in _rows(
        apartment, "SELECT * FROM expense_component WHERE expense_id=%s ORDER BY row_seq",
        (expense_id,))] == [("Cleaning (Sunita)", 300_000), ("Cooking (Sunita)", 450_000)]
    assert _standing(apartment)["chris"] == (100_000, 0, 100_000, "unpaid")
    _clean(apartment)


def test_excess_leave_is_deducted_or_waived_and_saving_again_changes_the_bill(apartment,
                                                                             clock):
    cleaning = _role(apartment, _worker(apartment), "Cleaning", 300_000)
    # Five days absent, two allowed: three days at ₹100 come off.
    month = _save(apartment, (cleaning, {"days_absent": 5, "extra_notes": "Village visit"}))
    role = month["roles"][0]
    assert (role["days_absent"], role["excess_leaves"], role["deduction_paise"],
            role["final_salary_paise"], role["extra_notes"]) == (5, 3, 30_000, 270_000,
                                                                 "Village visit")
    assert _shares(month) == {"alice": 90_000, "bob": 90_000, "chris": 90_000}
    expense_id = month["expense"]["expense_id"]

    # Waived, the full salary: the same bill, changed in place.
    month = _save(apartment, (cleaning, {"days_absent": 5, "excess_leave_action": "waive"}))
    assert (month["expense"]["expense_id"], month["total_paise"],
            month["roles"][0]["deduction_paise"]) == (expense_id, 300_000, 0)
    assert _shares(month) == {"alice": 100_000, "bob": 100_000, "chris": 100_000}

    # An agreed deduction and a Diwali bonus; the paise over go to Alice.
    month = _save(apartment, (cleaning, {"days_absent": 5, "deduction_paise": 25_000,
                                         "bonus_or_extra_paise": 20_000}))
    assert (month["roles"][0]["deduction_paise"], month["roles"][0]["bonus_or_extra_paise"],
            month["total_paise"]) == (25_000, 20_000, 295_000)
    assert _shares(month) == {"alice": 98_334, "bob": 98_333, "chris": 98_333}
    record = _rows(apartment, "SELECT * FROM househelp_monthly_record")
    assert [(row["days_absent"], row["deduction_paise"], row["final_salary_paise"])
            for row in record] == [(5, 25_000, 295_000)]

    with pytest.raises(ValidationError):
        _save(apartment, (cleaning, {"days_absent": 31}))
    with pytest.raises(ValidationError):
        _save(apartment, (cleaning, {"excess_leave_action": "halve"}))
    with pytest.raises(ValidationError) as twice:
        _save(apartment, (cleaning, {}), (cleaning, {}))
    assert twice.value.code == "househelp_duplicate_role"
    _clean(apartment)


def test_a_workers_leave_is_theirs_across_every_role_they_do(apartment, clock):
    sunita = _worker(apartment, allowed_leaves_per_month=1)
    cleaning = _role(apartment, sunita, "Cleaning", 300_000)
    cooking = _role(apartment, sunita, "Cooking", 450_000, "opt_in",
                    participant_ids=["alice", "bob"])
    # Away four days, one allowed: three days off each role, at that role's daily pay.
    month = _save(apartment, (cleaning, {"days_absent": 4}),
                  (cooking, {"days_absent": 4, "bonus_or_extra_paise": 10_000}))
    assert [(row["role_name"], row["allowed_leaves_per_month"], row["excess_leaves"],
             row["deduction_paise"], row["final_salary_paise"]) for row in month["roles"]] == [
        ("Cleaning", 1, 3, 30_000, 270_000), ("Cooking", 1, 3, 45_000, 415_000)]
    assert _workers(month)["Sunita"][0] == 685_000

    # Waived, for every role.
    month = _save(apartment, (cleaning, {"days_absent": 4, "excess_leave_action": "waive"}),
                  (cooking, {"days_absent": 4, "excess_leave_action": "waive"}))
    assert [row["deduction_paise"] for row in month["roles"]] == [0, 0]

    # Her days are hers: her roles cannot disagree about them.
    for cooking_entry in [{"days_absent": 2}, {"days_absent": 4, "excess_leave_action": "waive"}]:
        with pytest.raises(ValidationError) as differs:
            _save(apartment, (cleaning, {"days_absent": 4}), (cooking, cooking_entry))
        assert (differs.value.code, differs.value.details["worker_id"]) == (
            "househelp_worker_attendance_differs", sunita)

    # A new allowance reaches the month when it is saved again.
    househelp.update_worker(apartment, APARTMENT, sunita, {"allowed_leaves_per_month": 4})
    month = _save(apartment, (cleaning, {"days_absent": 4}), (cooking, {"days_absent": 4}))
    assert ([row["deduction_paise"] for row in month["roles"]], month["total_paise"]) == (
        [0, 0], 750_000)
    _clean(apartment)


def test_migration_0036_gives_each_worker_the_largest_allowance_their_roles_had(
        database_factory):
    db = database_factory(migrated=False)
    db.migrate(target_version=35)
    apartments.create_apartment(db, apartment_id=APARTMENT, name="Flat 402")
    stamp = "2026-11-01T10:00:00+05:30"
    with connect(db) as conn:
        for worker_id in ("sunita", "ramesh", "geeta"):
            conn.execute("""INSERT INTO househelp_worker (worker_id, apartment_id, name,
                                is_active, created_at, updated_at)
                            VALUES (%s, %s, %s, TRUE, %s, %s)""",
                         (worker_id, APARTMENT, worker_id.title(), stamp, stamp))
        for role_id, worker_id, leaves in (("cleaning", "sunita", 2), ("cooking", "sunita", 4),
                                           ("bathrooms", "ramesh", 0),
                                           ("ironing", None, 6)):
            conn.execute("""INSERT INTO househelp_role (role_id, worker_id, apartment_id,
                                role_name, monthly_salary_paise, allowed_leaves_per_month,
                                split_method, created_at)
                            VALUES (%s, %s, %s, %s, 100000, %s, 'equal', %s)""",
                         (role_id, worker_id, APARTMENT, role_id.title(), leaves, stamp))
    db.migrate()
    assert {row["worker_id"]: row["allowed_leaves_per_month"] for row in _rows(
        db, "SELECT * FROM househelp_worker")} == {"sunita": 4, "ramesh": 0, "geeta": 2}
    assert "allowed_leaves_per_month" not in _rows(db, "SELECT * FROM househelp_role")[0]


def test_paise_that_do_not_divide_still_add_up_to_the_bill(apartment, clock):
    sunita, ramesh = _worker(apartment), _worker(apartment, "Ramesh")
    cleaning = _role(apartment, sunita, "Cleaning", 500_000)
    cooking = _role(apartment, ramesh, "Cooking", 450_001, "opt_in",
                    participant_ids=["alice", "chris"])
    # October has 31 days: four absent, two allowed, at ₹161.29 a day; and a paisa's bonus.
    month = _save(apartment, (cleaning, {"days_absent": 4, "bonus_or_extra_paise": 1}),
                  (cooking, {}), month="2026-10")
    assert [(row["role_name"], row["deduction_paise"], row["final_salary_paise"])
            for row in month["roles"]] == [("Cooking", 0, 450_001),
                                           ("Cleaning", 32_258, 467_743)]
    assert _shares(month) == {"alice": 380_916, "bob": 155_914, "chris": 380_914}
    assert sum(_shares(month).values()) == month["total_paise"] == 917_744
    for share in month["shares"]:
        assert sum(row["share_paise"] for row in share["breakdown"]) == share["total_paise"]
    charged = _rows(apartment, "SELECT SUM(amount_paise) AS total FROM allocation "
                               "WHERE expense_id=%s", (month["expense"]["expense_id"],))
    assert charged[0]["total"] == 917_744
    _clean(apartment)


def test_a_worker_is_paid_from_the_day_they_joined(apartment, clock):
    sunita = _worker(apartment)
    cleaning = _role(apartment, sunita, "Cleaning", 300_000)
    ramesh = _worker(apartment, "Ramesh", joined_date="2026-11-15")
    cooking = _role(apartment, ramesh, "Cooking", 450_000)

    # October was before Ramesh joined: only Sunita's cleaning is on it.
    october = _month(apartment, "2026-10")
    assert [row["role_name"] for row in october["roles"]] == ["Cleaning"]
    with pytest.raises(ConflictError) as early:
        _save(apartment, (cleaning, {}), (cooking, {}), month="2026-10")
    assert early.value.code == "househelp_worker_not_joined"

    # He joined on the 15th: 16 of November's 30 days, and his salary for them.
    month = _save(apartment, (cleaning, {}), (cooking, {"days_absent": 3}))
    assert [(row["role_name"], row["pro_rated_days"], row["base_salary_paise"],
             row["deduction_paise"], row["final_salary_paise"]) for row in month["roles"]] == [
        ("Cooking", 16, 240_000, 15_000, 225_000), ("Cleaning", None, 300_000, 0, 300_000)]
    assert _workers(month)["Ramesh"][0] == 225_000
    assert sum(_shares(month).values()) == month["total_paise"] == 525_000
    # He cannot have been away for more than the days he was there.
    with pytest.raises(ValidationError) as away:
        _save(apartment, (cleaning, {}), (cooking, {"days_absent": 17}))
    assert away.value.code == "househelp_absent_before_joining"

    # December is his in full.
    assert [(row["role_name"], row["pro_rated_days"], row["base_salary_paise"])
            for row in _month(apartment, "2026-12")["roles"]] == [
        ("Cooking", None, 450_000), ("Cleaning", None, 300_000)]

    # Said to have joined on the 1st, November is his in full once saved again.
    househelp.update_worker(apartment, APARTMENT, ramesh, {"joined_date": "2026-11-01"})
    month = _save(apartment, (cleaning, {}), (cooking, {}))
    assert [(row["pro_rated_days"], row["final_salary_paise"]) for row in month["roles"]] == [
        (None, 450_000), (None, 300_000)]
    _clean(apartment)


def test_an_open_month_is_saved_and_billed_as_it_is_first_looked_at(apartment, clock):
    sunita = _worker(apartment)
    cleaning = _role(apartment, sunita, "Cleaning", 300_000)
    gone = _worker(apartment, "Ramesh")
    _role(apartment, gone, "Car wash", 60_000)
    househelp.update_worker(apartment, APARTMENT, gone, {"is_active": False})

    # Nobody absent, at full salary: billed like any month, by the default saver.
    month = _month(apartment)
    assert (month["bill_state"], month["saved"], month["total_paise"]) == ("live", True, 300_000)
    assert [(row["role_name"], row["days_absent"], row["final_salary_paise"])
            for row in month["roles"]] == [("Cleaning", 0, 300_000)]
    assert _shares(month) == {"alice": 100_000, "bob": 100_000, "chris": 100_000}
    assert [row["actor"] for row in _rows(
        apartment, "SELECT actor FROM audit_log WHERE action='attendance'")] == [
        househelp.AUTO_SAVE_ACTOR]
    # Looked at again: the same bill.
    _month(apartment)
    assert len(_rows(apartment, "SELECT 1 FROM expense_instance")) == 1

    # A month saved stays as it was saved.
    _save(apartment, (cleaning, {"days_absent": 5}))
    assert _month(apartment)["roles"][0]["days_absent"] == 5

    # October came before any role was set up, and December has not begun: neither is billed.
    for other in ("2026-10", "2026-12"):
        assert (_month(apartment, other)["saved"], _month(apartment, other)["expense"]) == (
            False, None)
    assert len(_rows(apartment, "SELECT 1 FROM expense_instance")) == 1
    _clean(apartment)


def test_a_month_not_saved_is_a_preview_and_an_opt_in_role_needs_its_sharers(apartment, clock):
    sunita = _worker(apartment)
    cleaning = _role(apartment, sunita, "Cleaning", 300_000)
    cooking = _role(apartment, sunita, "Cooking", 450_000, "opt_in")
    gone = _worker(apartment, "Ramesh")
    _role(apartment, gone, "Car wash", 60_000)
    househelp.update_worker(apartment, APARTMENT, gone, {"is_active": False})

    # Every active worker's roles at their full salary, nobody absent; nothing billed,
    # as nobody has opted in to the cooking, so it is not saved by default.
    preview = _month(apartment)
    assert (preview["bill_state"], preview["saved"], preview["expense"],
            preview["can_save"]) == ("not_billed", False, None, True)
    assert [(row["role_name"], row["final_salary_paise"], row["saved"])
            for row in preview["roles"]] == [("Cleaning", 300_000, False),
                                             ("Cooking", 450_000, False)]
    assert "opted in to Cooking (Sunita)" in preview["blocked_reason"]
    assert not _rows(apartment, "SELECT 1 FROM expense_instance")

    with pytest.raises(ValidationError) as nobody:
        _save(apartment, (cleaning, {}), (cooking, {}))
    assert nobody.value.code == "househelp_role_has_no_participants"
    househelp.set_role_participants(apartment, APARTMENT, cooking, ["bob"])
    month = _save(apartment, (cleaning, {}), (cooking, {}))
    assert _shares(month) == {"alice": 100_000, "bob": 550_000, "chris": 100_000}

    # A month ahead cannot be saved yet.
    december = _month(apartment, "2026-12")
    assert (december["bill_state"], december["can_save"]) == ("upcoming", False)
    with pytest.raises(ConflictError) as early:
        _save(apartment, (cleaning, {}), month="2026-12")
    assert early.value.code == "househelp_month_not_started"


# ------------------------------------------------------------- paying it --

def test_each_worker_is_paid_from_the_kitty_in_tranches_never_beyond_their_salary(apartment,
                                                                                  clock):
    sunita, ramesh = _worker(apartment), _worker(apartment, "Ramesh")
    cleaning = _role(apartment, sunita, "Cleaning", 300_000)
    bathrooms = _role(apartment, ramesh, "Bathrooms", 120_000)
    month = _save(apartment, (cleaning, {}), (bathrooms, {}))
    assert _shares(month) == {"alice": 140_000, "bob": 140_000, "chris": 140_000}

    _pay_in(apartment, "alice", 140_000)
    assert _standing(apartment)["alice"] == (140_000, 140_000, 0, "paid")
    with pytest.raises(ConflictError) as paid:
        _pay_in(apartment, "alice", 1)
    assert paid.value.code == "househelp_share_already_paid"

    # Sunita is paid what the kitty holds now, and the rest later; Ramesh is still owed his.
    month = _pay_worker(apartment, sunita, amount_paise=100_000)
    assert _workers(month) == {"Sunita": (300_000, 100_000, 200_000, "partially_disbursed"),
                               "Ramesh": (120_000, 0, 120_000, "pending")}
    with pytest.raises(ValidationError) as too_much:
        _pay_worker(apartment, sunita, amount_paise=200_001)
    assert too_much.value.code == "tranche_exceeds_remaining"
    _pay_in(apartment, "bob", 140_000)
    _pay_in(apartment, "chris", 140_000)
    _pay_worker(apartment, sunita)
    month = _pay_worker(apartment, ramesh)
    assert _workers(month) == {"Sunita": (300_000, 300_000, 0, "disbursed"),
                               "Ramesh": (120_000, 120_000, 0, "disbursed")}
    sunitas = next(row for row in month["workers"] if row["name"] == "Sunita")
    assert [(row["amount_paise"], row["paid_by"]) for row in sunitas["payouts"]] == [
        (100_000, "kitty"), (200_000, "kitty")]
    assert (month["payout_status"], month["disbursed_paise"]) == ("disbursed", 420_000)
    assert {row["worker_id"] for row in _rows(
        apartment, "SELECT worker_id FROM service_disbursement")} == {sunita, ramesh}
    with pytest.raises(ConflictError) as done:
        _pay_worker(apartment, sunita)
    assert done.value.code == "househelp_worker_already_paid"
    with pytest.raises(ConflictError) as stranger:
        _pay_worker(apartment, _worker(apartment, "Geeta"))
    assert stranger.value.code == "househelp_worker_not_on_month"
    _clean(apartment)


def test_a_resident_who_pays_a_worker_is_paid_back_the_rest_by_the_kitty(apartment, clock):
    sunita = _worker(apartment)
    cleaning = _role(apartment, sunita, "Cleaning", 300_000)
    _save(apartment, (cleaning, {}))
    # Bob pays Sunita the whole ₹3,000: his ₹1,000 share through the kitty, ₹2,000 beyond it.
    month = _pay_worker(apartment, sunita, "resident_advance", funding_resident_id="bob")
    assert _workers(month) == {"Sunita": (300_000, 300_000, 0, "disbursed")}
    payout = month["workers"][0]["payouts"]
    assert [(row["amount_paise"], row["paid_by"], row["resident_id"]) for row in payout] == [
        (300_000, "resident", "bob")]
    assert _standing(apartment)["bob"] == (100_000, 100_000, 0, "paid")
    assert _owed(apartment) == [("bob", 200_000, 200_000, False)]
    advance = _rows(apartment, "SELECT * FROM payment WHERE payment_kind='provider_advance'")
    assert [(row["amount_paise"], row["payee_or_provider"]) for row in advance] == [
        (200_000, "Sunita")]

    # The kitty pays him back what Alice and Chris pay in.
    _pay_in(apartment, "alice", 100_000)
    _pay_in(apartment, "chris", 100_000)
    assert _owed(apartment) == [("bob", 200_000, 200_000, True)]
    househelp.reimburse_from_kitty(apartment, APARTMENT, {
        "settlement_month": MONTH, "recipient_resident_id": "bob", "amount_paise": 200_000,
        "payment_date": "2026-11-12"})
    assert _owed(apartment) == [("bob", 200_000, 0, False)]
    with pytest.raises(ValidationError) as more:
        househelp.reimburse_from_kitty(apartment, APARTMENT, {
            "settlement_month": MONTH, "recipient_resident_id": "bob", "amount_paise": 1,
            "payment_date": "2026-11-12"})
    assert more.value.code == "exceeds_pending_reimbursement"
    _clean(apartment)


def test_saving_again_after_payments_moves_them_to_the_new_shares(apartment, clock):
    sunita = _worker(apartment)
    cleaning = _role(apartment, sunita, "Cleaning", 300_000)
    cooking = _role(apartment, sunita, "Cooking", 450_000, "opt_in",
                    participant_ids=["alice", "bob"])
    _save(apartment, (cleaning, {}))
    _pay_in(apartment, "alice", 100_000)

    # The cooking joins the month: Alice's ₹1,000 now stands against a ₹3,250 share.
    month = _save(apartment, (cleaning, {}), (cooking, {}))
    assert _shares(month) == {"alice": 325_000, "bob": 325_000, "chris": 100_000}
    assert _standing(apartment)["alice"] == (325_000, 100_000, 225_000, "partial")

    # Left out again, with five days absent: her share is ₹900, and the ₹100 she
    # paid beyond it is owed back to her from the kitty.
    month = _save(apartment, (cleaning, {"days_absent": 5}))
    assert _shares(month) == {"alice": 90_000, "bob": 90_000, "chris": 90_000}
    assert [row["role_name"] for row in month["roles"]] == ["Cleaning"]
    assert [row["role_name"] for row in month["roles_not_on_month"]] == ["Cooking"]
    assert _standing(apartment)["alice"] == (90_000, 90_000, 0, "paid")
    assert _owed(apartment) == [("alice", 10_000, 10_000, True)]
    _clean(apartment)


def test_a_worker_is_never_owed_less_than_they_have_been_paid(apartment, clock):
    sunita = _worker(apartment)
    cleaning = _role(apartment, sunita, "Cleaning", 300_000)
    _save(apartment, (cleaning, {}))
    _pay_worker(apartment, sunita)

    with pytest.raises(ConflictError) as overpaid:
        _save(apartment, (cleaning, {"days_absent": 5}))
    assert (overpaid.value.code, overpaid.value.details["paid_paise"],
            overpaid.value.details["due_paise"]) == ("househelp_worker_already_paid",
                                                      300_000, 270_000)
    with pytest.raises(ConflictError):
        househelp.save_monthly_attendance(apartment, APARTMENT, MONTH, [])
    # Nothing changed.
    month = _month(apartment)
    assert (month["total_paise"], month["roles"][0]["days_absent"]) == (300_000, 0)
    # What she was paid beyond the days is a bonus.
    month = _save(apartment, (cleaning, {"days_absent": 5, "bonus_or_extra_paise": 30_000}))
    assert _workers(month) == {"Sunita": (300_000, 300_000, 0, "disbursed")}
    _clean(apartment)


def test_deleting_a_role_takes_it_off_the_open_month(apartment, clock):
    sunita = _worker(apartment)
    cleaning = _role(apartment, sunita, "Cleaning", 300_000)
    cooking = _role(apartment, sunita, "Cooking", 450_000, "opt_in",
                    participant_ids=["alice", "bob"])
    _save(apartment, (cleaning, {}), (cooking, {}))
    _pay_in(apartment, "chris", 100_000)

    workers = househelp.delete_role(apartment, APARTMENT, cooking)["workers"]
    assert [role["role_name"] for role in workers[0]["roles"]] == ["Cleaning"]
    month = _month(apartment)
    assert (month["total_paise"], _shares(month)) == (
        300_000, {"alice": 100_000, "bob": 100_000, "chris": 100_000})
    assert _standing(apartment)["chris"] == (100_000, 100_000, 0, "paid")

    # The last role gone, and nothing paid but Chris's share: it stays, so the bill does.
    with pytest.raises(ConflictError) as paid:
        househelp.delete_role(apartment, APARTMENT, cleaning)
    assert paid.value.code == "househelp_bill_has_payments"
    _clean(apartment)


def test_mistakes_are_deleted_while_the_month_is_open(apartment, clock):
    sunita = _worker(apartment)
    cleaning = _role(apartment, sunita, "Cleaning", 300_000)
    _save(apartment, (cleaning, {}))
    month = _pay_in(apartment, "alice", 100_000)
    payment_id = next(row for row in month["shares"]
                      if row["resident_id"] == "alice")["payments"][0]["payment_id"]
    month = househelp.delete_payment(apartment, APARTMENT, payment_id)
    assert month["deleted"]["deleted_payment_ids"] == [payment_id]
    assert _standing(apartment)["alice"] == (100_000, 0, 100_000, "unpaid")

    # A resident's payout to a worker goes whole: their share through the kitty, and the advance.
    month = _pay_worker(apartment, sunita, "resident_advance", funding_resident_id="bob")
    payout_id = month["workers"][0]["payouts"][0]["payout_id"]
    month = househelp.delete_disbursement(apartment, APARTMENT, payout_id)
    assert (len(month["deleted"]["deleted_disbursement_ids"]),
            len(month["deleted"]["deleted_payment_ids"])) == (2, 2)
    assert _workers(month) == {"Sunita": (300_000, 0, 300_000, "pending")}
    assert (_standing(apartment)["bob"], _owed(apartment)) == (
        (100_000, 0, 100_000, "unpaid"), [])
    assert not _rows(apartment, "SELECT 1 FROM payment")

    with pytest.raises(NotFoundError):
        househelp.delete_payment(apartment, APARTMENT, "payment_nobody")
    with pytest.raises(NotFoundError):
        househelp.delete_disbursement(apartment, APARTMENT, "svcdisb_nobody")
    _clean(apartment)


def test_a_finalized_month_is_frozen(apartment, clock):
    sunita = _worker(apartment)
    cleaning = _role(apartment, sunita, "Cleaning", 300_000)
    _save(apartment, (cleaning, {"days_absent": 3}))
    statement = statements.create_monthly_statement(apartment, apartment_id=APARTMENT,
                                                    settlement_month=MONTH)
    statements.finalize_statement(apartment, statement_id=statement, finalized_by="operator",
                                  require_clean=False)
    househelp.update_role(apartment, APARTMENT, cleaning, {"monthly_salary_paise": 350_000})

    month = _month(apartment)
    assert (month["bill_state"], month["can_save"], month["total_paise"],
            month["roles"][0]["base_salary_paise"]) == ("frozen", False, 290_000, 300_000)
    with pytest.raises(ConflictError) as frozen:
        _save(apartment, (cleaning, {}))
    assert frozen.value.code == "househelp_month_finalized"
    with pytest.raises(ConflictError) as kept:
        househelp.delete_role(apartment, APARTMENT, cleaning)
    assert kept.value.code == "househelp_role_on_finalized_month"

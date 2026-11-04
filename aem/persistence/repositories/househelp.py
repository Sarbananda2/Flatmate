"""House help: the workers, their roles, who shares each, and each month's attendance.

The service row is the household's 'domestic_help' service. A month's bill,
its charges and its payouts to the workers are the ones every household
service shares, read through
:class:`~.furniture_rental.FurnitureRentalRepository` (``uow.furniture``); a
payout names its worker (migration 0034). The days off a month before pay is
deducted are the worker's, across every role they do (migration 0036).
"""
from __future__ import annotations

from typing import Any, Sequence

from .base import Repository

#: The household service type house help is (migration 0022).
SERVICE_TYPE = "domestic_help"

#: Columns a caller may change. Anything else is refused, so no caller-supplied
#: name ever reaches the SQL.
WORKER_COLUMNS = ("name", "phone", "upi_id", "notes", "is_active", "allowed_leaves_per_month")
ROLE_COLUMNS = ("worker_id", "role_name", "monthly_salary_paise", "split_method")

#: A role with the worker who does it, and the worker's leave allowance. A role
#: not yet given a worker (migration 0035) has these null.
ROLE_SELECT = """SELECT r.*, w.name AS worker_name, w.is_active AS worker_is_active,
                        w.allowed_leaves_per_month
                   FROM househelp_role r
                   LEFT JOIN househelp_worker w ON w.worker_id = r.worker_id"""

#: A month's record with its role and the worker who does it. A role on a
#: month always has its worker.
RECORD_SELECT = """SELECT m.*, r.worker_id, r.role_name, r.monthly_salary_paise,
                          w.allowed_leaves_per_month, r.split_method, w.name AS worker_name
                     FROM househelp_monthly_record m
                     JOIN househelp_role r ON r.role_id = m.role_id
                     JOIN househelp_worker w ON w.worker_id = r.worker_id"""


def _columns(allowed: Sequence[str], values: dict[str, Any], what: str) -> list[str]:
    columns = [column for column in allowed if column in values]
    if set(values) - set(columns):
        raise ValueError(f"Not {what} columns: {sorted(set(values) - set(columns))}")
    return columns


def _placeholder(column: str) -> str:
    # A Python bool is sent as 0/1 (adapter.py); is_active is a BOOLEAN.
    return "(%s)::int::boolean" if column == "is_active" else "%s"


class HouseHelpRepository(Repository):
    # ------------------------------------------------- the service and its bill --

    def config(self, service_id: str) -> dict[str, Any] | None:
        return self._s.fetch_one(
            "SELECT * FROM househelp_service_config WHERE service_id=%s", (service_id,))

    def insert_config(self, *, service_id: str, apartment_id: str,
                      expense_definition_id: str, created_at: str) -> None:
        self._s.execute(
            """INSERT INTO househelp_service_config (service_id, apartment_id,
                   expense_definition_id, created_at, updated_at)
               VALUES (%s,%s,%s,%s,%s)""",
            (service_id, apartment_id, expense_definition_id, created_at, created_at))

    def find_definition_id(self, apartment_id: str) -> str | None:
        """The active definition called, or with the ID, 'House help'."""
        return self._s.scalar(
            """SELECT expense_definition_id FROM expense_definition
                WHERE apartment_id=%s AND is_active=1
                  AND (lower(trim(name)) IN ('house help', 'househelp', 'domestic help')
                       OR lower(expense_definition_id) IN ('house-help', 'house_help',
                                                           'househelp'))
                ORDER BY sort_order, expense_definition_id LIMIT 1""", (apartment_id,))

    def definition_id_taken(self, definition_id: str) -> bool:
        return self._s.fetch_one(
            "SELECT 1 AS present FROM expense_definition WHERE expense_definition_id=%s",
            (definition_id,)) is not None

    def replace_components(self, expense_id: str, components: list[dict[str, Any]]) -> None:
        """The bill's components, one per role. Each resident's charge is the whole
        of what they owe on the bill, so no charge belongs to one of them."""
        self._s.execute("UPDATE allocation SET component_id=NULL WHERE expense_id=%s",
                        (expense_id,))
        self._s.execute("DELETE FROM expense_component WHERE expense_id=%s", (expense_id,))
        for component in components:
            self._s.execute(
                """INSERT INTO expense_component (component_id, expense_id, name,
                       amount_paise, allocation_method)
                   VALUES (%s,%s,%s,%s,'custom')""",
                (component["component_id"], expense_id, component["name"],
                 component["amount_paise"]))

    # ------------------------------------------------------------- the workers --

    def workers(self, apartment_id: str) -> list[dict[str, Any]]:
        """Every worker, the active first, then by name."""
        return self._s.fetch_all(
            """SELECT * FROM househelp_worker WHERE apartment_id=%s
                ORDER BY is_active DESC, lower(name), worker_id""", (apartment_id,))

    def worker(self, worker_id: str) -> dict[str, Any] | None:
        return self._s.fetch_one("SELECT * FROM househelp_worker WHERE worker_id=%s",
                                 (worker_id,))

    def insert_worker(self, *, worker_id: str, apartment_id: str, name: str,
                      phone: str | None, upi_id: str | None, notes: str | None,
                      is_active: bool, allowed_leaves_per_month: int, created_at: str) -> None:
        self._s.execute(
            """INSERT INTO househelp_worker (worker_id, apartment_id, name, phone, upi_id,
                   notes, is_active, allowed_leaves_per_month, created_at, updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,(%s)::int::boolean,%s,%s,%s)""",
            (worker_id, apartment_id, name, phone, upi_id, notes, is_active,
             allowed_leaves_per_month, created_at, created_at))

    def update_worker(self, worker_id: str, *, updated_at: str, **values: Any) -> None:
        columns = _columns(WORKER_COLUMNS, values, "worker")
        sets = "".join(f", {column}={_placeholder(column)}" for column in columns)
        self._s.execute(
            f"UPDATE househelp_worker SET updated_at=%s{sets} WHERE worker_id=%s",
            (updated_at, *(values[c] for c in columns), worker_id))

    # --------------------------------------------------------------- the roles --

    def roles(self, apartment_id: str) -> list[dict[str, Any]]:
        """Every role, by worker then role name; those with no worker last."""
        return self._s.fetch_all(
            f"""{ROLE_SELECT} WHERE r.apartment_id=%s
                ORDER BY lower(w.name), w.worker_id, lower(r.role_name), r.role_id""",
            (apartment_id,))

    def role(self, role_id: str) -> dict[str, Any] | None:
        return self._s.fetch_one(f"{ROLE_SELECT} WHERE r.role_id=%s", (role_id,))

    def insert_role(self, *, role_id: str, worker_id: str | None, apartment_id: str,
                    role_name: str, monthly_salary_paise: int, split_method: str,
                    created_at: str) -> None:
        self._s.execute(
            """INSERT INTO househelp_role (role_id, worker_id, apartment_id, role_name,
                   monthly_salary_paise, split_method, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (role_id, worker_id, apartment_id, role_name, monthly_salary_paise, split_method,
             created_at))

    def update_role(self, role_id: str, **values: Any) -> None:
        columns = _columns(ROLE_COLUMNS, values, "role")
        if not columns:
            return
        sets = ", ".join(f"{column}=%s" for column in columns)
        self._s.execute(f"UPDATE househelp_role SET {sets} WHERE role_id=%s",
                        (*(values[c] for c in columns), role_id))

    def delete_role(self, role_id: str) -> int:
        """A role, its participants and its months' records with it."""
        return self._s.execute("DELETE FROM househelp_role WHERE role_id=%s", (role_id,))

    # -------------------------------------------------------- who shares a role --

    def participants(self, apartment_id: str) -> dict[str, list[str]]:
        """The residents opted in to each role, by role, sorted."""
        found: dict[str, list[str]] = {}
        for row in self._s.fetch_all(
                """SELECT role_id, resident_id FROM househelp_role_participant
                    WHERE apartment_id=%s ORDER BY role_id, resident_id""", (apartment_id,)):
            found.setdefault(row["role_id"], []).append(row["resident_id"])
        return found

    def replace_participants(self, role_id: str, apartment_id: str,
                             resident_ids: Sequence[str]) -> None:
        self._s.execute("DELETE FROM househelp_role_participant WHERE role_id=%s", (role_id,))
        for resident_id in resident_ids:
            self._s.execute(
                """INSERT INTO househelp_role_participant (role_id, resident_id, apartment_id)
                   VALUES (%s,%s,%s)""", (role_id, resident_id, apartment_id))

    # -------------------------------------------------------- a month's records --

    def records_for_month(self, apartment_id: str, settlement_month: str) -> list[dict[str, Any]]:
        """The month's attendance, a record per role, by worker then role name."""
        return self._s.fetch_all(
            f"""{RECORD_SELECT} WHERE m.apartment_id=%s AND m.settlement_month=%s
                ORDER BY lower(w.name), w.worker_id, lower(r.role_name), r.role_id""",
            (apartment_id, settlement_month))

    def months_for_role(self, role_id: str) -> list[str]:
        """Every month the role has a record for, oldest first."""
        return [row["settlement_month"] for row in self._s.fetch_all(
            """SELECT settlement_month FROM househelp_monthly_record WHERE role_id=%s
                ORDER BY settlement_month""", (role_id,))]

    def upsert_record(self, *, record_id: str, role_id: str, settlement_month: str,
                      apartment_id: str, days_absent: int, excess_leave_action: str,
                      deduction_paise: int, bonus_or_extra_paise: int,
                      extra_notes: str | None, final_salary_paise: int) -> None:
        """The role's record for the month, written over the one there was."""
        self._s.execute(
            """INSERT INTO househelp_monthly_record (record_id, role_id, settlement_month,
                   apartment_id, days_absent, excess_leave_action, deduction_paise,
                   bonus_or_extra_paise, extra_notes, final_salary_paise)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (role_id, settlement_month) DO UPDATE SET
                   days_absent=EXCLUDED.days_absent,
                   excess_leave_action=EXCLUDED.excess_leave_action,
                   deduction_paise=EXCLUDED.deduction_paise,
                   bonus_or_extra_paise=EXCLUDED.bonus_or_extra_paise,
                   extra_notes=EXCLUDED.extra_notes,
                   final_salary_paise=EXCLUDED.final_salary_paise""",
            (record_id, role_id, settlement_month, apartment_id, days_absent,
             excess_leave_action, deduction_paise, bonus_or_extra_paise, extra_notes,
             final_salary_paise))

    def delete_record(self, role_id: str, settlement_month: str) -> int:
        return self._s.execute(
            "DELETE FROM househelp_monthly_record WHERE role_id=%s AND settlement_month=%s",
            (role_id, settlement_month))

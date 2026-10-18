"""Shared fixtures.

`household` builds a realistic multi-month household: three residents, rent and
broadband generated from recurring definitions, groceries under a
previous-month rule, an electricity bill funded by a linked provider advance,
partial payments, an overpayment, and a post-close adjustment. The accounting
invariants are asserted against *that*, not against a single tidy expense.

Every fixture hands tests a ``Database`` -- the handle services take -- backed
by a throwaway, freshly migrated schema in the PostgreSQL *test* database. The
schema is built by the migration role; the handle serves as the runtime role,
which cannot change it. See
``tests/support.py``. The test database must be running
(``./scripts/local_postgres.sh start``); the suite never touches any other.
"""
from __future__ import annotations

import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterator

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aem.config import get_settings  # noqa: E402
from aem.persistence import Database  # noqa: E402
from aem.services import (  # noqa: E402
    adjustments,
    apartments,
    definitions,
    electricity,
    expenses,
    payments,
    recurring,
    residents,
    statements,
)

from tests import support  # noqa: E402

APARTMENT = "home"
RESIDENTS = (("alice", "Alice"), ("bob", "Bob"), ("chris", "Chris"))


@pytest.fixture(autouse=True)
def cheap_password_hashing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hash test passwords at a far lower scrypt cost than the real one, which
    is deliberately slow. Hashes record their own cost, so they still verify;
    tests/test_auth.py checks the real cost on its own."""
    from aem.services import auth

    monkeypatch.setattr(auth, "SCRYPT_N", 1024)
    auth._decoy_hash.cache_clear()


@pytest.fixture(scope="session")
def test_database_config() -> support.TestTarget:
    """Where the test database is, for both roles. Checked once: reachable, and
    really a test database."""
    try:
        return support.test_config()
    except RuntimeError as exc:
        pytest.exit(str(exc), returncode=2)


@pytest.fixture()
def database_factory(
        test_database_config: support.TestTarget) -> Iterator[Callable[..., Database]]:
    """Make further isolated databases inside a test: ``database_factory()`` for
    a migrated one, ``database_factory(migrated=False)`` for an empty one. All
    are dropped when the test ends."""
    made: list[Database] = []

    def make(*, migrated: bool = True) -> Database:
        database = support.new_database(test_database_config, migrated=migrated)
        made.append(database)
        return database

    yield make
    for database in made:
        support.drop_database(database)


@pytest.fixture()
def db(database_factory: Callable[..., Database]) -> Database:
    """An empty, migrated database."""
    return database_factory()


@pytest.fixture()
def apartment(db: Database) -> Database:
    """A database with one apartment and three active residents."""
    apartments.create_apartment(db, apartment_id=APARTMENT, name="Flat 402")
    for resident_id, name in RESIDENTS:
        residents.create_resident(db, apartment_id=APARTMENT, resident_id=resident_id,
                                  display_name=name, active_from="2026-08-01")
    return db


@pytest.fixture()
def configured(apartment: Database) -> Database:
    """Expense definitions, with the period rules the household actually uses."""
    db = apartment
    definitions.create_expense_definition(
        db, apartment_id=APARTMENT, definition_id="rent", name="Rent", category="Housing",
        period_rule="current_month", default_allocation_method="equal",
        recurrence="monthly", default_amount_paise=4_500_000, day_of_month=1,
        default_provider_name="Landlord")
    definitions.create_expense_definition(
        db, apartment_id=APARTMENT, definition_id="broadband", name="Broadband",
        category="Utilities", period_rule="current_month",
        default_allocation_method="equal", recurrence="monthly",
        default_amount_paise=120_000, day_of_month=5)
    definitions.create_expense_definition(
        db, apartment_id=APARTMENT, definition_id="electricity", name="Electricity",
        category="Utilities", period_rule="previous_month",
        default_allocation_method="measured", recurrence="none")
    definitions.create_expense_definition(
        db, apartment_id=APARTMENT, definition_id="groceries", name="Groceries",
        category="Food", period_rule="previous_month", default_allocation_method="equal",
        recurrence="none")
    definitions.create_expense_definition(
        db, apartment_id=APARTMENT, definition_id="maid", name="Maid",
        category="Household help", period_rule="current_month",
        default_allocation_method="weighted", recurrence="monthly",
        default_amount_paise=300_000, default_weights={"alice": 2, "bob": 1, "chris": 1})
    return db


@pytest.fixture()
def household(configured: Database) -> Database:
    """Two settled-up months with the awkward cases in them.

    September: rent and broadband generated; groceries (previous-month rule) paid
    by Alice; the August electricity bill funded by a provider advance linked to
    it; Chris pays in and overpays; the statement is finalized and closed, then
    corrected by a signed adjustment.

    October: rent and broadband generated again, plus an unattached repair.
    """
    db = configured

    september = statements.create_monthly_statement(
        db, apartment_id=APARTMENT, settlement_month="2026-09", statement_id="stmt-2026-09")
    recurring.generate(
        db, apartment_id=APARTMENT, settlement_period="2026-09", statement_id=september,
        lines=[{"expense_definition_id": "rent"}, {"expense_definition_id": "broadband"},
               {"expense_definition_id": "maid"}])

    # Groceries: previous_month rule -> August service, September settlement.
    expenses.create_expense(
        db, apartment_id=APARTMENT, expense_id="groceries-2026-09",
        description="Groceries", amount_paise=333_401, expense_date="2026-09-03",
        expense_definition_id="groceries", allocation_method="equal",
        payer_resident_id="alice", statement_id=september)

    # The August electricity bill, settled in September.
    electricity.create_electricity_expense(
        db, apartment_id=APARTMENT, expense_id="electricity-2026-09",
        description="August electricity bill", expense_date="2026-09-08",
        expense_definition_id="electricity", total_amount_paise=735_000,
        payer_resident_id="bob", statement_id=september, bill_number="SEB/26/09/1188",
        smart_plug_readings=[
            {"resident_id": "alice", "kwh": "42.500", "device_label": "AC"},
            {"resident_id": "bob", "kwh": "31.000", "device_label": "AC"},
            {"resident_id": "chris", "kwh": "18.250", "device_label": "AC"},
        ],
        components=[
            {"id": "energy", "name": "Energy charges", "amount_paise": 650_000,
             "allocation_method": "usage"},
            {"id": "fixed", "name": "Fixed charges", "amount_paise": 85_000,
             "allocation_method": "equal"},
        ])
    # Bob's advance is linked, so his payer credit on the expense is replaced by it.
    payments.record_payment(
        db, apartment_id=APARTMENT, payment_id="advance-electricity-2026-09",
        payer_resident_id="bob", amount_paise=735_000, payment_date="2026-09-08",
        payment_kind="provider_advance", payee_or_provider="State Electricity Board",
        expense_id="electricity-2026-09")

    # A partial payment and an overpayment.
    payments.record_payment(
        db, apartment_id=APARTMENT, payment_id="pay-chris-2026-09",
        payer_resident_id="chris", amount_paise=2_500_000, payment_date="2026-09-20")
    payments.record_payment(
        db, apartment_id=APARTMENT, payment_id="pay-bob-2026-09",
        payer_resident_id="bob", amount_paise=50_000, payment_date="2026-09-22")

    finalized_at = datetime.now(timezone.utc) - timedelta(hours=100)
    statements.finalize_statement(db, statement_id=september, finalized_by="operator",
                                 now=finalized_at, require_clean=False)
    statements.close_expired_statements(db, now=datetime.now(timezone.utc))

    adjustments.record_adjustment(
        db, apartment_id=APARTMENT, resident_id="chris", amount_paise=-25_000,
        reason="September electricity share corrected after close",
        created_by="operator", statement_id=september, effective_date="2026-10-06",
        adjustment_id="adj-chris-oct")

    october = statements.create_monthly_statement(
        db, apartment_id=APARTMENT, settlement_month="2026-10", statement_id="stmt-2026-10")
    recurring.generate(
        db, apartment_id=APARTMENT, settlement_period="2026-10", statement_id=october,
        lines=[{"expense_definition_id": "rent"}, {"expense_definition_id": "broadband"}])
    expenses.create_expense(
        db, apartment_id=APARTMENT, expense_id="repair-2026-10",
        description="Geyser repair", amount_paise=250_000, expense_date="2026-10-07",
        period_rule="transaction_date", allocation_method="equal")
    return db


@pytest.fixture()
def client(db: Database):
    """A FastAPI test client over an empty, migrated database."""
    from fastapi.testclient import TestClient

    from aem.main import create_app

    settings = get_settings()
    with TestClient(create_app(settings, database=db)) as test_client:
        support.sign_in(test_client)
        yield test_client


@pytest.fixture()
def household_client(household: Database):
    from fastapi.testclient import TestClient

    from aem.main import create_app

    settings = replace(get_settings(), auto_close_expired_statements=False)
    with TestClient(create_app(settings, database=household)) as test_client:
        support.sign_in(test_client)
        yield test_client

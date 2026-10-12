"""The unit of work: one transaction, and the repositories that share it.

A service obtains a unit of work from the :class:`~aem.persistence.database.Database`
and reaches the database only through the repositories on it::

    with db.transaction() as uow:
        uow.expenses.insert_expense(...)
        uow.audit.append(...)

Everything done through one unit of work commits together when the block ends,
or rolls back together if it raises. Each repository is named after the service
module it serves (``uow.payments`` holds the SQL for ``aem.services.payments``).
"""
from __future__ import annotations

from functools import cached_property
from typing import ContextManager

from .repositories.adjustments import AdjustmentsRepository
from .repositories.apartments import ApartmentsRepository
from .repositories.audit import AuditRepository
from .repositories.dashboard import DashboardRepository
from .repositories.definitions import DefinitionsRepository
from .repositories.electricity import ElectricityRepository
from .repositories.expenses import ExpensesRepository
from .repositories.ledger_view import LedgerViewRepository
from .repositories.opening_balances import OpeningBalancesRepository
from .repositories.payments import PaymentsRepository
from .repositories.reconciliation import ReconciliationRepository
from .repositories.recurring import RecurringRepository
from .repositories.rent import RentRepository
from .repositories.reports import ReportsRepository
from .repositories.residents import ResidentsRepository
from .repositories.setup import SetupRepository
from .repositories.sharing import SharingRepository
from .repositories.statements import StatementsRepository
from .repositories.tapo_sync import TapoSyncRepository
from .repositories.validation import ValidationRepository
from .session import Session


class UnitOfWork:
    def __init__(self, session: Session) -> None:
        self._session = session

    def savepoint(self) -> ContextManager[None]:
        """A rollback point inside this unit of work.

        Use it around a write that is allowed to fail (a duplicate key the
        service turns into a friendly error and then carries on from): without
        one, the failed statement aborts the whole transaction.
        """
        return self._session.savepoint()

    @cached_property
    def apartments(self) -> ApartmentsRepository:
        return ApartmentsRepository(self._session)


    @cached_property
    def residents(self) -> ResidentsRepository:
        return ResidentsRepository(self._session)


    @cached_property
    def audit(self) -> AuditRepository:
        return AuditRepository(self._session)


    @cached_property
    def definitions(self) -> DefinitionsRepository:
        return DefinitionsRepository(self._session)


    @cached_property
    def expenses(self) -> ExpensesRepository:
        return ExpensesRepository(self._session)


    @cached_property
    def electricity(self) -> ElectricityRepository:
        return ElectricityRepository(self._session)


    @cached_property
    def payments(self) -> PaymentsRepository:
        return PaymentsRepository(self._session)


    @cached_property
    def opening_balances(self) -> OpeningBalancesRepository:
        return OpeningBalancesRepository(self._session)


    @cached_property
    def adjustments(self) -> AdjustmentsRepository:
        return AdjustmentsRepository(self._session)


    @cached_property
    def statements(self) -> StatementsRepository:
        return StatementsRepository(self._session)


    @cached_property
    def reconciliation(self) -> ReconciliationRepository:
        return ReconciliationRepository(self._session)


    @cached_property
    def validation(self) -> ValidationRepository:
        return ValidationRepository(self._session)


    @cached_property
    def ledger_view(self) -> LedgerViewRepository:
        return LedgerViewRepository(self._session)


    @cached_property
    def reports(self) -> ReportsRepository:
        return ReportsRepository(self._session)


    @cached_property
    def rent(self) -> RentRepository:
        return RentRepository(self._session)


    @cached_property
    def setup(self) -> SetupRepository:
        return SetupRepository(self._session)


    @cached_property
    def dashboard(self) -> DashboardRepository:
        return DashboardRepository(self._session)


    @cached_property
    def sharing(self) -> SharingRepository:
        return SharingRepository(self._session)


    @cached_property
    def recurring(self) -> RecurringRepository:
        return RecurringRepository(self._session)


    @cached_property
    def tapo_sync(self) -> TapoSyncRepository:
        return TapoSyncRepository(self._session)

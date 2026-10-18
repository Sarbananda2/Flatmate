"""API routers, one module per screen area."""
from . import (  # noqa: F401
    audit,
    auth,
    clearance,
    dashboard,
    definitions,
    deposits,
    electricity,
    expenses,
    health,
    opening_balances,
    payments,
    property,
    reconciliation,
    rent,
    reports,
    residents,
    rooms,
    services,
    setup,
    statements,
    transactions,
)

ALL_ROUTERS = [
    health.router,
    auth.router,
    setup.router,
    dashboard.router,
    residents.router,
    clearance.router,
    definitions.router,
    expenses.router,
    electricity.router,
    payments.router,
    opening_balances.router,
    rent.router,
    deposits.router,
    rooms.router,
    property.router,
    services.router,
    statements.router,
    reconciliation.router,
    transactions.router,
    audit.router,
    reports.router,
]

# Reachable without signing in: liveness, and the routes that sign someone in.
# Every other router answers only a signed-in user.
PUBLIC_ROUTERS = [health.router, auth.router]

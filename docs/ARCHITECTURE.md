# Architecture

How the pieces fit together, and the reasoning behind the arrangement.

Written for: engineers maintaining or extending this application.

---

## Shape

```
                    browser
                       │
            React + TypeScript + Vite
                       │  JSON over HTTP, one origin
                       ▼
┌──────────────────────────────────────────────┐
│  aem/api/routers/      routing only          │
│  aem/schemas/          typed request/response│
├──────────────────────────────────────────────┤
│  aem/services/         business logic        │
│    one module per area; transactions; audit  │
├──────────────────────────────────────────────┤
│  aem/engine/           pure accounting       │
│    no I/O, no state, no side effects         │
├──────────────────────────────────────────────┤
│  aem/persistence/      the only door to data │
│    unit_of_work.py     one transaction       │
│    repositories/       all application SQL   │
│    postgres/           driver, pool, schema  │
└──────────────────────────────────────────────┘
                       │
        PostgreSQL 18  (local cluster, or a
        managed endpoint — same code, different
        connection settings)
```

Each layer may call the one below it and never the one above. The engines know
nothing about the database; the services know nothing about HTTP or SQL; the
routers contain no arithmetic.

A request travels one way:

```
FastAPI route → application service → unit of work / repository
              → PostgreSQL adapter → PostgreSQL
```

---

## The layers

### `aem/engine/` — pure accounting

`ledger.py` and `electricity.py` are the source of truth for every financial
calculation: equal, weighted and custom splits; oldest-first payment
allocation; pooled net positions; the transfer planner; and the electricity
bill allocator.

They have no database, network, or UI dependency, they never mutate state, and
they never use floating point for money. **Services persist their output; they
do not re-implement it.** That is why the allocation preview shown in the UI
can never disagree with what is saved — both call the same function.

This is the prototype's original code, moved here and kept as the authority.
The only behavioural changes are documented in [`CHECKLIST.md`](CHECKLIST.md).

### `aem/services/` — business logic

One module per area of the application:

| Module | Owns |
| --- | --- |
| `apartments.py` | Apartments and operator settings |
| `residents.py` | Residents; deactivation that keeps history |
| `definitions.py` | Expense definitions, period rules, recurrence config |
| `allocation.py` | Turning an amount plus a method into resident shares |
| `expenses.py` | General expenses and their allocations |
| `electricity.py` | Bills over the electricity engine |
| `rent.py` | The master lease, its contract term, renewal and notice, monthly rent cycles, HRA receipts |
| `housing.py` | The Housing & Utilities summary: one read-only view over rent and electricity |
| `opening_balances.py` | Balances carried in from before the first statement |
| `payments.py` | Payments, credits, advances, reversals |
| `adjustments.py` | Signed ledger adjustments |
| `recurring.py` | Preview and confirmed generation |
| `setup.py` | Guided first-run setup; progress derived from real records |
| `statements.py` | Draft, snapshot, finalize, revise, close |
| `reconciliation.py` | The full cross-month ledger calculation |
| `validation.py` | Named, record-level accounting exceptions |
| `dashboard.py`, `reports.py`, `ledger_view.py` | Read-only views |
| `audit.py` | The append-only trail |
| `common.py` | Shared lookups and guards |

Every mutating service validates its inputs, writes inside **one transaction**,
and records an audit entry in that same transaction — so the trail cannot drift
from the data.

A service is given a `Database` and opens a unit of work on it. It never sees a
connection, a cursor, or a line of SQL, and it imports no database driver:

```python
def set_setting(db: Database, key: str, value: str, *, actor: str = "operator") -> None:
    ...
    with db.transaction() as uow:
        old = uow.apartments.get_setting(key)
        uow.apartments.set_setting(key, value, updated_at=now_timestamp())
        audit.record(uow, entity_type="app_setting", ...)
```

A helper that runs inside somebody else's transaction takes the `uow` instead
of the `db`; that is how several services contribute to one atomic change.

### `aem/api/` — HTTP only

Routers validate with `aem/schemas/`, call one service, and return what it
returns. `deps.py` hands routes the `Database` and the apartment, and translates
`AemError` into the HTTP envelope. There is no accounting logic here.

### `aem/persistence/` — the persistence boundary

Everything that touches the database is in this package, in three parts.

**`database.py` and `unit_of_work.py` — what services use.** `Database` offers
two things: `transaction()`, a read-write unit of work that commits when its
block ends and rolls back if it raises, and `read()`, a read-only one over a
single consistent snapshot. The `UnitOfWork` they yield carries the
repositories, all sharing that one transaction, plus `savepoint()` for the rare
write that is allowed to fail.

**`repositories/` — all of the application's SQL.** One module per service
module, with the same name (`uow.payments` holds the SQL for
`services/payments.py`). Methods are named for what the application needs —
`charges_for_resident`, `append_revision`, `mark_cycle_disbursed` — take and
return plain values, and contain no business rules. There is deliberately no
generic "run this query" or "update this table" method: a caller cannot reach
the data except through an operation somebody wrote for the purpose. The SQL is
explicit PostgreSQL; there is no ORM.

**`postgres/` — the adapter.** The only place a database driver (psycopg 3) is
imported. It owns the connection pool, transaction mechanics, and the
translation of every driver exception into an `aem.persistence` error
(`UniqueViolation`, `GuardViolation`, `DatabaseUnavailableError`, …), so
nothing above it handles a driver type.

Three properties of the adapter are worth knowing:

- **Writers run one at a time.** A write transaction takes a transaction-level
  advisory lock first. The services read a balance and then act on it, and
  under SQLite `BEGIN IMMEDIATE` guaranteed nobody wrote in between; this keeps
  that guarantee instead of asking every service to reason about concurrent
  writers. A writer waits up to 15 seconds for the lock, as it did before.
- **Reads are consistent.** `read()` is a `REPEATABLE READ, READ ONLY`
  transaction, so a report assembled from several queries sees one moment in
  time.
- **Money stays an integer.** PostgreSQL returns `SUM()` of a `BIGINT` as
  `NUMERIC`; the adapter hands whole numerics to the application as `int`, and
  repositories cast sums to `BIGINT` as well.

The adapter *connects* to a database. It does not start, stop, or provision
one, and neither does anything else in `aem/`: see
[Startup and shutdown](#startup-and-shutdown).

`aem/persistence/sqlite_import/` is operational tooling, not part of the
request path: the one-off importer for a database from when the application ran
on SQLite, and the SQLite schema history it needs. See
[`OPERATIONS.md`](OPERATIONS.md#moving-from-sqlite).

### `aem/persistence/postgres/migrations/` — versioned, forward-only

Numbered `NNNN_name.sql` files, each applied once inside its own transaction
together with the `schema_migration` row that records it and its checksum.
PostgreSQL DDL is transactional, so a migration either happened and is
recorded, or did not happen at all. An advisory lock stops two starting
processes migrating at once. Nothing drops a table or a column.

`0001_baseline.sql` is the schema the application had reached under SQLite,
carried over column for column — text IDs and ISO-8601 text dates, integer
0/1 flags, `BIGINT` paise — so that existing data imports without any value
being reinterpreted. It adds `row_seq`, an insertion-order column on every
table, because the order of same-day charges and payments used to come from
SQLite's implicit `rowid`.

---

## Decisions worth knowing

### The database is the last line of defence

The service layer enforces the rules. The database enforces them again:

- a closed statement's snapshot and status cannot be changed;
- expenses and allocations in a closed statement cannot be modified or deleted;
- a payment cannot be applied beyond its own amount;
- a charge cannot be settled beyond its own amount;
- `audit_log`, `statement_revision`, and `ledger_adjustment` are append-only;
- a partial unique index makes a duplicate generated recurring expense
  impossible, even under a race or a repeated request.

A direct SQL mistake cannot corrupt the closed history. The tests assert this
by trying it.

### Reconciliation is entry-based, not aggregate-based

Rather than summing columns, reconciliation turns every record into signed
**ledger entries** against named accounts, each naming the record it came from.
Positions are then sums of entries. That makes the result auditable — any
figure traces back to a row through `/api/reconciliation/entries` — and makes
the zero-sum property structural rather than coincidental.

A second, independent reading (charge level: allocations minus applications)
is cross-checked against the first on every run. Disagreement raises an error
rather than picking a winner.

### Errors are typed and carry structure

`AemError` subclasses carry a stable `code`, a human message, and structured
`details`. The API renders them as `{"error": {code, message, details}}` with a
matching status, and the frontend branches on the code instead of matching
strings. Request-validation failures use the same envelope, so there is one
error shape to handle.

### Setup progress is derived, never stored

The guided setup has no "wizard progress" record. Each step commits its own
records, and the step to resume at is computed from whether an apartment row,
resident rows, and definition rows exist. That is what makes closing the tab
harmless, and it cannot drift out of step with the data the way a separate
progress flag could. The browser holds only what is half-typed in the step in
progress, and the screen works correctly when that is unavailable.

### Two periods, not one

The prototype's `expense_period` meant both "the period the money was for" and
"the statement that charges it". Splitting it into `service_period` and
`settlement_period` is the single largest schema change, and it is what lets
the September electricity bill be charged in October while consumption reports
still say September. Existing rows were backfilled to their previous meaning.

### Compatibility was kept deliberately

The prototype's modules remain under `legacy/` as thin shims
re-exporting `aem.*`. The original 53 tests run **unchanged** against the
refactored services, which is how the refactor was shown not to have changed
behaviour. The terminal CLIs, also under `legacy/`, still work against the same database.

### `__pool__` is a real account

Modelling the collection pool as a synthetic account, rather than a residual,
is what makes the ledger zero-sum and lets a transfer plan include "pay the
pool" and "the pool owes you". Positive for the pool means residents owe it in
aggregate.

---

## Frontend

React 18 with React Router, no state-management library: screens load data with
`useResource`, write with `useMutation`, and share only the selected settlement
month through one small context.

Screens are one file each under `src/pages/`. The Housing & Utilities screens
sit together in `src/pages/services/`, mirroring their `/services/...` URLs;
the old `/rent`, `/rent/config` and `/electricity` addresses redirect there.

`useResource` makes the loading, error, and empty states explicit, and
`AsyncSection` makes rendering all three unavoidable — a screen cannot
accidentally render a blank table while data is in flight.

`src/lib/format.ts` is the only place money crosses between integer paise and
text. `parseRupees` rejects a third decimal place rather than rounding it, and
`formatInr` splits the integer rather than dividing it, so a value cannot drift
on the way to the screen.

Styling is one CSS file of design tokens with light and dark variants, printed
styles, and a mobile layout. There is no CSS framework. Financial semantics —
owed-to, owed-by, credit — each have a colour *and* a sign or a word, so no
figure depends on colour alone.

---

## Request lifecycle

```
POST /api/expenses
  → Pydantic validates the body            (floats, bad dates, unknown fields)
  → router calls expenses.create_expense
      → BEGIN IMMEDIATE
      → look up apartment and residents
      → resolve the period rule
      → plan or validate the allocation    (aem.engine)
      → assert the shares total the amount
      → insert expense, component, allocations
      → write the audit entry
      → COMMIT
  → router reads the saved expense back and returns it
```

Any failure between `BEGIN` and `COMMIT` rolls everything back. The audit entry
is inside the transaction, so it exists if and only if the change does.

---

## Startup and shutdown

**Startup** resolves settings, connects to the configured database, checks
that the schema is the version the code expects, and closes any statement
whose correction window elapsed while the application was stopped. It does not
migrate: the application serves as a runtime role that owns nothing and cannot
change the schema. Migrations are applied beforehand by `scripts/manage.py
init`, as the migration role that owns the tables. A schema that is behind is
reported, in the log and by `/health`, and left alone.

**Shutdown** closes the connection pool. Every unit of work commits or rolls
back before it returns, so a shutdown cannot leave a transaction open.

**The application never manages a database server.** FastAPI's startup and
shutdown only open and close connections. Who runs PostgreSQL depends on where
the application is:

| Where | Who runs PostgreSQL | How the app finds it |
| --- | --- | --- |
| Local development | `scripts/dev.sh`, through `scripts/local_postgres.sh`: a project-scoped PostgreSQL 18 cluster in `data/postgres/`, started before the API and stopped after it — and only if that run started it | Defaults: `127.0.0.1:55432`, password from `.env.local` |
| Deployment (AWS) | The managed PostgreSQL service. Nothing in this repository provisions it | `AEM_DATABASE_URL`, or `AEM_DB_HOST` and friends, with `AEM_DB_SSLMODE` |

`scripts/run.sh` and `scripts/manage.py serve` are the deployment entry points:
they connect and serve, nothing more.

---

## Testing

| Suite | What it covers |
| --- | --- |
| `tests/test_invariants.py` | The twenty accounting invariants, against a realistic two-month household |
| `tests/test_engine_and_periods.py` | The pure engines and the period rules, directly |
| `tests/test_api.py` | The HTTP contract: envelope, guards, filters, exports |
| `tests/test_migrations.py` | The PostgreSQL migration runner, and upgrading and importing a real prototype SQLite database without loss |
| `tests/test_end_to_end.py` | The complete operator journey, over HTTP, including a restart |
| `tests/test_rent.py` | The lease, equal split, rent payments, payouts, receipts, and the contract term: timeline, renewal, notice |
| `tests/test_opening_balances.py`, `tests/test_settlement_sharing_and_sync.py`, `tests/test_setup_wizard.py` | Opening balances, settlement sharing and smart-plug sync, the guided setup |
| `legacy/test_*.py` | The original prototype suite; its two database modules ported to PostgreSQL |
| `frontend/src/**/*.test.*` | Money formatting, UI guarantees, form interactions |

Database tests run against PostgreSQL. Each test gets its own freshly migrated
schema in a dedicated test database (`tests/support.py`), receives the same
`Database` handle the API layer gives a service, and uses a raw connection only
to inspect stored rows or to try to break a rule behind the services' backs.
The schema is created by the migration role and the test then runs as the
runtime role. What has been validated on PostgreSQL 18 is listed
in [`KNOWN_LIMITATIONS.md`](KNOWN_LIMITATIONS.md#what-has-and-has-not-been-validated-on-postgresql-18).

The `household` fixture is deliberately awkward: three residents, two months,
an expense that does not divide evenly, a linked provider advance, a partial
payment, an overpayment, a closed statement, and a post-close adjustment. An
invariant that only holds for one tidy expense will not pass against it.

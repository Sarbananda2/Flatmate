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

### UI and table design rules

**Mandatory Rule — Table Header Alignment (All Columns):**
Whenever a table is made or a new page is introduced, this rule must be strictly followed:
- **All table column headers (`<th>`) must be center-aligned (`text-align: center;`) across every single column.**
- This applies universally across the board: entity/resident names, descriptions, categories, dates, monetary amounts (`.num`), statuses, and action columns.
- Enforced at the base design system level in `frontend/src/index.css`:
  ```css
  table.data thead th,
  table.data thead th.num {
    text-align: center;
  }
  ```
- Individual pages and components must **not** override column headers to `text-left` or `text-right` via inline styles or utility classes.
- **Table Data Cells (`<td>`)**: Row content retains its natural semantic alignment — text/names/descriptions remain left-aligned, monetary and numerical figures remain right-aligned with monospace tabular figures (`.num`), and status chips/actions remain centered.

**Mandatory Rule — Comprehensive Form Field Validation:**
Every user input form created across the application must implement explicit field-level validation:
- **Client-Side Enforcement:**
  - **Required vs. Optional:** All mandatory fields must check for non-empty, non-whitespace content prior to submission.
  - **Format-Specific Validators:**
    - *Email addresses:* Valid standard email pattern (`name@domain.tld`).
    - *Phone numbers:* Standard digit validation (e.g., 10-digit mobile number, sanitizing non-numeric characters).
    - *UPI IDs:* Valid Virtual Payment Address syntax (`username@handle`).
    - *Monetary Amounts:* Must validate through `parseRupees` (strictly non-negative unless explicit adjustment, no third decimal place, integer paise precision).
    - *Dates & Periods:* Valid `YYYY-MM-DD` or `YYYY-MM` formats within plausible temporal bounds.
    - *Split Allocations:* Individual participant shares must mathematically sum exactly to the total expense amount.
  - **User Feedback & State:**
    - Inline field-level error messages beneath the offending input (never relying solely on generic alerts).
    - Erroneous fields must receive clear visual error cues (e.g., danger border/focus ring) and accessibility labels (`aria-invalid="true"`).
    - Save/Submit buttons should guard against invalid states or trigger clear field-level highlights on attempted submit.
- **Backend Dual-Enforcement:**
  - Client-side validation is never trusted in isolation; all fields are backed by Pydantic validators (`aem/schemas/models.py`) and service-layer rules (`aem/services/validation.py`) returning structured `AemError` responses.

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

---

## User Interface & Design System Standards

The frontend enforces strict UX, state-separation, and visual presentation invariants:

### Rule U1: Read-Only by Default for Saved State (No Permanent Form Mode)
* **Invariant:** Any entity, configuration, or record that has already been saved (e.g., Master Tenancy terms, Apartment settings, Deposit funding, Flatmate profiles) must be rendered in **Read-Only / Display Mode** by default.
* **Prohibition:** Static configurations must **never** be permanently left open as active input fields, textboxes, dropdowns, or radio groups. Saved records must render as clean, structured key-value labels with typography and badge styling.
* **Initial Setup Exception:** An input form is displayed by default only when the entity has never been configured (`null` / first-time setup). Once saved, it collapses immediately into Read-Only display mode.

### Rule U2: Strict Mutual Exclusion of View and Editor
* **Invariant:** The editor (form inputs, textboxes, dropdowns) and the saved read-only view must **never** be rendered on the screen simultaneously.
* **Explicit Edit Action:** Entering edit mode requires an explicit user action (an "Edit" button or pencil shortcut).
* **Edit Interaction Modes:**
  1. **Modal / Dialog Mode (Preferred for cards and lists):** Clicking "Edit" opens a focused dialog containing the form inputs, keeping the background read-only view undisturbed until save/cancel.
  2. **In-Place Toggle Mode:** Clicking "Edit" replaces the display view with the form inputs, along with explicit `Save Changes` and `Cancel` buttons. Canceling or saving restores the Read-Only display view.

### Rule U3: Universal Table Header Center-Alignment
* **Invariant:** In every data table across all screens, all column headers (`<th>`) must be center-aligned (`text-center`).

### Rule U4: Mandatory Client-Side Validation
* **Invariant:** Every form input must validate non-empty text, phone/email/PAN/IFSC syntax, non-negative amounts, and date bounds before submission.

### Rule U5: Scoped Per-Card Editing & Concise Action Triggers
* **Scoped Editing:** Every editable card/panel owns its own independent edit trigger. Entering edit mode on one section must **never** put the entire page or unrelated sections into edit mode.
* **Concise Labels:** Edit action buttons must use a clean, uniform label: `<Pencil className="w-3.5 h-3.5" /> Edit` (or simply `Edit`). Do not repeat the card's title in the button label (e.g., avoid `Edit Tenancy Terms`, `Edit Settings`, `Edit Landlord Profile`). The visual context is already established by the card header.

### Rule U6: Zero Dead Space for Empty Histories
* **Invariant:** Historical archives and completed term logs must remain hidden or collapsed until at least one record exists. Do not render giant, empty full-width panels with placeholder text explaining that history has not occurred yet.

### Rule U7: Summary-First Tables with On-Click Detail Modals (Progressive Table Disclosure)
* **Invariant:** Data tables must display only the primary, scannable, atomic summary columns required to identify and compare rows at a glance (e.g., `Date`, `Slot / Space`, `Activity`, `From`, `To`, `Amount`). Secondary, verbose, or audit-level attributes (such as payment methods, bank/UPI UTR references, free-text notes, deduction reasons, or multi-line breakdowns) must **never** be crammed into table columns where they cause horizontal crowding or ellipsis truncation.
* **On-Click Read-Only Detail Modal:** Clicking a table row opens a focused, read-only detail dialog (`<Modal>`) presenting the record's complete set of atomic fields in a structured key-value grid with full, untruncated text.
* **Zero Table Layout Shift:** Inspecting a row's details must **never** mutate row height or insert inline expansion/drop-down rows into the table body.
* **Keyboard & Screen-Reader Accessibility:** Every openable row must provide an accessible keyboard trigger (with `aria-haspopup="dialog"`) that opens the detail dialog on `Enter`/`Space` and returns focus to the trigger when closed.

---

## Domain Modeling & Field Atomicity Standards

The backend, database schemas, and frontend forms enforce strict domain field atomicity:

### Rule D1: Strict Field Atomicity (Zero Compound String Blobs)
* **Invariant:** Real-world entities, physical structures, legal identities, and financial references must **never** be compressed or stored as a single unstructured, multi-part string blob (e.g., storing flat number, tower, society, sector, city, and pincode in a single `"name"` or `"address"` column).
* **Dedicated Columns & Types:** Every distinct real-world attribute must be modeled as an independent, strongly-typed column with its own dedicated syntax validator (regex, enum, or range check).
* **Derived / Compiled Representations:** When a compound string is required (such as the premises address on an HRA rent receipt, or an account display label), it must be **dynamically compiled** from the underlying atomic attributes. The compound string is never the source of truth.
* **Granular Input Interfaces:** Form and modal interfaces must provide dedicated, individual input fields for each atomic attribute, complete with client-side syntax validation (e.g., 6-digit Indian PIN code `^[1-9][0-9]{5}$`, 10-character PAN `^[A-Z]{5}[0-9]{4}[A-Z]$`). Free-text textareas or generic catch-all inputs for structured domain entities are strictly prohibited.
* **Future Extension Enforcement:** Any future entity introduced into the system (e.g., vehicle parking slots, domestic help contracts, grocery pantry items) must adhere strictly to Rule D1. Multi-part composite data must be decomposed into atomic fields at schema design time.




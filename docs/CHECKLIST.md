# Implementation checklist

Derived from the actual repository contents on 2026-10-11.

## Baseline (verified before any change)

- `python3 -m unittest discover` → **53 tests, 0 failures** (recorded in `TEST_RESULTS.txt`).
- (These files now live under `legacy/`.) Working code found: `apartment_ledger.py` (pure split/payment/pool math),
  `electricity_engine.py` (bill allocation), `electricity_workflow.py` (SQLite
  persistence for electricity), `ledger_workflow.py` (expenses, payments,
  statements, pooled reconciliation), three terminal CLIs, `schema.sql`.
- A stale duplicate of the whole prototype existed in `apartment_expense_manager/`
  (older by ~3 minutes, smaller `ledger_workflow.py`). It was set aside during
  the build and **deleted on 2026-10-11** once verified to contain nothing
  unique: every filename, every definition, and every test name in it was
  confirmed present in the live codebase or superseded by a named replacement,
  and its `schema.sql` was a strict subset of the newer one (it lacked
  `payment.expense_id`).

## Gaps found by reading the code (not the docs)

| # | Gap | Resolution |
|---|-----|------------|
| 1 | `expense_period` was overloaded: it meant both "period the money was for" and "statement month". No way to express "September electricity settled in October". | Added `service_period` + `settlement_period` columns, and a documented period-rule mapping. |
| 2 | Period rules were stored on `expense_definition` but never applied anywhere. | `aem/periods.py` resolves them; expense creation and statement assembly use them. |
| 3 | Recurring definitions were stored but never instantiated. | Recurrence config + preview/confirm generation with duplicate protection. |
| 4 | Only `custom` allocations were possible through the workflow; `equal_split`/`weighted_split` existed in the engine but were unreachable. | Allocation planner service exposes equal/weighted/custom with a pre-save preview. |
| 5 | Payment due dates fell back to `expense_date`, so oldest-first was "oldest transaction date". | Explicit `due_date` column, defaulting to the settlement period end; documented. |
| 6 | `payment_application` could exceed the payment amount or the allocation amount — nothing checked it. | Enforced in the service and by DB triggers; invariant tests added. |
| 7 | Unapplied credit was returned by `record_payment` but never stored, so it was invisible afterwards. | Credit is derived from `payment` − `payment_application` and surfaced everywhere. |
| 8 | No reversal/correction path for payments. | `reverse_payment` writes an auditable reversal, never a silent delete. |
| 9 | `statement_revision` and the correction window existed in `apartment_ledger.py` only (in-memory); the SQLite path had no revisions. | `revise_statement` persists numbered revisions during the 72h window. |
| 10 | Closed statements were only protected by Python checks. | SQLite triggers block writes to non-draft statements and to closed snapshots. |
| 11 | No audit trail of changes. | `audit_log` table written by every mutating service. |
| 12 | Reconciliation raised a bare internal error on inconsistent data and had no way to report *which* record was ambiguous. | `validation.py` produces identified exceptions; reconciliation refuses to invent entries. |
| 13 | No API, no UI, no migrations, no backup procedure. | Added. |

## Build order

- [x] 1. Inspect repository, run existing tests, record baseline
- [x] 2. Accounting/reconciliation layer (`aem/engine`, `aem/services/reconciliation.py`)
- [x] 3. Versioned migrations + persistence layer (`aem/db.py`, `aem/migrations/`)
- [x] 4. FastAPI backend, typed schemas, health, OpenAPI
- [x] 5. Frontend scaffolding + navigation (React/TS/Vite)
- [x] 6. Residents + expense definitions
- [x] 7. General expenses + allocation preview
- [x] 8. Electricity management
- [x] 9. Payments, credits, provider advances
- [x] 10. Statements, finalization, correction window
- [x] 11. Cross-month reconciliation
- [x] 12. Transactions, reports, exports
- [x] 13. Invariant/integration/migration tests
- [x] 14. Documentation + startup scripts
- [x] 15. Full test run + verified app start
- [x] 16. Equal Split Master Lease Rent Management (targeted rent payments, optional deferred landlord receipts, HRA receipts)

---

## Final state

### Verified results

| Suite | Command | Result |
|-------|---------|--------|
| Backend (all) | `.venv/bin/python -m pytest` | **457 passed** |
| — original prototype tests | `python3 -m unittest discover` equivalent, collected by pytest | 53, unchanged |
| — rent management tests | `pytest tests/test_rent.py` | 104 |
| — accounting invariants | `pytest tests/test_invariants.py` | 94 |
| — engines and periods | `pytest tests/test_engine_and_periods.py` | 59 |
| — API contract | `pytest tests/test_api.py` | 34 |
| — guided setup | `pytest tests/test_setup_wizard.py` | 32 |
| — migrations | `pytest tests/test_migrations.py` | 15 |
| — end to end | `pytest tests/test_end_to_end.py` | 2 |
| Frontend | `cd frontend && npm test` | **90 passed** |
| TypeScript | `cd frontend && npm run typecheck` | clean |
| Production build | `cd frontend && npm run build` | clean |
| Running application | `./scripts/run.sh`, then HTTP + headless browser | dashboard, statement, reconciliation and electricity screens verified rendering real reconciled data |

### Deliberate behaviour changes

Three, each with a reason and a regression test.

1. **`Charge` gained a `sequence` field** (`aem/engine/ledger.py`). Oldest-first
   ordering previously fell back to the charge ID when due dates tied, and
   charge IDs are random UUIDs — so the order was not deterministic. Ordering
   is now (due date, sequence, charge ID), with the sequence supplied as the
   allocation row's `rowid`. Defaults to 0, so existing callers are unaffected;
   the original tests pass unchanged.

2. **A zero-amount usage component no longer raises** (`aem/engine/electricity.py`).
   The documented rule is that a *positive* usage-allocated charge with no
   recorded usage is rejected. The check did not test for positivity, so a zero
   charge was refused too. Now a zero charge allocates zero to everyone, which
   is a real answer needing no readings.

3. **A new expense defaults to an equal split** in the UI form. The prototype's
   only path was custom shares. Equal is what a shared household wants most of
   the time, and custom remains one click away. Existing expenses keep the
   method they were saved with.

### Two UI defects found by running the application

Neither would have been caught by a unit test; both came from launching the
real server and looking at it.

- **The first-run screen read as an error.** A brand-new database has no
  apartment, so every screen's first load failed with `no_apartment` and the
  dashboard rendered "Could not load this" to a new user. `AsyncSection` now
  special-cases that one code and shows a welcome with a link to Setup
  instead. Covered by a test.
- **Narrow layouts overflowed horizontally.** A wide financial table inside a
  CSS grid track stretched the track rather than scrolling, because a grid item
  does not shrink below its content without `min-width: 0`. That widened the
  whole page and pushed the stat cards off-screen on a phone. Fixed with an
  explicit `min-width: 0` on grid and flex children plus a single-column layout
  below 620px, and verified at 390px.

### Compatibility kept

- The prototype's five modules remain at the repository root as documented
  shims re-exporting `aem.*`. `LedgerWorkflowError` and
  `ElectricityWorkflowError` are aliases of `aem.errors.AemError`, the base
  class of every expected error, so existing `except` blocks keep working.
- `initialize_database` now creates *or upgrades*, preserving existing data.
- The three terminal CLIs work unchanged against the same database.
- All 53 original tests run unmodified.

### Still open

Business decisions that need a human answer are listed in
[`docs/ACCOUNTING_RULES.md` §15](ACCOUNTING_RULES.md#15-decisions-still-open)
and summarised in [`KNOWN_LIMITATIONS.md`](KNOWN_LIMITATIONS.md).

**Move-in/move-out proration — decided 2026-10-11: nothing prorates.** This was
the one open decision that affected money. A partial month never reduces a
share, for any category; dates are recorded for reference only. It is now a
stated rule in [`docs/ACCOUNTING_RULES.md` §7](ACCOUNTING_RULES.md#7-residents-and-partial-months-nothing-prorates),
surfaced in the UI, and locked in by tests.

The decisions still open are a paisa (the payer rounding preference on weighted
splits), a few hours (72 hours versus three calendar days), and possibly one
extra transfer (minimum-transfer settlement). None changes a material figure.


---

## Guided setup (added 2026-10-11)

Requested: set up the essentials in the fewest clicks, and survive the operator
accidentally closing the flow.

**Fewest clicks.** Identifiers are derived from the names typed, so none is
ever entered by hand. Residents go in as a list rather than one dialog at a
time. A catalogue of twelve common essentials — rent, electricity, water,
cooking gas, house help, society maintenance, broadband, groceries, and four
more — comes with the correct period rule already chosen and the usual eight
pre-ticked.
Amounts are optional. Four steps, one button each, and the last one offers to
open the month's statement so the operator does not have to go and do it.

**Surviving a close.** Each step commits when Continue is pressed, and the
resume point is computed from whether an apartment row, resident rows, and
definition rows actually exist — not from browser storage, and not from a
progress flag that could drift. Closing the tab, clearing the browser, or
moving to another machine all resume correctly. Navigating away mid-setup
raises a banner offering to pick it up again. Re-submitting a step is harmless:
an existing resident or category is skipped rather than duplicated, and a batch
that fails validation writes nothing at all.

Browser storage holds only what is half-typed in the step in progress, every
read and write wrapped in try/catch, and the screen works correctly without it.

Covered by 32 backend and 18 frontend tests.

# Known limitations

What this application does not do, stated plainly, so nothing comes as a
surprise later. See also the [Technical Debt Register](TECH_DEBT.md) for architectural trade-offs, legal/tax edge cases, and deferred entity extensions.

---

## Business rules that are deliberately not implemented

### Move-in and move-out proration

**Not a limitation — a decision.** The household decided on 2026-10-11 that
**nothing prorates**: a partial month does not reduce anybody's share, for any
category. A resident who takes part in an expense is charged a full ordinary
share of it, whether they were here for thirty days or three.

`active_from` and `active_until` are recorded for reference and never alter an
allocation. What the operator still controls, expense by expense, is who takes
part at all — leaving somebody out gives them an explicit zero, which is a
participation decision rather than a proration one.

Documented in [`docs/ACCOUNTING_RULES.md` §7](ACCOUNTING_RULES.md#7-residents-and-partial-months-nothing-prorates)
and locked in by tests, so it cannot drift back.

### The payer rounding preference on weighted and measured splits

For an **equal** split, the rule is fully implemented: remainder paise go to
non-payers first, so the payer is never charged the spare paisa.

For **weighted** and **measured** splits, the payer is placed last only in the
tie-break — when two residents have the *same* fractional remainder. A payer
with a genuinely larger remainder still receives the paisa, because doing
otherwise would break the largest-remainder method's proportionality.

The allocation preview warns about this on the weighted path.

### Minimum-transfer settlement

The suggested-transfer planner is greedy and deterministic. It produces few
transfers and never one that exceeds the balance it settles, but it does not
promise the globally minimal number of transfers for every possible set of
balances. For a household of a handful of people the difference is rarely more
than one transfer.

### Grocery "settled purchase" state

The specification describes grocery purchases being settled promptly and not
re-charged at month end. This is modelled through the ordinary mechanism — a
grocery expense creates charges, and a payment settles them — rather than a
separate flag on the purchase. If the household wants purchases marked settled
independently of payment applications, that rule needs defining first.

---

## Functional gaps

### Partial payment reversal

`reverse_payment` reverses a payment **in full**. To correct part of one,
reverse it and record a corrected payment, or use a signed adjustment.

### Rent: one lease, split equally

Rent management covers one arrangement: a single master lease with one monthly
rent, divided equally between the residents taking part. Unequal room rents,
several leases, and rent that changes part-way through a month are not
modelled. The lease form lists four other arrangements as "Under Development";
they are placeholders and cannot be selected. A month's rent can still be
recorded by hand as an ordinary expense with weighted or custom shares; the
Rent screen will not adopt it, because it only manages equal splits.

### The lease term is tracked, not enforced

The contract term, notice window and lock-in are information for the household.
Nothing is blocked when a term expires or a lock-in is broken; rent keeps
generating at the current rate until the lease is renewed.

The security deposit is recorded as a figure on the lease only. It is not a
ledger entry, and neither is its refund: the deposit recovery shown when
serving notice is an on-screen estimate, and the deductions entered there are
kept only as a note in the audit trail. There is no step that marks a lease as
finally vacated.

### Rent already generated at another amount is adopted as it is

If a month's rent already exists as an expense — for example from the
recurring generator, at the Rent category's default amount — generating rent
for that month links that expense at its own amount rather than charging the
lease rent. Keep the Rent category's default amount in step with the lease, or
generate rent from the Rent screen first.

### Removing a resident during setup marks them moved out

The guided setup can rename a saved resident and remove one, but removal is the
same as moving out: the record is kept as inactive. A name added by mistake
cannot be deleted outright.

### Household deposit slots: what is not modelled

Deposit slots track who holds which share of the landlord's deposit and what
was paid, kept back and handed over. Several things are deliberately left out:

- **The primary signatory cannot leave or be replaced.** Their own slot cannot
  be vacated, and the funding mode and signatory cannot be changed once slots
  exist. A signatory moving out, or the lease ending and the landlord
  returning the deposit, has no workflow yet.
- **Slots cannot be removed or merged.** A slot is permanent; its label, target
  and occupant can change, but the number of slots can only grow.
- **Settling a leaver is one step.** The `vacating` status in the schema is not
  used: a slot goes straight from occupied to vacant, or to its new occupant.
- **Deductions are recorded, not spent.** What is kept back from a leaver is
  counted against the signatory's capital at risk, but there is no record of
  it then being paid to the landlord or a repairer.
- **Changing the deposit on the lease does not rebalance the slots.** The
  Rent screen flags the mismatch; the slots have to be brought back in line
  by hand.
- **Rooms are not linked yet.** A slot has a label, not a room; attaching
  rooms and beds is a later phase.

### Exit clearance: what it does and does not settle

The move-out clearance settles a resident's deposit slot, optionally clears
their operating balance, marks them as moved out, and records the result.

- **The offset payment says the pool was paid; the cash is elsewhere.** When
  operating dues are netted against the deposit, one `to_pool` payment is
  recorded for the leaver (or a `refund`, if the household owed them). In
  reality that money stays with whoever held the deposit -- usually the primary
  signatory -- and it is for them to pass it to the pool. That hand-over is not
  tracked.
- **It clears the net position, not each charge.** The payment takes the
  resident's balance to exactly zero. If they had also paid expenses on the
  household's behalf, some of their individual charges can still show as
  outstanding afterwards even though nothing is owed.
- **A clearance cannot be undone or edited.** The record is append-only. The
  ledger payment can be reversed like any other, and the resident reactivated,
  but the slip stays as it was made.
- **The primary signatory cannot be cleared**, for the same reason their slot
  cannot be vacated.
- **The UPI code needs a UPI ID on file** for whoever is being paid, and a
  payment made by scanning it is not detected: the settlement reference is
  typed in by the operator.

### A recorded landlord payout cannot be undone

Recording a payout freezes the month's rent cycle and issues the HRA receipts,
which are immutable. There is no "undo payout". The advance that funded a
resident-paid payout cannot be reversed either. A payout recorded against the
wrong resident is corrected with a pair of signed ledger adjustments, which
leaves the mistake and its correction both visible.

### HRA receipts are household records, not the landlord's

The HRA slip is generated from the household's own records once the payout is
recorded. It states each resident's share of the rent that reached the
landlord, and says on its face that the landlord did not issue it. It is issued
whether or not the resident has yet paid their share into the pool; the screen
says so when they have not. Whether an employer accepts it is their decision.

### Editing an electricity bill's allocations in place

An electricity bill is corrected by **recalculating** it — supplying the
corrected readings or charges — not by editing individual allocations. The
superseded calculation is written to the audit trail first. Recalculation is
refused if any payment has already been applied to the bill's charges; reverse
those first.

### Editing an expense that has been partly paid

An expense whose charges have payments applied cannot have its allocations
replaced. Reverse the payment first, or record a ledger adjustment. This keeps
a payment from silently pointing at a charge that no longer means what it did.

### Expenses with multiple components in the general editor

The general expense editor handles single-component expenses. An expense with
itemised components (currently only electricity) is edited through its own
module. The editor says so rather than failing partway.

### No PDF generation

Statements print from the browser (**Print** on the statement screen) and
export as CSV and JSON. There is no server-side PDF generator. The printed
view and the exports come from the same stored snapshot, so they agree.

### No charts

Figures are presented as tables with explicit totals. Nothing on any screen is
a decorative visual, and there are no placeholder charts.

### One operator, no permission system

There is no login, no user accounts, and no roles. The audit trail records an
actor name taken from `AEM_OPERATOR_NAME`, which is a label, not an identity.
This matches the stated scope: a single operator on their own machine.

### One currency

Amounts are integer minor units and display as INR with Indian digit grouping.
The `currency_code` column exists but nothing converts between currencies or
formats another one.

---

## Technical limitations

### What has and has not been validated on PostgreSQL 18

Validated on PostgreSQL 18.6, on the project's own cluster, on 2026-10-12:

- `./scripts/dev.sh` end to end: cluster creation and start, migration as the
  migration role, the API serving as the runtime role, `/health`, the Vite
  proxy, and shutdown — PostgreSQL stopped when the launcher had started it and
  left running when it had not, with no process left behind, on both Ctrl-C and
  a terminate signal.
- The whole Python suite: **488 tests passed**, each on its own schema in
  `aem_test`, driving the services as the runtime role. The fixtures refused to
  run against a database not named `*_test`.
- `manage.py init`, `status`, `demo`, `check`, `backup`, and `restore` (into a
  new empty test database, whose contents then matched the source), and both
  seed scripts.
- The role checks in the next section.
- `manage.py import-sqlite --dry-run` against a *copy* of the household's
  SQLite file, into an empty test database: every row verified, then rolled
  back; the source file was byte-identical afterwards.
- The real import on 2026-10-12 into the empty local `aem` database: **31 rows**
  imported and compared column by column before commit. The source SHA-256 was
  identical before and after (`ab8d4d3feed13b20cc164388d5b2f2065f9d86191a12cd018dc4fd94c7d77f38`).
  Post-import `status` reported PostgreSQL 18.6 and the restricted runtime role;
  `check` reported zero errors and warnings.

Not yet done: any run against a managed PostgreSQL endpoint in AWS. The SQLite
source remains in place as a rollback reference; the app uses PostgreSQL now.

### What each database role can and cannot do

Three roles, with the dangerous abilities split between them so that no role
that handles requests can change the rules:

| Role | Used by | Owns | Can change the schema |
| --- | --- | --- | --- |
| Bootstrap administrator (`aem_admin`) | `scripts/local_postgres.sh` only | the cluster | yes (superuser) |
| Migration role (`aem_owner`) | `manage.py init`, `import-sqlite`, `restore`; test setup | the databases and every table | yes — that is its job |
| Runtime role (`aem`) | the API, and every other command | nothing | **no** |

**The runtime role**, confirmed on PostgreSQL 18.6, is refused all of:
`CREATE TABLE`, `CREATE TEMP TABLE`, `CREATE SCHEMA`, `CREATE INDEX`,
`CREATE TRIGGER`, `CREATE OR REPLACE FUNCTION`, `ALTER TABLE` (adding a column,
dropping a constraint, disabling a trigger, changing the owner), `DROP TABLE`,
`DROP TRIGGER`, `DROP FUNCTION`, `TRUNCATE`, `SET ROLE` to the migration role,
and `SET session_replication_role`. It owns no object. What it is granted is
row-level access to the tables, and less than that where less is enough:

- `schema_migration` is read-only to it;
- on the append-only tables — `audit_log`, `statement_revision`,
  `ledger_adjustment`, `rent_receipt` — it may `SELECT` and `INSERT` only.
  The triggers that forbid rewriting those tables are still there; the runtime
  role is turned away before they are consulted.

The grants are applied by the migration role every time it migrates, and set
as default privileges so a table added later is covered.

**The serving process cannot migrate.** It is not given the migration role's
password (the launch scripts blank it before starting the server), and it no
longer applies migrations at startup; `AEM_AUTO_MIGRATE` is gone. If the
schema is behind, the app says so in its log and in `/health` and does not
touch it.

**The migration role** is not a superuser either. It cannot switch off
foreign keys, cannot `SET session_replication_role`, and — because of the
trigger guard (`scripts/sql/trigger_guard.sql`) — cannot
`ALTER TABLE … DISABLE TRIGGER`. As the owner it can still change the schema
with ordinary DDL, including dropping a trigger or a constraint: that is what
a migration is. The protection is that this role is used only by reviewed
migration files and the import, never by a request.

The trigger guard needs a superuser to install. The local scripts do it. On a
managed database it has to be applied once by the administrative user;
`manage.py status` and `/health` say whether it is present, and also whether
the role they are connected as owns anything or could change the schema.

#### Workflows that need the migration role

Everything else runs as the runtime role.

| Workflow | Why it needs ownership |
| --- | --- |
| `manage.py init` (and so `dev.sh` and `run.sh`, which call it before serving) | creates and alters tables; grants the runtime role its access |
| `manage.py import-sqlite` | creates the schema if missing, sets `row_seq` values and sequences, grants access |
| `manage.py restore` | `pg_restore` creates the tables, which the restoring role then owns |
| The test suite's setup and teardown | creates, migrates, and drops a schema per test; four tests also connect as the owner on purpose (to reach a trigger the runtime role may not approach, to remove a guard, or to tamper with `schema_migration`) |
| `legacy/*_cli.py` — only when the schema is behind | they call `initialize_database`, which passes if the schema is current and otherwise tells the operator to run `manage.py init` |

`manage.py backup` does **not** need it: `pg_dump` runs as the runtime role.

### Behaviour that differs from the SQLite version

Each difference introduced by the port was reviewed against the previous
behaviour. Two were unrequested changes and have been reverted; one could not
be kept and is explained.

- **Reverted — a duplicate recurring expense.** A second generated expense for
  the same definition and month is answered, as before, with a 422 "Could not
  save expense". The first PostgreSQL draft turned this into the 409
  `duplicate_recurring_expense` the code has always *tried* to raise — the
  SQLite version looked for the index name in an error message that never
  contained it, so that branch never ran. Making it run is a change in
  behaviour nobody asked for (it would also make recurring generation skip the
  line rather than stop), so the port does not make it. It is one condition in
  `aem/services/expenses.py` if the household wants it; that is a product
  decision.
- **Reverted — a malformed date in the double-count check.** The
  `possible_double_counted_advance` check asks whether an advance and an
  expense are within seven days of each other. SQLite's date functions yielded
  NULL for a value that was not a date, which quietly left that pair out. The
  first draft cast the text to a date in SQL, which would have failed the whole
  validation run — and with it reconciliation and the dashboard — on one bad
  value. The comparison is now made in the service and leaves such a pair out,
  as before.
- **Kept, because it cannot be reproduced — electricity consumption history
  with two bills in one service month.** For each resident and service month
  the report shows energy and cost. The SQLite query summed the energy of
  every bill in the month but took the cost from a column outside the
  grouping, which SQLite resolves by picking one bill's value arbitrarily;
  PostgreSQL rejects such a query outright. There is no faithful translation
  of "an arbitrary one". The query now totals the cost of the same bills whose
  energy it totals. With one bill per service month — every case the tests and
  the sample data contain — the output is identical. No accounting figure
  depends on this report: balances, statements, and reconciliation do not read
  it.

Smaller differences that remain, none of them a change in what is computed:

- **Search is case-insensitive through `ILIKE`.** For ASCII this matches
  SQLite's `LIKE`.
- **Database error text is PostgreSQL's.** Messages that quote the database
  ("Could not save expense: …") carry its wording, not SQLite's. Error codes
  and HTTP statuses are unchanged.
- **Rows that had no defined order now have one.** A few queries had no
  `ORDER BY` and happened to return insertion order under SQLite (statements
  closed in one pass, existing expenses for a recurring month); they now order
  by insertion explicitly. Sorts on a column that can be NULL state where NULLs
  go, matching SQLite.

### One writer at a time

Write transactions are serialized with an advisory lock, and a writer waits up
to 15 seconds for its turn — the same behaviour SQLite's write lock gave.
Entirely adequate for one operator; it is deliberately not tuned for many
concurrent writers.

### PostgreSQL 18 must be installed separately

The scripts create and run the project's cluster but do not install
PostgreSQL, and refuse any major version other than 18.

### The legacy terminal CLIs lost their `--db` option

`legacy/*_cli.py` work against the configured PostgreSQL database rather than
a SQLite file named on the command line. The setup CLI writes through the
services, so it now also records audit entries and the default-apartment
setting, and its final writes are one transaction per record rather than one
for the whole run.

### No automatic background jobs

Nothing runs on a timer. Statements whose correction window elapsed are closed
at the next startup, or when you press the button, or via
`scripts/manage.py close-expired`. A household that wants it hands-off can put
that command in cron.

### Reconciliation recomputes from scratch

Every reconciliation reads the whole ledger. That is what makes it auditable
and reproducible, and at household scale — thousands of records — it is a few
milliseconds. It would need incremental caching at a scale this application is
not for.

### Snapshot reproducibility has a defined meaning

A finalized snapshot is **stored**, hashed, and re-readable byte for byte; the
hash is verified on every read, and exports are built from it. That is the
guarantee.

What is *not* guaranteed is that recomputing the figures today would produce
the same result, because a back-dated record added afterwards would legitimately
change what the live ledger says about that month. That is why the snapshot is
stored rather than recomputed, and why post-close corrections are adjustments
rather than edits.

### The frontend bundle is not code-split

One ~370 KB JavaScript bundle (about 100 KB gzipped). Loaded from localhost,
splitting it would buy nothing measurable.

### No frontend end-to-end browser tests

The frontend has unit and interaction tests (68, with React Testing Library)
covering money formatting, the display guarantees, and the critical forms. The
complete user journey is tested end to end **through the API** in
`tests/test_end_to_end.py` rather than through a browser driver. The rendered
UI was verified manually against a running server.

---

### Flatmate statements are live, and pay only the primary signatory

A resident's monthly statement (`GET /api/residents/{id}/statements/{month}`)
is read from the live ledger each time, not from the month's sealed snapshot,
so a later correction to a closed month changes what it shows. Its net due is
a running balance: brought forward, plus the month's charges, less the month's
credits. The UPI link and QR code name the lease's primary signatory. There is
no separate pool or apartment UPI ID, so a household without a signatory, a
signatory without a UPI ID, and the signatory's own statement all show no
payment link.

## Data notes

### Legacy columns are kept

`expense_instance.expense_period` and `expense_instance.is_settled` are
superseded by `settlement_period`/`service_period` and by derived settlement
respectively. They are retained so older data and the original callers keep
reading correctly, and they are written on new rows for the same reason. They
are documented as legacy in the data model.

### The legacy `adjustment` payment kind

`payment.payment_kind = 'adjustment'` is retained from the prototype, where it
meant "an extra amount due from this resident". Reconciliation still honours it
so historical data reconciles, but new corrections should use a signed
`ledger_adjustment`, which carries a reason and a reversal trail.

---

## What is covered, for contrast

So the list above is read in proportion — all of the following are implemented
and tested:

- the twenty required accounting invariants, against a realistic two-month
  household with partial payments, an overpayment, a linked provider advance,
  an electricity bill, a closed statement, and a post-close adjustment;
- migration from a populated prototype database with counts, totals, and
  reconciliation all verified;
- the complete operator journey over HTTP, including a restart;
- the API contract, the error envelope, the confirmation guards, and every
  report and export;
- integer-paise storage end to end, enforced in the browser, in request
  validation, in the services, and by the column types.

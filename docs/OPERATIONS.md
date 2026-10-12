# Operations

Installing, migrating, backing up, restoring, and troubleshooting.

Written for: whoever runs this on the machine it lives on.

---

## Installation

**Requirements:** Python 3.10 or later, Node 18 or later, SQLite 3.35+
(bundled with Python).

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'

cd frontend && npm install && npm run build && cd ..

./scripts/run.sh
```

If `python3 -m venv` fails with an `ensurepip` error on Debian or Ubuntu,
either install `python3-venv`, or install the dependencies for your user and
point the scripts at your interpreter:

```bash
pip3 install --user fastapi 'uvicorn[standard]' pydantic pytest httpx
PYTHON=python3 ./scripts/run.sh
```

### What `run.sh` does

1. Creates or upgrades the database.
2. Warns if the frontend is not built.
3. Starts uvicorn on `127.0.0.1:8787`, serving both the API and the built UI.

### Development mode

```bash
./scripts/dev.sh
```

The API reloads on `:8787`; the Vite dev server runs on `:5173` and proxies
`/api` and `/health` to it. Open the Vite URL. Both stop together on Ctrl-C.

---

## Configuration

Every setting is an `AEM_`-prefixed environment variable; see the table in the
[README](../README.md#configuration). The two that matter most:

```bash
export AEM_DB_PATH=/home/you/household/apartment.sqlite
export AEM_HOST=127.0.0.1          # do not change this without adding auth
```

---

## Database location and layout

The whole application state is **one SQLite file**, `data/apartment.sqlite` by
default. In WAL mode you may also see `-wal` and `-shm` files beside it; those
are SQLite's own journal and shared-memory files. A clean shutdown checkpoints
the WAL back into the main file, which is why the backup command and a clean
stop both leave a self-contained file.

```bash
.venv/bin/python scripts/manage.py status
```

reports the schema version, the file size, the integrity check, and the record
count per table.

---

## Migrations

Migrations are numbered SQL files in `aem/migrations/`, applied once each,
in order, inside their own transaction, and recorded in `schema_migration`
with a checksum.

They run **automatically at startup** (`AEM_AUTO_MIGRATE=true`). To run them
explicitly:

```bash
.venv/bin/python scripts/manage.py init
```

### Upgrading an existing prototype database

A database created by the original command-line prototype has the baseline
tables but no `schema_migration`. The runner recognises that and **adopts** it
at version 1 — the baseline is marked applied without being re-run — then
applies everything after it.

**Nothing is dropped and nothing is rewritten.** Every resident, expense,
allocation, payment, statement, reading, and adjustment is preserved, and the
new columns are backfilled to the meaning the data already had:

| New column | Backfilled from |
| --- | --- |
| `expense_instance.settlement_period` | `expense_period` when it is a `YYYY-MM`, else the month of `expense_date` |
| `expense_instance.service_period` | the settlement period |
| `expense_instance.due_date` | the last day of the settlement month |
| `expense_instance.period_rule` | `custom` — an existing row keeps an explicit period rather than inheriting a rule retroactively |
| `expense_instance.apartment_id` | the statement, else any allocated resident, else the payer |
| `expense_instance.expense_kind` | `electricity` where smart-plug readings exist, else `general` |
| `payment.apartment_id` | the payer's apartment |
| `payment.status` | `active` |
| `ledger_adjustment.effective_date` | the date part of `created_at` |
| `expense_definition.recurrence` | `monthly` where `is_recurring = 1`, else `none` |

**Before upgrading a database with real data in it, take a backup.** The
migration runs in a transaction and rolls back on failure, but a backup costs
nothing:

```bash
.venv/bin/python scripts/manage.py backup
.venv/bin/python scripts/manage.py init
.venv/bin/python scripts/manage.py status     # check the counts
.venv/bin/python scripts/manage.py check      # check it still reconciles
```

`tests/test_migrations.py` builds a populated prototype database and asserts
that record counts, totals, and reconciliation all survive.

### Writing a new migration

1. Add `aem/migrations/NNNN_short_name.sql` with the next number.
2. Forward-only: never drop a table or a column, never delete rows. A test
   asserts this against the file text.
3. Backfill any new column so existing rows keep their meaning.
4. `ALTER TABLE ... ADD COLUMN` with `NOT NULL` needs a non-NULL default, and
   a column with `REFERENCES` must default to NULL — SQLite requires both.
5. Never edit a migration that has been applied. The runner compares checksums
   and reports a mismatch rather than ignoring it; add a new migration instead.

---

## Backup

```bash
.venv/bin/python scripts/manage.py backup
```

This uses SQLite's own online backup API, so it is **safe while the
application is running**, and it verifies the copy's integrity before
reporting success. Output lands in `./backups/apartment-<timestamp>.sqlite`.

```bash
.venv/bin/python scripts/manage.py backup --out /mnt/usb/household-backups
```

**Keep at least one copy somewhere other than this machine.** A backup beside
the original protects you from a bad migration, not from a lost disk.

### Copying the file by hand

If you prefer to copy it yourself, stop the application first so the WAL is
checkpointed, then copy `apartment.sqlite` — and the `-wal` file if one is
still present.

### A suggested rhythm

- Back up **before** every upgrade and before any bulk correction.
- Back up **after** finalizing each month, since that is the record you most
  need to be able to reproduce.
- Keep the last few months of backups, and at least one copy off the machine.

---

## Restore

```bash
.venv/bin/python scripts/manage.py restore backups/apartment-20261011T013036.sqlite
```

The restore:

1. refuses a backup that fails its integrity check;
2. refuses to overwrite an existing database unless you pass `--force`;
3. with `--force`, **moves the existing file aside** as
   `apartment-replaced-<timestamp>.sqlite` rather than deleting it;
4. copies the backup into place;
5. runs any pending migrations on it.

Afterwards:

```bash
.venv/bin/python scripts/manage.py status
.venv/bin/python scripts/manage.py check
```

---

## Health and validation

**`/health`** reports the schema version, whether it is current, the database
path, the SQLite integrity check, whether foreign keys are enforced, and
whether the server is bound to loopback. The Setup & health screen shows the
same thing.

**`scripts/manage.py check`** runs the accounting validation and prints every
named issue with the record at fault and a suggested action, then the account
positions. It exits non-zero if any error-severity issue stands, which makes
it usable as a periodic check.

---

## Troubleshooting

### "No apartment has been set up yet"

Expected on a fresh database. Create one on the Setup screen, or
`scripts/manage.py demo` for sample data.

### "Could not reach the backend"

The frontend could not reach the API. Check it is running
(`curl http://127.0.0.1:8787/health`) and that the port matches. In dev mode,
open the **Vite** URL (`:5173`), not the API port.

### The UI says the schema is out of date

```bash
.venv/bin/python scripts/manage.py init
```

### "database is locked"

Another process is holding a write lock. Connections use a 15-second busy
timeout, so this usually means a second copy of the server is running, or a
`sqlite3` shell is sitting in an open transaction. Close it.

### A statement will not finalize

It has error-severity validation issues. The statement screen lists them with
the record at fault. Fix those, or finalize with *Refuse to finalize while
accounting errors remain* unchecked if you have a deliberate reason.

### An expense cannot be edited

Its statement is closed. That is intentional. Record a ledger adjustment
instead — it corrects the balance without rewriting a closed month.

### Reconciliation refuses to report figures

The ledger is internally inconsistent — almost always an expense whose
allocations do not sum to its amount, which means something was edited outside
the application. The error names the expense. Fix the allocation and it will
reconcile.

### A resident appears to be credited twice

A provider advance is not linked to the expense it funds while that expense
also names a payer. The Reconciliation screen flags it as
`possible_double_counted_advance`. Link the advance, or clear the expense's
*Paid by* field.

### The frontend is not served at all

`frontend/dist` does not exist. Build it:

```bash
cd frontend && npm install && npm run build
```

---

## Running the tests

```bash
.venv/bin/python -m pytest        # backend: 289 tests
cd frontend && npm test           # frontend: 68 tests
cd frontend && npm run typecheck  # TypeScript
```

Useful subsets:

```bash
.venv/bin/python -m pytest tests/test_invariants.py   # the twenty accounting invariants
.venv/bin/python -m pytest tests/test_migrations.py   # upgrade safety
.venv/bin/python -m pytest tests/test_end_to_end.py   # the full operator journey
.venv/bin/python -m pytest test_apartment_ledger.py   # the original prototype suite
```

---

## Security posture

This application has **no authentication**, by design, for a single operator on
their own machine.

- It binds `127.0.0.1` by default. Setting `AEM_HOST` to anything else logs a
  warning at startup and shows one on the Setup screen.
- CORS allows only the local Vite dev origins.
- No endpoint accepts SQL from the client; every query is parameterised, and
  sort and filter values are matched against fixed allowlists.
- No endpoint exposes the filesystem. Static serving is confined to
  `frontend/dist` with a path check.
- The database is an ordinary file protected by ordinary file permissions. If
  the machine is shared, `chmod 600` it.

**Do not expose this to the internet**, and do not put it behind a reverse
proxy without adding authentication first.

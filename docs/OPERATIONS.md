# Operations

Installing, running the database, migrating, importing from SQLite, backing
up, restoring, and troubleshooting.

Written for: whoever runs this on the machine it lives on.

---

## Installation

**Requirements:** Python 3.10 or later, Node 18 or later, and **PostgreSQL 18**.

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'          # includes the PostgreSQL driver, psycopg 3

cd frontend && npm install && npm run build && cd ..
```

If `python3 -m venv` fails with an `ensurepip` error on Debian or Ubuntu,
either install `python3-venv`, or install the dependencies for your user and
point the scripts at your interpreter:

```bash
pip3 install --user fastapi 'uvicorn[standard]' pydantic 'psycopg[binary,pool]' pytest httpx
PYTHON=python3 ./scripts/dev.sh
```

### PostgreSQL 18

The application's database is PostgreSQL 18. For local use it runs as a
**project-scoped cluster**: its own data directory inside this project, its own
port, started and stopped by the development launcher. It is separate from any
PostgreSQL server the operating system may already run, and the scripts never
touch such a server.

You need PostgreSQL 18's programs (`initdb`, `pg_ctl`, `postgres`, `psql`,
`pg_dump`) installed; nothing installs them for you. For example:

| Platform | Install |
| --- | --- |
| Debian / Ubuntu | Add the [PGDG apt repository](https://www.postgresql.org/download/linux/), then `sudo apt install postgresql-18` |
| macOS (Homebrew) | `brew install postgresql@18` |
| Without root | `micromamba create -p ~/.local/pgsql18 -c conda-forge postgresql=18`, or build from [source](https://www.postgresql.org/ftp/source/) with `./configure --prefix=$HOME/.local/pgsql18` |

Then tell the project where the `bin` directory is, unless it is on `PATH` or
in one of the standard places above:

```bash
cp .env.example .env.local
# in .env.local:
AEM_PG_BIN_DIR=/usr/lib/postgresql/18/bin
```

**The major version is checked, and only 18 is used.** If the scripts find a
different version — on `PATH`, or in `AEM_PG_BIN_DIR` — they say which one
they found and stop. They never fall back to it.

### Development mode

```bash
./scripts/dev.sh
```

In order, this:

1. starts the project's PostgreSQL cluster and waits until it accepts
   connections;
2. creates or upgrades the schema, as the migration role;
3. starts the API with reload on `:8787` and the Vite dev server on `:5173`,
   which proxies `/api` and `/health` to it. Open the Vite URL.

On Ctrl-C, or when either process exits, the API and Vite are stopped first
and PostgreSQL after them — **but only if this run started it**. If the
cluster was already running when you launched `dev.sh`, it is left running.

The very first run also creates the cluster: `initdb` into `data/postgres/`,
a generated password appended to `.env.local`, and the `aem` database. Later
runs reuse it. An existing data directory is never re-initialised.

### The local cluster on its own

```bash
./scripts/local_postgres.sh start     # create on first use, then start
./scripts/local_postgres.sh status
./scripts/local_postgres.sh stop
```

Use this when you want the database up without the dev servers — to run
`manage.py` commands, or `./scripts/run.sh`. A cluster started this way stays
up until you stop it.

| | |
| --- | --- |
| Data directory | `data/postgres/` (ignored by Git; override with `AEM_PG_DATA_DIR`) |
| Address | `127.0.0.1:55432` only — no other interface, no Unix socket |
| Application database | `aem` (`AEM_DB_NAME`) |
| Test database | `aem_test` (`AEM_TEST_DB_NAME`) |
| Runtime role | `aem` (`AEM_DB_USER`), password `AEM_DB_PASSWORD`. Serves requests; owns nothing |
| Migration role | `aem_owner` (`AEM_DB_MIGRATION_USER`), password `AEM_DB_MIGRATION_PASSWORD`. Owns both databases and their tables |
| Bootstrap administrator | `aem_admin` (`AEM_PG_ADMIN_USER`), password `AEM_PG_ADMIN_PASSWORD` |
| Authentication | `scram-sha-256` passwords, generated into `.env.local` |
| Locale | `C`, so text sorts by byte value as the application expects |
| Server log | `data/postgres/server.log` |

Port `55432` is deliberate: it is not PostgreSQL's default, so the cluster
cannot collide with, or be mistaken for, another server. Change it with
`AEM_PG_PORT`.

**Three roles, deliberately.**

- The **bootstrap administrator** is the cluster's superuser. Only
  `local_postgres.sh` uses it: to create the other two roles, the two
  databases, and the trigger guard. The application is never given its
  password and never connects as it.
- The **migration role** owns the databases and so every table created in
  them. Only `manage.py init`, `import-sqlite`, `restore`, and the test setup
  connect as it.
- The **runtime role** is what the application serves requests as. It owns
  nothing and cannot create, alter, drop, or truncate anything; the migration
  role grants it row-level access to the tables whenever it migrates.

Neither application role is a superuser or has `CREATEDB`, `CREATEROLE`,
`REPLICATION`, or `BYPASSRLS`; every start re-checks that and refuses to
continue otherwise. `dev.sh` migrates as the migration role and then starts the
server with that role's password blanked, so the serving process could not
change the schema even if it tried. The full account of what each role can do
is in
[`KNOWN_LIMITATIONS.md`](KNOWN_LIMITATIONS.md#what-each-database-role-can-and-cannot-do).

What the script will not do:

- initialise over a directory that already holds anything;
- start a cluster that a different PostgreSQL major version created (it tells
  you, and leaves the directory alone);
- stop any server other than the one in its own data directory.

### Serving without the dev servers

```bash
./scripts/local_postgres.sh start     # locally; a deployment skips this
./scripts/run.sh
```

`run.sh` creates or upgrades the schema (when the migration role's password is
available to it; a serve-only host skips this), warns if the frontend is not
built, and starts uvicorn on `127.0.0.1:8787` serving both the API and the built UI.
It **connects** to the configured database and does nothing else to it — it
does not start or stop PostgreSQL. That makes it the entry point for a
deployment as well.

---

## Configuration

Every setting is an `AEM_`-prefixed environment variable; see the table in the
[README](../README.md#configuration).

Local settings go in **`.env.local`** at the project root. The application and
the scripts both read it, Git ignores it, and `.env.example` is the template. A
variable set in the real environment always wins over the file, so a deployment
that configures itself through its environment needs no file at all.

```bash
AEM_DB_PASSWORD=...                # runtime role; generated on first ./scripts/dev.sh
AEM_DB_MIGRATION_PASSWORD=...      # migration role; generated the same way
AEM_PG_ADMIN_PASSWORD=...          # bootstrap administrator; scripts only
AEM_PG_BIN_DIR=/usr/lib/postgresql/18/bin
AEM_HOST=127.0.0.1                 # do not change this without adding auth
```

**Never commit a password.** `.env.local` is created with mode `600`, and no
script prints the password or puts it on a command line.

If `.env.local` is lost while `data/postgres/` still exists, the cluster keeps
its old password and the scripts will say they cannot sign in. Put the password
back; the data is unaffected.

### Connecting to a PostgreSQL endpoint in AWS

The application is the same; only the connection settings change. Nothing in
this repository provisions AWS resources — create the database with whatever
you use for infrastructure, then point the application at it:

```bash
AEM_DATABASE_URL=postgresql://aem:PASSWORD@your-endpoint.region.rds.amazonaws.com:5432/aem
AEM_DB_SSLMODE=verify-full
AEM_DB_SSLROOTCERT=/etc/ssl/certs/aws-global-bundle.pem
```

or the same thing as `AEM_DB_HOST`, `AEM_DB_PORT`, `AEM_DB_NAME`,
`AEM_DB_USER`, `AEM_DB_PASSWORD`. Supply the password from your secret store
through the environment rather than a file on disk. Start the application with
`./scripts/run.sh` or `scripts/manage.py serve`; do not use `dev.sh` or
`local_postgres.sh` there.

Three things to get right on a managed database:

- **Engine.** Standard PostgreSQL 18 (Amazon RDS for PostgreSQL, or Aurora
  PostgreSQL-Compatible). The schema uses PL/pgSQL triggers, deferrable
  foreign keys, partial unique indexes, and advisory locks. **Aurora DSQL is
  not supported**: it has none of those.
- **Collation.** Create the database with the `C` collation, as the local
  cluster does, so sorted lists come back in the order the application was
  written against:
  `CREATE DATABASE aem TEMPLATE template0 ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C';`
  `manage.py status` tells you if the database uses another one.
- **Roles.** Do not connect the application as the instance's master user.
  As the master user, create two ordinary roles and give the database to the
  migration role:

  ```sql
  CREATE ROLE aem_owner LOGIN PASSWORD '…';   -- migration role
  CREATE ROLE aem       LOGIN PASSWORD '…';   -- runtime role
  CREATE DATABASE aem OWNER aem_owner TEMPLATE template0
         ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C';
  REVOKE TEMPORARY ON DATABASE aem FROM PUBLIC;
  ```

  Neither needs `rds_superuser`, `CREATEDB`, or `CREATEROLE`.
- **Migrate as one, serve as the other.** Run
  `scripts/manage.py init` as a deployment step with
  `AEM_DB_MIGRATION_USER` / `AEM_DB_MIGRATION_PASSWORD` (or
  `AEM_MIGRATION_DATABASE_URL`) set. It creates the tables, owned by the
  migration role, and grants the runtime role its access. Give the serving
  hosts only `AEM_DB_USER` / `AEM_DB_PASSWORD`: the application does not
  migrate, and without the migration password it cannot. It logs a warning at
  startup if it finds itself connected as a role that is privileged, owns
  tables, or can change the schema.
- **Trigger guard.** As the master user, once, apply
  `scripts/sql/trigger_guard.sql` to the database. It stops even the migration
  role from disabling the schema's integrity triggers. `manage.py status`
  reports whether it is installed.

The application has no authentication of its own (see
[Security posture](#security-posture)), so a deployment must put it behind
something that does before anybody else can reach it.

---

## Database location and layout

The whole application state is **one PostgreSQL database**. Locally its files
are in `data/postgres/`; that directory is PostgreSQL's own, and is only ever
read or written by a PostgreSQL 18 server — never copy it while the server is
running, and never edit it by hand.

```bash
.venv/bin/python scripts/manage.py status
```

reports where the database is, whether it is reachable, the server version and
collation, the schema version, which role it is connected as, whether that
role is privileged, owns any table, or could change the schema (it should be
none of those), whether the trigger guard is installed, and the record count
per table.

---

## Migrations

Migrations are numbered SQL files in `aem/persistence/postgres/migrations/`,
applied once each, in order, inside their own transaction, and recorded in
`schema_migration` with a checksum.

They are applied by one command, as the migration role:

```bash
.venv/bin/python scripts/manage.py init
```

`dev.sh` and `run.sh` run it for you before starting the server (`run.sh` only
where the migration role's password is configured). **The application itself
never migrates**: it serves as the runtime role, which cannot change the
schema. If the schema is behind the code, the app logs an error and `/health`
reports `degraded` until `init` has been run. After applying migrations,
`init` re-grants the runtime role its access, so new tables are reachable.

**Before upgrading a database with real data in it, take a backup.** A
migration runs in a transaction and rolls back on failure, but a backup costs
nothing:

```bash
.venv/bin/python scripts/manage.py backup
.venv/bin/python scripts/manage.py init
.venv/bin/python scripts/manage.py status     # check the counts
.venv/bin/python scripts/manage.py check      # check it still reconciles
```

### Writing a new migration

1. Add `aem/persistence/postgres/migrations/NNNN_short_name.sql` with the next
   number.
2. Forward-only: never drop a table or a column, never delete rows.
3. Backfill any new column so existing rows keep their meaning.
4. Use standard PostgreSQL only — nothing that needs a superuser or an
   extension — so the same file runs on a managed database.
5. Give a new table a `row_seq BIGINT GENERATED BY DEFAULT AS IDENTITY` column
   and declare its foreign keys `DEFERRABLE`, like the others.
6. Never edit a migration that has been applied. The runner compares checksums
   and reports a mismatch rather than ignoring it; add a new migration instead.

---

## Moving from SQLite

Earlier versions kept everything in one SQLite file, `data/apartment.sqlite`.
The application no longer reads that file. **It is never deleted, changed, or
imported automatically**: moving its contents into PostgreSQL is a command you
run, once.

```bash
./scripts/local_postgres.sh start

# 1. Rehearse. Does the whole import and every check, then rolls it back.
.venv/bin/python scripts/manage.py import-sqlite --dry-run

# 2. Do it.
.venv/bin/python scripts/manage.py import-sqlite

# 3. Confirm.
.venv/bin/python scripts/manage.py status     # compare the counts
.venv/bin/python scripts/manage.py check      # it must still reconcile
```

The source defaults to `data/apartment.sqlite`; name another with
`--sqlite /path/to/file.sqlite`. Stop the old application first, so nothing is
writing to the file while it is read.

### What the import guarantees

- **The SQLite file is only read.** It is opened read-only and copied with
  SQLite's backup API to a temporary file; all work happens on the copy. Its
  SHA-256 is taken before and after and must match, or the import is abandoned.
- **The PostgreSQL database must be empty.** If any application table holds a
  single row, the import refuses and changes nothing. It never merges into, or
  overwrites, existing records — so it cannot be run twice by accident.
- **All or nothing.** Every row is copied in one transaction. Any failure —
  a value that does not fit its column, a reference to a missing record, a
  total that does not match — rolls the whole thing back, leaving the target
  as it was.
- **Nothing is reinterpreted.** Record IDs, dates, timestamps, JSON documents
  (including finalized statement snapshots and every revision), the audit
  trail, and landlord receipt files are copied byte for byte. Amounts stay
  integer paise. SQLite's `rowid` becomes `row_seq`, so records keep the order
  they were entered in.
- **It is verified before it commits.** Every table is read back from
  PostgreSQL and compared with SQLite row by row, column by column, in order —
  receipt files included — and every foreign key is validated.
- **No guard is switched off.** The import runs as the migration role — it
  has to create the schema — with every trigger and constraint in force, so
  the schema's checks apply to the imported history as well. When it finishes
  it grants the runtime role its access.

An older SQLite database — including one from the original command-line
prototype — is brought up to the final SQLite schema first, **on the temporary
copy**, using the migration history kept in
`aem/persistence/sqlite_import/legacy_migrations/`. The file you point at is
not upgraded.

Because the guards stay on, the order of loading matters in one place. The
allocations of a closed statement cannot be inserted — that is exactly what a
guard forbids — so statements are loaded as drafts, their expenses and
allocations follow, and each statement is then given its real status in one
update, which the guards permit. The comparison afterwards is against the final
rows, status included.

### Rolling back

The SQLite file is untouched, so the way back is simply not to use the
PostgreSQL database: check out the last SQLite-based version of the code and
run it against the same file. Anything entered into PostgreSQL after the
import is not in the SQLite file, so decide before you start recording new
data. Keep the SQLite file, and a copy of it elsewhere, until you are
satisfied.

To redo the import from scratch, point `AEM_DB_NAME` at a new, empty database
(or remove the local cluster's `data/postgres/` directory yourself, with the
cluster stopped) and run it again. No command here deletes data for you.

---

## Backup

```bash
.venv/bin/python scripts/manage.py backup
```

This runs `pg_dump`, which takes a consistent snapshot and is **safe while the
application is running**. Output lands in
`./backups/apartment-<timestamp>.dump` (PostgreSQL's custom format), and the
command checks that `pg_restore` can read the file back before reporting
success. Landlord receipt files are stored in the database, so they are in the
dump too.

```bash
.venv/bin/python scripts/manage.py backup --out /mnt/usb/household-backups
```

`pg_dump` must be PostgreSQL 18's — the same `AEM_PG_BIN_DIR` the cluster
uses. The password is passed to it through the environment, never on the
command line.

**Keep at least one copy somewhere other than this machine.** A backup beside
the original protects you from a bad migration, not from a lost disk.

On a managed database, use the provider's snapshots as well; `manage.py backup`
still works against it and gives you a portable copy.

### Do not copy the data directory by hand

`data/postgres/` is not a backup format. A copy taken while the server runs is
inconsistent, and even a clean one only opens with the same PostgreSQL major
version. Use `manage.py backup`.

### A suggested rhythm

- Back up **before** every upgrade and before any bulk correction.
- Back up **after** finalizing each month, since that is the record you most
  need to be able to reproduce.
- Keep the last few months of backups, and at least one copy off the machine.

---

## Restore

```bash
.venv/bin/python scripts/manage.py restore backups/apartment-20261012T013036.dump
```

The restore loads a dump into an **empty** database, in one transaction, as the
migration role (which becomes the owner of the restored tables), then runs any
pending migrations and grants the runtime role its access. It **refuses a database that already
has the application's tables**: restore never overwrites, and there is no flag
to make it. To go back to a backup, create a new empty database, point
`AEM_DB_NAME` (or `AEM_DATABASE_URL`) at it, and restore there; the database
you were using is left as it was until you decide what to do with it.

Afterwards:

```bash
.venv/bin/python scripts/manage.py status
.venv/bin/python scripts/manage.py check
```

Backups made by earlier versions are `.sqlite` files. They are not restored
with this command; bring one in with
`manage.py import-sqlite --sqlite backups/<file>.sqlite`.

---

## Health and validation

**`/health`** reports the schema version, whether it is current, which
database the application is connected to (as `postgresql://user@host:port/name`
in the `database_path` field, without the password), whether it is reachable,
and whether the server is bound to loopback. The Setup & health screen shows the
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

### "PostgreSQL 18 is required, and was not found"

The scripts could not find PostgreSQL 18's programs, and will not use another
version. Install PostgreSQL 18 and set `AEM_PG_BIN_DIR` in `.env.local`; see
[PostgreSQL 18](#postgresql-18).

### "The database could not be reached"

The application is running but PostgreSQL is not, or the connection settings
are wrong. `scripts/manage.py status` shows what it is trying to connect to.
Locally, `./scripts/local_postgres.sh start` (or use `./scripts/dev.sh`, which
starts it for you). If the cluster will not start, read
`data/postgres/server.log`; a common cause is another program on port `55432`,
which `AEM_PG_PORT` moves you off.

### "Another change is still being saved"

Writes run one at a time, and one waited 15 seconds for its turn. That usually
means a `psql` session is sitting in an open transaction holding a lock. Close
it.

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
./scripts/local_postgres.sh start
.venv/bin/python -m pytest        # backend
cd frontend && npm test           # frontend: 121 tests
cd frontend && npm run typecheck  # TypeScript
```

The backend tests need the project's PostgreSQL cluster running. They use
**only the test database** (`aem_test`, created alongside the application's
database): each test gets a schema of its own there, created and migrated by
the migration role and dropped afterwards, while the test itself drives the
services as the runtime role — the same privileges they have in production. The fixtures refuse to run
against any database whose name does not end in `_test`, so the household's
records cannot be touched by a test run. To aim the tests elsewhere, set
`AEM_TEST_DB_NAME`, or `AEM_TEST_DATABASE_URL` for a different server.

The suite (488 tests) passes on PostgreSQL 18; see
[`KNOWN_LIMITATIONS.md`](KNOWN_LIMITATIONS.md#what-has-and-has-not-been-validated-on-postgresql-18).

Useful subsets:

```bash
.venv/bin/python -m pytest tests/test_engine_and_periods.py   # the pure engines; no database
.venv/bin/python -m pytest tests/test_invariants.py   # the accounting invariants and schema guards
.venv/bin/python -m pytest tests/test_migrations.py   # schema, SQLite upgrade and import safety
.venv/bin/python -m pytest tests/test_end_to_end.py   # the full operator journey
.venv/bin/python -m pytest tests/test_rent.py         # rent and the lease lifecycle
.venv/bin/python -m pytest legacy                     # the original prototype suite
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
- The local PostgreSQL cluster listens on `127.0.0.1` only, has no Unix
  socket, and requires a password for every connection. Its data directory is
  mode `700` and `.env.local`, which holds the password, is mode `600`.
- Credentials are never committed, logged, or returned by the API: `/health`
  shows the database's host, port, and name, not its password.

**Do not expose this to the internet**, and do not put it behind a reverse
proxy without adding authentication first.

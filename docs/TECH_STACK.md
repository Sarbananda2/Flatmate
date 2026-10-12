# Technology Stack

An overview of the technologies used by Apartment Expense Manager and how the
application stores its data.

## Stack at a glance

| Area | Technology | Role |
| --- | --- | --- |
| Frontend | React 18, TypeScript 5, React Router 6 | Browser interface and page navigation |
| Frontend tooling | Vite 5, npm | Development server, build, and package management |
| Styling | Project CSS and design tokens; Tailwind CSS is present in the frontend toolchain | Application appearance and responsive layout |
| Backend | Python 3.10+, FastAPI, Uvicorn, Pydantic 2 | HTTP API, application services, request and response validation |
| Database | PostgreSQL 18 | Persistent application records |
| Database access | psycopg 3 with a connection pool, behind a repository layer | The only route from application code to the database |
| Schema changes | Numbered SQL migrations | Versioned, checksummed, transactional database upgrades |
| Local database lifecycle | `initdb` / `pg_ctl`, driven by `scripts/local_postgres.sh` | A project-scoped cluster started and stopped with the dev launcher |
| Tests | pytest; Vitest, Testing Library, jsdom | Backend and frontend test suites |

There is no ORM. Routes call services, services call repositories through a
unit of work, and only the PostgreSQL adapter imports a database driver:

```text
FastAPI route → application service → repository / unit of work
              → PostgreSQL adapter → PostgreSQL
```

The frontend calls the backend over JSON HTTP. In development, Vite proxies API
requests to FastAPI. In a built installation, the backend serves the frontend
and API from the same local application.

## Storage model

### Application data: one PostgreSQL database

The canonical application data is stored in one PostgreSQL 18 database. It
contains the household, residents, expense definitions and expenses,
allocations, payments, statements, audit history, electricity readings, and
rent receipt attachments. Keeping attachments in the database means a normal
database backup includes them too.

**Locally**, that database lives in a PostgreSQL cluster belonging to this
project: its files are in `data/postgres/`, it listens only on
`127.0.0.1:55432`, and it requires a password kept in `.env.local`.
`./scripts/dev.sh` starts it before the API and stops it afterwards — only if
that run started it — and the data persists between runs. It is independent of
any other PostgreSQL server on the machine. PostgreSQL 18 itself has to be
installed; the scripts check the version and refuse any other.

**In a deployment**, the same application connects to a PostgreSQL endpoint
named by `AEM_DATABASE_URL` or the `AEM_DB_*` settings, for example a managed
PostgreSQL service in AWS. No AWS infrastructure is defined or provisioned in
this repository, and the application never starts or stops a database server
in either setting: it only connects.

The schema uses standard PostgreSQL features (PL/pgSQL triggers, deferrable
foreign keys, partial unique indexes, advisory locks) and needs no extension.
It does not run on SQLite or on Aurora DSQL.

The pre-PostgreSQL installation stored data in `data/apartment.sqlite`. Its
contents have been imported into the local PostgreSQL database; the SQLite file
remains unchanged as a rollback reference. Normal app operation does not read
it. The explicit `scripts/manage.py import-sqlite` command remains available
for a new, empty PostgreSQL database and verifies records and references before
committing.

### Browser storage: convenience state only

The browser uses `localStorage` for a few client-side convenience values, such
as the selected month and theme. The guided setup also saves its currently
edited draft there. Completed setup steps and all saved accounting records are
read from the API and persisted in PostgreSQL. Clearing browser storage can discard
an unsaved setup draft or reset preferences; it does not delete saved ledger
data.

### Write safety and consistency

- Each write unit of work is one PostgreSQL transaction. A successful
  operation commits as a whole; an exception rolls it back.
- Write transactions run one at a time (an advisory lock), so a service that
  reads a balance and then writes against it cannot be interleaved with another
  writer. A writer waits up to 15 seconds for its turn. Reads run in a
  read-only snapshot and are not blocked by a write in progress.
- Foreign keys are always enforced.
- Schema migrations run in order, each in its own transaction, and each is
  recorded with a checksum. They are applied by `scripts/manage.py init`, as a
  dedicated migration role that owns the schema. The application serves as a
  separate runtime role that owns nothing and cannot change the schema, so it
  does not migrate at startup.
- Database constraints and triggers protect important accounting invariants,
  including finalized or closed statement history and append-only records.
- Money is represented as integer paise in the application and database, not
  floating-point values.

## Backup and restore

Use the management command to make a consistent backup while the application
is running:

```bash
.venv/bin/python scripts/manage.py backup
```

By default, timestamped dumps are written under `backups/`. The command runs
PostgreSQL's `pg_dump` and checks that the dump can be read back. Restore with:

```bash
.venv/bin/python scripts/manage.py restore backups/<backup-file>.dump
```

The restore command loads a dump into an empty database only; it never
overwrites one that already holds the application's tables. Keep a backup on a
different device or location as well as any local copies. The database files,
dumps, and `.env.local` are runtime data and are not committed to the source
repository.

## Local access and credentials

The server defaults to `127.0.0.1:8787`. The local PostgreSQL cluster accepts
connections from `127.0.0.1` only and requires a password, which is generated
on first run into `.env.local` (mode `600`, ignored by Git) and is never
logged or returned by the API. The app has no user authentication, so it is intended
to remain local; exposing the HTTP port to a network would expose read and
write access to the household records. Optional Tapo smart-plug credentials
are supplied through `AEM_TAPO_USERNAME` and `AEM_TAPO_PASSWORD`; they are not
stored in the database or returned by the API.

## Where to look next

- [Architecture](ARCHITECTURE.md) — layers, request flow, and design decisions
- [Data model](DATA_MODEL.md) — tables and relationships
- [Operations](OPERATIONS.md) — installation, the local PostgreSQL cluster,
  AWS connection settings, migrations, SQLite import, backup, and restore
- [README](../README.md) — quick start and application overview

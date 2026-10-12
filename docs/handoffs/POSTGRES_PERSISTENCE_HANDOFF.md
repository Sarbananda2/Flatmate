# PostgreSQL Persistence and Local Development Handoff

**Status:** User-approved direction; implementation delegated to Claude Code
**Purpose:** Record the agreed design and repository context for the PostgreSQL and persistence-layer work.

## Objective

Move Apartment Expense Manager from direct SQLite access to a clear persistence
boundary backed by PostgreSQL, while keeping local development simple and
matching the intended AWS PostgreSQL environment.

The application must use the database only through persistence/repository
operations. PostgreSQL connections, SQL, driver errors, and transaction
mechanics belong inside the persistence layer. Application services retain
business rules and coordinate use cases; they do not receive a raw connection
or execute SQL.

## Decisions agreed with the user

1. **Database engine:** PostgreSQL 18 for local development, aligned with the
   intended AWS PostgreSQL deployment. Keep SQL portable to standard
   PostgreSQL; do not use SQLite-only behaviors or Aurora DSQL.
2. **Local host:** Native PostgreSQL server installation, managed as a
   project-specific local cluster. The current environment has PostgreSQL
   16.15 tools under `/home/sarbanandataw/.local/pgsql/bin`; this is not the
   selected version. The project needs PostgreSQL 18 tools for its cluster.
3. **Lifecycle:** The local development launcher starts PostgreSQL before the
   API/frontend and stops only the project cluster it started when the
   launcher exits or receives Ctrl-C. The database files persist between
   sessions. Do not start or stop PostgreSQL from FastAPI lifespan hooks.
4. **AWS configuration:** Local and AWS connections are configuration values
   for the same PostgreSQL-backed application. Do not provision AWS resources
   as part of this implementation. The exact AWS managed offering is not
   provisioned or finalized in this repository; keep the persistence layer
   compatible with standard PostgreSQL.
5. **Data safety:** Existing SQLite data, if present, is source data. Preserve
   it and provide an explicit, non-destructive migration/import path. Never
   delete, overwrite, or silently migrate the SQLite file on startup.

## Target architecture

```text
FastAPI routes / request dependencies
                 ↓
Application services (business rules, use cases, transaction coordination)
                 ↓
Repository interfaces / domain-specific persistence operations
                 ↓
PostgreSQL adapter (driver, SQL, row mapping, database errors)
                 ↓
Local PostgreSQL 18 or AWS PostgreSQL
```

Use domain-specific methods such as `save_expense_with_allocations`,
`find_open_charges`, or `append_audit_entry`. Avoid a generic CRUD layer that
allows callers to bypass business rules. Repositories may use explicit SQL;
the goal is a controlled boundary, not hiding SQL behind a generic ORM.

The accounting engines in `aem/engine/` should remain pure and database-free.
HTTP routers should remain transport-only. A transaction/unit-of-work boundary
must allow a service to coordinate multiple repository changes atomically.

## Current repository facts

- `aem/db.py` is a SQLite persistence helper: it uses Python `sqlite3`, file
  paths, SQLite pragmas, `BEGIN IMMEDIATE`, and SQLite row behavior.
- `aem/config.py` currently exposes `AEM_DB_PATH` and defaults to
  `data/apartment.sqlite`.
- Services under `aem/services/` issue SQL directly. A repository scan found
  about 290 SQLite/SQL/connection references under `aem/`.
- `aem/migrations/` contains SQLite-specific SQL and a SQLite-specific
  migration runner. Existing triggers encode important accounting and
  finalized-history protections.
- `aem/main.py` does startup migrations and a SQLite WAL checkpoint during
  shutdown. Database lifecycle must be reworked for PostgreSQL.
- `scripts/dev.sh` starts API and Vite together and handles child cleanup.
  `scripts/run.sh` runs the built app. Keep external/AWS database operation
  separate from the local-dev PostgreSQL lifecycle.
- `scripts/manage.py` currently has SQLite-specific init/status/backup/restore
  commands. Rework or replace them with safe PostgreSQL-aware equivalents.
- `data/` and `backups/` are ignored by Git. Keep the PostgreSQL cluster data
  under an ignored runtime-data directory, not in source control.
- The existing test suite and fixtures are SQLite-based. Preserve their
  behavioral coverage and identify the required test-adapter changes; do not
  remove tests to make the port easier.
- At handoff time, `README.md` has an uncommitted documentation-link change and
  `docs/TECH_STACK.md` is a new untracked document. Preserve both and any other
  pre-existing user edits; do not reset or discard workspace changes.

## Local server lifecycle requirements

- Use PostgreSQL's own `initdb` and `pg_ctl` for a dedicated cluster, with a
  project-scoped data directory such as `data/postgres/` and a port that avoids
  accidentally taking over an existing server (for example `55432`).
- Make the PostgreSQL binary directory, data directory, port, database name,
  username, and password configurable. Do not hard-code the current user's
  home path.
- Check the major version before initialization/startup. Fail with actionable
  instructions if PostgreSQL 18 tools are not available; do not silently use
  16 or another major version.
- Initialize the project cluster once. On subsequent launches, reuse its
  persistent data. Do not reinitialize an existing data directory.
- Start the cluster before app processes; wait for readiness before running
  migrations or serving requests. On exit, stop the API/frontend and then
  cleanly stop only the cluster launched by this invocation.
- Handle startup failure and Ctrl-C without leaving child processes running.
  If the cluster was already running before the launcher, do not stop it.
- Never put passwords in committed files or print secrets in logs. Provide an
  ignored local environment file/example and document setup.
- Do not have the application server itself manage a local or remote database
  process. Production/AWS startup only connects to its configured endpoint.

## Migration and compatibility requirements

- Convert the schema to PostgreSQL migrations while retaining the accounting
  invariants currently enforced by SQLite constraints and triggers.
- Make migration application transactional and record schema version/checksum
  (or use a well-supported migration runner with equivalent safeguards).
- Provide an explicit SQLite-to-PostgreSQL import for existing data. It must
  preserve IDs, relationships, integer paise amounts, timestamps, rent receipt
  blobs, audit history, finalized statement snapshots, and ordering-sensitive
  records. Preserve the SQLite source untouched.
- Import must be repeat-safe or refuse to run when the PostgreSQL target is
  non-empty; it must never merge ambiguously. Report counts and integrity
  checks before/after.
- Move database-specific SQL out of services and request handlers. Check for
  remaining direct driver/SQL access outside persistence, migrations, and
  operational tooling.
- Keep behavior aligned with the existing API and accounting rules. Do not
  simplify away triggers, audit logging, transaction boundaries, or immutability
  protections.

## Suggested execution stages

1. Inspect workspace instructions and map current access patterns, migrations,
   triggers, and SQLite fixtures. Preserve all existing edits.
2. Establish persistence contracts and a transaction/unit-of-work interface;
   move service SQL behind domain-specific repositories without changing
   behavior.
3. Add PostgreSQL 18 driver/configuration and PostgreSQL migrations. Convert
   routes/services to use the new persistence boundary.
4. Add project-scoped local cluster lifecycle commands and wire the dev
   launcher to start/wait/stop the cluster safely. Keep production connection
   behavior external and configuration-driven.
5. Add an explicit SQLite import/migration command and document the safe
   cutover/rollback process.
6. Update architecture, tech-stack, operations, and developer setup docs. Give
   a concise completion report listing files changed, decisions, migration
   instructions, and any remaining blockers.

## Out of scope

- Provisioning or changing AWS resources.
- Exposing PostgreSQL or the web app publicly.
- Deleting or rewriting the existing SQLite database.
- Replacing business rules or changing the user-facing API without necessity.
- Treating the existence of a local PostgreSQL cluster as proof that the app
  has been migrated; document the active backend clearly.

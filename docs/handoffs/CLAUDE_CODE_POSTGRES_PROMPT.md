# Claude Code Prompt: PostgreSQL Persistence and Local Development

Copy the prompt below into Claude Code from the project root.

---

You are implementing the user-approved PostgreSQL and persistence-boundary
plan for this repository. Read
[`POSTGRES_PERSISTENCE_HANDOFF.md`](POSTGRES_PERSISTENCE_HANDOFF.md) first and
follow it as the source of truth. Inspect any `AGENTS.md` or other repository
instructions before editing.

## Goal

Refactor Apartment Expense Manager so routes and application services do not
access a database driver, connection, cursor, or SQL directly. Put all database
access behind domain-specific repositories and a PostgreSQL adapter. Run
PostgreSQL 18 locally as a project-scoped service that starts with the
development launcher and is stopped by that launcher on exit. Keep its data
between runs. Configure the same app to connect to a PostgreSQL endpoint in
AWS later, without provisioning AWS now.

## Required architecture

```text
FastAPI route → application service → repository/unit of work
              → PostgreSQL adapter → PostgreSQL
```

- Services own business rules and orchestrate operations/transactions.
- Repositories own SQL and map database rows to application values.
- A unit-of-work/transaction abstraction allows multiple repositories to
  commit or roll back together.
- Routes must not execute SQL. Services must not import `sqlite3`, psycopg, or
  another database driver, and must not receive raw connections/cursors.
- Keep `aem/engine/` pure and independent of persistence.
- Prefer domain-specific repository methods over generic unrestricted CRUD.
  Use explicit SQL inside the adapter/repositories; an ORM is not required.

## Database and local development requirements

- Target PostgreSQL 18 and use standard PostgreSQL features compatible with
  AWS PostgreSQL offerings. Do not use SQLite or Aurora DSQL as the active
  backend after the cutover.
- `scripts/dev.sh` should start the local PostgreSQL cluster, wait until it is
  ready, then start backend and frontend. On exit/Ctrl-C it must stop only the
  cluster it started, after stopping the app processes.
- Keep the cluster's data in an ignored project runtime directory (for example
  `data/postgres/`). Persist it across restarts. Never reinitialize an existing
  data directory.
- Use `initdb`/`pg_ctl` from PostgreSQL 18. Make binary path and connection
  settings configurable. The current environment has PostgreSQL 16.15 at
  `/home/sarbanandataw/.local/pgsql/bin`; do not silently use it or hard-code
  that home path. If PostgreSQL 18 is unavailable, fail clearly with setup
  instructions rather than installing system packages or escalating privileges
  without user approval.
- Use a project-specific port such as `55432` to avoid colliding with an
  existing PostgreSQL instance. Never stop a server that was running before
  this launcher invocation.
- Store credentials outside committed source. Update `.gitignore` as needed.
- Do not make FastAPI startup/shutdown responsible for starting/stopping a
  database process. AWS deployments must only connect to the configured remote
  database.

## Data preservation and schema invariants

- Replace the SQLite migration path with versioned PostgreSQL migrations while
  preserving accounting checks, constraints, triggers, audit append-only
  behavior, and closed/finalized statement immutability.
- Provide an explicit, non-destructive SQLite-to-PostgreSQL import path for
  existing installations. Preserve source SQLite files; preserve IDs, foreign
  key relationships, integer paise values, attachments, audit history, and
  finalized snapshots. Refuse or safely handle a non-empty PostgreSQL target;
  never merge records ambiguously.
- Preserve existing API behavior and data semantics. Do not remove legacy
  behavior just to make the port smaller.
- Update developer/operations documentation, architecture documentation, and
  `docs/TECH_STACK.md` to describe the actual implemented state, not a future
  intention.

## Workspace safety

- Start by checking `git status` and preserve existing user changes.
- At handoff time, `README.md` contains an uncommitted change linking
  `docs/TECH_STACK.md`, and `docs/TECH_STACK.md` is a new untracked file. Keep
  both. Do not run `git reset`, `git checkout --`, `git clean`, or overwrite
  unrelated edits.
- The current application uses SQLite and its existing database may contain
  real data. Never delete, overwrite, or migrate that database automatically.
- Do not provision AWS infrastructure.
- Do not run test commands unless the user separately asks for testing or
  verification. Preserve existing tests/fixtures; identify necessary changes
  and report the validation commands that should be run later.

## Execution approach

This is a broad change. Work incrementally in the stages in the handoff
document, keeping each stage coherent. Do not stop after producing a plan;
implement the requested work within the repository. If an unresolved decision
or missing PostgreSQL 18 installation blocks a stage, complete independent
work, then report the exact blocker and the smallest action needed from the
user. Do not make destructive guesses.

Before finishing:

1. Inspect the diff and make sure no user changes were lost.
2. Search for remaining direct database-driver/SQL access outside the
   persistence boundary, migrations, and operational tools; report any
   intentional exceptions.
3. Do not claim the app is PostgreSQL-backed unless its normal API/service
   paths actually use PostgreSQL.
4. Report the implementation stages completed, configuration/setup steps,
   SQLite import command and safeguards, any unrun validation, and blockers.

---

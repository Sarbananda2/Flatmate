"""Configuration, from the environment with safe local-first defaults.

Local security assumptions
--------------------------

This is a single-operator application that runs on the operator's own machine.
There is no authentication, and none is pretended: anyone who can reach the HTTP
port can read and write the household's accounts. The defaults are chosen so
that only this machine can reach it:

* the server binds ``127.0.0.1`` by default, never ``0.0.0.0``;
* CORS allows only the local Vite dev origins;
* the database is PostgreSQL. Locally it is a project-scoped cluster that
  listens on loopback only and requires a password; in AWS it is whatever
  endpoint the settings below name.

Do not expose this to the internet, and do not put it behind a reverse proxy
without adding authentication first. Every setting below can be overridden with
an ``AEM_``-prefixed environment variable.

Database settings
-----------------

The application uses two PostgreSQL roles and keeps them apart:

* the **runtime role** (``AEM_DB_USER`` / ``AEM_DB_PASSWORD``) serves requests.
  It owns nothing and cannot change the schema;
* the **migration role** (``AEM_DB_MIGRATION_USER`` /
  ``AEM_DB_MIGRATION_PASSWORD``) owns the tables. Only ``manage.py init``,
  ``import-sqlite``, ``restore``, and the test setup use it. A serving process
  does not need its password, and should not be given it.

``AEM_DATABASE_URL`` (a libpq URL) or the separate ``AEM_DB_HOST``,
``AEM_DB_PORT``, ``AEM_DB_NAME``, ``AEM_DB_USER``, and ``AEM_DB_PASSWORD`` say
where the database is. The defaults point at the local development cluster on
``127.0.0.1:55432``. The application only ever *connects*: it does not start,
stop, or provision a database server.

Secrets are never committed. For local development they live in ``.env.local``
at the project root (ignored by Git; see ``.env.example``), which is read here
if it exists. A variable already set in the environment always wins, so a
deployment configures itself through its environment and needs no file.

The optional Tapo connector is the one place this application reaches out: it
talks to smart plugs on the local network with the operator's TP-Link account
(``AEM_TAPO_USERNAME`` / ``AEM_TAPO_PASSWORD``). Those are read from the
environment only, never stored in the database and never returned by the API.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

from .persistence.postgres.config import (
    DEFAULT_HOST as DEFAULT_DB_HOST,
    DEFAULT_NAME as DEFAULT_DB_NAME,
    DEFAULT_PORT as DEFAULT_DB_PORT,
    DEFAULT_USER as DEFAULT_DB_USER,
    PostgresConfig,
)

ENV_PREFIX = "AEM_"
ENV_FILE_NAME = ".env.local"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787


def load_env_file(path: Path) -> list[str]:
    """Read ``KEY=value`` lines into the environment, without overriding it.

    Returns the names that were set. Blank lines and ``#`` comments are
    skipped, an optional leading ``export`` is allowed, and matching quotes
    around a value are removed. Values are never logged.
    """
    if not path.is_file():
        return []
    loaded: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.removeprefix("export ").partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(f"{ENV_PREFIX}{name}")
    return default if value is None or value == "" else value


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class TapoPlug:
    """One smart plug and the resident whose usage it measures."""

    resident_id: str
    ip_address: str
    label: str


def _tapo_plugs() -> tuple[TapoPlug, ...]:
    """Parse ``AEM_TAPO_PLUGS``: ``alice=192.168.1.21:Room 1 AC,bob=192.168.1.22``.

    A malformed entry is skipped rather than guessed at; the sync endpoint
    reports how many plugs it actually has.
    """
    plugs: list[TapoPlug] = []
    for entry in (_env("TAPO_PLUGS") or "").split(","):
        resident_id, _, target = entry.strip().partition("=")
        ip_address, _, label = target.partition(":")
        if resident_id.strip() and ip_address.strip():
            plugs.append(TapoPlug(resident_id.strip(), ip_address.strip(),
                                  label.strip() or "Tapo plug"))
    return tuple(plugs)


@dataclass(frozen=True)
class Settings:
    """Resolved configuration. Read once at startup via :func:`get_settings`."""

    project_root: Path
    # The runtime role's connection: what serving requests uses.
    database: PostgresConfig
    # The schema owner's connection, or None when this process has not been
    # given it. Used only by migration and import tooling.
    migration_database: PostgresConfig | None = None
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    cors_origins: tuple[str, ...] = ()
    auto_close_expired_statements: bool = True
    auto_close_interval_minutes: int = 60
    default_correction_window_hours: int = 72
    operator_name: str = "operator"
    # Mark the session cookie Secure even when the request reaches the app over
    # plain HTTP, as it does behind a TLS-terminating proxy that does not pass
    # the original scheme on. Over HTTPS it is marked Secure regardless.
    secure_cookies: bool = False
    log_level: str = "INFO"
    log_sql: bool = False
    tapo_username: str | None = None
    tapo_password: str | None = None
    tapo_plugs: tuple[TapoPlug, ...] = ()

    def describe(self) -> dict[str, object]:
        """Non-secret view of the configuration, for the health endpoint."""
        return {
            "database_backend": "postgresql",
            # Host, port, and names only. The password is never included.
            "database": self.database.describe(),
            "database_is_local": self.database.is_local,
            "host": self.host,
            "port": self.port,
            "cors_origins": list(self.cors_origins),
            "auto_close_expired_statements": self.auto_close_expired_statements,
            "default_correction_window_hours": self.default_correction_window_hours,
            "operator_name": self.operator_name,
            "log_level": self.log_level,
            "auto_close_interval_minutes": self.auto_close_interval_minutes,
            # Whether a connector is set up, never the credentials themselves.
            "tapo_configured": bool(self.tapo_username and self.tapo_password
                                    and self.tapo_plugs),
            "tapo_plug_count": len(self.tapo_plugs),
            "binds_loopback_only": self.host in {"127.0.0.1", "localhost", "::1"},
        }


def _default_origins() -> tuple[str, ...]:
    raw = _env("CORS_ORIGINS")
    if raw:
        return tuple(origin.strip() for origin in raw.split(",") if origin.strip())
    # The Vite dev server. The built frontend is served by this app itself and
    # needs no CORS entry at all.
    return ("http://127.0.0.1:5173", "http://localhost:5173")


def _database_config() -> PostgresConfig:
    return PostgresConfig(
        host=_env("DB_HOST", DEFAULT_DB_HOST) or DEFAULT_DB_HOST,
        port=_env_int("DB_PORT", DEFAULT_DB_PORT),
        name=_env("DB_NAME", DEFAULT_DB_NAME) or DEFAULT_DB_NAME,
        user=_env("DB_USER", DEFAULT_DB_USER) or DEFAULT_DB_USER,
        password=_env("DB_PASSWORD"),
        url=_env("DATABASE_URL"),
        sslmode=_env("DB_SSLMODE"),
        sslrootcert=_env("DB_SSLROOTCERT"),
        schema=_env("DB_SCHEMA"),
        connect_timeout_seconds=max(1, _env_int("DB_CONNECT_TIMEOUT", 10)),
        pool_min_size=max(0, _env_int("DB_POOL_MIN", 1)),
        pool_max_size=max(1, _env_int("DB_POOL_MAX", 5)),
    )


DEFAULT_DB_MIGRATION_USER = "aem_owner"


def _migration_database_config(runtime: PostgresConfig) -> PostgresConfig | None:
    """The schema owner's connection: the same database, a different role.

    ``None`` unless that role's password (or a URL of its own) is configured,
    which is how a serving process ends up unable to migrate.
    """
    url = _env("MIGRATION_DATABASE_URL")
    password = _env("DB_MIGRATION_PASSWORD")
    if not url and not password:
        return None
    return replace(
        runtime,
        user=_env("DB_MIGRATION_USER", DEFAULT_DB_MIGRATION_USER) or DEFAULT_DB_MIGRATION_USER,
        password=password,
        url=url,
        grant_to=_env("DB_USER", DEFAULT_DB_USER) or DEFAULT_DB_USER,
        pool_min_size=0, pool_max_size=1,
    )


def get_settings(*, project_root: Path | None = None) -> Settings:
    root = Path(project_root or _env("PROJECT_ROOT") or Path(__file__).resolve().parent.parent)
    env_file = Path(_env("ENV_FILE") or root / ENV_FILE_NAME).expanduser()
    load_env_file(env_file)
    database = _database_config()
    return Settings(
        project_root=root,
        database=database,
        migration_database=_migration_database_config(database),
        host=_env("HOST", DEFAULT_HOST) or DEFAULT_HOST,
        port=_env_int("PORT", DEFAULT_PORT),
        cors_origins=_default_origins(),
        auto_close_expired_statements=_env_bool("AUTO_CLOSE_EXPIRED", True),
        default_correction_window_hours=_env_int("CORRECTION_WINDOW_HOURS", 72),
        operator_name=_env("OPERATOR_NAME", "operator") or "operator",
        secure_cookies=_env_bool("SECURE_COOKIES", False),
        log_level=(_env("LOG_LEVEL", "INFO") or "INFO").upper(),
        log_sql=_env_bool("LOG_SQL", False),
        auto_close_interval_minutes=max(0, _env_int("AUTO_CLOSE_INTERVAL_MINUTES", 60)),
        tapo_username=_env("TAPO_USERNAME"),
        tapo_password=_env("TAPO_PASSWORD"),
        tapo_plugs=_tapo_plugs(),
    )

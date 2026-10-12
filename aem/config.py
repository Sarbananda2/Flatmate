"""Configuration, from the environment with safe local-first defaults.

Local security assumptions
--------------------------

This is a single-operator application that runs on the operator's own machine.
There is no authentication, and none is pretended: anyone who can reach the HTTP
port can read and write the household's accounts. The defaults are chosen so
that only this machine can reach it:

* the server binds ``127.0.0.1`` by default, never ``0.0.0.0``;
* CORS allows only the local Vite dev origins;
* the database is a file under the project directory, with normal file
  permissions.

Do not expose this to the internet, and do not put it behind a reverse proxy
without adding authentication first. Every setting below can be overridden with
an ``AEM_``-prefixed environment variable.

The optional Tapo connector is the one place this application reaches out: it
talks to smart plugs on the local network with the operator's TP-Link account
(``AEM_TAPO_USERNAME`` / ``AEM_TAPO_PASSWORD``). Those are read from the
environment only, never stored in the database and never returned by the API.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ENV_PREFIX = "AEM_"
DEFAULT_DB_FILENAME = "apartment.sqlite"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787


def _env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(f"{ENV_PREFIX}{name}", default)


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
    database_path: Path
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    cors_origins: tuple[str, ...] = ()
    auto_migrate: bool = True
    auto_close_expired_statements: bool = True
    auto_close_interval_minutes: int = 60
    default_correction_window_hours: int = 72
    operator_name: str = "operator"
    log_level: str = "INFO"
    log_sql: bool = False
    tapo_username: str | None = None
    tapo_password: str | None = None
    tapo_plugs: tuple[TapoPlug, ...] = ()

    def describe(self) -> dict[str, object]:
        """Non-secret view of the configuration, for the health endpoint."""
        return {
            "database_path": str(self.database_path),
            "host": self.host,
            "port": self.port,
            "cors_origins": list(self.cors_origins),
            "auto_migrate": self.auto_migrate,
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


def get_settings(*, project_root: Path | None = None) -> Settings:
    root = Path(project_root or _env("PROJECT_ROOT") or Path(__file__).resolve().parent.parent)
    raw_db = _env("DB_PATH")
    database_path = Path(raw_db).expanduser() if raw_db else root / "data" / DEFAULT_DB_FILENAME
    if not database_path.is_absolute():
        database_path = (root / database_path).resolve()
    return Settings(
        project_root=root,
        database_path=database_path,
        host=_env("HOST", DEFAULT_HOST) or DEFAULT_HOST,
        port=_env_int("PORT", DEFAULT_PORT),
        cors_origins=_default_origins(),
        auto_migrate=_env_bool("AUTO_MIGRATE", True),
        auto_close_expired_statements=_env_bool("AUTO_CLOSE_EXPIRED", True),
        default_correction_window_hours=_env_int("CORRECTION_WINDOW_HOURS", 72),
        operator_name=_env("OPERATOR_NAME", "operator") or "operator",
        log_level=(_env("LOG_LEVEL", "INFO") or "INFO").upper(),
        log_sql=_env_bool("LOG_SQL", False),
        auto_close_interval_minutes=max(0, _env_int("AUTO_CLOSE_INTERVAL_MINUTES", 60)),
        tapo_username=_env("TAPO_USERNAME"),
        tapo_password=_env("TAPO_PASSWORD"),
        tapo_plugs=_tapo_plugs(),
    )

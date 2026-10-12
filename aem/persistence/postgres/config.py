"""Connection settings for a PostgreSQL database.

The same settings describe the project's local development cluster and a
managed PostgreSQL endpoint in AWS; only the values differ. Nothing here
starts, stops, or provisions a server.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

DEFAULT_HOST = "127.0.0.1"
# Deliberately not 5432, so the project's cluster never collides with, or is
# mistaken for, another PostgreSQL server on the same machine.
DEFAULT_PORT = 55432
DEFAULT_NAME = "aem"
DEFAULT_USER = "aem"


@dataclass(frozen=True)
class PostgresConfig:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    name: str = DEFAULT_NAME
    user: str = DEFAULT_USER
    password: str | None = None
    # A complete libpq URL or conninfo string. When set it replaces host, port,
    # name, user, and password above.
    url: str | None = None
    # libpq's sslmode. Leave unset locally; use "require" or "verify-full"
    # (with sslrootcert) for a remote endpoint.
    sslmode: str | None = None
    sslrootcert: str | None = None
    # Use a schema other than the role's default. Mostly for isolated test runs.
    schema: str | None = None
    # Only meaningful on the *migration* role's settings: the runtime role that
    # is granted day-to-day access to whatever this role creates.
    grant_to: str | None = None
    connect_timeout_seconds: int = 10
    pool_min_size: int = 1
    pool_max_size: int = 5

    def connection_kwargs(self) -> dict[str, object]:
        """Keyword arguments for the driver. Contains the password."""
        kwargs: dict[str, object] = {
            "connect_timeout": self.connect_timeout_seconds,
            "application_name": "apartment-expense-manager",
        }
        if self.url:
            # psycopg.connect() needs the URL passed as its conninfo argument;
            # merely omitting host/dbname/etc. would silently fall back to
            # libpq defaults instead of connecting to the configured endpoint.
            kwargs["conninfo"] = self.url
        else:
            kwargs.update(host=self.host, port=self.port, dbname=self.name, user=self.user)
            if self.password:
                kwargs["password"] = self.password
        if self.sslmode:
            kwargs["sslmode"] = self.sslmode
        if self.sslrootcert:
            kwargs["sslrootcert"] = self.sslrootcert
        if self.schema:
            kwargs["options"] = f"-c search_path={self.schema}"
        return kwargs

    def describe(self) -> str:
        """``postgresql://user@host:port/name`` -- never the password."""
        if self.url:
            if "://" not in self.url:
                return "postgresql (connection string configured)"
            parts = urlsplit(self.url)
            user = f"{parts.username}@" if parts.username else ""
            port = f":{parts.port}" if parts.port else ""
            target = f"postgresql://{user}{parts.hostname or ''}{port}{parts.path}"
        else:
            target = f"postgresql://{self.user}@{self.host}:{self.port}/{self.name}"
        return f"{target} (schema {self.schema})" if self.schema else target

    @property
    def is_local(self) -> bool:
        if self.url:
            host = urlsplit(self.url).hostname if "://" in self.url else None
        else:
            host = self.host
        return host in {"127.0.0.1", "localhost", "::1"}

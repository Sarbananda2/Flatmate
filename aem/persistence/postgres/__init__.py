"""PostgreSQL implementation of the persistence boundary.

``PostgresConfig`` is plain data and importable without the driver;
``PostgresDatabase`` loads the driver when first asked for.
"""
from .config import PostgresConfig

__all__ = ["PostgresConfig", "PostgresDatabase"]


def __getattr__(name: str):
    if name == "PostgresDatabase":
        from .adapter import PostgresDatabase
        return PostgresDatabase
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

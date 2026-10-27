"""Database connections by name.

A tool names its connection (``config.connection`` or ``config.connectionName``) and
never carries a connection string. The unnamed (or ``default``) connection is the
MSSQL one from ``DB_CONNECTION_STRING`` / ``MSSQL_*``; other names come from
``DATABASE_CONNECTIONS`` (JSON ``{"name": "<dsn>"}``). A DSN that starts with
``postgresql://`` or ``postgres://`` is a PostgreSQL connection; any other is an
ODBC connection string for SQL Server.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

DEFAULT_CONNECTION = 'default'
MSSQL = 'mssql'
POSTGRESQL = 'postgresql'

_POSTGRES_SCHEMES = ('postgresql://', 'postgres://')

_registry: ConnectionRegistry | None = None


@dataclass(frozen=True)
class DatabaseConnection:
    """A resolved connection: its name, its kind (``mssql`` or ``postgresql``) and DSN."""

    name: str
    kind: str
    dsn: str

    def __repr__(self) -> str:  # never print the DSN: it holds the password
        return f'DatabaseConnection(name={self.name!r}, kind={self.kind!r})'


def kind_of(dsn: str) -> str:
    return POSTGRESQL if dsn.strip().lower().startswith(_POSTGRES_SCHEMES) else MSSQL


class ConnectionRegistry:
    """Resolves connection names to connections from the settings."""

    def __init__(self) -> None:
        self._connection_string: str | None = None

    def get_connection_string(self) -> str:
        """The default (MSSQL) connection string."""
        if self._connection_string is None:
            from app.config import get_settings
            value = get_settings().db_connection_string
            if not value:
                raise ValueError(
                    'DB_CONNECTION_STRING is not configured. Set the MSSQL_* variables '
                    '(or DB_CONNECTION_STRING directly) in .env.'
                )
            self._connection_string = value
        return self._connection_string

    def get(self, name: str | None = None) -> DatabaseConnection:
        """The connection called *name*; the default connection when it is empty or ``default``."""
        name = (name or '').strip()
        if name in ('', DEFAULT_CONNECTION):
            return DatabaseConnection(DEFAULT_CONNECTION, MSSQL, self.get_connection_string())
        from app.config import get_settings
        named = get_settings().database_connections
        if name not in named:
            raise ValueError(
                f'Unknown database connection "{name}". Configured: {sorted(named) or "none"}.')
        dsn = named[name].get_secret_value()
        return DatabaseConnection(name, kind_of(dsn), dsn)


def get_connection_registry() -> ConnectionRegistry:
    global _registry
    if _registry is None:
        _registry = ConnectionRegistry()
    return _registry

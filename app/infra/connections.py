from __future__ import annotations

import logging

log = logging.getLogger(__name__)

_registry: ConnectionRegistry | None = None

class ConnectionRegistry:
    """Provides the pyodbc connection string from Settings.db_connection_string."""

    def __init__(self) -> None:
        self._connection_string: str | None = None

    def get_connection_string(self) -> str:
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

def get_connection_registry() -> ConnectionRegistry:
    global _registry
    if _registry is None:
        _registry = ConnectionRegistry()
    return _registry

"""Settings: the Django project's environment variable names keep working."""
from __future__ import annotations

import pytest

from app.config import DEFAULT_EXECUTOR_TYPES, Settings


def make(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return Settings(_env_file=None)


def test_defaults_match_the_django_settings() -> None:
    s = Settings(_env_file=None)
    assert s.mcp_execute_tool_name == 'execute'
    assert s.mcp_json_response is True and s.mcp_stateless is True
    assert s.max_concurrency == 8
    assert s.default_call_timeout_seconds == 30 and s.http_timeout_seconds == 30
    assert s.allowed_http_methods == {'GET', 'POST', 'PUT', 'PATCH', 'DELETE'}
    assert s.cache_api_url == 'http://localhost:8400/api/v1/cache'
    assert s.cache_default_ttl_seconds == 1800
    assert s.function_allowlist == []
    assert s.function_dynamic_registration_enabled is False
    assert s.executor_types == DEFAULT_EXECUTOR_TYPES


def test_comma_separated_lists(monkeypatch: pytest.MonkeyPatch) -> None:
    s = make(monkeypatch, FUNCTION_ALLOWLIST='polars, pandas,,numpy',
             FUNCTION_ALLOWED_MODULE_PREFIXES='mycompany', ALLOWED_HTTP_METHODS='get,post')
    assert s.function_allowlist == ['polars', 'pandas', 'numpy']
    assert s.function_allowed_module_prefixes == ['mycompany']
    assert s.allowed_http_methods == {'GET', 'POST'}


def test_booleans_read_as_the_django_project_wrote_them(monkeypatch: pytest.MonkeyPatch) -> None:
    s = make(monkeypatch, MCP_JSON_RESPONSE='False', FUNCTION_DYNAMIC_REGISTRATION_ENABLED='True')
    assert s.mcp_json_response is False
    assert s.function_dynamic_registration_enabled is True


def test_connection_string_is_assembled_from_mssql_values(monkeypatch: pytest.MonkeyPatch) -> None:
    s = make(monkeypatch, MSSQL_HOST='db.local', MSSQL_PORT='1433', MSSQL_DATABASE='bills',
             MSSQL_USERNAME='reader', MSSQL_PASSWORD='pw', MSSQL_DRIVER='ODBC Driver 18 for SQL Server',
             MSSQL_EXTRA_PARAMS='Encrypt=yes;')
    assert s.db_connection_string == (
        'Driver={ODBC Driver 18 for SQL Server};Server=db.local,1433;Database=bills;'
        'UID=reader;PWD=pw;Encrypt=yes;')


def test_db_connection_string_overrides_assembly(monkeypatch: pytest.MonkeyPatch) -> None:
    s = make(monkeypatch, MSSQL_HOST='ignored', DB_CONNECTION_STRING='Driver={x};Server=y;')
    assert s.db_connection_string == 'Driver={x};Server=y;'


def test_no_database_configured_gives_empty_string() -> None:
    assert Settings(_env_file=None).db_connection_string == ''


def test_secrets_are_not_shown_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    s = make(monkeypatch, MSSQL_PASSWORD='pw-123', CONFIG_API_TOKEN='tok-456')
    assert 'pw-123' not in repr(s) and 'tok-456' not in repr(s)

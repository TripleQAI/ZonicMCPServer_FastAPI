"""Typed settings — replaces the Django project's core/settings.py.

Every value comes from the environment (or ``.env``); the names are the Django
project's environment variable names, so an existing ``.env`` keeps working. The
Django-only settings (DJANGO_SECRET_KEY, DJANGO_DEBUG, ALLOWED_HOSTS and the ORM
DATABASES block) are gone: the ORM was never used at runtime, and the ASGI router
bypassed Django's request handling, so none of them took effect.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent

#: Executor type → class path. Adding a tool type is a new executor class and an entry
#: here (or the EXECUTOR_TYPES env var, as JSON).
DEFAULT_EXECUTOR_TYPES: dict[str, str] = {
    'database': 'app.engine.executors.database.executor.DatabaseExecutor',
    'api':      'app.engine.executors.api.executor.ApiExecutor',
    'function': 'app.engine.executors.function.executor.FunctionExecutor',
}


def _split(value: Any) -> Any:
    """Comma-separated env value → list of trimmed, non-empty entries."""
    if isinstance(value, str):
        return [part.strip() for part in value.split(',') if part.strip()]
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / '.env', env_file_encoding='utf-8', extra='ignore')

    # ── MCP ───────────────────────────────────────────────────────────────────
    mcp_execute_tool_name: str = 'execute'
    mcp_json_response: bool = True
    mcp_stateless: bool = True

    # ── Execution ─────────────────────────────────────────────────────────────
    max_concurrency: int = Field(8, gt=0)
    default_call_timeout_seconds: float = Field(30, gt=0)
    http_timeout_seconds: float = Field(30, gt=0)
    allowed_http_methods: Annotated[set[str], NoDecode] = Field(
        default_factory=lambda: {'GET', 'POST', 'PUT', 'PATCH', 'DELETE'})

    # ── Cache service (HTTP API) ──────────────────────────────────────────────
    cache_api_url: str = 'http://localhost:8400/api/v1/cache'
    cache_http_timeout_seconds: float = Field(5, gt=0)
    cache_default_ttl_seconds: int = Field(1800, gt=0)
    cache_large_result_threshold: int = 1000

    # ── Database (MSSQL via pyodbc) ───────────────────────────────────────────
    mssql_host: str = ''
    mssql_port: str = ''
    mssql_database: str = ''
    mssql_username: str = ''
    mssql_password: SecretStr = SecretStr('')
    mssql_driver: str = ''
    mssql_extra_params: str = ''
    #: Set directly to bypass assembly from the MSSQL_* values.
    db_connection_string_override: SecretStr = Field(
        SecretStr(''), validation_alias='DB_CONNECTION_STRING')

    # ── Function executor ─────────────────────────────────────────────────────
    function_allowlist: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # ── Dynamic function registration (lazy install-on-first-use) ─────────────
    # OFF by default: the function executor then uses FUNCTION_ALLOWLIST only.
    function_dynamic_registration_enabled: bool = False
    config_api_url: str = ''
    config_api_token: SecretStr = SecretStr('')
    config_api_timeout_seconds: float = Field(5, gt=0)
    function_allowed_module_prefixes: Annotated[list[str], NoDecode] = Field(
        default_factory=list)
    function_package_index_url: str = ''
    function_negative_cache_ttl_seconds: float = 60

    # ── Executor type → class path ────────────────────────────────────────────
    executor_types: dict[str, str] = Field(
        default_factory=lambda: dict(DEFAULT_EXECUTOR_TYPES))

    # ── Logging ───────────────────────────────────────────────────────────────
    log_level: str = 'INFO'

    @field_validator('function_allowlist', 'function_allowed_module_prefixes', mode='before')
    @classmethod
    def _comma_list(cls, value: Any) -> Any:
        return _split(value)

    @field_validator('allowed_http_methods', mode='before')
    @classmethod
    def _methods(cls, value: Any) -> Any:
        value = _split(value)
        return {m.upper() for m in value} if isinstance(value, (list, set)) else value

    @property
    def db_connection_string(self) -> str:
        """The pyodbc connection string: DB_CONNECTION_STRING, or assembled from MSSQL_*.

        Empty when neither is configured, so the database executor reports it clearly.
        """
        override = self.db_connection_string_override.get_secret_value()
        if override:
            return override
        if not self.mssql_host:
            return ''
        return (
            f'Driver={{{self.mssql_driver}}};'
            f'Server={self.mssql_host},{self.mssql_port};'
            f'Database={self.mssql_database};'
            f'UID={self.mssql_username};'
            f'PWD={self.mssql_password.get_secret_value()};'
            f'{self.mssql_extra_params}'
        )


@lru_cache
def get_settings() -> Settings:
    """The settings, loaded once per process."""
    return Settings()

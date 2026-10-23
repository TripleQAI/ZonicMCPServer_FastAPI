"""Where custom-package config comes from — a PORT and its HTTP adapter.

The dynamic-registration subsystem depends on the ``PackageConfigSource`` interface,
never on a database. The default adapter reads a PostgreSQL-backed HTTP config API
(``CONFIG_API_URL``); tests inject a fake. Swapping the origin never touches the
registry/installer.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

log = logging.getLogger(__name__)


@dataclass
class PackageConfig:
    """One custom package's definition, as returned by the config API."""

    name: str
    distribution: str                       # pip spec, e.g. 'mycompany-utils==1.4.2'
    module_path: str                         # import path, e.g. 'mycompany.utils'
    allow_prefix: str | None = None          # allowlist prefix (defaults to module_path)
    alias: str | None = None
    index_url: str | None = None
    hashes: list[str] | None = None
    enabled: bool = True

    @property
    def prefix(self) -> str:
        """The module prefix this package registers (allow_prefix or module_path)."""
        return self.allow_prefix or self.module_path

    @classmethod
    def from_dict(cls, d: dict) -> PackageConfig:
        return cls(
            name=d.get('name', ''),
            distribution=d.get('distribution', ''),
            module_path=d.get('module_path', d.get('modulePath', '')),
            allow_prefix=d.get('allow_prefix', d.get('allowPrefix')),
            alias=d.get('alias'),
            index_url=d.get('index_url', d.get('indexUrl')),
            hashes=d.get('hashes'),
            enabled=d.get('enabled', True),
        )


class PackageConfigSource(ABC):
    """Read package config by reference (name or top-level module). Backed by HTTP."""

    @abstractmethod
    async def get(self, ref: str) -> PackageConfig | None:
        """Return the config for a package, or None if unknown/disabled/unreachable."""


class HttpPackageConfigSource(PackageConfigSource):
    """Reads ``GET {CONFIG_API_URL}/packages/{ref}`` over the shared pooled httpx client.

    Degrades gracefully (returns None on miss or any error), like infra/cache.py.
    """

    async def get(self, ref: str) -> PackageConfig | None:
        try:
            from app.config import get_settings
            from app.infra.http_client import get_http_client

            settings = get_settings()
            base = settings.config_api_url.rstrip('/')
            if not base:
                return None
            token = settings.config_api_token.get_secret_value()
            timeout = float(settings.config_api_timeout_seconds)
            headers = {'Authorization': f'Bearer {token}'} if token else {}

            client = await get_http_client()
            resp = await client.get(f'{base}/packages/{quote(ref, safe="")}',
                                    headers=headers, timeout=timeout)
            if resp.status_code != 200:
                return None
            data: dict[str, Any] = resp.json().get('data') or {}
            if not data or not data.get('enabled', True):
                return None
            cfg = PackageConfig.from_dict(data)
            return cfg if cfg.distribution and cfg.module_path else None
        except Exception as exc:
            log.warning('Config API fetch error for "%s": %s', ref, exc)
            return None

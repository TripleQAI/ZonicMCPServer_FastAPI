"""Function registry — the runtime source of truth for which packages are available.

Two implementations behind one interface (Null Object pattern):
  - NullFunctionRegistry: the feature is off — a no-op; only the static allowlist applies.
  - LazyFunctionRegistry: on first use of an unregistered package, fetch its config,
    install it, and register it (single-flight, positive/negative caching).

The executor consults the registry uniformly, so it needs no `if enabled:` branches.
``get_function_registry()`` picks the implementation from the master flag.
"""
from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod

from .config_source import PackageConfig, PackageConfigSource
from .installer import PackageInstaller


class FunctionRegistry(ABC):
    @abstractmethod
    async def ensure_available(self, function_path: str) -> None:
        """Lazily install & register the package providing function_path, if needed."""

    @abstractmethod
    def is_registered(self, function_path: str) -> bool:
        """True if function_path falls under a dynamically registered package."""


class NullFunctionRegistry(FunctionRegistry):
    """Feature disabled: do nothing. The executor falls back to FUNCTION_ALLOWLIST."""

    async def ensure_available(self, function_path: str) -> None:
        return None

    def is_registered(self, function_path: str) -> bool:
        return False


class LazyFunctionRegistry(FunctionRegistry):
    """Install-and-register a custom package the first time one of its callables is used."""

    def __init__(self, source: PackageConfigSource, installer: PackageInstaller,
                 allowed_prefixes: list[str] | None = None,
                 negative_ttl: float = 60.0) -> None:
        self._source = source
        self._installer = installer
        self._allowed_prefixes = allowed_prefixes or []
        self._negative_ttl = negative_ttl
        self._registered: set[str] = set()          # registered module prefixes
        self._negative: dict[str, float] = {}        # ref -> expiry (monotonic)
        self._locks: dict[str, asyncio.Lock] = {}    # ref -> single-flight lock

    def is_registered(self, function_path: str) -> bool:
        return any(function_path == p or function_path.startswith(p + '.')
                   for p in self._registered)

    async def ensure_available(self, function_path: str) -> None:
        if self.is_registered(function_path):
            return                                    # fast path — already available
        ref = self._ref(function_path)
        if self._negative_hit(ref):
            return

        lock = self._locks.setdefault(ref, asyncio.Lock())
        async with lock:                              # single-flight: install once
            if self.is_registered(function_path):
                return
            cfg = await self._source.get(ref)
            if cfg is None or not self._allowed(cfg):
                self._mark_negative(ref)
                return
            if not await self._installer.ensure_installed(cfg):
                self._mark_negative(ref)
                return
            self._registered.add(cfg.prefix)
            self._negative.pop(ref, None)

    # ── helpers ──────────────────────────────────────────────────────────────
    @staticmethod
    def _ref(function_path: str) -> str:
        return function_path.split('.', 1)[0]

    def _allowed(self, cfg: PackageConfig) -> bool:
        if not self._allowed_prefixes:               # empty = allow (like FUNCTION_ALLOWLIST)
            return True
        mp = cfg.module_path
        return any(mp == p or mp.startswith(p + '.') for p in self._allowed_prefixes)

    def _negative_hit(self, ref: str) -> bool:
        expiry = self._negative.get(ref)
        if expiry is None:
            return False
        if expiry <= time.monotonic():
            self._negative.pop(ref, None)             # expired — allow a retry
            return False
        return True

    def _mark_negative(self, ref: str) -> None:
        self._negative[ref] = time.monotonic() + self._negative_ttl


_registry: FunctionRegistry | None = None


def _build_registry() -> FunctionRegistry:
    from app.config import get_settings
    settings = get_settings()
    if not settings.function_dynamic_registration_enabled:
        return NullFunctionRegistry()
    from .config_source import HttpPackageConfigSource
    from .installer import PipPackageInstaller
    return LazyFunctionRegistry(
        source=HttpPackageConfigSource(),
        installer=PipPackageInstaller(),
        allowed_prefixes=list(settings.function_allowed_module_prefixes),
        negative_ttl=float(settings.function_negative_cache_ttl_seconds),
    )


def get_function_registry() -> FunctionRegistry:
    global _registry
    if _registry is None:
        _registry = _build_registry()
    return _registry

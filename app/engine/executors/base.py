from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import pydash

from app.models import CachedResult

if TYPE_CHECKING:
    from app.engine.container import ExecutionContext
    from app.models import ToolResult, ToolSpec


class BaseExecutor(ABC):
    """Template Method: resolve → cache-wrapped run → normalize."""

    async def execute(self, tool: ToolSpec, ctx: ExecutionContext) -> ToolResult:
        from app.infra.resolver import resolve_dict
        from app.models import ToolMeta, ToolResult

        t0 = time.monotonic()
        try:
            resolved_input = resolve_dict(tool.input, ctx)
            resolved_config = resolve_dict(tool.config, ctx)

            async def produce() -> Any:
                return await self._run(resolved_config, resolved_input, ctx)

            output = await self._run_with_cache(resolved_config, produce)
            duration = (time.monotonic() - t0) * 1000
            return ToolResult(
                id=tool.id,
                status='success',
                output=output,
                meta=ToolMeta(type=tool.type, duration_ms=round(duration, 2),
                              cached=isinstance(output, dict) and bool(output.get('is_cached'))),
            )
        except Exception as exc:
            duration = (time.monotonic() - t0) * 1000
            return ToolResult(
                id=tool.id,
                status='error',
                error=self._safe_error(exc),
                meta=ToolMeta(type=tool.type, duration_ms=round(duration, 2)),
            )

    @abstractmethod
    async def _run(self, config: dict[str, Any], resolved_input: dict[str, Any],
                   ctx: ExecutionContext) -> Any:
        ...

    @abstractmethod
    async def _run_with_cache(self, config: dict,
                              produce: Callable[[], Awaitable[Any]]) -> dict:
        """Wrap produce() with caching and return the response envelope.

        Implemented by each executor — usually by delegating to _read_through,
        overriding the body only when that executor's caching differs.
        """
        ...

    async def _read_through(self, config: dict,
                            produce: Callable[[], Awaitable[Any]]) -> dict:
        """Default read-through cache around produce().

        - saveToCache off        -> run produce(), return the data inline
        - key exists (hit)       -> return the key pointer, produce() is skipped
        - key missing (miss)     -> run produce(), store, return the key pointer
        - store unconfirmed      -> return the data inline (never a dead pointer)
        """
        result_cfg = config.get('result', {}) if isinstance(config, dict) else {}
        if not result_cfg.get('saveToCache'):
            return CachedResult.inline(await produce()).to_dict()

        digest = self._require(result_cfg, 'cacheKeyConfig.digest')
        flow_key = self._require(result_cfg, 'saveToFlowKey')

        from app.infra.cache import get_cache_service
        svc = get_cache_service()
        key = svc.make_key('', digest, {})

        # Lightweight existence check — no full-value fetch — and skip produce() on a hit.
        if await svc.exists(key):
            return CachedResult.cached(flow_key, key).to_dict()

        raw = await produce()
        # set() reports success, so no confirming re-fetch; on an unconfirmed store
        # return the data inline rather than a dead pointer.
        if not await svc.set(key, raw, ttl=self._cache_ttl(result_cfg)):
            return CachedResult.inline(raw, variable=flow_key).to_dict()
        return CachedResult.cached(flow_key, key).to_dict()

    @staticmethod
    def _require(result_cfg: dict, path: str) -> Any:
        """Read a required value at a dot-path under config.result, or raise clearly."""
        if not pydash.has(result_cfg, path):
            raise ValueError(f'saveToCache requires "config.result.{path}".')
        return pydash.get(result_cfg, path)

    @staticmethod
    def _cache_ttl(result_cfg: dict) -> int:
        """TTL from config.result.ttlSeconds, falling back to CACHE_DEFAULT_TTL_SECONDS."""
        ttl = result_cfg.get('ttlSeconds')
        if ttl is None:
            from app.config import get_settings
            ttl = get_settings().cache_default_ttl_seconds
        return int(ttl)

    def _safe_error(self, exc: Exception) -> str:
        cls = type(exc).__name__
        msg = str(exc)
        if any(kw in msg.lower() for kw in ('password', 'secret', 'token', 'key', 'pwd')):
            return f'{cls}: <redacted>'
        return f'{cls}: {msg}'

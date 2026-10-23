from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
from collections.abc import Callable
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from app.engine.executors.base import BaseExecutor

if TYPE_CHECKING:
    from app.engine.container import ExecutionContext

log = logging.getLogger(__name__)


@lru_cache(maxsize=256)
def _import_callable(function_path: str) -> Callable:
    parts = function_path.rsplit('.', 1)
    if len(parts) != 2:
        raise ImportError(f'Invalid function path "{function_path}". Expected "module.callable".')
    module_path, func_name = parts
    mod = importlib.import_module(module_path)
    fn = getattr(mod, func_name, None)
    if fn is None:
        raise ImportError(f'"{func_name}" not found in "{module_path}".')
    if not callable(fn):
        raise TypeError(f'"{function_path}" is not callable.')
    return fn


def _is_allowed(function_path: str, allowlist: list[str]) -> bool:
    if not allowlist:
        return True
    return any(function_path == p or function_path.startswith(p + '.') for p in allowlist)


class FunctionExecutor(BaseExecutor):
    async def _run_with_cache(self, config, produce):
        return await self._read_through(config, produce)

    async def _run(self, config: dict[str, Any], resolved_input: dict[str, Any],
                   ctx: ExecutionContext) -> Any:
        from app.config import get_settings

        function_path = config.get('functionPath', '')
        if not function_path:
            raise ValueError('config.functionPath is required for function tools.')

        allowlist = get_settings().function_allowlist
        if allowlist and not _is_allowed(function_path, allowlist):
            # Not on the static allowlist — give the (flag-gated) registry a chance to
            # lazily install & register it. Off by default → no-op → same as before.
            from .registry import get_function_registry
            registry = get_function_registry()
            await registry.ensure_available(function_path)
            if not registry.is_registered(function_path):
                raise PermissionError(
                    f'Function "{function_path}" is not in FUNCTION_ALLOWLIST '
                    'and is not a registered custom package.')

        fn = _import_callable(function_path)
        call_style = config.get('callStyle', 'kwargs')

        if call_style == 'kwargs':
            if inspect.iscoroutinefunction(fn):
                return await fn(**resolved_input)
            return await asyncio.to_thread(fn, **resolved_input)
        elif call_style == 'single':
            payload = resolved_input
            if inspect.iscoroutinefunction(fn):
                return await fn(payload)
            return await asyncio.to_thread(fn, payload)
        elif call_style == 'args':
            args = list(resolved_input.values())
            if inspect.iscoroutinefunction(fn):
                return await fn(*args)
            return await asyncio.to_thread(fn, *args)
        else:
            raise ValueError(f'Unknown callStyle "{call_style}".')

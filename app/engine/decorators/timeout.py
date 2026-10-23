from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from app.engine.executors.base import BaseExecutor

if TYPE_CHECKING:
    from app.engine.container import ExecutionContext
    from app.models import ToolResult, ToolSpec


class TimeoutDecorator(BaseExecutor):
    def __init__(self, inner: BaseExecutor, timeout_seconds: float | None = None) -> None:
        self._inner = inner
        self._timeout = timeout_seconds

    async def _run(self, config: dict, resolved_input: dict, ctx: ExecutionContext) -> Any:
        raise NotImplementedError

    async def _run_with_cache(self, config, produce):
        raise NotImplementedError

    async def execute(self, tool: ToolSpec, ctx: ExecutionContext) -> ToolResult:
        from app.config import get_settings
        from app.models import ToolMeta, ToolResult

        timeout = self._timeout or get_settings().default_call_timeout_seconds
        try:
            return await asyncio.wait_for(self._inner.execute(tool, ctx), timeout=timeout)
        except TimeoutError:
            return ToolResult(
                id=tool.id,
                status='error',
                error=f'TimeoutError: exceeded {timeout}s limit.',
                meta=ToolMeta(type=tool.type),
            )

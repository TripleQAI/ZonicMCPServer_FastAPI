from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from app.engine.executors.base import BaseExecutor

if TYPE_CHECKING:
    from app.engine.container import ExecutionContext
    from app.models import ToolResult, ToolSpec

log = logging.getLogger(__name__)


class TraceDecorator(BaseExecutor):
    def __init__(self, inner: BaseExecutor) -> None:
        self._inner = inner

    async def _run(self, config: dict, resolved_input: dict, ctx: ExecutionContext) -> Any:
        raise NotImplementedError

    async def _run_with_cache(self, config, produce):
        raise NotImplementedError

    async def execute(self, tool: ToolSpec, ctx: ExecutionContext) -> ToolResult:
        t0 = time.monotonic()
        log.info('[%s] tool=%s type=%s START', ctx.request_id, tool.id, tool.type)
        result = await self._inner.execute(tool, ctx)
        duration_ms = round((time.monotonic() - t0) * 1000, 2)
        log.info('[%s] tool=%s status=%s duration=%sms cached=%s',
                 ctx.request_id, tool.id, result.status, duration_ms,
                 result.meta.cached if result.meta else False)
        return result

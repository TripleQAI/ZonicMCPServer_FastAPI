from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from app.engine.executors.base import BaseExecutor

if TYPE_CHECKING:
    from app.engine.container import ExecutionContext
    from app.models import ToolResult, ToolSpec

log = logging.getLogger(__name__)


class RetryDecorator(BaseExecutor):
    def __init__(self, inner: BaseExecutor, max_attempts: int = 3,
                 backoff_seconds: float = 1.0) -> None:
        self._inner = inner
        self._max_attempts = max_attempts
        self._backoff = backoff_seconds

    async def _run(self, config: dict, resolved_input: dict, ctx: ExecutionContext) -> Any:
        raise NotImplementedError

    async def _run_with_cache(self, config, produce):
        raise NotImplementedError

    async def execute(self, tool: ToolSpec, ctx: ExecutionContext) -> ToolResult:
        last_result = None
        for attempt in range(self._max_attempts):
            result = await self._inner.execute(tool, ctx)
            if result.status == 'success':
                return result
            last_result = result
            if attempt < self._max_attempts - 1:
                await asyncio.sleep(self._backoff * (2 ** attempt))
                log.warning('Retrying tool %s (attempt %d/%d)', tool.id, attempt + 2, self._max_attempts)
        return last_result

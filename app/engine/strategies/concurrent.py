from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from .base import BaseStrategy

if TYPE_CHECKING:
    from app.engine.container import ExecutionContext
    from app.models import ToolSpec


class ConcurrentStrategy(BaseStrategy):
    async def run(self, tools: list[ToolSpec], ctx: ExecutionContext) -> None:
        from app.config import get_settings
        from app.engine.executors.registry import get_executor

        max_c = get_settings().max_concurrency
        sem = asyncio.Semaphore(max_c)

        async def _run_one(tool: ToolSpec) -> None:
            async with sem:
                executor = get_executor(tool.type)
                result = await executor.execute(tool, ctx)
                ctx.add_result(result)

        await asyncio.gather(*[_run_one(t) for t in tools])

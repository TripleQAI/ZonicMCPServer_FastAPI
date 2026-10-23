from __future__ import annotations

from typing import TYPE_CHECKING

from .base import BaseStrategy

if TYPE_CHECKING:
    from app.engine.container import ExecutionContext
    from app.models import ToolSpec


class SequentialStrategy(BaseStrategy):
    async def run(self, tools: list[ToolSpec], ctx: ExecutionContext) -> None:
        from app.engine.executors.registry import get_executor

        fail_fast = False

        for tool in tools:
            # Check dependency failures
            for dep_id in tool.depends_on:
                dep_result = ctx.results.get(dep_id)
                if dep_result and dep_result.status == 'error' and fail_fast:
                    from app.models import ToolMeta, ToolResult
                    ctx.add_result(ToolResult(
                        id=tool.id,
                        status='error',
                        error=f'Skipped: dependency "{dep_id}" failed.',
                        meta=ToolMeta(type=tool.type),
                    ))
                    continue

            executor = get_executor(tool.type)
            result = await executor.execute(tool, ctx)
            ctx.add_result(result)

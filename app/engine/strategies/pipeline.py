from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from typing import TYPE_CHECKING

from .base import BaseStrategy

if TYPE_CHECKING:
    from app.engine.container import ExecutionContext
    from app.models import ToolSpec


def _build_stages(tools: list[ToolSpec]) -> list[list[ToolSpec]]:
    """Kahn's topological sort grouped into execution stages."""
    id_to_tool = {t.id: t for t in tools}
    in_degree: dict[str, int] = {t.id: 0 for t in tools}
    dependents: dict[str, list[str]] = defaultdict(list)

    for t in tools:
        for dep in t.depends_on:
            in_degree[t.id] += 1
            dependents[dep].append(t.id)

    # Also respect explicit stage field as a minimum ordering
    queue = deque(t for t in tools if in_degree[t.id] == 0)
    stages: list[list[ToolSpec]] = []
    visited: set[str] = set()

    while queue:
        current_stage = list(queue)
        queue.clear()
        current_stage.sort(key=lambda t: t.stage)
        stages.append(current_stage)
        for t in current_stage:
            visited.add(t.id)
            for dep_id in dependents[t.id]:
                in_degree[dep_id] -= 1
                if in_degree[dep_id] == 0:
                    queue.append(id_to_tool[dep_id])

    return stages


class PipelineStrategy(BaseStrategy):
    async def run(self, tools: list[ToolSpec], ctx: ExecutionContext) -> None:
        from app.config import get_settings
        from app.engine.executors.registry import get_executor

        max_c = get_settings().max_concurrency
        sem = asyncio.Semaphore(max_c)
        stages = _build_stages(tools)

        for stage in stages:
            async def _run_one(tool: ToolSpec) -> None:
                # Skip if any dependency failed
                for dep_id in tool.depends_on:
                    dep = ctx.results.get(dep_id)
                    if dep and dep.status == 'error':
                        from app.models import ToolMeta, ToolResult
                        ctx.add_result(ToolResult(
                            id=tool.id,
                            status='error',
                            error=f'Skipped: dependency "{dep_id}" failed.',
                            meta=ToolMeta(type=tool.type),
                        ))
                        return
                async with sem:
                    executor = get_executor(tool.type)
                    result = await executor.execute(tool, ctx)
                    ctx.add_result(result)

            await asyncio.gather(*[_run_one(t) for t in stage])

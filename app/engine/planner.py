from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.engine.strategies.base import BaseStrategy
from app.engine.strategies.concurrent import ConcurrentStrategy
from app.engine.strategies.pipeline import PipelineStrategy
from app.engine.strategies.sequential import SequentialStrategy

if TYPE_CHECKING:
    from app.models import ExecuteRequest, ToolSpec


@dataclass
class ExecutionPlan:
    strategy: BaseStrategy
    tools: list[ToolSpec]


_STRATEGIES = {
    'concurrent': ConcurrentStrategy(),
    'sequential': SequentialStrategy(),
    'pipeline': PipelineStrategy(),
}


class ExecutionPlanner:
    def plan(self, request: ExecuteRequest) -> ExecutionPlan:
        mode = request.execution.mode
        strategy = _STRATEGIES.get(mode)
        if strategy is None:
            raise ValueError(f'Unknown execution mode "{mode}". Valid: {list(_STRATEGIES)}.')
        return ExecutionPlan(strategy=strategy, tools=request.tools)

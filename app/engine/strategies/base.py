from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.engine.container import ExecutionContext
    from app.models import ToolSpec


class BaseStrategy(ABC):
    @abstractmethod
    async def run(self, tools: list[ToolSpec], ctx: ExecutionContext) -> None:
        """Execute tools, adding results to ctx."""
        ...

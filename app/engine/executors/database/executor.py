from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.engine.executors.base import BaseExecutor

if TYPE_CHECKING:
    from app.engine.container import ExecutionContext


class DatabaseExecutor(BaseExecutor):
    async def _run_with_cache(self, config, produce):
        return await self._read_through(config, produce)

    async def _run(self, config: dict[str, Any], resolved_input: dict[str, Any],
                   ctx: ExecutionContext) -> Any:
        from app.infra.connections import get_connection_registry

        from .query_mode import get_query_mode

        connection_string = get_connection_registry().get_connection_string()

        query_type = config.get('queryType', 'script')
        mode = get_query_mode(query_type)
        return await mode.execute(connection_string, config, resolved_input, ctx)

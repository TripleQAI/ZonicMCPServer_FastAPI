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

        # The tool names its connection (never a connection string); none is the default.
        connection = get_connection_registry().get(
            config.get('connection') or config.get('connectionName'))
        database_type = (config.get('databaseType') or '').strip().lower()
        if database_type and database_type != connection.kind:
            raise ValueError(f'Connection "{connection.name}" is {connection.kind}, '
                             f'not {database_type}.')

        query_type = config.get('queryType', 'script')
        mode = get_query_mode(query_type)
        return await mode.execute(connection, config, resolved_input, ctx)

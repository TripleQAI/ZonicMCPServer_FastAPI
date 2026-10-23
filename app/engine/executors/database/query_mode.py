"""Query modes: how a single database tool runs its query.

A query mode selects between running a SQL *script* and calling a *stored
procedure*. This is distinct from the engine's execution **strategies**
(sequential / concurrent / pipeline in ``engine/strategies/``), which decide how
*multiple* tools are orchestrated. One is "how this query runs", the other is
"how these tools run together".
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.engine.container import ExecutionContext


class QueryMode(ABC):
    @abstractmethod
    async def execute(self, connection_string: str, config: dict,
                      resolved_input: dict, ctx: ExecutionContext) -> Any: ...


class ScriptMode(QueryMode):
    async def execute(self, connection_string: str, config: dict,
                      resolved_input: dict, ctx: ExecutionContext) -> Any:
        from app.infra.parameters import ToolParameter, substitute

        from .runner import run_script
        from .sql_encoders import get_sql_encoder

        parameters = ToolParameter.from_list(config.get('parameters', []))
        query_script = substitute(config.get('script', ''), parameters,
                                  resolved_input, get_sql_encoder)
        # Values are inlined above, so there is nothing left to bind.
        return await run_script(connection_string, query_script, {})


class StoredProcedureMode(QueryMode):
    async def execute(self, connection_string: str, config: dict,
                      resolved_input: dict, ctx: ExecutionContext) -> Any:
        from .runner import run_stored_procedure
        sp_name = config.get('procedureName', '')
        parameters = config.get('parameters', [])
        for p in parameters:
            key = p.get('name', '')
            if key in resolved_input:
                p['value'] = resolved_input[key]
        return await run_stored_procedure(connection_string, sp_name, parameters)


_MODES: dict[str, QueryMode] = {
    'script':           ScriptMode(),
    'stored_procedure': StoredProcedureMode(),
}


def get_query_mode(query_type: str) -> QueryMode:
    mode = _MODES.get(query_type)
    if mode is None:
        raise KeyError(f'Unknown queryType "{query_type}". Valid: {list(_MODES)}')
    return mode

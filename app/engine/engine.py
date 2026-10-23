from __future__ import annotations

import logging
import time

from app.engine.container import ExecutionContext
from app.engine.planner import ExecutionPlanner
from app.models import ExecuteRequest, ExecuteResponse, ResponseMeta

log = logging.getLogger(__name__)
_planner = ExecutionPlanner()


class ExecutionEngine:
    """Facade: receives an ExecuteRequest, plans, runs, returns ExecuteResponse."""

    async def run(self, request: ExecuteRequest) -> ExecuteResponse:
        t0 = time.monotonic()
        ctx = ExecutionContext.from_request(request.request_id, request.context)
        plan = _planner.plan(request)

        log.info('[%s] engine.run mode=%s tools=%d',
                 request.request_id, request.execution.mode, len(request.tools))

        try:
            await plan.strategy.run(plan.tools, ctx)
        except Exception as exc:
            log.error('[%s] Strategy raised unexpectedly: %s', request.request_id, exc)

        total_ms = round((time.monotonic() - t0) * 1000, 2)

        results_dict = {r_id: r.to_dict() for r_id, r in ctx.results.items()}
        completed = sum(1 for r in ctx.results.values() if r.status == 'success')
        has_error = any(r.status == 'error' for r in ctx.results.values())

        if not ctx.results or has_error and completed == 0:
            batch_status = 'error'
        elif has_error:
            batch_status = 'partial'
        else:
            batch_status = 'success'

        return ExecuteResponse(
            request_id=request.request_id,
            status=batch_status,
            results=results_dict,
            meta=ResponseMeta(
                total_duration_ms=total_ms,
                tool_count=len(request.tools),
                completed=completed,
                flow_updates=ctx.flow_updates,
            ),
        )


_engine: ExecutionEngine | None = None


def get_engine() -> ExecutionEngine:
    global _engine
    if _engine is None:
        _engine = ExecutionEngine()
    return _engine

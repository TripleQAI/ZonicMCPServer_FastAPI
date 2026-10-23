import pytest

from app.engine.planner import ExecutionPlanner
from app.engine.strategies.concurrent import ConcurrentStrategy
from app.engine.strategies.pipeline import PipelineStrategy
from app.engine.strategies.sequential import SequentialStrategy
from app.models import ExecuteRequest


def _request(mode: str) -> ExecuteRequest:
    return ExecuteRequest.from_dict({
        'execution': {'mode': mode},
        'tools': [{'id': 't1', 'type': 'function'}],
    })


def test_planner_concurrent():
    plan = ExecutionPlanner().plan(_request('concurrent'))
    assert isinstance(plan.strategy, ConcurrentStrategy)


def test_planner_sequential():
    plan = ExecutionPlanner().plan(_request('sequential'))
    assert isinstance(plan.strategy, SequentialStrategy)


def test_planner_pipeline():
    plan = ExecutionPlanner().plan(_request('pipeline'))
    assert isinstance(plan.strategy, PipelineStrategy)


def test_planner_unknown_mode():
    with pytest.raises(ValueError):
        ExecutionPlanner().plan(_request('unknown'))

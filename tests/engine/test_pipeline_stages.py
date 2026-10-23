from app.engine.strategies.pipeline import _build_stages
from app.models import ToolSpec


def _tool(id, depends_on=None, stage=0):
    return ToolSpec(id=id, type='function', depends_on=depends_on or [], stage=stage)


def test_no_deps_one_stage():
    tools = [_tool('a'), _tool('b'), _tool('c')]
    stages = _build_stages(tools)
    assert len(stages) == 1
    assert {t.id for t in stages[0]} == {'a', 'b', 'c'}


def test_linear_chain():
    tools = [_tool('a'), _tool('b', ['a']), _tool('c', ['b'])]
    stages = _build_stages(tools)
    assert len(stages) == 3
    assert stages[0][0].id == 'a'
    assert stages[1][0].id == 'b'
    assert stages[2][0].id == 'c'


def test_diamond_dependency():
    tools = [_tool('a'), _tool('b', ['a']), _tool('c', ['a']), _tool('d', ['b', 'c'])]
    stages = _build_stages(tools)
    assert stages[0][0].id == 'a'
    assert {t.id for t in stages[1]} == {'b', 'c'}
    assert stages[2][0].id == 'd'

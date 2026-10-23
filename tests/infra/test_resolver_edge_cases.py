from app.engine.container import ExecutionContext
from app.infra.resolver import resolve_value


def test_missing_flow_key_returns_none():
    ctx = ExecutionContext(request_id='t')
    assert resolve_value('{{flow.missing}}', ctx) is None


def test_nested_dict_resolution():
    ctx = ExecutionContext(request_id='t')
    ctx.flow['db'] = {'host': 'localhost'}
    # Resolver only flattens dot-path over dicts
    result = resolve_value('{{flow.db}}', ctx)
    assert result == {'host': 'localhost'}


def test_list_items_resolved():
    ctx = ExecutionContext(request_id='t')
    ctx.flow['x'] = 'val'
    result = resolve_value(['static', '{{flow.x}}', 42], ctx)
    assert result == ['static', 'val', 42]

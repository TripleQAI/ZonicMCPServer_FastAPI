from app.models import ExecuteRequest, ToolMeta, ToolResult, ToolSpec


def test_tool_spec_from_dict_defaults():
    t = ToolSpec.from_dict({'id': 't1', 'type': 'function'})
    assert t.id == 't1'
    assert t.type == 'function'
    assert t.stage == 0
    assert t.depends_on == []


def test_tool_spec_from_dict_full():
    t = ToolSpec.from_dict({
        'id': 'q1', 'type': 'database', 'stage': 2,
        'dependsOn': ['q0'],
        'config': {'queryType': 'script'},
        'input': {'userId': 42},
    })
    assert t.stage == 2
    assert t.depends_on == ['q0']


def test_execute_request_from_dict():
    req = ExecuteRequest.from_dict({
        'requestId': 'req-1',
        'context': {'flow': {'x': 1}},
        'execution': {'mode': 'pipeline', 'maxConcurrency': 4},
        'tools': [{'id': 't1', 'type': 'function'}],
    })
    assert req.request_id == 'req-1'
    assert req.context == {'flow': {'x': 1}}
    assert req.execution.mode == 'pipeline'
    assert req.execution.max_concurrency == 4
    assert len(req.tools) == 1


def test_tool_result_to_dict():
    r = ToolResult(id='t1', status='success', output=[1, 2],
                   meta=ToolMeta(type='function', duration_ms=12.5))
    d = r.to_dict()
    assert d['id'] == 't1'
    assert d['status'] == 'success'
    assert d['output'] == [1, 2]
    assert d['meta']['type'] == 'function'
    assert d['meta']['durationMs'] == 12.5
    assert d['meta']['cached'] is False

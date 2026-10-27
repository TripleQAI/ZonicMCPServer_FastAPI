"""The FastAPI application end to end, over the real MCP transport.

The scenarios are the ones in testing/curl_smoke.sh that need no network or database:
function tools in each execution mode, cross-tool references, and error isolation.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import Settings
from app.main import create_app

HEADERS = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream'}


@pytest.fixture
def client(default_settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(default_settings)) as test_client:   # runs the lifespan
        yield test_client


def rpc(client: TestClient, method: str, params: dict[str, Any], path: str = '/mcp/') -> dict:
    response = client.post(path, headers=HEADERS,
                           json={'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params})
    assert response.status_code == 200, response.text
    return response.json()


def execute(client: TestClient, arguments: dict[str, Any]) -> dict:
    """Call the execute tool and return its decoded response envelope."""
    body = rpc(client, 'tools/call', {'name': 'execute', 'arguments': arguments})
    result = body['result']
    assert result.get('isError') is not True, result
    return json.loads(result['content'][0]['text'])


def fn(tool_id: str, path: str, style: str, inputs: dict, **extra: Any) -> dict:
    return {'id': tool_id, 'type': 'function',
            'config': {'functionPath': path, 'callStyle': style}, 'input': inputs, **extra}


# ── the HTTP surface ──────────────────────────────────────────────────────────

def test_health(client: TestClient) -> None:
    body = client.get('/health').json()
    assert body['status'] == 'ok'
    assert body['tool'] == 'execute'
    assert set(body['executors']) == {'database', 'api', 'function'}


@pytest.mark.parametrize('path', ['/', '/docs', '/openapi.json', '/api/v1/execute'])
def test_non_mcp_paths_answer_406(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert response.status_code == 406
    assert response.json() == {'detail': 'Not Acceptable. This server speaks MCP only.'}


def test_browser_get_on_mcp_is_refused(client: TestClient) -> None:
    """A plain GET without the MCP Accept header is refused by the transport."""
    assert client.get('/mcp/', headers={'Accept': 'text/html'}).status_code == 406


@pytest.mark.parametrize('path', ['/mcp', '/mcp/'])
def test_tools_list_exposes_only_execute(client: TestClient, path: str) -> None:
    tools = (rpc(client, 'tools/list', {}, path=path))['result']['tools']
    assert [t['name'] for t in tools] == ['execute']
    assert tools[0]['inputSchema']['required'] == ['tools']


def test_tool_name_comes_from_settings(default_settings: Settings) -> None:
    default_settings.mcp_execute_tool_name = 'run'
    with TestClient(create_app(default_settings)) as test_client:
        tools = rpc(test_client, 'tools/list', {})['result']['tools']
    assert [t['name'] for t in tools] == ['run']


def test_unknown_tool_is_an_error(client: TestClient) -> None:
    result = (rpc(client, 'tools/call', {'name': 'other', 'arguments': {'tools': []}}))['result']
    assert result['isError'] is True
    assert 'exposes only "execute"' in result['content'][0]['text']


# ── bearer token ──────────────────────────────────────────────────────────────

@pytest.fixture
def secured(default_settings: Settings) -> Iterator[TestClient]:
    default_settings.mcp_auth_token = SecretStr('s3cret')
    with TestClient(create_app(default_settings)) as test_client:
        yield test_client


@pytest.mark.parametrize('authorization', [None, 'Bearer wrong', 's3cret', 'Basic s3cret'])
def test_token_is_required_when_configured(secured: TestClient, authorization: str | None) -> None:
    headers = HEADERS | ({'Authorization': authorization} if authorization else {})
    response = secured.post('/mcp/', headers=headers,
                            json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list', 'params': {}})
    assert response.status_code == 401
    assert response.headers['WWW-Authenticate'] == 'Bearer'


def test_matching_token_reaches_the_transport(secured: TestClient) -> None:
    secured.headers['Authorization'] = 'Bearer s3cret'
    tools = rpc(secured, 'tools/list', {})['result']['tools']
    assert [t['name'] for t in tools] == ['execute']


def test_health_needs_no_token(secured: TestClient) -> None:
    assert secured.get('/health').status_code == 200


# ── execution modes ───────────────────────────────────────────────────────────

def test_concurrent_functions(client: TestClient) -> None:
    out = execute(client, {
        'requestId': 'conc', 'execution': {'mode': 'concurrent'},
        'tools': [fn('max', 'builtins.max', 'args', {'a': 3, 'b': 17, 'c': 9}),
                  fn('pow', 'builtins.pow', 'kwargs', {'base': 2, 'exp': 10})]})
    assert out['requestId'] == 'conc'
    assert out['status'] == 'success'
    assert out['results']['max']['output'] == {'is_cached': False, 'data': {'variable': None, 'value': 17}}
    assert out['results']['pow']['output']['data']['value'] == 1024
    assert out['meta']['toolCount'] == 2 and out['meta']['completed'] == 2


def test_sequential_cross_tool_reference(client: TestClient) -> None:
    out = execute(client, {
        'execution': {'mode': 'sequential'},
        'tools': [fn('step1', 'builtins.max', 'args', {'a': 5, 'b': 40, 'c': 7}),
                  fn('step2', 'builtins.pow', 'kwargs',
                     {'base': '{{results.step1.data.value}}', 'exp': 2})]})
    assert out['results']['step2']['output']['data']['value'] == 1600


def test_pipeline_dag(client: TestClient) -> None:
    ref = '{{results.%s.data.value}}'
    out = execute(client, {
        'execution': {'mode': 'pipeline'},
        'tools': [fn('a', 'builtins.max', 'args', {'x': 10, 'y': 3}),
                  fn('b', 'builtins.pow', 'kwargs', {'base': ref % 'a', 'exp': 2}, dependsOn=['a']),
                  fn('c', 'builtins.pow', 'kwargs', {'base': ref % 'a', 'exp': 3}, dependsOn=['a']),
                  fn('d', 'builtins.max', 'args', {'b': ref % 'b', 'c': ref % 'c'}, dependsOn=['b', 'c'])]})
    assert out['status'] == 'success'
    assert out['results']['d']['output']['data']['value'] == 1000


def test_pipeline_skips_dependents_of_a_failed_tool(client: TestClient) -> None:
    out = execute(client, {
        'execution': {'mode': 'pipeline'},
        'tools': [fn('bad', 'nonexistent.module.func', 'args', {}),
                  fn('after', 'builtins.max', 'args', {'a': 1, 'b': 2}, dependsOn=['bad'])]})
    assert out['status'] == 'error'
    assert out['results']['after']['error'] == 'Skipped: dependency "bad" failed.'


def test_error_isolation_gives_partial(client: TestClient) -> None:
    out = execute(client, {
        'execution': {'mode': 'concurrent'},
        'tools': [fn('good', 'builtins.max', 'args', {'a': 1, 'b': 2}),
                  fn('bad', 'nonexistent.module.func', 'args', {'a': 1})]})
    assert out['status'] == 'partial'
    assert out['results']['good']['status'] == 'success'
    assert out['results']['bad']['status'] == 'error'
    assert out['results']['bad']['error'].startswith('ModuleNotFoundError')


def test_function_allowlist_is_enforced(client: TestClient, default_settings: Settings) -> None:
    default_settings.function_allowlist = ['math']
    out = execute(client, {'tools': [fn('denied', 'builtins.max', 'args', {'a': 1, 'b': 2})]})
    assert out['status'] == 'error'
    assert 'not in FUNCTION_ALLOWLIST' in out['results']['denied']['error']


def test_database_tool_without_connection_reports_it(client: TestClient) -> None:
    out = execute(client, {'tools': [{'id': 'q', 'type': 'database',
                                      'config': {'queryType': 'script', 'script': 'SELECT 1'}}]})
    assert out['status'] == 'error'
    assert 'DB_CONNECTION_STRING is not configured' in out['results']['q']['error']


def test_unknown_mode_is_refused_by_the_input_schema(client: TestClient) -> None:
    """The MCP SDK validates arguments against the tool's inputSchema before the engine runs."""
    body = rpc(client, 'tools/call', {'name': 'execute', 'arguments': {
        'execution': {'mode': 'parallel'}, 'tools': [fn('a', 'builtins.max', 'args', {'a': 1})]}})
    result = body['result']
    assert result['isError'] is True
    assert "'parallel' is not one of" in result['content'][0]['text']

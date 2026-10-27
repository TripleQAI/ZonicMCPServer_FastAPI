"""API tools fill their <placeholders> (url, queryParams, headers, body), each location encoded."""
import asyncio
import json

import pytest

from app.engine.executors.api.encoders import api_encoder
from app.engine.executors.api.executor import ApiExecutor
from app.engine.executors.base import summarize
from app.models import ToolSpec


class FakeResponse:
    def __init__(self, body):
        self._body = body
        self.text = json.dumps(body)

    def raise_for_status(self):
        pass

    def json(self):
        return self._body


class FakeClient:
    def __init__(self):
        self.calls = []

    async def request(self, method, url, **kwargs):
        self.calls.append({'method': method, 'url': url, **kwargs})
        return FakeResponse([{'ok': True}])


@pytest.fixture
def client(monkeypatch):
    fake = FakeClient()

    async def get_client():
        return fake

    monkeypatch.setattr('app.infra.http_client.get_http_client', get_client)
    return fake


class _Ctx:
    flow = {'periodStartMonth': '2025-01'}
    system: dict = {}
    results: dict = {}


def _run(tool):
    return asyncio.run(ApiExecutor().execute(tool, _Ctx()))


def test_query_param_placeholders_are_filled_from_source_paths(client):
    """The facility_operations_swarm template's nyc_electricity_benchmark tool (decision H-2)."""
    result = _run(ToolSpec(id='bench', type='api', config={
        'method': 'GET',
        'url': 'https://data.cityofnewyork.us/resource/jr24-e7cr.json',
        'queryParams': {'$where': "revenue_month >= '<periodMonth>'", '$limit': '25'},
        'parameters': [{'name': 'periodMonth', 'placeholder': '<periodMonth>',
                        'sourcePath': '{{flow.periodStartMonth}}'}],
    }))
    assert result.status == 'success', result.error
    assert client.calls[0]['params'] == {'$where': "revenue_month >= '2025-01'", '$limit': '25'}


def test_url_header_and_json_body_placeholders_are_encoded_for_their_location(client):
    _run(ToolSpec(id='t', type='api', config={
        'method': 'POST',
        'url': 'https://api.example.test/sites/<site>/report',
        'headers': {'X-Site': '<site>'},
        'bodyType': 'json',
        'bodyTemplate': '{"site": "<site>", "days": <days>}',
        'parameters': [{'name': 'site', 'placeholder': '<site>'},
                       {'name': 'days', 'placeholder': '<days>', 'dataType': 'integer'}],
    }, input={'site': 'A/B "1"', 'days': 7}))
    call = client.calls[0]
    assert call['url'] == 'https://api.example.test/sites/A%2FB%20%221%22/report'
    assert call['headers']['X-Site'] == 'A/B "1"'
    assert call['json'] == {'site': 'A/B "1"', 'days': 7}


def test_typed_values_are_validated_and_line_breaks_refused_in_headers(client):
    bad_number = _run(ToolSpec(id='t', type='api', config={
        'url': 'https://api.example.test/x?n=<n>',
        'parameters': [{'name': 'n', 'placeholder': '<n>', 'dataType': 'number'}],
    }, input={'n': '1 OR 1=1'}))
    assert bad_number.status == 'error' and 'not a valid number' in bad_number.error
    split = _run(ToolSpec(id='t', type='api', config={
        'url': 'https://api.example.test/x', 'headers': {'X-A': '<a>'},
        'parameters': [{'name': 'a', 'placeholder': '<a>'}],
    }, input={'a': 'x\r\nSet-Cookie: y'}))
    assert split.status == 'error' and 'line break' in split.error
    assert client.calls == []


def test_a_tool_without_parameters_is_sent_unchanged(client):
    _run(ToolSpec(id='t', type='api', config={
        'url': 'https://api.example.test/<literal>', 'queryParams': {'q': '<kept>'}}))
    assert client.calls[0]['url'] == 'https://api.example.test/<literal>'
    assert client.calls[0]['params'] == {'q': '<kept>'}


def test_api_encoders():
    assert api_encoder('json')('string').encode('a"b\n') == 'a\\"b\\n'
    assert api_encoder('url')('string').encode('a b') == 'a%20b'
    assert api_encoder('query')('boolean').encode('yes') == 'true'


# ── summaries of cached results (decision H-3) ────────────────────────────────

def test_summary_of_rows():
    rows = [{'a': 1, 'b': 'x'}, {'a': 2, 'c': None}, {'a': 3}]
    summary = summarize(rows, 2)
    assert summary['rows'] == rows[:2]
    assert summary['rowCount'] == 3
    assert summary['columns'] == ['a', 'b', 'c']
    text = json.dumps(rows, separators=(',', ':'))
    assert summary['sizeBytes'] == len(text)
    assert len(summary['sha256']) == 64


def test_summary_of_a_stored_procedure_and_of_a_non_row_result():
    assert summarize({'resultSets': [[{'x': 1}], [{'y': 2}, {'y': 3}]]}, 1)['rows'] == [{'y': 2}]
    assert 'rowCount' not in summarize({'value': 1}, 5)

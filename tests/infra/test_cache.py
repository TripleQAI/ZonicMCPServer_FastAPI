"""Tests for infra.cache HTTP serialization.

A fake httpx client captures outgoing requests, so these run without a live cache.
Focus: request bodies must serialize datetime.date (and other non-JSON-native
types) via default=str, the way _make_key and the MCP response serializer do —
httpx's json= would raise TypeError and the store would silently fail.
"""
import datetime
import json

import pytest

from app.infra.cache import get_cache_service


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class FakeClient:
    """Records requests; returns a miss on GET and success on PUT/POST."""

    def __init__(self):
        self.calls = []
        self.ttl_remaining = None       # what the /ttl endpoint reports

    async def get(self, url, **kw):
        self.calls.append(('GET', url, kw))
        if url.endswith('/ttl'):
            return FakeResponse(200, {'data': {'ttl_remaining': self.ttl_remaining}})
        return FakeResponse(200, {'data': {'value': None, 'hit': False}})

    async def put(self, url, **kw):
        self.calls.append(('PUT', url, kw))
        return FakeResponse(201, {'data': {'ok': True}})

    async def post(self, url, **kw):
        self.calls.append(('POST', url, kw))
        return FakeResponse(200, {'data': {'value': None, 'hit': False}})


@pytest.fixture
def fake_client(monkeypatch):
    client = FakeClient()

    async def _get_client():
        return client

    monkeypatch.setattr('app.infra.http_client.get_http_client', _get_client)
    return client


ROW = {
    'BillDate': datetime.date(2026, 4, 22),
    'PeriodStart': datetime.date(2026, 3, 19),
    'Expense': 189938.23,
    'UtilityMeter': None,
    'VendorSk': -1,
}


def _body_of(call):
    """Parse the JSON body a captured PUT/POST carried in content=."""
    _, _, kw = call
    return json.loads(kw['content'])


def test_set_serializes_dates_without_error(fake_client):
    import asyncio
    ok = asyncio.run(get_cache_service().set('k', [ROW], ttl=60))
    assert ok is True                                        # 201 → confirmed store

    put = next(c for c in fake_client.calls if c[0] == 'PUT')
    body = _body_of(put)
    assert body['ttl'] == 60
    assert body['value'][0]['BillDate'] == '2026-04-22'      # date -> ISO string
    assert body['value'][0]['UtilityMeter'] is None
    assert body['value'][0]['VendorSk'] == -1
    assert put[2]['headers']['Content-Type'] == 'application/json'


def test_set_returns_false_on_error_status(fake_client, monkeypatch):
    import asyncio

    async def bad_put(url, **kw):
        fake_client.calls.append(('PUT', url, kw))
        return FakeResponse(500, {})

    monkeypatch.setattr(fake_client, 'put', bad_put)
    assert asyncio.run(get_cache_service().set('k', [1], ttl=60)) is False


def test_exists_true_hits_ttl_endpoint_not_value(fake_client):
    import asyncio
    fake_client.ttl_remaining = 120
    assert asyncio.run(get_cache_service().exists('k')) is True
    get = next(c for c in fake_client.calls if c[0] == 'GET')
    assert get[1].endswith('/ttl')                           # lightweight, no value fetch


def test_exists_false_when_key_absent(fake_client):
    import asyncio
    fake_client.ttl_remaining = None
    assert asyncio.run(get_cache_service().exists('k')) is False


def test_get_or_set_serializes_dates_without_error(fake_client):
    import asyncio

    async def factory():
        return [ROW]

    asyncio.run(get_cache_service().get_or_set('k', factory, ttl=90))

    post = next(c for c in fake_client.calls if c[0] == 'POST')
    body = _body_of(post)
    assert body['ttl'] == 90
    assert body['value'][0]['PeriodStart'] == '2026-03-19'   # date -> ISO string
    assert post[2]['headers']['Content-Type'] == 'application/json'


def test_get_or_set_actually_posts_when_value_has_dates(fake_client):
    """Regression: previously httpx json= raised TypeError on date, so no POST was
    ever sent and the store silently failed. It must now reach the POST."""
    import asyncio

    async def factory():
        return [ROW]

    asyncio.run(get_cache_service().get_or_set('k', factory, ttl=60))
    assert any(c[0] == 'POST' for c in fake_client.calls)

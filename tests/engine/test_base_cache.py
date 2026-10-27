"""Tests for BaseExecutor's read-through cache hook.

The cache service is faked (in-memory) and produce() counts its calls, so these run
without a live cache, database, or network. Envelope rule: is_cached True -> value is
the cache key (pointer); is_cached False -> value is the data.
"""
import asyncio

import pytest

from app.engine.executors.api.executor import ApiExecutor
from app.engine.executors.base import BaseExecutor
from app.engine.executors.database.executor import DatabaseExecutor
from app.engine.executors.function.executor import FunctionExecutor


class FakeCacheService:
    def __init__(self, store_fails: bool = False):
        self.store = {}
        self.store_fails = store_fails
        self.made_keys = []
        self.set_calls = []

    def make_key(self, tool_type, config, resolved_input):
        self.made_keys.append((tool_type, config, resolved_input))
        return f'mcp:{tool_type}:{config}'

    async def get(self, key):
        return self.store.get(key)

    async def exists(self, key):
        return key in self.store

    async def set(self, key, value, ttl=300):
        self.set_calls.append((key, ttl))
        if self.store_fails:
            return False
        self.store[key] = value
        return True


class ProbeExecutor(BaseExecutor):
    """Minimal concrete executor to exercise the base cache hook."""

    async def _run(self, config, resolved_input, ctx):
        return None

    async def _run_with_cache(self, config, produce):
        return await self._read_through(config, produce)


@pytest.fixture
def fake_cache(monkeypatch):
    svc = FakeCacheService()
    monkeypatch.setattr('app.infra.cache.get_cache_service', lambda: svc)
    return svc


def _run(coro):
    return asyncio.run(coro)


def _counting_producer(value):
    calls = [0]

    async def produce():
        calls[0] += 1
        return value

    return produce, calls


def _cache_config(**over):
    cfg = {'saveToCache': True, 'saveToFlowKey': 'billsData',
           'cacheKeyConfig': {'digest': 'abc123'}}
    cfg.update(over)
    return {'result': cfg}


# ── _read_through: envelope by case ───────────────────────────────────────────

def test_inline_when_saveToCache_absent():
    produce, calls = _counting_producer([{'a': 1}])
    out = _run(ProbeExecutor()._read_through({'result': {}}, produce))
    assert out == {'is_cached': False, 'data': {'variable': None, 'value': [{'a': 1}]}}
    assert calls[0] == 1


def test_hit_returns_key_pointer_and_skips_produce(fake_cache):
    fake_cache.store['mcp::abc123'] = ['already cached']     # pre-populate
    produce, calls = _counting_producer(['fresh'])
    out = _run(ProbeExecutor()._read_through(_cache_config(), produce))
    assert out == {'is_cached': True, 'data': {'variable': 'billsData', 'value': 'mcp::abc123'}}
    assert calls[0] == 0                                     # produce() skipped on a hit


def test_miss_produces_stores_and_returns_key_pointer(fake_cache):
    produce, calls = _counting_producer([{'a': 1}])
    out = _run(ProbeExecutor()._read_through(_cache_config(), produce))
    assert out == {'is_cached': True, 'data': {'variable': 'billsData', 'value': 'mcp::abc123'}}
    assert calls[0] == 1
    assert fake_cache.made_keys == [('', 'abc123', {})]      # make_key('', digest, {})
    assert fake_cache.store['mcp::abc123'] == [{'a': 1}]     # stored the raw data


def test_miss_with_preview_returns_a_summary_with_the_key(fake_cache):
    produce, _ = _counting_producer([{'a': 1}, {'a': 2}, {'a': 3}])
    out = _run(ProbeExecutor()._read_through(_cache_config(preview={'rows': 2}), produce))
    assert out['is_cached'] is True
    assert out['data']['value'] == 'mcp::abc123'
    assert out['data']['preview'] == {
        'rows': [{'a': 1}, {'a': 2}], 'rowCount': 3, 'columns': ['a'],
        'sizeBytes': 25, 'sha256': out['data']['preview']['sha256']}


def test_store_unconfirmed_returns_inline_data(monkeypatch):
    svc = FakeCacheService(store_fails=True)                 # cache down
    monkeypatch.setattr('app.infra.cache.get_cache_service', lambda: svc)
    produce, _ = _counting_producer([{'a': 1}])
    out = _run(ProbeExecutor()._read_through(_cache_config(), produce))
    assert out == {'is_cached': False, 'data': {'variable': 'billsData', 'value': [{'a': 1}]}}


def test_uses_config_ttl(fake_cache):
    produce, _ = _counting_producer([])
    _run(ProbeExecutor()._read_through(_cache_config(ttlSeconds=42), produce))
    assert fake_cache.set_calls[0][1] == 42


def test_falls_back_to_default_ttl(fake_cache):
    from app.config import get_settings
    settings = get_settings()
    produce, _ = _counting_producer([])
    _run(ProbeExecutor()._read_through(_cache_config(), produce))
    assert fake_cache.set_calls[0][1] == settings.cache_default_ttl_seconds


def test_raises_on_missing_digest_before_producing(fake_cache):
    cfg = {'result': {'saveToCache': True, 'saveToFlowKey': 'k', 'cacheKeyConfig': {}}}
    produce, calls = _counting_producer([])
    with pytest.raises(ValueError, match=r'config\.result\.cacheKeyConfig\.digest'):
        _run(ProbeExecutor()._read_through(cfg, produce))
    assert calls[0] == 0                                     # fail fast, no query


def test_raises_on_missing_flow_key(fake_cache):
    cfg = {'result': {'saveToCache': True, 'cacheKeyConfig': {'digest': 'd'}}}
    produce, _ = _counting_producer([])
    with pytest.raises(ValueError, match=r'config\.result\.saveToFlowKey'):
        _run(ProbeExecutor()._read_through(cfg, produce))


# ── every executor inherits the hook and delegates to _read_through ───────────

@pytest.mark.parametrize('Executor', [DatabaseExecutor, ApiExecutor, FunctionExecutor])
def test_executor_hit_skips_the_real_call(fake_cache, Executor):
    fake_cache.store['mcp::abc123'] = ['cached']
    produce, calls = _counting_producer(['fresh'])
    out = _run(Executor()._run_with_cache(_cache_config(), produce))
    assert out['is_cached'] is True
    assert out['data']['value'] == 'mcp::abc123'
    assert calls[0] == 0                                     # api/db/function call skipped


def test_api_caches_non_get_method(fake_cache):
    """Decision 6: caching applies to every HTTP method, not just GET."""
    fake_cache.store['mcp::abc123'] = ['cached']
    produce, calls = _counting_producer({'created': True})
    config = {'method': 'POST', 'url': 'http://x', 'result': _cache_config()['result']}
    out = _run(ApiExecutor()._run_with_cache(config, produce))
    assert out['is_cached'] is True
    assert calls[0] == 0                                     # POST served from cache on a hit

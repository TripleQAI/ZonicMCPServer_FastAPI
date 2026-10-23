"""Tests for the lazy dynamic function registration subsystem.

No live config API and no real pip: a fake config source + fake installer drive the
registry, and the executor integration test registers a genuinely-importable stdlib
module (json) so the call actually resolves.
"""
import asyncio

import pytest
from pydantic import SecretStr

from app.engine.executors.function import registry as reg
from app.engine.executors.function.config_source import HttpPackageConfigSource, PackageConfig
from app.engine.executors.function.registry import (
    LazyFunctionRegistry,
    NullFunctionRegistry,
    get_function_registry,
)


def _run(coro):
    return asyncio.run(coro)


class FakeConfigSource:
    """Returns a configured PackageConfig per ref; counts calls."""

    def __init__(self, by_ref: dict[str, PackageConfig]):
        self._by_ref = by_ref
        self.calls: list[str] = []

    async def get(self, ref):
        self.calls.append(ref)
        await asyncio.sleep(0)                     # yield, to exercise single-flight
        return self._by_ref.get(ref)


class FakeInstaller:
    def __init__(self, ok: bool = True):
        self.ok = ok
        self.installed: list[str] = []

    async def ensure_installed(self, cfg):
        self.installed.append(cfg.distribution)
        await asyncio.sleep(0)
        return self.ok


def _cfg(**kw):
    base = dict(name='pkg', distribution='pkg==1.0', module_path='pkg')
    base.update(kw)
    return PackageConfig(**base)


# ── PackageConfig ──────────────────────────────────────────────────────────────

def test_package_config_prefix_defaults_to_module_path():
    assert _cfg(module_path='a.b').prefix == 'a.b'
    assert _cfg(module_path='a.b', allow_prefix='a').prefix == 'a'


def test_package_config_from_dict_camel_or_snake():
    c = PackageConfig.from_dict({'name': 'u', 'distribution': 'u==1',
                                 'modulePath': 'mycompany.utils', 'allowPrefix': 'mycompany'})
    assert c.module_path == 'mycompany.utils' and c.allow_prefix == 'mycompany'


# ── NullFunctionRegistry (feature off) ───────────────────────────────────────

def test_null_registry_is_noop():
    r = NullFunctionRegistry()
    _run(r.ensure_available('anything.at.all'))
    assert r.is_registered('anything.at.all') is False


# ── LazyFunctionRegistry ─────────────────────────────────────────────────────

def test_lazy_registers_on_first_use():
    src = FakeConfigSource({'mypkg': _cfg(name='mypkg', module_path='mypkg')})
    inst = FakeInstaller(ok=True)
    r = LazyFunctionRegistry(src, inst)
    _run(r.ensure_available('mypkg.tools.run'))
    assert r.is_registered('mypkg.tools.run') is True
    assert src.calls == ['mypkg'] and inst.installed == ['pkg==1.0']


def test_lazy_fast_path_skips_source_and_installer():
    src = FakeConfigSource({'mypkg': _cfg(module_path='mypkg')})
    inst = FakeInstaller()
    r = LazyFunctionRegistry(src, inst)
    _run(r.ensure_available('mypkg.a'))
    _run(r.ensure_available('mypkg.b'))            # already registered → no more calls
    assert src.calls == ['mypkg'] and inst.installed == ['pkg==1.0']


def test_lazy_unknown_package_negative_cached():
    src = FakeConfigSource({})                     # ref not found
    inst = FakeInstaller()
    r = LazyFunctionRegistry(src, inst, negative_ttl=999)
    _run(r.ensure_available('ghost.x'))
    _run(r.ensure_available('ghost.y'))            # negative cache → source not re-hit
    assert r.is_registered('ghost.x') is False
    assert src.calls == ['ghost'] and inst.installed == []


def test_lazy_disallowed_prefix_is_refused():
    src = FakeConfigSource({'os': _cfg(name='os', module_path='os')})
    inst = FakeInstaller()
    r = LazyFunctionRegistry(src, inst, allowed_prefixes=['mycompany'])
    _run(r.ensure_available('os.system'))
    assert r.is_registered('os.system') is False
    assert inst.installed == []                    # never even attempted


def test_lazy_allowed_prefix_permits():
    src = FakeConfigSource({'mycompany': _cfg(module_path='mycompany.utils', allow_prefix='mycompany')})
    inst = FakeInstaller(ok=True)
    r = LazyFunctionRegistry(src, inst, allowed_prefixes=['mycompany'])
    _run(r.ensure_available('mycompany.utils.clean'))
    assert r.is_registered('mycompany.utils.clean') is True


def test_lazy_install_failure_negative_cached():
    src = FakeConfigSource({'mypkg': _cfg(module_path='mypkg')})
    inst = FakeInstaller(ok=False)                 # install fails
    r = LazyFunctionRegistry(src, inst, negative_ttl=999)
    _run(r.ensure_available('mypkg.a'))
    _run(r.ensure_available('mypkg.b'))
    assert r.is_registered('mypkg.a') is False
    assert src.calls == ['mypkg'] and inst.installed == ['pkg==1.0']   # tried once


def test_lazy_negative_cache_expires_and_retries():
    src = FakeConfigSource({})
    r = LazyFunctionRegistry(src, FakeInstaller(), negative_ttl=0)     # instant expiry
    _run(r.ensure_available('ghost.x'))
    _run(r.ensure_available('ghost.x'))            # ttl=0 → retried
    assert src.calls == ['ghost', 'ghost']


def test_lazy_single_flight_installs_once_under_concurrency():
    src = FakeConfigSource({'mypkg': _cfg(module_path='mypkg')})
    inst = FakeInstaller(ok=True)
    r = LazyFunctionRegistry(src, inst)

    async def main():
        await asyncio.gather(*[r.ensure_available('mypkg.f') for _ in range(8)])

    _run(main())
    assert src.calls == ['mypkg'] and inst.installed == ['pkg==1.0']   # exactly once
    assert r.is_registered('mypkg.f') is True


# ── factory picks implementation from the flag ────────────────────────────────

@pytest.fixture
def reset_registry():
    reg._registry = None
    yield
    reg._registry = None


def test_factory_returns_null_when_flag_off(reset_registry, monkeypatch):
    from app.config import get_settings
    settings = get_settings()
    monkeypatch.setattr(settings, 'function_dynamic_registration_enabled', False, raising=False)
    assert isinstance(get_function_registry(), NullFunctionRegistry)


def test_factory_returns_lazy_when_flag_on(reset_registry, monkeypatch):
    from app.config import get_settings
    settings = get_settings()
    monkeypatch.setattr(settings, 'function_dynamic_registration_enabled', True, raising=False)
    assert isinstance(get_function_registry(), LazyFunctionRegistry)


# ── HttpPackageConfigSource (fake httpx) ──────────────────────────────────────

class _Resp:
    def __init__(self, status, payload): self.status_code = status; self._p = payload
    def json(self): return self._p


@pytest.fixture
def http(monkeypatch):
    calls = {}
    class Client:
        async def get(self, url, **kw):
            calls['url'] = url; calls['kw'] = kw
            return calls['resp']
    client = Client()
    async def _get_client(): return client
    monkeypatch.setattr('app.infra.http_client.get_http_client', _get_client)
    from app.config import get_settings
    settings = get_settings()
    monkeypatch.setattr(settings, 'config_api_url', 'http://cfg/api', raising=False)
    monkeypatch.setattr(settings, 'config_api_token', SecretStr('tok'), raising=False)
    return calls


def test_http_source_returns_config(http):
    http['resp'] = _Resp(200, {'data': {'name': 'u', 'distribution': 'u==1',
                                         'module_path': 'mycompany.utils', 'enabled': True}})
    cfg = _run(HttpPackageConfigSource().get('u'))
    assert cfg is not None and cfg.module_path == 'mycompany.utils'
    assert http['url'] == 'http://cfg/api/packages/u'
    assert http['kw']['headers']['Authorization'] == 'Bearer tok'


def test_http_source_none_on_404(http):
    http['resp'] = _Resp(404, {})
    assert _run(HttpPackageConfigSource().get('missing')) is None


def test_http_source_none_when_disabled(http):
    http['resp'] = _Resp(200, {'data': {'name': 'u', 'distribution': 'u==1',
                                        'module_path': 'm', 'enabled': False}})
    assert _run(HttpPackageConfigSource().get('u')) is None


def test_http_source_none_when_no_url(monkeypatch):
    from app.config import get_settings
    settings = get_settings()
    monkeypatch.setattr(settings, 'config_api_url', '', raising=False)
    assert _run(HttpPackageConfigSource().get('u')) is None


# ── executor integration ──────────────────────────────────────────────────────

def _fake_registry(**by_ref):
    return LazyFunctionRegistry(FakeConfigSource(by_ref), FakeInstaller(ok=True))


def test_executor_lazily_registers_then_calls(monkeypatch):
    """With a non-empty static allowlist that excludes 'json', the lazy registry
    registers it (json is already importable) and the call resolves."""
    from app.config import get_settings
    settings = get_settings()
    from app.engine.executors.function.executor import FunctionExecutor

    monkeypatch.setattr(settings, 'function_allowlist', ['builtins'], raising=False)
    lazy = _fake_registry(json=_cfg(name='json', distribution='json', module_path='json'))
    monkeypatch.setattr('app.engine.executors.function.registry.get_function_registry', lambda: lazy)

    out = _run(FunctionExecutor()._run(
        {'functionPath': 'json.dumps', 'callStyle': 'single'}, {'a': 1}, None))
    assert out == '{"a": 1}'


def test_executor_denies_when_not_allowed_and_not_registered(monkeypatch):
    """Flag-off equivalent: Null registry + non-empty allowlist → unregistered path is
    denied, exactly as before dynamic registration existed."""
    from app.config import get_settings
    settings = get_settings()
    from app.engine.executors.function.executor import FunctionExecutor

    monkeypatch.setattr(settings, 'function_allowlist', ['builtins'], raising=False)
    monkeypatch.setattr('app.engine.executors.function.registry.get_function_registry',
                        lambda: NullFunctionRegistry())
    with pytest.raises(PermissionError, match='not in FUNCTION_ALLOWLIST'):
        _run(FunctionExecutor()._run(
            {'functionPath': 'json.dumps', 'callStyle': 'single'}, {'a': 1}, None))

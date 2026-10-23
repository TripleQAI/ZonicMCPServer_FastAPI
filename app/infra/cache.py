"""Cache service backed by the HTTP cache API (no direct Redis dependency).

Talks to the cache service documented at ``CACHE_API_URL`` over HTTP, reusing the
shared pooled httpx client. Consumed by ``BaseExecutor._read_through`` via
``make_key`` / ``get`` / ``set``.

Endpoints used (all under ``CACHE_API_URL``):
    GET  keys/<key>              -> {"data": {"value", "hit"}}   (read-only)
    PUT  keys/<key>              -> stores/overwrites {"value", "ttl"}
    POST keys/<key>/get_or_set   -> atomic get-or-set {"value", "ttl"}

The cache degrades gracefully: any transport or service error is logged and
treated as a miss (``get``) or a no-op (``set``) so it never breaks tool execution.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any
from urllib.parse import quote

log = logging.getLogger(__name__)


def _base_url() -> str:
    from app.config import get_settings
    return get_settings().cache_api_url.rstrip('/')


def _timeout() -> float:
    from app.config import get_settings
    return float(get_settings().cache_http_timeout_seconds)


def _key_url(key: str, suffix: str = '') -> str:
    # Keep the key intact in the path: ':' is a legal path-segment character
    # (RFC 3986), so mark it safe — only genuinely unsafe chars (e.g. '/', space)
    # get encoded. This keeps the wire key identical to make_key's output.
    encoded = quote(key, safe=':')
    url = f'{_base_url()}/keys/{encoded}'
    return f'{url}/{suffix}' if suffix else url


def _make_key(tool_type: str, config: dict, resolved_input: dict) -> str:
    payload = json.dumps({'type': tool_type, 'config': config, 'input': resolved_input},
                         sort_keys=True, default=str)
    return 'mcp:' + hashlib.sha256(payload.encode()).hexdigest()


#: Serialize a request body ourselves rather than via httpx's json= — its encoder
#: rejects datetime.date/Decimal, whereas default=str coerces them to strings, the
#: same way _make_key and the MCP response serializer do.
_JSON_HEADERS = {'Content-Type': 'application/json'}


def _json_body(payload: dict) -> str:
    return json.dumps(payload, default=str)


class CacheService:
    async def get(self, key: str) -> Any | None:
        """Return the cached value, or None on miss or any error. Read-only."""
        try:
            from app.infra.http_client import get_http_client
            client = await get_http_client()
            resp = await client.get(_key_url(key), timeout=_timeout())
            if resp.status_code != 200:
                return None
            data = resp.json().get('data', {})
            return data.get('value') if data.get('hit') else None
        except Exception:
            return None

    async def exists(self, key: str) -> bool:
        """Lightweight existence check — hits the ttl endpoint, not the full value.

        Avoids transferring/parsing the whole payload just to test presence. Cached
        keys are always stored with a TTL, so ttl_remaining is non-null iff present.
        """
        try:
            from app.infra.http_client import get_http_client
            client = await get_http_client()
            resp = await client.get(_key_url(key, 'ttl'), timeout=_timeout())
            if resp.status_code != 200:
                return False
            return resp.json().get('data', {}).get('ttl_remaining') is not None
        except Exception:
            return False

    async def set(self, key: str, value: Any, ttl: int = 300) -> bool:
        """Store (overwrite) a value. Returns True iff the store was confirmed."""
        try:
            from app.infra.http_client import get_http_client
            client = await get_http_client()
            resp = await client.put(_key_url(key),
                                    content=_json_body({'value': value, 'ttl': ttl}),
                                    headers=_JSON_HEADERS, timeout=_timeout())
            return resp.status_code in (200, 201)
        except Exception:
            return False

    async def get_or_set(self, key: str, factory, ttl: int = 300) -> tuple[Any, bool]:
        """Return (value, hit). On miss, compute via factory() and store atomically.

        The read is done first so factory() — which may be expensive — runs only on a
        miss. The store then goes through the service's atomic get_or_set endpoint, so
        concurrent producers converge on a single stored value.
        """
        cached = await self.get(key)
        if cached is not None:
            return cached, True

        value = await factory()

        try:
            from app.infra.http_client import get_http_client
            client = await get_http_client()
            # Atomic store; under a race another producer may have stored first, in
            # which case the endpoint returns that authoritative value. hit is False
            # here because this caller reached the store path through an observed miss.
            resp = await client.post(_key_url(key, 'get_or_set'),
                                     content=_json_body({'value': value, 'ttl': ttl}),
                                     headers=_JSON_HEADERS, timeout=_timeout())
            if resp.status_code == 200:
                return resp.json().get('data', {}).get('value', value), False
        except Exception:
            pass
        return value, False

    def make_key(self, tool_type: str, config: dict, resolved_input: dict) -> str:
        return _make_key(tool_type, config, resolved_input)


_service: CacheService | None = None


def get_cache_service() -> CacheService:
    global _service
    if _service is None:
        _service = CacheService()
    return _service

from __future__ import annotations

import asyncio
import logging

log = logging.getLogger(__name__)
_client = None
_lock = asyncio.Lock()


async def get_http_client():
    global _client
    if _client is not None:
        return _client
    async with _lock:
        if _client is not None:
            return _client
        try:
            import httpx

            from app.config import get_settings
            timeout = get_settings().http_timeout_seconds
            _client = httpx.AsyncClient(
                timeout=httpx.Timeout(timeout),
                limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            )
        except Exception as exc:
            log.error('Failed to create httpx client: %s', exc)
            raise
    return _client


async def close_http_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None

"""FastAPI application — replaces the Django project's core/asgi.py and mcp_server/asgi.py.

Routes:
    /mcp and /mcp/   MCP streamable-HTTP transport (the only business surface)
    GET /health      liveness: the tool name and the registered executor types
    anything else    406 Not Acceptable, as the Django router answered

Run with ``uvicorn app.main:app`` (see run.bat). ``create_app()`` builds a fresh app,
with its own MCP session manager, for tests.
"""
from __future__ import annotations

import hmac
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse, Response
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from app.config import Settings, get_settings
from app.infra.http_client import close_http_client
from app.mcp_server.server import build_mcp_server

log = logging.getLogger(__name__)

MCP_PATHS = ('/mcp', '/mcp/')
NOT_ACCEPTABLE = {'detail': 'Not Acceptable. This server speaks MCP only.'}
UNAUTHORIZED = {'detail': 'Unauthorized. Send Authorization: Bearer <MCP_AUTH_TOKEN>.'}


class McpTransport:
    """ASGI endpoint handing a request to the MCP session manager.

    A class instance, not a function, so Starlette's Route passes the raw ASGI
    scope through and accepts every HTTP method; the transport answers 405 itself.
    With an auth token, a request without the matching bearer header gets 401.
    """

    def __init__(self, manager: StreamableHTTPSessionManager, auth_token: str = '') -> None:
        self._manager = manager
        self._expected = f'Bearer {auth_token}'.encode() if auth_token else None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self._expected is not None and not self._authorized(scope):
            response = JSONResponse(UNAUTHORIZED, status_code=401,
                                    headers={'WWW-Authenticate': 'Bearer'})
            await response(scope, receive, send)
            return
        await self._manager.handle_request(scope, receive, send)

    def _authorized(self, scope: Scope) -> bool:
        presented = dict(scope['headers']).get(b'authorization', b'')
        return hmac.compare_digest(presented, self._expected)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    if not logging.getLogger().handlers:
        logging.basicConfig(level=settings.log_level.upper(),
                            format='%(asctime)s %(levelname)s %(name)s: %(message)s')

    manager = StreamableHTTPSessionManager(
        app=build_mcp_server(settings.mcp_execute_tool_name),
        event_store=None,
        json_response=settings.mcp_json_response,
        stateless=settings.mcp_stateless,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        async with manager.run():
            try:
                yield
            finally:
                # The pooled httpx client (api executor, cache, config API) is shared
                # process-wide; close it on shutdown.
                await close_http_client()

    app = FastAPI(
        title='Zonic MCP Server',
        version='2.0.0',
        description='MCP streamable-HTTP server with one tool, '
                    f'"{settings.mcp_execute_tool_name}".',
        lifespan=lifespan,
        # MCP only: no OpenAPI document or docs pages, as before.
        docs_url=None, redoc_url=None, openapi_url=None,
    )

    transport = McpTransport(manager, settings.mcp_auth_token.get_secret_value())
    for path in MCP_PATHS:
        app.router.routes.append(Route(path, endpoint=transport, include_in_schema=False))

    @app.get('/health', include_in_schema=False)
    async def health() -> dict[str, object]:
        from app.engine.executors.registry import registered_types
        return {'status': 'ok', 'tool': settings.mcp_execute_tool_name,
                'executors': registered_types()}

    @app.exception_handler(StarletteHTTPException)
    async def not_mcp(request: Request, exc: StarletteHTTPException) -> Response:
        # Unknown paths answer 406, as the Django ASGI router did for any non-MCP path.
        if exc.status_code == 404:
            return JSONResponse(NOT_ACCEPTABLE, status_code=406)
        return await http_exception_handler(request, exc)

    log.info('Zonic MCP server ready: tool "%s" at %s',
             settings.mcp_execute_tool_name, ' and '.join(MCP_PATHS))
    return app


app = create_app()

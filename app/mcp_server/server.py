"""MCP Server — single 'execute' tool, no business tools/list."""
from __future__ import annotations

import json
import logging
from typing import Any

from mcp.server.lowlevel import Server
from mcp.types import TextContent, Tool

from app.engine.engine import get_engine
from app.models import ExecuteRequest

log = logging.getLogger(__name__)


def build_mcp_server(tool_name: str | None = None) -> Server:
    if tool_name is None:
        from app.config import get_settings
        tool_name = get_settings().mcp_execute_tool_name
    server = Server(name='zonic-mcp-server', version='2.0.0')

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name=tool_name,
                description=(
                    'Execute one or more tools (database / api / function) in a single '
                    'round-trip. Pass the complete tool-configuration set as arguments.'
                ),
                inputSchema={
                    'type': 'object',
                    'properties': {
                        'requestId': {'type': 'string', 'description': 'Optional idempotency key'},
                        'context': {
                            'type': 'object',
                            'properties': {
                                'flow':   {'type': 'object'},
                                'system': {'type': 'object'},
                            },
                        },
                        'execution': {
                            'type': 'object',
                            'properties': {
                                'mode':           {'type': 'string', 'enum': ['sequential', 'concurrent', 'pipeline']},
                                'maxConcurrency': {'type': 'integer'},
                                'failFast':       {'type': 'boolean'},
                            },
                        },
                        'tools': {
                            'type': 'array',
                            'items': {
                                'type': 'object',
                                'required': ['id', 'type'],
                                'properties': {
                                    'id':        {'type': 'string'},
                                    'type':      {'type': 'string', 'enum': ['database', 'api', 'function']},
                                    'config':    {'type': 'object'},
                                    'input':     {'type': 'object'},
                                    'stage':     {'type': 'integer'},
                                    'dependsOn': {'type': 'array', 'items': {'type': 'string'}},
                                },
                            },
                        },
                    },
                    'required': ['tools'],
                },
            )
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        if name != tool_name:
            raise ValueError(f'Unknown tool "{name}". This server exposes only "{tool_name}".')

        request = ExecuteRequest.from_dict(arguments)
        log.info('MCP execute requestId=%s tools=%d mode=%s',
                 request.request_id, len(request.tools), request.execution.mode)

        engine = get_engine()
        response = await engine.run(request)
        payload = json.dumps(response.to_dict(), default=str, ensure_ascii=False)
        return [TextContent(type='text', text=payload)]

    return server

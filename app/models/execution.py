"""Execution I/O models — the MCP ``execute`` request/response contract.

Dataclasses for a request (``ExecuteRequest`` → ``ToolSpec`` + ``ExecutionConfig``)
and its response (``ExecuteResponse`` → per-tool ``ToolResult`` + ``ToolMeta``).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolSpec:
    id: str
    type: str                          # database | api | function
    config: dict[str, Any] = field(default_factory=dict)
    input: dict[str, Any] = field(default_factory=dict)
    stage: int = 0
    depends_on: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> ToolSpec:
        return cls(
            id=d['id'],
            type=d['type'],
            config=d.get('config', {}),
            input=d.get('input', {}),
            stage=d.get('stage', 0),
            depends_on=d.get('dependsOn', []),
        )


@dataclass
class ExecutionConfig:
    mode: str = 'sequential'           # sequential | concurrent | pipeline
    max_concurrency: int = 8
    fail_fast: bool = False

    @classmethod
    def from_dict(cls, d: dict) -> ExecutionConfig:
        return cls(
            mode=d.get('mode', 'sequential'),
            max_concurrency=d.get('maxConcurrency', 8),
            fail_fast=d.get('failFast', False),
        )


@dataclass
class ExecuteRequest:
    request_id: str
    context: dict[str, Any] = field(default_factory=dict)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    tools: list[ToolSpec] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict, request_id: str = '') -> ExecuteRequest:
        import uuid
        return cls(
            request_id=d.get('requestId', request_id or str(uuid.uuid4())),
            context=d.get('context', {}),
            execution=ExecutionConfig.from_dict(d.get('execution', {})),
            tools=[ToolSpec.from_dict(t) for t in d.get('tools', [])],
        )


@dataclass
class ToolMeta:
    type: str
    duration_ms: float = 0.0
    cached: bool = False

    def to_dict(self) -> dict:
        return {'type': self.type, 'durationMs': self.duration_ms, 'cached': self.cached}


@dataclass
class ToolResult:
    id: str
    status: str                        # success | error
    output: Any = None
    error: str | None = None
    meta: ToolMeta = field(default_factory=lambda: ToolMeta(type='unknown'))

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'status': self.status,
            'output': self.output,
            'error': self.error,
            'meta': self.meta.to_dict(),
        }


@dataclass
class ResponseMeta:
    total_duration_ms: float = 0.0
    tool_count: int = 0
    completed: int = 0
    flow_updates: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            'totalDurationMs': self.total_duration_ms,
            'toolCount': self.tool_count,
            'completed': self.completed,
            'flowUpdates': self.flow_updates,
        }


@dataclass
class ExecuteResponse:
    request_id: str
    status: str                        # success | partial | error
    results: dict[str, dict] = field(default_factory=dict)
    meta: ResponseMeta = field(default_factory=ResponseMeta)

    def to_dict(self) -> dict:
        return {
            'requestId': self.request_id,
            'status': self.status,
            'results': self.results,
            'meta': self.meta.to_dict(),
        }

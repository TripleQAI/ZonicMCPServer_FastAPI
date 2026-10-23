from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.models import ToolResult


@dataclass
class ExecutionContext:
    request_id: str
    flow: dict[str, Any] = field(default_factory=dict)
    system: dict[str, Any] = field(default_factory=dict)
    results: dict[str, ToolResult] = field(default_factory=dict)
    flow_updates: dict[str, Any] = field(default_factory=dict)

    def add_result(self, result: ToolResult) -> None:
        self.results[result.id] = result

    def update_flow(self, key: str, value: Any) -> None:
        self.flow[key] = value
        self.flow_updates[key] = value

    @classmethod
    def from_request(cls, request_id: str, context: dict) -> ExecutionContext:
        return cls(
            request_id=request_id,
            flow=dict(context.get('flow', {})),
            system=dict(context.get('system', {})),
        )

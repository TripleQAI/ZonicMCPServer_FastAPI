"""Pydantic schemas matching design plan §3 — the single common schema.

Carried over from the Django project, where they were not used at runtime either:
the execute tool parses its arguments with ``ExecuteRequest.from_dict``.
"""
from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ToolSpecSchema(BaseModel):
    id: str
    type: str
    config: dict[str, Any] = Field(default_factory=dict)
    input: dict[str, Any] = Field(default_factory=dict)
    stage: int = 0
    depends_on: list[str] = Field(default_factory=list, alias='dependsOn')

    model_config = ConfigDict(populate_by_name=True)


class ExecutionConfigSchema(BaseModel):
    mode: str = 'sequential'
    max_concurrency: int = Field(8, alias='maxConcurrency')
    fail_fast: bool = Field(False, alias='failFast')

    model_config = ConfigDict(populate_by_name=True)


class ExecuteRequestSchema(BaseModel):
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias='requestId')
    context: dict[str, Any] = Field(default_factory=dict)
    execution: ExecutionConfigSchema = Field(default_factory=ExecutionConfigSchema)
    tools: list[ToolSpecSchema] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)

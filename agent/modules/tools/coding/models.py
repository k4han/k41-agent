"""Validated contracts for coding tool invocations and results."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from agent.modules.tools.coding.contracts import CodingError, InvocationContext, ResultContent


class ResultError(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ToolResult(ResultContent, BaseModel):
    status: Literal["success", "running", "error", "partial_failure"] = "success"
    data: dict[str, Any] = Field(default_factory=dict)
    content: str | list[dict[str, Any]] = ""
    display_content: str | None = None
    display_truncated: bool = False
    output_refs: list[str] = Field(default_factory=list)
    output_paths: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: ResultError | None = None
    capture_truncated: bool = False
    output_truncated: bool = False


class PermissionRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str = Field(default="*", min_length=1)
    resource: str = Field(default="*", min_length=1)
    effect: Literal["allow", "ask", "deny"]


class PermissionResume(BaseModel):
    action: Literal["permission"]
    request_id: str
    decision: Literal["allow_once", "allow_thread", "deny"]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: type[BaseModel]
    execute: Callable[[BaseModel, InvocationContext, Any], Awaitable[ToolResult]]
    output_schema: type[ToolResult] = ToolResult
    render: Callable[[ToolResult], str | list[dict[str, Any]]] | None = None


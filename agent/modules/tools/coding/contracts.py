"""Standard-library contracts shared with the sandbox runtime.

Pydantic validation stays at the application boundary. The portable engine
uses these serializable envelopes without installing packages in sandboxes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from typing import Any


class ResultContent:
    def model_content(self, tool_name: str) -> str | list[dict[str, Any]]:
        """Render mutation receipts from metadata without including UI diffs."""
        if self.display_content is not None and self.output_truncated:
            return self.content
        if tool_name in {"edit", "write"} and self.status == "success":
            if {"path", "additions", "deletions", "version"}.issubset(self.data):
                return (f"Changed {self.data['path']}: +{self.data['additions']}/-{self.data['deletions']}; "
                        f"version={self.data['version']}")
        return self.content


@dataclass
class RuntimeError:
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class RuntimeResult(ResultContent):
    status: str = "success"
    data: dict[str, Any] = field(default_factory=dict)
    content: str | list[dict[str, Any]] = ""
    display_content: str | None = None
    display_truncated: bool = False
    output_refs: list[str] = field(default_factory=list)
    output_paths: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: RuntimeError | None = None
    capture_truncated: bool = False
    output_truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> RuntimeResult:
        fields = dict(value)
        if isinstance(fields.get("error"), dict):
            fields["error"] = RuntimeError(**fields["error"])
        return cls(**fields)


@dataclass(frozen=True)
class InvocationContext:
    agent_name: str
    workspace: str
    thread_id: str
    message_id: str = ""
    tool_call_id: str = ""
    approval_supported: bool = False
    permission_rules: tuple[Any, ...] = ()
    backend: str = "local"
    locator: str = ""

    @property
    def owner(self) -> str:
        scope = self.workspace if self.backend == "local" else json.dumps(
            [self.backend, self.locator, self.workspace], separators=(",", ":"), ensure_ascii=True)
        return f"{scope}\0{self.thread_id}"



class CodingError(Exception):
    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.details = details

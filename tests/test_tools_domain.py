"""Tests for the tool domain types, error result, and ToolContext."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.tools import tool

from agent.modules.tools import (
    ToolCapability,
    ToolCategory,
    ToolContext,
    ToolDescriptor,
    ToolError,
    ToolErrorCode,
    ToolSource,
    format_tool_error,
)


@tool
def _dummy_tool(text: str) -> str:
    """Dummy tool for descriptor tests."""
    return text


class TestToolDescriptor:
    def test_basic_fields(self) -> None:
        desc = ToolDescriptor(
            id="builtin.utility.dummy",
            name="dummy",
            description="dummy",
            source=ToolSource.BUILTIN,
            category=ToolCategory.UTILITY,
            tool=_dummy_tool,
        )
        assert desc.id == "builtin.utility.dummy"
        assert desc.source is ToolSource.BUILTIN
        assert desc.category is ToolCategory.UTILITY
        assert desc.capabilities == frozenset()
        assert desc.tags == frozenset()
        assert desc.version == "1.0.0"

    def test_has_capability_and_tag(self) -> None:
        desc = ToolDescriptor(
            id="builtin.file.read",
            name="read_file",
            description="read",
            source=ToolSource.BUILTIN,
            category=ToolCategory.FILE,
            tool=_dummy_tool,
            capabilities=frozenset(
                {ToolCapability.READ_FS, ToolCapability.REQUIRES_WORKSPACE}
            ),
            tags=frozenset({"fs", "io"}),
        )
        assert desc.has_capability(ToolCapability.READ_FS)
        assert not desc.has_capability(ToolCapability.WRITE_FS)
        assert desc.has_tag("fs")
        assert not desc.has_tag("net")

    def test_descriptor_is_frozen(self) -> None:
        desc = ToolDescriptor(
            id="x",
            name="x",
            description="x",
            source=ToolSource.BUILTIN,
            category=ToolCategory.UTILITY,
            tool=_dummy_tool,
        )
        with pytest.raises(Exception):
            desc.name = "y"  # type: ignore[misc]


class TestToolError:
    def test_format_canonical(self) -> None:
        err = ToolError(ToolErrorCode.NOT_FOUND, "file missing")
        assert format_tool_error(err) == "[error] not_found: file missing"
        assert err.to_string() == "[error] not_found: file missing"

    def test_details_default_empty(self) -> None:
        err = ToolError(ToolErrorCode.INVALID_INPUT, "bad")
        assert err.details == {}

    def test_details_preserved(self) -> None:
        err = ToolError(
            ToolErrorCode.TIMEOUT, "slow", details={"seconds": 30}
        )
        assert err.details == {"seconds": 30}

    def test_str_returns_message(self) -> None:
        err = ToolError(ToolErrorCode.UPSTREAM, "boom")
        assert str(err) == "boom"


class TestToolContext:
    def test_from_runtime_with_dict_context(self) -> None:
        runtime = SimpleNamespace(
            context={
                "agent_name": "writer",
                "workspace": "/tmp/ws",
                "provider": "openai",
                "model": "gpt-4",
            },
            config={"configurable": {"thread_id": "tg:42:abc"}},
        )
        ctx = ToolContext.from_runtime(runtime)
        assert ctx.agent_name == "writer"
        assert ctx.workspace == "/tmp/ws"
        assert ctx.thread_id == "tg:42:abc"
        assert ctx.provider == "openai"
        assert ctx.model == "gpt-4"
        assert ctx.workspace_or_dir == "/tmp/ws"

    def test_from_runtime_falls_back_to_working_dir(self) -> None:
        runtime = SimpleNamespace(
            context={"working_dir": "/tmp/legacy"},
            config={"configurable": {}},
        )
        ctx = ToolContext.from_runtime(runtime)
        assert ctx.workspace is None
        assert ctx.working_dir == "/tmp/legacy"
        assert ctx.workspace_or_dir == "/tmp/legacy"
        assert ctx.thread_id is None
        assert ctx.agent_name == "default"

    def test_from_runtime_with_object_context(self) -> None:
        runtime = SimpleNamespace(
            context=SimpleNamespace(agent_name="bob", workspace=None),
            config={},
        )
        ctx = ToolContext.from_runtime(runtime)
        assert ctx.agent_name == "bob"
        assert ctx.workspace is None

    def test_from_runtime_with_missing_context(self) -> None:
        runtime = SimpleNamespace(context=None, config=None)
        ctx = ToolContext.from_runtime(runtime)
        assert ctx.agent_name == "default"
        assert ctx.workspace is None
        assert ctx.thread_id is None

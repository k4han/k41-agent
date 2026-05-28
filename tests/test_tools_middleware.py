"""Tests for tool middleware (error normalization, logging, wrap_tool)."""

from __future__ import annotations

import logging

import pytest
from langchain_core.tools import tool

from agent.modules.tools import ToolError, ToolErrorCode, format_tool_error
from agent.modules.tools.middleware import (
    apply_default_middleware,
    error_normalization,
    error_normalization_async,
    invocation_logging,
    invocation_logging_async,
    wrap_tool,
)
from agent.modules.tools.middleware.base import MIDDLEWARE_APPLIED_ATTR


class TestErrorNormalizationSync:
    def test_passes_through_normal_return(self) -> None:
        @error_normalization
        def ok(x: int) -> int:
            return x * 2

        assert ok(3) == 6

    def test_converts_tool_error(self) -> None:
        @error_normalization
        def boom() -> str:
            raise ToolError(ToolErrorCode.NOT_FOUND, "missing")

        assert boom() == "[error] not_found: missing"

    def test_catches_unexpected_exception(self) -> None:
        @error_normalization
        def boom() -> str:
            raise RuntimeError("bad thing")

        assert boom() == "[error] unexpected: bad thing"

    def test_unexpected_with_no_message_uses_class_name(self) -> None:
        @error_normalization
        def boom() -> str:
            raise RuntimeError("")

        assert boom() == "[error] unexpected: RuntimeError"


class TestErrorNormalizationAsync:
    @pytest.mark.asyncio
    async def test_async_passes_through(self) -> None:
        @error_normalization_async
        async def ok(x: int) -> int:
            return x + 1

        assert await ok(4) == 5

    @pytest.mark.asyncio
    async def test_async_converts_tool_error(self) -> None:
        @error_normalization_async
        async def boom() -> str:
            raise ToolError(ToolErrorCode.TIMEOUT, "too slow")

        assert await boom() == "[error] timeout: too slow"

    @pytest.mark.asyncio
    async def test_async_catches_unexpected(self) -> None:
        @error_normalization_async
        async def boom() -> str:
            raise ValueError("invalid")

        assert await boom() == "[error] unexpected: invalid"


class TestInvocationLogging:
    def test_sync_logs_and_returns(self, caplog: pytest.LogCaptureFixture) -> None:
        @invocation_logging
        def hello(name: str) -> str:
            return f"hi {name}"

        with caplog.at_level(logging.DEBUG, logger="agent.modules.tools.middleware.logging"):
            assert hello("world") == "hi world"

        debug_messages = [r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG]
        assert any("invoked" in m for m in debug_messages)
        assert any("finished" in m for m in debug_messages)

    @pytest.mark.asyncio
    async def test_async_logs_and_returns(self, caplog: pytest.LogCaptureFixture) -> None:
        @invocation_logging_async
        async def hello_a(name: str) -> str:
            return f"hi {name}"

        with caplog.at_level(logging.DEBUG, logger="agent.modules.tools.middleware.logging"):
            assert await hello_a("world") == "hi world"

        debug_messages = [r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG]
        assert any("invoked" in m for m in debug_messages)
        assert any("finished" in m for m in debug_messages)


class TestWrapTool:
    def test_wraps_sync_tool(self) -> None:
        @tool
        def my_tool(x: str) -> str:
            """raise."""
            raise ToolError(ToolErrorCode.INVALID_INPUT, f"bad: {x}")

        wrapped = apply_default_middleware(my_tool)
        assert getattr(wrapped, MIDDLEWARE_APPLIED_ATTR, False)
        assert my_tool.invoke({"x": "abc"}) == "[error] invalid_input: bad: abc"

    def test_idempotent(self) -> None:
        @tool
        def t2(x: str) -> str:
            """passthrough."""
            return x

        first = apply_default_middleware(t2)
        first_func = t2.func
        second = apply_default_middleware(t2)
        assert first is second
        assert t2.func is first_func

    def test_custom_middleware_chain(self) -> None:
        calls: list[str] = []

        def tracer(label: str):
            def deco(func):
                def wrapper(*args, **kwargs):
                    calls.append(f"enter:{label}")
                    try:
                        return func(*args, **kwargs)
                    finally:
                        calls.append(f"exit:{label}")
                return wrapper
            return deco

        @tool
        def t3(x: str) -> str:
            """."""
            calls.append(f"body:{x}")
            return x

        wrap_tool(t3, sync_middlewares=[tracer("inner"), tracer("outer")])
        t3.invoke({"x": "ok"})
        # outer wraps after inner so call order is outer -> inner -> body
        assert calls == [
            "enter:outer",
            "enter:inner",
            "body:ok",
            "exit:inner",
            "exit:outer",
        ]


class TestToolErrorEndToEndOnBuiltin:
    def test_read_file_uses_normalized_format(self, tmp_path) -> None:
        from types import SimpleNamespace

        from agent.modules.tools.langchain.file_tools import read_file as read_file_module

        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        result = read_file_module.read_file.func(
            file_path="../escape.txt",
            runtime=SimpleNamespace(context={"working_dir": str(sandbox)}),
        )
        assert result.startswith("[error] ")
        assert "Path escapes working directory" in result

    def test_skill_tool_uses_normalized_format(self, monkeypatch) -> None:
        import agent.modules.tools.langchain.skill_tools.skill as skill_module

        monkeypatch.setattr(skill_module, "get_skill_content_xml", lambda name: None)
        result = skill_module.skill.func(name="missing")
        assert result == format_tool_error(
            ToolError(ToolErrorCode.NOT_FOUND, "skill not found")
        )

"""Interactive chat REPL for the Kaka agent CLI."""

from __future__ import annotations

import asyncio
import logging
import os
import selectors
import sys

from agent.delivery.cli.commands import (
    dispatch_slash_command,
    parse_slash_command,
)
from agent.delivery.cli.runtime import CLIRuntime
from agent.delivery.cli.session import CLISession
from agent.modules.agent_runtime import run_agent_stream

logger = logging.getLogger(__name__)


_BANNER = (
    "Kaka Agent CLI — interactive chat\n"
    "Type your message, or /help to list commands. Use /quit to exit.\n"
)


def _print_prompt(session: CLISession) -> None:
    print(f"[{session.agent_name}]> ", end="", flush=True)


async def _read_line() -> str | None:
    """Read a line from stdin without blocking the event loop."""
    try:
        line = await asyncio.to_thread(sys.stdin.readline)
    except (KeyboardInterrupt, EOFError):
        return None
    if not line:
        return None
    return line.rstrip("\r\n")


async def _stream_agent_response(session: CLISession, user_input: str) -> None:
    last_text = ""
    try:
        async for event in run_agent_stream(
            user_input=user_input,
            thread_id=session.thread_id,
            agent_name=session.agent_name,
        ):
            event_type = event.get("type")
            if event_type == "tool_call":
                name = event.get("name", "?")
                args = event.get("args")
                if args:
                    print(f"  [tool] {name}({args})")
                else:
                    print(f"  [tool] {name}")
            elif event_type == "final":
                content = event.get("content", "")
                if content:
                    last_text = content
    except Exception as exc:
        logger.exception("Agent run failed")
        print(f"  [error] {exc}")
        return

    if last_text:
        print(last_text)
    else:
        print("(no response)")


async def _chat_loop(session: CLISession) -> None:
    print(_BANNER)
    print(f"Thread: {session.thread_id}\n")

    while True:
        _print_prompt(session)
        line = await _read_line()
        if line is None:
            print()
            return

        if not line.strip():
            continue

        parsed = parse_slash_command(line)
        if parsed is not None:
            name, args = parsed
            should_continue = await dispatch_slash_command(session, name, args)
            if not should_continue:
                return
            continue

        await _stream_agent_response(session, line)


async def _run_repl_async() -> None:
    runtime = CLIRuntime()
    session = CLISession()
    await runtime.startup()
    try:
        await _chat_loop(session)
    finally:
        await runtime.shutdown()


def run_repl() -> None:
    """Synchronous entrypoint suitable for CLI command wiring."""
    try:
        if os.name == "nt":
            asyncio.run(
                _run_repl_async(),
                loop_factory=lambda: asyncio.SelectorEventLoop(
                    selectors.SelectSelector()
                ),
            )
            return
        asyncio.run(_run_repl_async())
    except KeyboardInterrupt:
        print()
        logger.info("CLI interrupted by user.")


__all__ = ["run_repl"]

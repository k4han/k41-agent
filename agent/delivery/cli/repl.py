"""Interactive chat REPL for the Kai agent CLI."""

from __future__ import annotations

import asyncio
import logging
import os
import selectors
import sys

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.history import FileHistory
from prompt_toolkit.styles import Style
from rich.console import Console

from agent.delivery.cli.commands import (
    COMMANDS_ORDER,
    dispatch_slash_command,
    parse_slash_command,
)
from agent.delivery.cli.runtime import CLIRuntime
from agent.delivery.cli.session import CLISession
from agent.modules.agent_runtime import run_agent_stream

logger = logging.getLogger(__name__)
console = Console()

_HISTORY_FILE = os.path.join(os.path.expanduser("~"), ".k41-agent", ".cli_history")

_PROMPT_STYLE = Style.from_dict({
    "prompt": "bold cyan",
    "agent": "bold green",
})

_BANNER = (
    "[bold cyan]Kai Agent CLI[/bold cyan] — interactive chat\n"
    "Type your message, or [bold]/help[/bold] to list commands. Use [bold]/quit[/bold] to exit.\n"
)


class SlashCommandCompleter(Completer):
    """Autocomplete for slash commands."""

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/"):
            return
        word = text[1:]
        for spec in COMMANDS_ORDER:
            if spec.name.startswith(word):
                yield Completion(
                    spec.name,
                    start_position=-len(word),
                    display_meta=spec.summary,
                )


def _get_prompt(session: CLISession) -> str:
    return f"[{session.agent_name}]> "


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
                    console.print(f"  [dim][tool][/dim] [yellow]{name}[/yellow]({args})")
                else:
                    console.print(f"  [dim][tool][/dim] [yellow]{name}[/yellow]")
            elif event_type == "final":
                content = event.get("content", "")
                if content:
                    last_text = content
    except Exception as exc:
        logger.exception("Agent run failed")
        console.print(f"  [red][error][/red] {exc}")
        return

    if last_text:
        console.print(last_text)
    else:
        console.print("[dim](no response)[/dim]")


async def _chat_loop(session: CLISession) -> None:
    console.print(_BANNER)
    console.print(f"Thread: [dim]{session.thread_id}[/dim]\n")

    history = FileHistory(_HISTORY_FILE)
    completer = SlashCommandCompleter()
    prompt_session = PromptSession(
        history=history,
        completer=completer,
        style=_PROMPT_STYLE,
        complete_while_typing=True,
    )

    while True:
        prompt_text = _get_prompt(session)
        try:
            line = await asyncio.to_thread(
                prompt_session.prompt,
                prompt_text,
            )
        except (KeyboardInterrupt, EOFError):
            console.print()
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
        console.print()
        logger.info("CLI interrupted by user.")


__all__ = ["run_repl"]

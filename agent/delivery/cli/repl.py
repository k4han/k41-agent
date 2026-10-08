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

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

console = Console()

_HISTORY_FILE = os.path.join(os.path.expanduser("~"), ".k41-agent", ".cli_history")

_PROMPT_STYLE = Style.from_dict({
    "prompt": "bold cyan",
    "agent": "dim cyan",
})


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


def _get_prompt(session: CLISession) -> list[tuple[str, str]]:
    if session.agent_name and session.agent_name != "default":
        return [
            ("class:agent", f"{session.agent_name} "),
            ("class:prompt", "❯ "),
        ]
    return [("class:prompt", "❯ ")]


def _print_welcome_banner(session: CLISession) -> None:
    short_thread = session.thread_id
    if "_" in short_thread:
        parts = short_thread.split("_")
        short_thread = parts[-1]
    console.print(
        f"\n[bold]kai[/bold] [dim]› session {short_thread} · agent:[/dim] [green]{session.agent_name}[/green]"
    )
    console.print(
        "[dim]Type a message to chat, /help for commands, /quit to exit.[/dim]\n"
    )


async def _stream_agent_response(session: CLISession, user_input: str) -> None:
    last_text = ""
    try:
        async for event in run_agent_stream(
            user_input=user_input,
            thread_id=session.thread_id,
            usage_context={"platform": "cli", "user_id": "local", "channel_id": session.channel_id},
            agent_name=session.agent_name,
        ):
            event_type = event.get("type")
            if event_type == "tool_call":
                name = event.get("name", "?")
                args = event.get("args")
                if args:
                    console.print(f"  [dim]·[/dim] [cyan]tool[/cyan] [dim]{name}({args})[/dim]")
                else:
                    console.print(f"  [dim]·[/dim] [cyan]tool[/cyan] [dim]{name}[/dim]")
            elif event_type == "final":
                content = event.get("content", "")
                if content:
                    last_text = content
    except Exception as exc:
        logger.exception("Agent run failed")
        message = str(exc)
        if "No providers configured" in message or "No enabled providers" in message:
            console.print(f"  [bold red]✖[/bold red] [red]{exc}[/red]\n")
            console.print(
                "  [yellow]Chưa cấu hình LLM provider.[/yellow]\n"
                "  Hãy mở Dashboard [cyan]/dashboard -> Settings -> Providers[/cyan] để thêm provider,\n"
                "  hoặc cấu hình [cyan]llm.providers.<name>.api_key[/cyan] và [cyan]llm.default_model[/cyan] trong runtime settings.\n"
                "  Sau đó chạy lại [cyan]k41 cli[/cyan].\n"
            )
        else:
            console.print(f"  [bold red]✖[/bold red] [red]{exc}[/red]\n")
        return

    if last_text:
        from rich.markdown import Markdown

        console.print()
        console.print(Markdown(last_text))
        console.print()
    else:
        console.print("[dim](no response)[/dim]\n")


async def _chat_loop(session: CLISession) -> None:
    _print_welcome_banner(session)

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
            if name == "skill":
                await _stream_agent_response(session, line)
                continue
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
        else:
            asyncio.run(_run_repl_async())
    except KeyboardInterrupt:
        pass


__all__ = ["run_repl"]

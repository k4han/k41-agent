"""Slash command handlers for the interactive CLI."""

from __future__ import annotations

import logging
import shlex
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from rich.console import Console
from rich.table import Table

from agent.delivery.cli.session import CLISession, CLI_PLATFORM, CLI_USER_ID
from agent.modules.agent_runtime import clear_agent_session
from agent.modules.agents import get_catalog_service
from agent.modules.tools import get_default_tool_names
from agent.shared.config import get_config_service

logger = logging.getLogger(__name__)
console = Console()

CommandHandler = Callable[[CLISession, list[str]], Awaitable[bool]]


@dataclass(frozen=True, slots=True)
class CommandSpec:
    name: str
    summary: str
    handler: CommandHandler


async def cmd_help(session: CLISession, args: list[str]) -> bool:
    console.print()
    table = Table(title="Slash Commands", show_header=True, header_style="bold cyan")
    table.add_column("Command", style="bold")
    table.add_column("Description")
    for spec in COMMANDS_ORDER:
        table.add_row(f"/{spec.name}", spec.summary)
    console.print(table)
    console.print("\nAnything else is sent to the active agent.\n")
    return True


async def cmd_quit(session: CLISession, args: list[str]) -> bool:
    console.print("[dim]Bye.[/dim]")
    return False


async def cmd_new(session: CLISession, args: list[str]) -> bool:
    old_thread = session.thread_id
    try:
        await clear_agent_session(
            platform=CLI_PLATFORM,
            user_id=CLI_USER_ID,
            channel_id=session.channel_id,
        )
    except Exception as exc:
        logger.warning("Failed to clear previous thread '%s': %s", old_thread, exc)

    new_thread = session.reset_thread()
    console.print(f"[green]Started new thread:[/green] {new_thread}")
    return True


async def cmd_clear(session: CLISession, args: list[str]) -> bool:
    try:
        await clear_agent_session(
            platform=CLI_PLATFORM,
            user_id=CLI_USER_ID,
            channel_id=session.channel_id,
        )
        console.print(f"[green]Cleared thread:[/green] {session.thread_id}")
    except Exception as exc:
        console.print(f"[red]Failed to clear thread:[/red] {exc}")
    return True


async def cmd_resume(session: CLISession, args: list[str]) -> bool:
    if not args:
        console.print(f"Current thread: [dim]{session.thread_id}[/dim]")
        console.print("Usage: /resume <channel_id-or-full-thread-id>")
        return True

    target = args[0]
    prefix = f"{CLI_PLATFORM}_{CLI_USER_ID}_"
    if target.startswith(prefix):
        channel_id = target[len(prefix):]
    else:
        channel_id = target

    if not channel_id:
        console.print("[red]Invalid thread id.[/red]")
        return True

    session.use_thread(channel_id)
    console.print(f"[green]Resumed thread:[/green] {session.thread_id}")
    return True


async def cmd_agent(session: CLISession, args: list[str]) -> bool:
    catalog = get_catalog_service()
    if not args:
        console.print(f"Active agent: [bold]{session.agent_name}[/bold]")
        return True

    name = args[0]
    if catalog.get_agent(name) is None:
        available = ", ".join(a.name for a in catalog.list_agents()) or "(none)"
        console.print(f"[red]Agent '{name}' not found.[/red] Available: {available}")
        return True

    session.agent_name = name
    console.print(f"[green]Switched to agent:[/green] {name}")
    return True


async def cmd_agents(session: CLISession, args: list[str]) -> bool:
    catalog = get_catalog_service()
    agents = catalog.list_agents()
    if not agents:
        console.print("[dim]No agents defined.[/dim]")
        return True

    table = Table(title="Agents", show_header=True, header_style="bold cyan")
    table.add_column("", width=2)
    table.add_column("Name", style="bold")
    table.add_column("Graph Type")
    table.add_column("Display Name")
    table.add_column("Sub-agents")
    for a in agents:
        marker = "*" if a.name == session.agent_name else " "
        display = a.display_name or a.name
        sub = ", ".join(a.sub_agents) if a.sub_agents else "-"
        table.add_row(marker, a.name, a.graph_type, display, sub)
    console.print(table)
    return True


async def cmd_tools(session: CLISession, args: list[str]) -> bool:
    names = get_default_tool_names()
    if not names:
        console.print("[dim]No tools registered.[/dim]")
        return True

    table = Table(
        title=f"Registered Tools ({len(names)})",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Name")
    for name in sorted(names):
        table.add_row(name)
    console.print(table)
    return True


async def cmd_setting(session: CLISession, args: list[str]) -> bool:
    config = get_config_service()
    if not args:
        overview = config.get_settings_overview()
        if not overview:
            console.print("[dim](no runtime settings)[/dim]")
            return True
        table = Table(title="Runtime Settings", show_header=True, header_style="bold cyan")
        table.add_column("Key", style="bold")
        table.add_column("Value")
        table.add_column("Source", style="dim")
        for key, entry in overview.items():
            value = entry.get("value")
            source = entry.get("source")
            table.add_row(key, repr(value), source)
        console.print(table)
        return True

    key = args[0]
    if len(args) == 1:
        effective = config.get_effective(key)
        if effective is None:
            console.print(f"[dim]{key}: (not set)[/dim]")
        else:
            console.print(f"{key} = [bold]{effective.value!r}[/bold]  [{effective.source}]")
        return True

    new_value = " ".join(args[1:])
    try:
        config.update_setting(key, new_value)
        console.print(f"[green]Updated[/green] {key} = {new_value!r}")
    except Exception as exc:
        console.print(f"[red]Failed to update setting:[/red] {exc}")
    return True


async def cmd_scheduler(session: CLISession, args: list[str]) -> bool:
    from agent.modules.scheduler import get_scheduler

    try:
        scheduler = get_scheduler()
    except RuntimeError as exc:
        console.print(f"[red]Scheduler unavailable:[/red] {exc}")
        return True

    jobs = scheduler.get_jobs()
    if not jobs:
        console.print("[dim]No scheduled jobs.[/dim]")
        return True

    table = Table(
        title=f"Scheduled Jobs ({len(jobs)})",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Job ID", style="bold")
    table.add_column("Next Run")
    table.add_column("Trigger")
    for job in jobs:
        next_run = job.next_run_time.isoformat() if job.next_run_time else "-"
        table.add_row(job.id, next_run, str(job.trigger))
    console.print(table)
    return True


COMMANDS_ORDER: tuple[CommandSpec, ...] = (
    CommandSpec("help", "Show this help", cmd_help),
    CommandSpec("new", "Start a new conversation thread", cmd_new),
    CommandSpec("resume", "Resume a previous thread by id", cmd_resume),
    CommandSpec("clear", "Clear current thread checkpoint", cmd_clear),
    CommandSpec("agent", "Show or switch active agent (/agent [name])", cmd_agent),
    CommandSpec("agents", "List available agents", cmd_agents),
    CommandSpec("tools", "List registered tools", cmd_tools),
    CommandSpec(
        "setting",
        "Show/update settings (/setting [key] [value])",
        cmd_setting,
    ),
    CommandSpec("scheduler", "List scheduled jobs", cmd_scheduler),
    CommandSpec("quit", "Exit the CLI", cmd_quit),
    CommandSpec("exit", "Exit the CLI", cmd_quit),
)

COMMANDS: dict[str, CommandSpec] = {spec.name: spec for spec in COMMANDS_ORDER}


def parse_slash_command(line: str) -> tuple[str, list[str]] | None:
    """Parse a `/command arg1 arg2` line. Returns None if not a slash command."""
    stripped = line.strip()
    if not stripped.startswith("/"):
        return None

    body = stripped[1:]
    if not body:
        return ("help", [])

    try:
        tokens = shlex.split(body)
    except ValueError:
        tokens = body.split()

    if not tokens:
        return ("help", [])

    return (tokens[0].lower(), tokens[1:])


async def dispatch_slash_command(
    session: CLISession,
    name: str,
    args: list[str],
) -> bool:
    """Dispatch a slash command. Returns False to signal exit."""
    spec = COMMANDS.get(name)
    if spec is None:
        console.print(f"[red]Unknown command: /{name}.[/red] Type /help for available commands.")
        return True
    return await spec.handler(session, args)


__all__ = [
    "CLISession",
    "CommandSpec",
    "COMMANDS",
    "COMMANDS_ORDER",
    "dispatch_slash_command",
    "parse_slash_command",
]

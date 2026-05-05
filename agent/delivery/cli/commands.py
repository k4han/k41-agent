"""Slash command handlers for the interactive CLI."""

from __future__ import annotations

import logging
import shlex
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from agent.delivery.cli.session import CLISession, CLI_PLATFORM, CLI_USER_ID
from agent.modules.agent_runtime import clear_agent_session
from agent.modules.agents import get_catalog_service
from agent.modules.tools import get_default_tool_names
from agent.shared.config import get_config_service

logger = logging.getLogger(__name__)


CommandHandler = Callable[[CLISession, list[str]], Awaitable[bool]]


@dataclass(frozen=True, slots=True)
class CommandSpec:
    name: str
    summary: str
    handler: CommandHandler


async def cmd_help(session: CLISession, args: list[str]) -> bool:
    print()
    print("Available slash commands:")
    for spec in COMMANDS_ORDER:
        print(f"  /{spec.name:<14} {spec.summary}")
    print()
    print("Anything else is sent to the active agent.")
    print()
    return True


async def cmd_quit(session: CLISession, args: list[str]) -> bool:
    print("Bye.")
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
    print(f"Started new thread: {new_thread}")
    return True


async def cmd_clear(session: CLISession, args: list[str]) -> bool:
    try:
        await clear_agent_session(
            platform=CLI_PLATFORM,
            user_id=CLI_USER_ID,
            channel_id=session.channel_id,
        )
        print(f"Cleared thread: {session.thread_id}")
    except Exception as exc:
        print(f"Failed to clear thread: {exc}")
    return True


async def cmd_resume(session: CLISession, args: list[str]) -> bool:
    if not args:
        print(f"Current thread: {session.thread_id}")
        print("Usage: /resume <channel_id-or-full-thread-id>")
        return True

    target = args[0]
    prefix = f"{CLI_PLATFORM}_{CLI_USER_ID}_"
    if target.startswith(prefix):
        channel_id = target[len(prefix):]
    else:
        channel_id = target

    if not channel_id:
        print("Invalid thread id.")
        return True

    session.use_thread(channel_id)
    print(f"Resumed thread: {session.thread_id}")
    return True


async def cmd_agent(session: CLISession, args: list[str]) -> bool:
    catalog = get_catalog_service()
    if not args:
        print(f"Active agent: {session.agent_name}")
        return True

    name = args[0]
    if catalog.get_agent(name) is None:
        available = ", ".join(a.name for a in catalog.list_agents()) or "(none)"
        print(f"Agent '{name}' not found. Available: {available}")
        return True

    session.agent_name = name
    print(f"Switched to agent: {name}")
    return True


async def cmd_agents(session: CLISession, args: list[str]) -> bool:
    catalog = get_catalog_service()
    agents = catalog.list_agents()
    if not agents:
        print("No agents defined.")
        return True

    print("Agents:")
    for a in agents:
        marker = "*" if a.name == session.agent_name else " "
        display = a.display_name or a.name
        sub = (
            f" sub_agents={a.sub_agents}"
            if a.sub_agents is not None
            else ""
        )
        print(f"  {marker} {a.name:<24} graph={a.graph_type:<16} ({display}){sub}")
    return True


async def cmd_tools(session: CLISession, args: list[str]) -> bool:
    names = get_default_tool_names()
    if not names:
        print("No tools registered.")
        return True

    print(f"Registered tools ({len(names)}):")
    for name in sorted(names):
        print(f"  - {name}")
    return True


async def cmd_setting(session: CLISession, args: list[str]) -> bool:
    config = get_config_service()
    if not args:
        overview = config.get_settings_overview()
        if not overview:
            print("(no runtime settings)")
            return True
        print("Runtime settings:")
        for key, entry in overview.items():
            value = entry.get("value")
            source = entry.get("source")
            print(f"  {key} = {value!r}  [{source}]")
        return True

    key = args[0]
    if len(args) == 1:
        effective = config.get_effective(key)
        if effective is None:
            print(f"{key}: (not set)")
        else:
            print(f"{key} = {effective.value!r}  [{effective.source}]")
        return True

    new_value = " ".join(args[1:])
    try:
        config.update_setting(key, new_value)
        print(f"Updated {key} = {new_value!r}")
    except Exception as exc:
        print(f"Failed to update setting: {exc}")
    return True


async def cmd_scheduler(session: CLISession, args: list[str]) -> bool:
    from agent.modules.scheduler import get_scheduler

    try:
        scheduler = get_scheduler()
    except RuntimeError as exc:
        print(f"Scheduler unavailable: {exc}")
        return True

    jobs = scheduler.get_jobs()
    if not jobs:
        print("No scheduled jobs.")
        return True

    print(f"Scheduled jobs ({len(jobs)}):")
    for job in jobs:
        next_run = job.next_run_time.isoformat() if job.next_run_time else "-"
        print(f"  - {job.id}  next={next_run}  trigger={job.trigger}")
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
        print(f"Unknown command: /{name}. Type /help for available commands.")
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

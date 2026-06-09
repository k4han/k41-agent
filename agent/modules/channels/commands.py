from __future__ import annotations

import shlex
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from agent.modules.agent_runtime import clear_agent_session
from agent.modules.agents import get_catalog_service
from agent.modules.channels.agent_bridge import (
    build_channel_run_params,
    resolve_code_agent_name,
    resolve_research_agent_name,
    stream_agent_response,
)
from agent.modules.channels.contracts import InboundMessage, OutboundMessage, ParsedCommand
from agent.modules.users import handle_pairing_command


CommandHandler = Callable[[InboundMessage, ParsedCommand], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class CommandSpec:
    name: str
    summary: str
    usage: str
    handler: CommandHandler
    public: bool = False
    aliases: tuple[str, ...] = ()
    suggestable: bool = True


class CommandRegistry:
    def __init__(self) -> None:
        self._commands: dict[str, CommandSpec] = {}
        self._aliases: dict[str, str] = {}

    def register(self, spec: CommandSpec) -> None:
        name = _normalize_command_name(spec.name)
        if not name:
            raise ValueError("Command name is required.")
        self._commands[name] = spec
        for alias in spec.aliases:
            alias_name = _normalize_command_name(alias)
            if alias_name:
                self._aliases[alias_name] = name

    def list(self) -> list[CommandSpec]:
        return list(self._commands.values())

    def get(self, name: str) -> CommandSpec | None:
        normalized = _normalize_command_name(name)
        canonical = self._aliases.get(normalized, normalized)
        return self._commands.get(canonical)

    def is_public(self, name: str) -> bool:
        spec = self.get(name)
        return bool(spec and spec.public)

    def parse(self, text: str) -> ParsedCommand | None:
        stripped = (text or "").strip()
        if not stripped.startswith("/"):
            return None

        body = stripped[1:]
        if not body:
            return ParsedCommand(name="help")

        command_token, raw_args = _split_command_body(body)
        name = _normalize_command_name(command_token.split("@", 1)[0])
        if not name:
            return ParsedCommand(name="help")

        try:
            args = shlex.split(raw_args)
        except ValueError:
            args = raw_args.split()
        return ParsedCommand(name=name, args=args, raw_args=raw_args)

    async def dispatch(self, message: InboundMessage, parsed: ParsedCommand) -> bool:
        spec = self.get(parsed.name)
        if spec is None:
            await _reply(message, f"Unknown command: /{parsed.name}. Type /help for available commands.")
            return True
        await spec.handler(message, parsed)
        return True


def _split_command_body(body: str) -> tuple[str, str]:
    parts = body.split(maxsplit=1)
    if not parts:
        return "help", ""
    return parts[0], parts[1].strip() if len(parts) > 1 else ""


def _normalize_command_name(name: str) -> str:
    return name.strip().lower().removeprefix("/")


async def _reply(
    message: InboundMessage,
    text: str,
    *,
    mode: str = "plain",
) -> None:
    if message.reply is None:
        return
    await message.reply(OutboundMessage(text, mode=mode))


async def cmd_start(message: InboundMessage, parsed: ParsedCommand) -> None:
    await _reply(message, "Hello! I am an AI assistant.\nType anything to start.")


async def cmd_help(message: InboundMessage, parsed: ParsedCommand) -> None:
    registry = get_default_command_registry()
    lines = ["Commands:"]
    for spec in registry.list():
        lines.append(f"/{spec.name:<8} - {spec.summary}")
    await _reply(message, "\n".join(lines))


async def cmd_pair(message: InboundMessage, parsed: ParsedCommand) -> None:
    code = parsed.raw_args.strip()
    if not code:
        await _reply(message, "Please provide a pairing code.")
        return

    await handle_pairing_command(
        message.platform,
        message.user_id,
        f"/pair {code}",
        lambda text: _reply(message, text),
    )


async def cmd_clear(message: InboundMessage, parsed: ParsedCommand) -> None:
    await clear_agent_session(
        platform=message.platform,
        user_id=message.user_id,
        channel_id=message.channel_id,
    )
    await _reply(message, "Chat history has been cleared.")


async def cmd_code(message: InboundMessage, parsed: ParsedCommand) -> None:
    task = parsed.raw_args.strip()
    if not task:
        await _reply(message, "Example: /code list files in directory")
        return

    params = build_channel_run_params(
        message,
        resolve_code_agent_name(message.platform),
        user_input=task,
    )
    await stream_agent_response(message, params)


async def cmd_research(message: InboundMessage, parsed: ParsedCommand) -> None:
    task = parsed.raw_args.strip()
    if not task:
        await _reply(message, "Example: /research pros and cons of microservices")
        return

    research_agent = resolve_research_agent_name(message.platform)
    params = build_channel_run_params(
        message,
        research_agent,
        user_input=task,
        workflow="research_chain" if research_agent is None else None,
    )
    await stream_agent_response(message, params)


async def cmd_agent(message: InboundMessage, parsed: ParsedCommand) -> None:
    text = parsed.raw_args.strip()
    if not text:
        await _reply(message, "Example: /agent researcher Find info about AI")
        return

    parts = text.split(None, 1)
    agent_name = parts[0]
    task = parts[1] if len(parts) > 1 else ""
    if not task:
        await _reply(message, "Example: /agent researcher Find info about AI")
        return

    catalog = get_catalog_service()
    if catalog.get_agent(agent_name) is None:
        agents = catalog.list_agents()
        if not agents:
            await _reply(message, "No custom agents defined. Use /code or /research.")
        else:
            available = ", ".join(agent.name for agent in agents)
            await _reply(message, f"Agent '{agent_name}' does not exist. Available: {available}")
        return

    params = build_channel_run_params(message, agent_name, user_input=task)
    await stream_agent_response(message, params)


async def cmd_agents(message: InboundMessage, parsed: ParsedCommand) -> None:
    catalog = get_catalog_service()
    agents = catalog.list_agents()
    if not agents:
        await _reply(
            message,
            "No custom agents defined.\n"
            "Use /code or /research, or create .md files in ~/.k41-agent/agents/.",
        )
        return

    lines = ["Available agents:"]
    for agent in agents:
        display_name = agent.display_name or agent.name
        suffix = (
            f" (can call: {', '.join(agent.sub_agents)})"
            if agent.sub_agents is not None
            else ""
        )
        lines.append(
            f"- {display_name} {agent.name} - graph: {agent.graph_type}{suffix}"
        )
    await _reply(message, "\n".join(lines), mode="plain")


_default_registry: CommandRegistry | None = None


def build_default_command_registry() -> CommandRegistry:
    registry = CommandRegistry()
    registry.register(
        CommandSpec("start", "Start", "/start", cmd_start, public=True)
    )
    registry.register(
        CommandSpec("help", "Help", "/help", cmd_help, public=True)
    )
    registry.register(
        CommandSpec("pair", "Pair account", "/pair CODE", cmd_pair, public=True)
    )
    registry.register(
        CommandSpec("clear", "Clear chat history", "/clear", cmd_clear)
    )
    registry.register(
        CommandSpec("code", "Coding assistant", "/code <task>", cmd_code)
    )
    registry.register(
        CommandSpec("research", "Research and synthesis", "/research <task>", cmd_research)
    )
    registry.register(
        CommandSpec("agent", "Run a specific agent", "/agent <name> <task>", cmd_agent)
    )
    registry.register(
        CommandSpec("agents", "List available agents", "/agents", cmd_agents)
    )
    return registry


def get_default_command_registry() -> CommandRegistry:
    global _default_registry
    if _default_registry is None:
        _default_registry = build_default_command_registry()
    return _default_registry


__all__ = [
    "CommandRegistry",
    "CommandSpec",
    "build_default_command_registry",
    "get_default_command_registry",
]

import logging

from agent.modules.agent_runtime.public import build_run_params, clear_agent_session
from agent.modules.agents.public import resolve_catalog_agent_name, get_catalog_service
from agent.modules.channels.infrastructure.telegram.streaming import (
    handle_streaming_response,
)
from agent.modules.users.public import authenticate_channel_message, Platform
from agent.modules.workflows import DEFAULT_WORKING_DIR
from agent.shared.config import get_config_service

logger = logging.getLogger(__name__)

_default_agent_name: str | None = None
_code_agent_name: str | None = None
_research_agent_name: str | None = None


def resolve_agent_config() -> None:
    """Resolve and cache agent names from config."""
    global _default_agent_name, _code_agent_name, _research_agent_name

    config = get_config_service()
    _default_agent_name = resolve_catalog_agent_name(
        config.get_str("channels.telegram.default_agent", ""),
        "default",
    )
    _code_agent_name = resolve_catalog_agent_name(
        config.get_str("channels.telegram.code_agent", ""),
        "code-agent",
        "coder",
    )
    _research_agent_name = resolve_catalog_agent_name(
        config.get_str("channels.telegram.research_agent", ""),
        "research-agent",
        "researcher",
    )


async def auth_middleware(handler, event, data: dict):
    if not getattr(event, "text", None) or not getattr(event, "from_user", None):
        return await handler(event, data)

    user_id = str(event.from_user.id)

    if not await authenticate_channel_message(Platform.TELEGRAM, user_id, event.text, event.answer):
        return

    return await handler(event, data)


def _build_telegram_run_params(message, agent_name, **overrides) -> dict:
    """Build run params for a Telegram message with common context pre-filled."""
    return build_run_params(
        platform="telegram",
        user_id=str(message.from_user.id),
        user_input=message.text,
        channel_id=str(message.chat.id),
        agent_name=agent_name,
        **overrides,
    )


async def cmd_start(message):
    """Handle /start command."""
    await message.answer("Hello! I am an AI assistant.\nType anything to start.")


async def cmd_help(message):
    """Handle /help command."""
    await message.answer(
        "Commands:\n"
        "/start    - Start\n"
        "/help     - Help\n"
        "/code     - Coding assistant\n"
        "/research - Research & synthesis\n"
        "/clear    - Clear chat history"
    )


async def cmd_clear(message):
    """Handle /clear command."""
    await clear_agent_session(
        platform="telegram",
        user_id=str(message.from_user.id),
        channel_id=str(message.chat.id),
    )
    await message.answer("Cuộc trò chuyện đã được xoá.")


async def cmd_code(message):
    """Handle /code command — run the coding agent."""
    text = message.text.removeprefix("/code").strip()
    if not text:
        await message.answer("Example: /code list files in directory")
        return

    params = _build_telegram_run_params(message, _code_agent_name, user_input=text)
    await handle_streaming_response(message, params)


async def cmd_research(message):
    """Handle /research command — run the research agent."""
    text = message.text.removeprefix("/research").strip()
    if not text:
        await message.answer("Example: /research pros and cons of microservices")
        return

    params = _build_telegram_run_params(
        message,
        _research_agent_name,
        user_input=text,
        workflow="research_chain" if _research_agent_name is None else None,
    )
    await handle_streaming_response(message, params)


async def cmd_agent(message):
    """Handle /agent <name> <task> command — run a specific agent."""
    text = message.text.removeprefix("/agent").strip()
    if not text:
        await message.answer("Example: /agent researcher Find info about AI")
        return

    parts = text.split(None, 1)
    agent_name = parts[0]
    task = parts[1] if len(parts) > 1 else ""

    if not task:
        await message.answer("Example: /agent researcher Find info about AI")
        return

    catalog = get_catalog_service()
    agent_config = catalog.get_agent(agent_name)
    if agent_config is None:
        agents = catalog.list_agents()
        if not agents:
            await message.answer("No custom agents defined. Use /code or /research.")
        else:
            available = ", ".join(a.name for a in agents)
            await message.answer(
                f"Agent '{agent_name}' không tồn tại. Có sẵn: {available}"
            )
        return

    params = _build_telegram_run_params(message, agent_name, user_input=task)
    await handle_streaming_response(message, params)


async def cmd_agents(message):
    """Handle /agents command — list all available agents from MD files."""
    catalog = get_catalog_service()
    agents = catalog.list_agents()
    if not agents:
        await message.answer(
            "No custom agents defined.\n"
            "Use /code or /research, or create .md files in ~/.kaka-agent/agents/."
        )
        return

    lines = ["Available agents:"]
    for a in agents:
        dn = a.display_name or a.name
        sub = (
            f" (can call: {', '.join(a.sub_agents)})"
            if a.sub_agents is not None
            else ""
        )
        lines.append(
            f"- <b>{dn}</b> <code>{a.name}</code> — graph: {a.graph_type}{sub}"
        )

    await message.answer("\n".join(lines))


async def on_message(message):
    """Default message handler — run the default agent."""
    if not message.text:
        return

    params = _build_telegram_run_params(
        message,
        _default_agent_name,
        working_dir=DEFAULT_WORKING_DIR,
    )
    await handle_streaming_response(message, params)
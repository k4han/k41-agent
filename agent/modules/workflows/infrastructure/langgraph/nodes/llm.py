"""Dynamic LLM node — resolves model, system prompt, and tools from agent config at runtime."""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from langchain_core.messages import BaseMessage, SystemMessage

from agent.modules.providers.public import get_chat_model
from agent.modules.skills.public import get_skills_catalog_xml
from agent.modules.workflows.infrastructure.langgraph.tools.registry import (
    get_default_tools,
    resolve_tools,
)

if TYPE_CHECKING:
    from langgraph.runtime import Runtime
    from agent.modules.workflows.infrastructure.langgraph.run_config import (
        WorkflowContext,
    )


SKILLS_DISCLOSURE_PROMPT = (
    "The following skills provide specialized instructions for specific tasks.\n"
    "When a task matches a skill description, call the skill tool with the skill "
    "name to load full instructions before proceeding."
)

SUB_AGENT_DISCLOSURE_PROMPT = (
    "The call_agent tool can delegate work to the following specialized "
    "sub-agents.\n"
    "Use the exact sub_agent name when delegating:"
)

SUB_AGENT_EMPTY_PROMPT = (
    "The call_agent tool is available, but this agent has no callable "
    "sub-agents configured.\n"
    "Do not call call_agent unless a callable sub-agent is listed."
)

DEFAULT_MODEL = "devstral-2512"

SYSTEM_PROMPTS: dict[str, str] = {
    "default": "You are a helpful AI assistant.\nWorking directory: {working_dir}",
    "backend": (
        "You are a Python/backend engineer assistant.\n"
        "Working directory: {working_dir}\n"
        "Focus on Pythonic implementations, type hints, and maintainable code."
    ),
    "frontend": (
        "You are a React/TypeScript frontend engineer assistant.\n"
        "Working directory: {working_dir}\n"
        "Prefer functional components, hooks, and modern frontend best practices."
    ),
    "devops": (
        "You are a DevOps engineer assistant.\n"
        "Working directory: {working_dir}\n"
        "Help with Docker, CI/CD, shell automation, and deployment operations."
    ),
}


def _get_context_value(ctx: dict, key: str, default):
    return ctx.get(key, default) if isinstance(ctx, dict) else default


@lru_cache(maxsize=2)
def _build_skills_prompt_section() -> str:
    catalog_xml = get_skills_catalog_xml().strip()
    if catalog_xml == "<available_skills/>":
        return ""
    return f"\n\n{SKILLS_DISCLOSURE_PROMPT}\n{catalog_xml}"


def _build_sub_agents_prompt_section(agent_name: str, catalog) -> str:
    get_callable_agents = getattr(catalog, "get_callable_agents", None)
    if not callable(get_callable_agents):
        return ""

    callable_agents = list(get_callable_agents(agent_name) or [])
    if not callable_agents:
        return f"\n\n{SUB_AGENT_EMPTY_PROMPT}"

    lines = [SUB_AGENT_DISCLOSURE_PROMPT]
    for sub_agent_name in callable_agents:
        sub_agent_config = catalog.get_agent(sub_agent_name)
        description = ""
        if sub_agent_config is not None:
            description = str(getattr(sub_agent_config, "description", "") or "").strip()
        if not description:
            description = "No description provided."
        lines.append(f"- {sub_agent_name}: {description}")
    section = "\n".join(lines)
    return f"\n\n{section}"


@lru_cache(maxsize=32)
def _resolve_tools(tool_names_key: tuple[str, ...]):
    return resolve_tools(tool_names_key)


@lru_cache(maxsize=1)
def _get_default_tools():
    return get_default_tools()


def llm_node(state, runtime: "Runtime[WorkflowContext]"):
    """Dynamic node: reads agent_name from context, resolves full config at runtime."""
    from agent.modules.agents.public import get_catalog_service

    ctx = runtime.context
    agent_name = _get_context_value(ctx, "agent_name", "default")
    working_dir = _get_context_value(ctx, "working_dir", "")

    # Load agent config from catalog
    catalog = get_catalog_service()
    config = catalog.get_agent(agent_name)

    # Fallback to default agent if not found
    if config is None:
        config = catalog.get_agent("default")

    # Extract config values with fallbacks
    if config:
        model = config.model or DEFAULT_MODEL
        system_prompt_template = config.system_prompt or SYSTEM_PROMPTS["default"]
        tool_names = config.tools if config.tools else None
    else:
        # Ultimate fallback (should not happen with builtin default)
        model = DEFAULT_MODEL
        system_prompt_template = SYSTEM_PROMPTS["default"]
        tool_names = None

    # Override tools if specified in context (for sub-agent calls)
    ctx_tool_names = _get_context_value(ctx, "allowed_tool_names", None)
    if ctx_tool_names is not None:
        tool_names = ctx_tool_names

    # Resolve tools
    if tool_names is None:
        tools = _get_default_tools()
    else:
        tools = _resolve_tools(tuple(tool_names))

    # Build system prompt
    system_prompt = system_prompt_template
    if working_dir and "{working_dir}" in system_prompt:
        system_prompt = system_prompt.format(working_dir=working_dir)

    if tools and any(getattr(t, "name", "") == "call_agent" for t in tools):
        system_prompt = (
            f"{system_prompt}{_build_sub_agents_prompt_section(agent_name, catalog)}"
        )

    # Inject skills catalog
    if tools and any(getattr(t, "name", "") == "skill" for t in tools):
        system_prompt = f"{system_prompt}{_build_skills_prompt_section()}"

    messages: list[BaseMessage] = [
        SystemMessage(content=system_prompt),
        *state["messages"],
    ]

    llm = get_chat_model(model=model).bind_tools(tools)
    response = llm.invoke(messages)
    return {"messages": [response]}


# Compatibility alias for code that still calls make_llm_node().
# The new design resolves config at runtime, so this just returns llm_node.
def make_llm_node(tools=None, model=None, **_kwargs):
    """Deprecated. Use llm_node directly — config is resolved at runtime."""
    return llm_node

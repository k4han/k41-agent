"""Dynamic LLM node — resolves model, system prompt, and tools from agent config at runtime."""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from langchain_core.messages import BaseMessage, SystemMessage

from agent.modules.providers import get_chat_model
from agent.modules.workflows.message_history import normalize_messages_for_chat_model
from agent.modules.workflows.prompt_builders import (
    build_llm_system_prompt,
)
from agent.modules.tools import get_default_tools, resolve_tools

if TYPE_CHECKING:
    from langgraph.runtime import Runtime
    from agent.modules.workflows.run_config import (
        WorkflowContext,
    )


@lru_cache(maxsize=32)
def _resolve_tools(tool_names_key: tuple[str, ...]):
    return resolve_tools(tool_names_key)


@lru_cache(maxsize=1)
def _get_default_tools():
    return get_default_tools()


def llm_node(state, runtime: Runtime[WorkflowContext]):
    """Dynamic node: reads agent_name from context, resolves full config at runtime."""
    from agent.modules.agents import get_catalog_service

    ctx = runtime.context
    agent_name = ctx.get_agent_name()
    working_dir = ctx.get_working_dir()

    # Load agent config from catalog
    catalog = get_catalog_service()
    config = catalog.get_agent(agent_name)

    # Fallback to "default" agent if the requested agent is not found.
    # The builtin default is always guaranteed to exist after catalog.load().
    if config is None:
        config = catalog.get_agent("default")

    # config is guaranteed non-None at this point (builtin default always present).
    provider = ctx.get_provider() or config.provider
    model = ctx.get_model() or config.model or None
    system_prompt_template = config.system_prompt
    tool_names = config.tools if config.tools else None

    # Override tools if specified in context (for sub-agent calls)
    ctx_tool_names = ctx.get_allowed_tool_names()
    if ctx_tool_names is not None:
        tool_names = ctx_tool_names

    # Resolve tools
    if tool_names is None:
        tools = _get_default_tools()
    else:
        tools = _resolve_tools(tuple(tool_names))

    system_prompt = build_llm_system_prompt(
        system_prompt_template=system_prompt_template,
        working_dir=working_dir,
        agent_name=agent_name,
        tools=tools,
        catalog=catalog,
    )

    messages: list[BaseMessage] = normalize_messages_for_chat_model(
        [
            SystemMessage(content=system_prompt),
            *state["messages"],
        ]
    )

    llm = get_chat_model(provider_name=provider, model=model).bind_tools(tools)
    response = llm.invoke(messages)
    return {"messages": [response]}

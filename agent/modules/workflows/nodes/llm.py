"""Dynamic LLM node — resolves model, system prompt, and tools from agent config at runtime."""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from langchain_core.messages import BaseMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from agent.modules.mcp import get_all_mcp_tools, get_mcp_server_tools
from agent.modules.providers import get_resolved_chat_model
from agent.modules.usage import with_usage_tracking
from agent.modules.prompt_variables import get_runtime_prompt_variable_values
from agent.modules.workflows.message_history import normalize_messages_for_chat_model
from agent.modules.workflows.prompt_builders import (
    build_llm_system_prompt,
)
from agent.modules.tools import get_default_tools, resolve_tools

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool
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


async def _collect_mcp_tools(
    tool_names: tuple[str, ...] | None,
    agent_name: str = "default",
    mcp_servers: list[str] | None = None,
) -> list["BaseTool"]:
    """Return MCP tools relevant for this LLM call.

    - If ``mcp_servers`` is provided (ticked), load only tools for those servers.
    - ``tool_names is None`` means the agent uses the full default toolset, so
      include every loaded MCP tool.
    - If ``tool_names`` does not contain any tool starting with 'mcp__' and the
      agent is the 'default' chat agent, we also include every loaded MCP tool
      to make dynamically installed MCP tools instantly available.
    - Otherwise only include MCP tools whose prefixed name was explicitly listed.
    """
    if mcp_servers is not None:
        tools = []
        for server in mcp_servers:
            try:
                server_tools = await get_mcp_server_tools(server)
                tools.extend(server_tools)
            except Exception:
                pass
        return tools

    try:
        all_mcp = await get_all_mcp_tools()
    except Exception:
        return []
    if not all_mcp:
        return []
    if tool_names is None:
        return list(all_mcp)
    wanted = {name for name in tool_names if name.startswith("mcp__")}
    if not wanted:
        if agent_name == "default":
            return list(all_mcp)
        return []
    return [tool for tool in all_mcp if tool.name in wanted]


async def llm_node(state, config: RunnableConfig, runtime: Runtime[WorkflowContext]):
    """Dynamic node: reads agent_name from context, resolves full config at runtime."""
    from agent.modules.agents import get_catalog_service

    ctx = runtime.context
    agent_name = ctx.get_agent_name()
    working_dir = ctx.get_working_dir()
    workspace = ctx.get_workspace()

    # Load agent config from catalog
    catalog = get_catalog_service()
    agent_config = catalog.get_agent(agent_name)

    # Fallback to "default" agent if the requested agent is not found.
    # The builtin default is always guaranteed to exist after catalog.load().
    if agent_config is None:
        agent_config = catalog.get_agent("default")

    # config is guaranteed non-None at this point (builtin default always present).
    provider = ctx.get_provider() or agent_config.provider
    model = ctx.get_model() or agent_config.model or None
    system_prompt_template = agent_config.system_prompt
    tool_names = agent_config.tools if agent_config.tools else None

    # Override tools if specified in context (for sub-agent calls)
    ctx_tool_names = ctx.get_allowed_tool_names()
    if ctx_tool_names is not None:
        tool_names = ctx_tool_names

    # Resolve built-in tools
    if tool_names is None:
        tools = list(_get_default_tools())
    else:
        tools = list(_resolve_tools(tuple(tool_names)))

    # Append MCP tools (on-demand loaded + cached by MCPService)
    mcp_tools = await _collect_mcp_tools(
        tuple(tool_names) if tool_names else None,
        agent_name=agent_name,
        mcp_servers=getattr(agent_config, "mcp_servers", None),
    )
    if mcp_tools:
        known_names = {tool.name for tool in tools}
        tools.extend(tool for tool in mcp_tools if tool.name not in known_names)

    prompt_variables = await get_runtime_prompt_variable_values()
    system_prompt = build_llm_system_prompt(
        system_prompt_template=system_prompt_template,
        working_dir=working_dir,
        workspace=workspace.display_label(),
        agent_name=agent_name,
        tools=tools,
        catalog=catalog,
        prompt_variables=prompt_variables,
    )

    messages: list[BaseMessage] = normalize_messages_for_chat_model(
        [
            SystemMessage(content=system_prompt),
            *state["messages"],
        ]
    )

    resolved = get_resolved_chat_model(provider_name=provider, model=model)
    llm = resolved.model.bind_tools(tools)
    response = await llm.ainvoke(
        messages,
        config=with_usage_tracking(
            config,
            agent_name=agent_name,
            provider_name=resolved.provider_name,
            model_name=resolved.model_name,
            call_kind="agent",
        ),
    )
    return {"messages": [response]}

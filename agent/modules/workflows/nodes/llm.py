"""Dynamic LLM node — resolves model, system prompt, and tools from agent config at runtime."""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.messages import BaseMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from agent.modules.providers import get_resolved_chat_model
from agent.modules.usage import with_usage_tracking
from agent.modules.prompt_variables import get_runtime_prompt_variable_values
from agent.modules.workflows.message_history import normalize_messages_for_chat_model
from agent.modules.workflows.prompt_builders import (
    build_llm_system_prompt,
)
from agent.modules.tools import ToolResolver

if TYPE_CHECKING:
    from langgraph.runtime import Runtime
    from agent.modules.workflows.run_config import (
        WorkflowContext,
    )


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

    # Override tools if specified in context (for sub-agent calls)
    ctx_tool_names = ctx.get_allowed_tool_names()

    tools = await ToolResolver().aresolve_for_agent(
        agent_name,
        override_tool_names=ctx_tool_names,
    )

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

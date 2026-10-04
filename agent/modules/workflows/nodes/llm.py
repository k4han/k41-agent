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
from agent.modules.workflows.system_prompt_cache import (
    build_system_prompt_cache_key,
    get_cached_system_prompt,
    store_system_prompt,
)
from agent.modules.tools import ToolResolver, get_thread_id

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
    tools = ToolResolver().for_workspace(tools, workspace, agent_name)

    thread_id = get_thread_id(config)
    from agent.modules.tools import conversation_key
    cache_key = build_system_prompt_cache_key(
        agent_name=agent_name,
        working_dir=working_dir,
        workspace_label=workspace.display_label(),
        tool_names=[getattr(tool, "name", "") for tool in tools],
        allowed_skill_names=ctx.get_allowed_skill_names(),
        thread_id=thread_id,
    )
    system_prompt = get_cached_system_prompt(cache_key)

    if system_prompt is None:
        prompt_variables = await get_runtime_prompt_variable_values()
        skills_catalog_xml = None
        if any(getattr(tool, "name", "") == "skill" for tool in tools):
            from agent.modules.skills import get_effective_skills_catalog_xml

            skills_catalog_xml = await get_effective_skills_catalog_xml(
                allowed_names=ctx.get_allowed_skill_names(),
                workspace=workspace,
                thread_id=thread_id,
            )
        system_prompt = build_llm_system_prompt(
            system_prompt_template=system_prompt_template,
            working_dir=working_dir,
            workspace=workspace.display_label(),
            agent_name=agent_name,
            tools=tools,
            catalog=catalog,
            prompt_variables=prompt_variables,
            skills_catalog_xml=skills_catalog_xml,
            scratchpad_path=f".k41-agent/scratchpad/{conversation_key(thread_id or '')}/",
        )
        store_system_prompt(cache_key, system_prompt)

    from types import SimpleNamespace
    from agent.modules.tools import migrate_history_outputs
    history = await migrate_history_outputs(state["messages"], SimpleNamespace(context=ctx, config=config))
    messages: list[BaseMessage] = normalize_messages_for_chat_model([SystemMessage(content=system_prompt), *history])

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

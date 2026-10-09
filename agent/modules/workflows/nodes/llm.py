"""Dynamic LLM node — resolves model, system prompt, and tools from agent config at runtime."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from agent.modules.providers import get_resolved_chat_model
from agent.modules.workflows.model_effort import get_workflow_reasoning_effort_kwargs
from agent.modules.usage import estimate_context_breakdown, with_usage_tracking
from agent.modules.prompt_variables import get_runtime_prompt_variable_values
from agent.modules.workflows.message_history import normalize_messages_for_chat_model
from agent.modules.workflows.model_context import emit_reported_context_usage, prepare_model_context
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

logger = logging.getLogger(__name__)


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
    from agent.modules.skills import get_repository_skill_dir

    try:
        repository_skill_dir = get_repository_skill_dir()
    except ValueError as exc:
        logger.debug("Invalid repository skill dir: %s", exc)
        repository_skill_dir = ""
    cache_key = build_system_prompt_cache_key(
        agent_name=agent_name,
        working_dir=working_dir,
        workspace_label=workspace.display_label(),
        tool_names=[getattr(tool, "name", "") for tool in tools],
        allowed_skill_names=ctx.get_allowed_skill_names(),
        thread_id=thread_id,
        workspace=workspace,
        repository_skill_dir=repository_skill_dir,
    )
    prompt_sections: dict[str, str] = {}
    system_prompt = get_cached_system_prompt(cache_key, sections=prompt_sections)
    catalog_has_skills = None

    if system_prompt is None:
        prompt_variables = await get_runtime_prompt_variable_values()
        skills_catalog_xml = None
        if any(getattr(tool, "name", "") == "skill" for tool in tools):
            from agent.modules.skills import get_effective_skills_catalog_xml

            skills_catalog_xml = await get_effective_skills_catalog_xml(
                allowed_names=ctx.get_allowed_skill_names(),
                workspace=workspace,
                repository_dir=repository_skill_dir,
                thread_id=thread_id,
            )
            catalog_has_skills = skills_catalog_xml != "<available_skills/>"
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
            sections=prompt_sections,
        )
        store_system_prompt(cache_key, system_prompt, sections=prompt_sections)

    from types import SimpleNamespace
    from agent.modules.tools import migrate_history_outputs
    history = await migrate_history_outputs(state["messages"], SimpleNamespace(context=ctx, config=config))
    from agent.modules.skills import active_context, model_skill_history, skill_commands
    skill_context, active_skills = ("", state.get("active_skills", {}))
    processed_skill_messages = state.get("processed_skill_messages", [])
    try:
        if state.get("active_skills") or skill_commands(state["messages"])[0]:
            skill_context, active_skills, processed_skill_messages = await active_context(state, workspace=workspace, thread_id=thread_id or "",
                agent_name=agent_name, names=ctx.get_allowed_skill_names(), tools=tools)
    except (PermissionError, FileNotFoundError, ValueError) as exc:
        return {
            "messages": [AIMessage(content=f"Unable to use the requested skill: {exc}")],
            "active_skills": active_skills if any(tool.name == "skill" for tool in tools) else {},
        }
    resolved = get_resolved_chat_model(provider_name=provider, model=model)
    system = SystemMessage(content=system_prompt + skill_context)
    if not skill_context and (catalog_has_skills is False or (catalog_has_skills is None and "<available_skills>" not in system_prompt)):
        tools = [tool for tool in tools if tool.name != "skill"]
    if skill_context:
        from langchain_core.messages.utils import count_tokens_approximately
        from agent.modules.providers import DEFAULT_CONTEXT_WINDOW
        if count_tokens_approximately([system], tools=tools) >= getattr(resolved, "context_window", DEFAULT_CONTEXT_WINDOW) * 3 // 4:
            raise ValueError("Active skill instructions exceed the context budget. Unload a skill or select a larger context model.")
    history = model_skill_history(history)
    history, history_updates = await prepare_model_context(
        history, system=system, tools=tools, context=ctx,
        agent_config=agent_config, resolved=resolved, config=config,
    )
    messages: list[BaseMessage] = normalize_messages_for_chat_model([system, *history])
    prompt_sections["active_skills"] = skill_context
    context_breakdown = estimate_context_breakdown(messages, tools=tools, prompt_sections=prompt_sections)
    model_kwargs = get_workflow_reasoning_effort_kwargs(ctx, agent_config, resolved)
    llm = resolved.model.bind_tools(tools, **model_kwargs)
    response = await llm.ainvoke(
        messages,
        config=with_usage_tracking(
            config,
            agent_name=agent_name,
            provider_name=resolved.provider_name,
            model_name=resolved.model_name,
            call_kind="agent",
            context_breakdown=context_breakdown,
        ),
    )
    emit_reported_context_usage(response, resolved=resolved, config=config, context_breakdown=context_breakdown)
    updates = {"messages": [*history_updates, response]}
    if active_skills or state.get("active_skills") or processed_skill_messages:
        updates.update(active_skills=active_skills, processed_skill_messages=processed_skill_messages)
    return updates

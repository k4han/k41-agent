from langchain_core.messages import SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from agent.modules.agents import render_agent_prompt
from agent.modules.workflows.prompt_builders import build_llm_system_prompt

from agent.modules.workflows.checkpoint import (
    get_checkpointer,
)
from agent.modules.workflows.registry import (
    GraphRegistry,
)
from agent.modules.workflows.nodes.trim import (
    make_prepare_context_node,
)
from agent.modules.workflows.run_config import WorkflowContext
from agent.modules.workflows.message_history import normalize_messages_for_chat_model
from agent.modules.workflows.model_context import emit_reported_context_usage, prepare_model_context
from agent.modules.workflows.state.extensions import (
    ResearchState,
)
from agent.modules.providers import DEFAULT_CONTEXT_WINDOW, get_resolved_chat_model, model_response_reserve, execute_model_call
from agent.modules.workflows.model_effort import get_workflow_reasoning_effort_kwargs
from agent.modules.usage import estimate_context_breakdown, with_usage_tracking
from agent.shared.infrastructure.parsing import extract_final_text_content


async def _research_system(name, runtime, agent_config):
    from agent.modules.prompt_variables import get_runtime_prompt_variable_values

    ctx = runtime.context
    working_dir = ctx.get_working_dir()
    workspace = ctx.get_workspace().display_label()
    prompt_variables = await get_runtime_prompt_variable_values()
    instructions = await render_agent_prompt(
        name, working_dir=working_dir, workspace=workspace, prompt_variables=prompt_variables,
    )
    caller_instructions = build_llm_system_prompt(
        system_prompt_template=agent_config.system_prompt, working_dir=working_dir,
        workspace=workspace, agent_name=ctx.get_agent_name(), tools=[], catalog=None,
        prompt_variables=prompt_variables,
    )
    if caller_instructions.strip():
        instructions += f"\n\n<agent_instructions>\n{caller_instructions}\n</agent_instructions>"
    return SystemMessage(content=instructions)


def _resolve_runtime_model(runtime):
    from agent.modules.agents import get_catalog_service

    ctx = runtime.context
    catalog = get_catalog_service()
    agent_config = catalog.get_agent(ctx.get_agent_name())
    if agent_config is None:
        raise RuntimeError(f"Agent '{ctx.get_agent_name()}' not found in catalog.")
    provider = ctx.get_provider() or agent_config.provider
    model = ctx.get_model() or agent_config.model or None
    resolved = get_resolved_chat_model(provider_name=provider, model=model)
    model_kwargs = get_workflow_reasoning_effort_kwargs(ctx, agent_config, resolved)
    return resolved, model_kwargs, agent_config


async def _research_node(state: ResearchState, config: RunnableConfig, runtime):
    """Collect research directions for the request."""
    resolved, model_kwargs, agent_config = _resolve_runtime_model(runtime)
    initial_trim_applied = getattr(runtime.context, "channel_trim_applied", False)

    async def attempt(resolved):
        runtime.context.channel_trim_applied = initial_trim_applied
        model_kwargs = get_workflow_reasoning_effort_kwargs(runtime.context, agent_config, resolved)
        llm = resolved.model
        system = await _research_system("research-planner", runtime, agent_config)
        history, history_updates = await prepare_model_context(
            state["messages"], system=system, context=runtime.context,
            agent_config=agent_config, resolved=resolved, config=config,
        )
        messages = normalize_messages_for_chat_model([system, *history])
        context_breakdown = estimate_context_breakdown(messages)
        response = await llm.ainvoke(
            messages,
            config=with_usage_tracking(
                config,
                agent_name=runtime.context.get_agent_name(),
                provider_name=resolved.provider_name,
                model_name=resolved.model_name,
                call_kind="research",
                context_breakdown=context_breakdown,
                context_window=getattr(resolved, "context_window", DEFAULT_CONTEXT_WINDOW),
                reserved_tokens=model_response_reserve(resolved),
            ),
            **model_kwargs,
        )
        emit_reported_context_usage(response, resolved=resolved, config=config, context_breakdown=context_breakdown)
        return {"messages": [*history_updates, response]}

    return await execute_model_call(resolved, attempt)


async def _summarize_node(state: ResearchState, config: RunnableConfig, runtime):
    """Summarize the collected research context."""
    resolved, model_kwargs, agent_config = _resolve_runtime_model(runtime)
    initial_trim_applied = getattr(runtime.context, "channel_trim_applied", False)

    async def attempt(resolved):
        runtime.context.channel_trim_applied = initial_trim_applied
        model_kwargs = get_workflow_reasoning_effort_kwargs(runtime.context, agent_config, resolved)
        llm = resolved.model
        system = await _research_system("research-summarizer", runtime, agent_config)
        history, history_updates = await prepare_model_context(
            state["messages"], system=system, context=runtime.context,
            agent_config=agent_config, resolved=resolved, config=config,
        )
        messages = normalize_messages_for_chat_model([system, *history])
        context_breakdown = estimate_context_breakdown(messages)
        response = await llm.ainvoke(
            messages,
            config=with_usage_tracking(
                config,
                agent_name=runtime.context.get_agent_name(),
                provider_name=resolved.provider_name,
                model_name=resolved.model_name,
                call_kind="research_summary",
                context_breakdown=context_breakdown,
                context_window=getattr(resolved, "context_window", DEFAULT_CONTEXT_WINDOW),
                reserved_tokens=model_response_reserve(resolved),
            ),
            **model_kwargs,
        )
        emit_reported_context_usage(response, resolved=resolved, config=config, context_breakdown=context_breakdown)
        return {
            "messages": [*history_updates, response],
            "summary": extract_final_text_content(response.content),
        }

    return await execute_model_call(resolved, attempt)


def build_research_graph() -> None:
    graph = StateGraph(ResearchState, context_schema=WorkflowContext)
    graph.add_node("prepare_context", make_prepare_context_node())
    graph.add_node("research", _research_node)
    graph.add_node("summarize", _summarize_node)

    graph.add_edge(START, "prepare_context")
    graph.add_edge("prepare_context", "research")
    graph.add_edge("research", "summarize")
    graph.add_edge("summarize", END)

    GraphRegistry.register(
        "research_chain",
        graph.compile(checkpointer=get_checkpointer()),
        description="research, information synthesis, and multi-step analysis",
    )

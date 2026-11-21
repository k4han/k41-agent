import asyncio

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
from agent.modules.tools import get_thread_id


async def _research_system(name, runtime, agent_config):
    from agent.modules.prompt_variables import get_runtime_prompt_variable_values

    ctx = runtime.context
    execution_workspace = ctx.get_workspace()
    workspace = execution_workspace.display_label()
    prompt_variables = await get_runtime_prompt_variable_values(execution_workspace)
    working_dir = prompt_variables.get("working_dir", ctx.get_working_dir())
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


async def _run_research_model(state: ResearchState, config: RunnableConfig, runtime, *, prompt_name: str, call_kind: str):
    resolved, _, agent_config = _resolve_runtime_model(runtime)
    context = runtime.context
    initial_trim_applied = getattr(context, "channel_trim_applied", False)
    queue_turn = getattr(context, "chat_queue_turn", None)
    insertions = []

    async def attempt(resolved):
        context.channel_trim_applied = initial_trim_applied
        model_kwargs = get_workflow_reasoning_effort_kwargs(context, agent_config, resolved)
        llm = resolved.model
        system = await _research_system(prompt_name, runtime, agent_config)
        if queue_turn is not None:
            insertions.extend(await queue_turn.take_insertions(get_thread_id(config), context.get_workspace()))
        model_history = [*state["messages"], *insertions] if insertions else state["messages"]
        history, history_updates = await prepare_model_context(
            model_history, system=system, context=context,
            agent_config=agent_config, resolved=resolved, config=config,
            **({"retain_from_id": insertions[0].id} if insertions else {}),
        )
        messages = await asyncio.to_thread(normalize_messages_for_chat_model, [system, *history])
        context_breakdown = await asyncio.to_thread(estimate_context_breakdown, messages)
        response = await llm.ainvoke(
            messages,
            config=with_usage_tracking(
                config,
                agent_name=context.get_agent_name(),
                provider_name=resolved.provider_name,
                model_name=resolved.model_name,
                call_kind=call_kind,
                context_breakdown=context_breakdown,
                context_window=getattr(resolved, "context_window", DEFAULT_CONTEXT_WINDOW),
                reserved_tokens=model_response_reserve(resolved),
            ),
            **model_kwargs,
        )
        emit_reported_context_usage(response, resolved=resolved, config=config, context_breakdown=context_breakdown)
        return {"messages": [*(history_updates or insertions), response]}

    return await execute_model_call(resolved, attempt)


async def _research_node(state: ResearchState, config: RunnableConfig, runtime):
    """Collect research directions for the request."""
    return await _run_research_model(state, config, runtime, prompt_name="research-planner", call_kind="research")


async def _summarize_node(state: ResearchState, config: RunnableConfig, runtime):
    """Summarize the collected research context."""
    result = await _run_research_model(
        state, config, runtime, prompt_name="research-summarizer", call_kind="research_summary",
    )
    return {**result, "summary": extract_final_text_content(result["messages"][-1].content)}


def build_research_graph() -> None:
    graph = StateGraph(ResearchState, context_schema=WorkflowContext)
    # Keep the old entry node available for resuming existing checkpoints.
    graph.add_node("prepare_context", make_prepare_context_node())
    graph.add_node("research", _research_node)
    graph.add_node("summarize", _summarize_node)

    graph.add_edge(START, "research")
    graph.add_edge("prepare_context", "research")
    graph.add_edge("research", "summarize")
    graph.add_edge("summarize", END)

    GraphRegistry.register(
        "research_chain",
        graph.compile(checkpointer=get_checkpointer()),
        description="research, information synthesis, and multi-step analysis",
    )

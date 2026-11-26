"""Registry discovery and dispatch for an agent's on-demand tools."""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from typing import Annotated, Any

from langchain_core.tools import InjectedToolArg, tool
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.prebuilt import ToolRuntime
from pydantic import BaseModel, ConfigDict, Field

from agent.modules.tools.decorators import register_tool
from agent.modules.tools.domain import ToolCategory, ToolDescriptor, ToolSource
from agent.modules.tools.permissions.risk import RiskLevel
from agent.modules.tools.policy import DISCOVERY_TOOL_NAMES
from agent.modules.tools.resolver import discovery_catalog
from agent.modules.tools.result import ToolError, ToolErrorCode
from agent.modules.tools.runtime.context import ToolContext
from agent.modules.tools.sources.mcp import _extract_server_name

logger = logging.getLogger(__name__)


def _group(descriptor: ToolDescriptor) -> str:
    if descriptor.source == ToolSource.MCP:
        return "mcp:" + _extract_server_name(descriptor.name)
    if descriptor.category == ToolCategory.UTILITY:
        return descriptor.name
    return descriptor.category.value


def _words(text: str) -> set[str]:
    return set(re.findall(r"[^\W_]+", text.casefold())) - {
        "a",
        "an",
        "the",
        "to",
        "for",
        "on",
        "or",
        "of",
        "and",
        "in",
        "with",
        "tool",
    }


def _local_groups(query, groups):
    words = _words(query)
    scores = {}
    for group, descriptors in groups.items():
        scores[group] = max(
            3 * len(words & _words(" ".join([group, d.name, *d.tags])))
            + len(words & _words(d.description))
            for d in descriptors
        )
    best = max(scores.values(), default=0)
    return [key for key, score in scores.items() if best and score == best]


async def _matching_tools(query, deferred):
    query = query.strip()
    if not query or not deferred:
        return []
    groups = defaultdict(list)
    for descriptor in deferred.values():
        if descriptor.name.casefold() == query.casefold():
            return [descriptor]
        groups[_group(descriptor)].append(descriptor)
    for key, descriptors in groups.items():
        if key.casefold() == query.casefold():
            return descriptors

    from agent.bootstrap.container import require_active_container
    from agent.modules.decisions import ToolFilterContext, ToolPreFilterEvaluator

    selected = None
    try:
        service = require_active_container().decision_service
        if service.is_configured:
            criteria = {
                key: " | ".join(
                    f"{d.name}: {d.description.splitlines()[0][:180] if d.description else d.name}"
                    for d in descriptors
                )
                for key, descriptors in groups.items()
            }
            criteria["no_match"] = (
                "None of the available tools can fulfill the request."
            )
            decision = await ToolPreFilterEvaluator(service).evaluate(
                ToolFilterContext(
                    user_query=query,
                    categories=criteria,
                    available_tools=list(groups),
                )
            )
            if decision.selected_category in criteria:
                selected = (
                    []
                    if decision.selected_category == "no_match"
                    else [
                        key
                        for key in dict.fromkeys(
                            [decision.selected_category, *decision.relevant_categories]
                        )
                        if key in groups
                    ]
                )
    except Exception:
        logger.warning(
            "Decision provider unavailable for tool discovery; using local search",
            exc_info=True,
        )
    if selected is None:
        selected = _local_groups(query, groups)
    return [descriptor for key in selected for descriptor in groups[key]]


class SearchToolsInput(BaseModel):
    query: str = Field(
        min_length=1, description="Task description, group, or exact tool name."
    )


@register_tool(category=ToolCategory.UTILITY, risk=RiskLevel.SAFE, may_interrupt=False)
@tool(args_schema=SearchToolsInput, response_format="content_and_artifact")
async def search_tools(
    query: str, runtime: Annotated[ToolRuntime[Any, Any], InjectedToolArg]
) -> tuple[str, dict]:
    """Find on-demand tools by task, group, or exact name. Returns usage and input schemas.

    Call execute_tool with the returned name and a JSON arguments object to use a tool.
    """
    catalog = discovery_catalog(runtime.tools)
    if catalog is None:
        raise ToolError(
            ToolErrorCode.PERMISSION_DENIED,
            "Tool discovery is disabled for this agent.",
        )
    ctx = ToolContext.from_runtime(runtime)
    deferred = catalog.deferred_for_run(ctx.workspace_or_dir, runtime.config)
    matches = await _matching_tools(query, deferred)
    definitions = []
    for descriptor in sorted(matches, key=lambda d: d.name):
        instance = catalog.materialize(descriptor.name)
        if instance.name == "skill":
            from agent.modules.skills import (
                get_effective_skills_catalog_xml,
                get_repository_skill_dir,
            )
            from agent.modules.tools.catalog_context import (
                bind_tool_catalogs,
                build_tool_catalogs,
            )
            from agent.modules.tools.runtime.context import get_context_value

            xml = await get_effective_skills_catalog_xml(
                allowed_names=get_context_value(runtime.context, "allowed_skill_names"),
                workspace=ctx.workspace_or_dir,
                thread_id=ctx.thread_id or "",
                repository_dir=get_repository_skill_dir(),
            )
            sections = build_tool_catalogs(
                tools=[instance],
                agent_name=ctx.agent_name,
                catalog=None,
                skills_catalog_xml=xml,
            )
            (instance,) = bind_tool_catalogs([instance], sections, keep_skill=True)
        definitions.append(convert_to_openai_tool(instance)["function"])
    content = json.dumps(definitions, ensure_ascii=False, separators=(",", ":"))
    no_matches = "No matching on-demand tools."
    names = "\n".join(definition["name"] for definition in definitions)
    return (
        content if definitions else no_matches,
        {"kind": "tool_discovery", "display_content": names or no_matches},
    )


class ExecuteToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_name: str = Field(min_length=1, description="Exact registered tool name.")
    arguments: dict[str, Any] = Field(
        description="JSON object matching the target tool's input schema."
    )


@register_tool(
    category=ToolCategory.UTILITY, risk=RiskLevel.DANGEROUS, may_interrupt=True
)
@tool(args_schema=ExecuteToolInput)
def execute_tool(tool_name: str, arguments: dict[str, Any]) -> str:
    """Run a tool by name with its JSON arguments. Use search_tools when you need its schema."""
    # The workflow dispatches before authorization so it can preflight the actual target.
    raise ToolError(
        ToolErrorCode.PERMISSION_DENIED,
        "Tool dispatch requires an enabled agent workflow.",
    )


def resolve_dispatched_calls(calls, tools, workspace, config, *, restored_ids=frozenset()):
    """Translate wrapper calls without making their targets directly callable."""
    direct = {item.name: item for item in tools}
    catalog = discovery_catalog(tools)
    if catalog is None or "execute_tool" not in direct:
        raise ToolError(
            ToolErrorCode.PERMISSION_DENIED, "Tool dispatch is disabled for this agent."
        )
    call_ids = [call.get("id") for call in calls]
    if not all(call_ids) or len(set(call_ids)) != len(call_ids):
        raise ToolError(
            ToolErrorCode.INVALID_INPUT, "Each tool call needs a distinct, nonempty ID."
        )
    deferred = catalog.deferred_for_run(workspace, config)
    effective = []
    targets = {}
    for call in calls:
        if call["name"] != "execute_tool":
            if call["name"] not in direct and call["id"] not in restored_ids:
                raise ToolError(
                    ToolErrorCode.PERMISSION_DENIED,
                    f"Tool '{call['name']}' is unavailable for direct calls.",
                )
            effective.append(call)
            continue
        request = ExecuteToolInput.model_validate(call["args"])
        name = request.tool_name
        if name in DISCOVERY_TOOL_NAMES:
            raise ToolError(
                ToolErrorCode.INVALID_INPUT,
                "Discovery tools cannot be dispatched recursively.",
            )
        if name in deferred:
            target = catalog.materialize(name)
        elif name in direct and name in catalog.preloaded:
            target = direct[name]
        else:
            raise ToolError(
                ToolErrorCode.PERMISSION_DENIED,
                f"Tool '{name}' is unavailable to this agent.",
            )
        targets[call["id"]] = target
        effective.append({**call, "name": name, "args": request.arguments})
    return effective, targets

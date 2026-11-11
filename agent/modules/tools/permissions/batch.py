"""Authorize a whole tool batch before entering checkpointed execution."""

import inspect
from types import SimpleNamespace

from agent.modules.tools.decorators import META_ATTR
from agent.modules.tools.permissions import authorize_tool
from agent.modules.tools.permissions.risk import RiskLevel


async def prepare_permissions(tools, calls, runtime):
    available = {tool.name: tool for tool in tools}
    for call in calls:
        tool = available.get(call["name"])
        if tool is None:
            continue
        if getattr(tool, "coding_definition", None) is not None:
            continue
        meta = getattr(tool, META_ATTR, None)
        metadata = tool.metadata or {}
        risk = metadata.get("permission_risk", getattr(meta, "risk", None)) or RiskLevel.DANGEROUS
        own = metadata.get("permission_self_authorizing", getattr(meta, "self_authorizing", False))
        prepare = getattr(tool, "permission_prepare", None) or getattr(meta, "prepare_permissions", None)
        scoped = SimpleNamespace(context=runtime.context, state=runtime.state, config=runtime.config,
                                 tool_call_id=call["id"])
        arguments = dict(call.get("args") or {})
        if own and prepare is not None:
            # Match the function's defaults so preparation and execution grant the same operation.
            for key, parameter in inspect.signature(tool.coroutine or tool.func).parameters.items():
                if key != "runtime" and parameter.default is not inspect.Parameter.empty:
                    arguments.setdefault(key, parameter.default)
            await prepare(scoped, call["name"], arguments)
        else:
            authorize_tool(scoped, call["name"], "*", risk=risk, arguments=arguments)

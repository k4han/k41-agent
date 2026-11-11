"""The single coding tool catalog for local and sandbox workspaces."""

from langchain_core.tools import BaseTool

from agent.modules.tools.coding.adapter import make_coding_tool
from agent.modules.tools.coding.names import CODING_TOOLS
from agent.modules.tools.permissions.risk import RiskLevel, classify
from agent.modules.tools.decorators import register_tool
from agent.modules.tools.domain import ToolCapability, ToolCategory


def registered(name: str, category: ToolCategory, mutates: bool = False):
    capabilities = [ToolCapability.REQUIRES_WORKSPACE, ToolCapability.REQUIRES_THREAD]
    if category == ToolCategory.SHELL and mutates:
        capabilities.append(ToolCapability.EXEC_SHELL)
    if category == ToolCategory.FILE:
        capabilities.append(ToolCapability.WRITE_FS if mutates else ToolCapability.READ_FS)
    if mutates:
        capabilities.append(ToolCapability.MUTATES_STATE)
    return register_tool(risk=RiskLevel.MUTATING if mutates else RiskLevel.SAFE, self_authorizing=True, may_interrupt=False, category=category, capabilities=capabilities, tags=["coding"])(make_coding_tool(name))


bash = registered("bash", ToolCategory.SHELL, True)
read_process_output = registered("read_process_output", ToolCategory.SHELL)
write_process_input = registered("write_process_input", ToolCategory.SHELL, True)
stop_process = registered("stop_process", ToolCategory.SHELL, True)

read = registered("read", ToolCategory.FILE)
list_dir = registered("list_dir", ToolCategory.FILE)
glob = registered("glob", ToolCategory.FILE)
grep = registered("grep", ToolCategory.FILE)
edit = registered("edit", ToolCategory.FILE, True)
write = registered("write", ToolCategory.FILE, True)

_TARGETED_TOOLS: dict[str, BaseTool] = {}


def targeted_tool(name: str) -> BaseTool | None:
    """The same coding tool with a ``target`` argument for auxiliary machines."""
    if name not in CODING_TOOLS:
        return None
    tool = _TARGETED_TOOLS.get(name)
    if tool is None:
        from agent.modules.tools.middleware import apply_default_middleware

        tool = _TARGETED_TOOLS[name] = apply_default_middleware(make_coding_tool(name, targeted=True))
    return tool

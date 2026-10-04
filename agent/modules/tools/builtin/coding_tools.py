"""The single coding tool catalog for local and sandbox workspaces."""

from agent.modules.tools.coding.adapter import make_coding_tool
from agent.modules.tools.decorators import register_tool
from agent.modules.tools.domain import ToolCapability, ToolCategory


def registered(name: str, category: ToolCategory, mutates: bool = False):
    capabilities = [ToolCapability.REQUIRES_WORKSPACE, ToolCapability.REQUIRES_THREAD]
    if category == ToolCategory.SHELL:
        capabilities.append(ToolCapability.EXEC_SHELL)
    if category == ToolCategory.FILE:
        capabilities.append(ToolCapability.WRITE_FS if mutates else ToolCapability.READ_FS)
    if mutates:
        capabilities.append(ToolCapability.MUTATES_STATE)
    return register_tool(category=category, capabilities=capabilities, tags=["coding"],
                         apply_middleware=False)(make_coding_tool(name))


exec_command = registered("exec_command", ToolCategory.SHELL, True)
read_process_output = registered("read_process_output", ToolCategory.SHELL)
write_process_input = registered("write_process_input", ToolCategory.SHELL, True)
stop_process = registered("stop_process", ToolCategory.SHELL, True)
apply_patch = registered("apply_patch", ToolCategory.FILE, True)
read_tool_output = registered("read_tool_output", ToolCategory.FILE)

read_file = registered("read_file", ToolCategory.FILE)
list_dir = registered("list_dir", ToolCategory.FILE)
glob = registered("glob", ToolCategory.FILE)
grep = registered("grep", ToolCategory.FILE)
edit_file = registered("edit_file", ToolCategory.FILE, True)
write_file = registered("write_file", ToolCategory.FILE, True)

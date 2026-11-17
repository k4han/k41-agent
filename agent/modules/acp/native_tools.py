"""AGY native tools available to managed ACP agent profiles.

Names are AGY profile component identifiers, verified with the CLI. Callable
names can differ (for example, ``view_file`` provides ``read_file``). Keep this
catalog shared by card validation, the dashboard and CLI profile generation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class NativeTool:
    name: str
    description: str
    always_enabled: bool = False


ACP_TOOLS = (
    NativeTool("view_file", "Read the contents of a file."),
    NativeTool("write_to_file", "Create or overwrite a file."),
    NativeTool("replace_file_content", "Replace a contiguous block of text in a file."),
    NativeTool("search_web", "Search the web."),
    NativeTool("run_command", "Run a terminal command."),
    NativeTool("manage_task", "Manage background tasks. AGY requires this internal helper.", True),
    NativeTool("schedule", "Set timers or recurring background jobs."),
    NativeTool("run_workflow", "Run an AGY workflow."),
    NativeTool("invoke_subagent", "Start an AGY subagent."),
    NativeTool("define_subagent", "Define a custom AGY subagent."),
    NativeTool("send_message", "Communicate with AGY agents. AGY requires this internal helper.", True),
    NativeTool("manage_subagents", "List or stop AGY subagents."),
    NativeTool("ask_question", "Ask the user a multiple-choice question."),
    NativeTool("generate_image", "Generate or edit an image."),
)

ALWAYS_ENABLED_TOOL_NAMES = frozenset(tool.name for tool in ACP_TOOLS if tool.always_enabled)
SELECTABLE_TOOL_NAMES = frozenset(tool.name for tool in ACP_TOOLS if not tool.always_enabled)


def normalize_native_tools(tools: list[str] | None) -> list[str] | None:
    """Validate exact CLI names and canonicalize an optional native allowlist."""
    if tools is None:
        return None
    unknown = set(tools) - SELECTABLE_TOOL_NAMES - ALWAYS_ENABLED_TOOL_NAMES
    if unknown:
        raise ValueError(f"Unknown ACP tools: {', '.join(sorted(unknown))}.")
    return sorted(set(tools) - ALWAYS_ENABLED_TOOL_NAMES)


def effective_native_tools(tools: list[str] | None) -> list[str]:
    """Resolve the card default and include AGY's required internal tools."""
    selected = normalize_native_tools(tools)
    return sorted((SELECTABLE_TOOL_NAMES if selected is None else set(selected)) | ALWAYS_ENABLED_TOOL_NAMES)


def native_tools_payload() -> list[dict]:
    return [asdict(tool) for tool in ACP_TOOLS]

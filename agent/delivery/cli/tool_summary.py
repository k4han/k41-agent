"""Tool group summary formatter for CLI following Pattern 2 (Total + Breakdown + Status)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

READ_TOOLS = {
    "read_file",
    "list_dir",
    "grep",
    "glob",
    "search_code",
    "read_resource",
    "list_resources",
    "read",
}

COMMAND_TOOLS = {
    "bash",
    "run_bash",
    "exec_command",
    "read_process_output",
    "write_process_input",
    "stop_process",
    "shell",
    "command",
}

EDIT_TOOLS = {
    "write_file",
    "edit_file",
    "patch_file",
    "create_file",
    "delete_file",
    "edit",
    "write",
}

WEB_TOOLS = {
    "web_search",
    "web_fetch",
    "search_web",
    "fetch_web",
    "web",
}


def is_tool_failed(tool: dict[str, Any]) -> bool:
    """Determine whether a tool execution resulted in a failure."""
    if not tool:
        return False

    status = tool.get("status")
    if status == "error":
        return True

    result = tool.get("result")
    if result is None:
        return False

    if isinstance(result, str):
        trimmed = result.strip()
        match = re.search(r"Command exited with code (-?\d+)\.", trimmed)
        if match:
            return int(match.group(1)) != 0
        if "Command timed out." in trimmed or "Command was cancelled." in trimmed:
            return True
        if trimmed.startswith(
            (
                "[error]",
                "[Error]",
                "Error:",
                "ToolError:",
                "Traceback (most recent call last):",
            )
        ):
            return True
        if trimmed.startswith("{") and trimmed.endswith("}"):
            try:
                parsed = json.loads(trimmed)
                if isinstance(parsed, dict):
                    if parsed.get("status") == "error" or parsed.get("failed") is True:
                        return True
                    if parsed.get("error") and not parsed.get("success"):
                        return True
            except Exception:
                pass
        return False

    if isinstance(result, dict):
        if result.get("status") == "error" or result.get("failed") is True:
            return True
        if result.get("error") and not result.get("success"):
            return True

    return False


def get_tool_category(name: str | None) -> tuple[str, str, str]:
    """Map tool name to (category_key, singular_label, plural_label)."""
    raw = (name or "").strip().lower()
    if raw.startswith("mcp__"):
        parts = raw.split("__")
        action = parts[-1] if parts else "mcp"
        return action, action, f"{action}s"

    if raw in READ_TOOLS:
        return "reads", "read", "reads"
    if raw in COMMAND_TOOLS:
        return "commands", "command", "commands"
    if raw in EDIT_TOOLS:
        return "edits", "edit", "edits"
    if raw in WEB_TOOLS:
        return "web", "web", "web"

    clean = raw.replace("_", " ").strip() or "other"
    return clean, clean, f"{clean}s"


@dataclass(frozen=True)
class ToolGroupSummary:
    total: int
    total_failed: int
    has_failed: bool
    total_text: str
    breakdown_text: str
    status_text: str
    full_label: str
    rich_markup: str


def format_tool_group_summary(
    tools: list[dict[str, Any]],
    running: bool = False,
    live_detail: str = "",
    live: bool | None = None,
) -> ToolGroupSummary:
    """Format tool execution summary using Pattern 2 (Total + Breakdown + Status)."""
    is_live = live if live is not None else running

    if not tools:
        status_text = "loading" if is_live else ""
        full = "0 tools"
        rich = (
            f"[bold cyan]◌[/bold cyan] [dim]0 tools[/dim]"
            if is_live
            else f"[bold green]✓[/bold green] [dim]0 tools[/dim]"
        )
        return ToolGroupSummary(
            total=0,
            total_failed=0,
            has_failed=False,
            total_text="0 tools:",
            breakdown_text="",
            status_text=status_text,
            full_label=full,
            rich_markup=rich,
        )

    categories_map: dict[str, dict[str, Any]] = {}
    total_failed = 0

    for tool in tools:
        key, singular, plural = get_tool_category(tool.get("name"))
        if key not in categories_map:
            categories_map[key] = {
                "singular": singular,
                "plural": plural,
                "count": 0,
                "failed": 0,
            }
        categories_map[key]["count"] += 1
        if is_tool_failed(tool):
            categories_map[key]["failed"] += 1
            total_failed += 1

    total = len(tools)
    total_text = f"{total} tool:" if total == 1 else f"{total} tools:"

    preferred_order = ["reads", "commands", "edits", "web"]

    def sort_key(item: str) -> tuple[int, str]:
        if item in preferred_order:
            return 0, str(preferred_order.index(item))
        return 1, item

    sorted_keys = sorted(categories_map.keys(), key=sort_key)

    breakdown_parts: list[str] = []
    rich_breakdown_parts: list[str] = []

    for key in sorted_keys:
        entry = categories_map[key]
        label = entry["singular"] if entry["count"] == 1 else entry["plural"]
        failed_count = entry["failed"]
        if failed_count > 0:
            breakdown_parts.append(f"{entry['count']} {label} ({failed_count} failed)")
            rich_breakdown_parts.append(
                f"{entry['count']} {label} [bold red]({failed_count} failed)[/bold red]"
            )
        else:
            breakdown_parts.append(f"{entry['count']} {label}")
            rich_breakdown_parts.append(f"{entry['count']} {label}")

    breakdown_text = ", ".join(breakdown_parts)
    rich_breakdown_text = ", ".join(rich_breakdown_parts)

    if is_live:
        status_text = f"loading {live_detail}".strip() if live_detail else "loading"
        full_label = f"{total_text} {breakdown_text}"
        detail_markup = f" [dim]({live_detail})[/dim]" if live_detail else ""
        rich_markup = (
            f"[bold cyan]◌[/bold cyan] [bold cyan]{total_text}[/bold cyan] [dim]{breakdown_text}[/dim]{detail_markup}"
        )
    elif total_failed > 0:
        status_text = f"{total_failed} failed"
        status_markup = f"[bold red]{status_text}[/bold red]"
        full_label = f"{total_text} {breakdown_text} · {status_text}"
        rich_markup = (
            f"[bold red]✖[/bold red] [bold yellow]{total_text}[/bold yellow] {rich_breakdown_text} "
            f"[dim]·[/dim] {status_markup}"
        )
    else:
        status_text = ""
        full_label = f"{total_text} {breakdown_text}"
        rich_markup = (
            f"[bold green]✓[/bold green] [bold cyan]{total_text}[/bold cyan] [dim]{breakdown_text}[/dim]"
        )

    return ToolGroupSummary(
        total=total,
        total_failed=total_failed,
        has_failed=total_failed > 0,
        total_text=total_text,
        breakdown_text=breakdown_text,
        status_text=status_text,
        full_label=full_label,
        rich_markup=rich_markup,
    )

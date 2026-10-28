"""Unit tests for CLI tool execution summary (Pattern 2)."""

import pytest

from agent.delivery.cli.tool_summary import (
    format_tool_group_summary,
    get_tool_category,
    is_tool_failed,
)


def test_tool_category_mapping():
    assert get_tool_category("read_file") == ("reads", "read", "reads")
    assert get_tool_category("list_dir") == ("reads", "read", "reads")
    assert get_tool_category("bash") == ("commands", "command", "commands")
    assert get_tool_category("exec_command") == ("commands", "command", "commands")
    assert get_tool_category("write_file") == ("edits", "edit", "edits")
    assert get_tool_category("edit_file") == ("edits", "edit", "edits")
    assert get_tool_category("web_search") == ("web", "web", "web")
    assert get_tool_category("mcp__stitch__create_screen") == (
        "create_screen",
        "create_screen",
        "create_screens",
    )


def test_tool_failure_detection():
    assert is_tool_failed({"name": "read_file", "result": "content"}) is False
    assert (
        is_tool_failed(
            {"name": "bash", "result": "stdout\n\nCommand exited with code 0."}
        )
        is False
    )
    assert (
        is_tool_failed(
            {"name": "bash", "result": "stderr\n\nCommand exited with code 1."}
        )
        is True
    )
    assert (
        is_tool_failed(
            {
                "name": "read_file",
                "result": "[error] not_found: file does not exist",
            }
        )
        is True
    )
    assert (
        is_tool_failed(
            {"name": "custom", "result": {"status": "error", "error": "failed"}}
        )
        is True
    )


@pytest.mark.parametrize("status", [None, "success", "error"])
def test_stop_process_cancellation_is_success_including_legacy_status(status):
    result = "Command exited with code -9.\n\nCommand was cancelled."
    tools = [{"name": "stop_process", "result": result, "status": status} for _ in range(2)]
    summary = format_tool_group_summary(tools)
    assert summary.total_failed == 0
    assert summary.full_label == "2 tools: 2 commands"
    for name in ("bash", "read_process_output", "write_process_input"):
        assert is_tool_failed({"name": name, "result": result}) is True


@pytest.mark.parametrize("result", [
    "[error] not_found: Process does not exist.",
    "[error] permission_denied: Not allowed to stop this process.",
    "[error] execution_error: Could not terminate process.",
    "detail\n\nCommand timed out.",
    "detail\n\nCommand exited with code 2.",
    "Command was cancelled.\n\nCommand exited with code 2.",
])
def test_stop_process_real_errors_still_fail(result):
    assert is_tool_failed({"name": "stop_process", "result": result}) is True


def test_pattern_2_summary_with_failures():
    # 9 reads, 8 commands (2 failed), 2 edits -> 19 tools: 9 reads, 8 commands (2 failed), 2 edits · 2 failed
    tools = (
        [{"name": "read_file", "result": "data"} for _ in range(9)]
        + [
            {
                "name": "bash",
                "result": "ok\n\nCommand exited with code 0.",
            }
            for _ in range(6)
        ]
        + [
            {
                "name": "bash",
                "result": "err\n\nCommand exited with code 2.",
            }
            for _ in range(2)
        ]
        + [{"name": "edit_file", "result": "saved"} for _ in range(2)]
    )

    summary = format_tool_group_summary(tools, running=False)
    assert summary.total == 19
    assert summary.total_failed == 2
    assert summary.has_failed is True
    assert summary.total_text == "19 tools:"
    assert (
        summary.breakdown_text
        == "9 reads, 8 commands (2 failed), 2 edits"
    )
    assert summary.status_text == "2 failed"
    assert (
        summary.full_label
        == "19 tools: 9 reads, 8 commands (2 failed), 2 edits · 2 failed"
    )


def test_pattern_2_summary_all_succeeded():
    tools = [
        {"name": "read_file", "result": "data"},
        {"name": "bash", "result": "ok\n\nCommand exited with code 0."},
        {"name": "edit_file", "result": "saved"},
    ]

    summary = format_tool_group_summary(tools, running=False)
    assert summary.total == 3
    assert summary.total_failed == 0
    assert summary.has_failed is False
    assert summary.total_text == "3 tools:"
    assert summary.breakdown_text == "1 read, 1 command, 1 edit"
    assert summary.status_text == ""
    assert summary.full_label == "3 tools: 1 read, 1 command, 1 edit"
    assert summary.rich_markup == "[bold green]✓[/bold green] [bold cyan]3 tools:[/bold cyan] [dim]1 read, 1 command, 1 edit[/dim]"


def test_pattern_2_summary_running():
    tools = [
        {"name": "read_file", "result": "data"},
        {"name": "bash", "result": None},
    ]

    summary = format_tool_group_summary(
        tools, running=True, live_detail="bash(npm test)"
    )
    assert summary.total == 2
    assert summary.total_text == "2 tools:"
    assert summary.breakdown_text == "1 read, 1 command"
    assert summary.status_text == "loading bash(npm test)"
    assert summary.full_label == "2 tools: 1 read, 1 command"
    assert "◌" in summary.rich_markup


def test_pattern_2_summary_stays_running_when_live_is_true():
    # Between tool calls, tools have completed results but stream is still live.
    tools = [
        {"name": "read_file", "result": "data"},
    ]

    summary = format_tool_group_summary(tools, live=True, running=False)
    assert summary.total == 1
    assert summary.status_text == "loading"
    assert summary.full_label == "1 tool: 1 read"
    assert "◌" in summary.rich_markup

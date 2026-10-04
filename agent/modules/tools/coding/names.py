"""Canonical coding tool names used by catalogs and agent allow lists."""

FILE_TOOLS = frozenset({"read", "list_dir", "edit", "write", "glob", "grep"})
PROCESS_TOOLS = frozenset({"bash", "read_process_output", "write_process_input", "stop_process"})
CODING_TOOLS = FILE_TOOLS | PROCESS_TOOLS | {"read_tool_output"}
TOOL_ALIASES = {"read_file": "read", "write_file": "write", "edit_file": "edit",
                "exec_command": "bash", "run_bash": "bash",
                "bash_read_output": "read_process_output", "bash_send_input": "write_process_input",
                "bash_interrupt": "stop_process", "bash_close": "stop_process"}
REMOVED_TOOLS = frozenset({"bash_list_sessions", "apply_patch"})


def canonical_tool_names(names):
    """Normalize persisted allow lists without exposing another tool profile."""
    # Retired names remain an explicit restriction. Dropping the last entry
    # would turn an existing allow list into the empty-list allow-all default.
    return list(dict.fromkeys(TOOL_ALIASES.get(name, name) for name in names))

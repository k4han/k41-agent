"""Shared workspace layout and lifetime information for model context."""


def build_workspace_context(scratchpad_path: str | None = None) -> str:
    scratchpad = scratchpad_path or ".k41-agent/scratchpad/"
    return (
        "<workspace_context>\n"
        "The working directory is the project workspace. Create project folders, source code, "
        "dependencies, and deliverables there. Keep shell workdir at the workspace or a project subdirectory.\n"
        "Use .k41-agent/ only for internal supporting files, never as the project workspace:\n"
        f"- {scratchpad} holds project-shared drafts, notes, and temporary helper scripts. "
        "All conversations and sub-agents in this workspace share these files. Reuse existing project notes, "
        "and use distinct filenames for independent tasks. Notes survive turns, restarts, and conversation reset or deletion.\n"
        "- .k41-agent/outputs/ holds project-shared retained tool output for seven days.\n"
        "- .k41-agent/uploads/ holds user attachments.\n"
        "- .k41-agent/generated-images/ holds generated images.\n"
        "Create directories only when writing files. Do not create assets/ or memory/ inside .k41-agent/.\n"
        "These are real files accessible from the workspace. The internal directory is excluded from Git.\n"
        "</workspace_context>"
    )

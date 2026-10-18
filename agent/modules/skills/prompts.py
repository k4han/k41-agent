"""Shared resource and runtime instructions for activated skill packages."""

from dataclasses import replace
import json
from pathlib import Path
import shlex

RUNTIME_GUIDANCE = """Use read for supporting text or images, and bash for scripts.
Resolve task input/output paths against workspace_root.
Python: prefer uv run --no-project --script PATH (PEP 723). Use --with PACKAGE or
--with-requirements FILE only for dependencies explicitly declared by the skill.
For a bundled pyproject.toml use uv run --project SKILL_ROOT, respecting existing lockfiles.
JavaScript: node for plain scripts, pnpm dlx for package CLIs, or pnpm --ignore-workspace install
in skill_root for a bundled package.json; use --frozen-lockfile when a pnpm lockfile is provided.
Only omit --ignore-workspace when the skill itself bundles pnpm-workspace.yaml.
Use cached isolated dependencies. Do not infer package names from imports, alter the source
skill or workspace dependency files, or automatically retry a script after side effects.
Other runtimes and system packages must already be available."""

RESOURCE_LIST_LIMIT_GUIDANCE = "  Listing capped at 200 files. Use glob or bash to list more files in skill_root."


def shell_guidance(shell: str, variable: str, *, direct: bool = False) -> str:
    name = Path(shell).name.lower().removesuffix(".exe")
    common = (
        f"The bash tool executes {shell}; use that shell's syntax. "
        f"Each invocation provides {variable}=skill_root and K41_WORKSPACE_ROOT=workspace_root. "
        "Keep workdir at workspace_root and pass explicit workspace output paths. "
        "Prefer a script's --limit option over truncating it with a pipeline. "
        "Skill resources are read-only through file tools; do not modify bundled instructions or assets."
    )
    if name in {"pwsh", "powershell"}:
        common += (
            "\nShell syntax: PowerShell. POSIX head/export commands are not available by default. "
            "Use Select-Object -First only when a pipeline is needed. "
            f"Example syntax: uv run --no-project --script (Join-Path $env:{variable} 'scripts/SCRIPT.py')"
        )
    elif name == "cmd":
        common += f'\nShell syntax: cmd. Example: uv run --no-project --script "%{variable}%\\scripts\\SCRIPT.py"'
    else:
        common += f'\nShell syntax: POSIX. Example: uv run --no-project --script "${{{variable}}}/scripts/SCRIPT.py"'
    if direct:
        common += (
            "\nExecution mode: source. This is the live local source, not an isolated copy. "
            "Do not install dependencies or write outputs inside it. Use uv script dependencies in its cache. "
            "If the source changes, refresh the skill before continuing."
        )
    return common


def build_skill_instructions(parsed, skill_root, workspace_root, resources, runtime, mode, variable):
    """Combine the loaded skill with one shared set of execution instructions."""
    from agent.modules.skills.rendering import skill_content_xml

    parsed = replace(parsed, path=Path(skill_root), resources=resources)
    content = skill_content_xml(parsed)
    content += f"\n<skill_root>{skill_root}</skill_root>\n<workspace_root>{workspace_root}</workspace_root>\n"
    content += RUNTIME_GUIDANCE + "\n" + shell_guidance(runtime["shell"], variable, direct=mode == "source")
    content += "\nAvailable runtime executables: " + json.dumps(runtime["tools"])
    if runtime.get("bin_directory"):
        content += "\nFor sandbox shell commands prepend: export PATH=" + shlex.quote(runtime["bin_directory"]) + ":$PATH"
    return content + "\n" + "\n".join(runtime.get("errors", []))


def skill_resource_guidance(path) -> list[str]:
    return [f"Skill directory: {path}", "Relative paths in this skill are relative to the skill directory."]

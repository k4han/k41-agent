"""Short execution locations and shell-specific skill invocation guidance."""

import hashlib
import os
from pathlib import Path
import re

from agent.modules.skills.package_worker import native_path


CACHE_LAYOUT = "shared-v1"


def cache_path(version: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{64}", version):
        raise ValueError("Invalid skill content version.")
    return "sk-" + version[:20]


def environment_variable(name: str) -> str:
    identifier = re.sub(r"[^A-Z0-9_]", "_", name.upper().replace("-", "_"))
    if not name.isascii():
        identifier += "_" + hashlib.sha256(name.encode()).hexdigest()[:8].upper()
    return "K41_SKILL_" + identifier


def execution_path(root: str, relative: str = "", resources=()) -> str:
    """Consider child resources as well as the package root on Windows."""
    target = Path(root) / relative
    plain = str(target.resolve())
    if os.name == "nt" and (len(plain) >= 240 or any(len(plain) + 1 + len(item) >= 240 for item in resources)):
        return str(native_path(target))
    return plain


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

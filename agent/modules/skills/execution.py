"""Short execution locations for skill resources; shell_guidance remains a compatibility export."""

import hashlib
import os
from pathlib import Path
import re

from agent.modules.skills.package_worker import native_path
from agent.modules.skills.prompts import shell_guidance


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

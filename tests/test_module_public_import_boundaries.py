from __future__ import annotations

import re
from pathlib import Path


INTERNAL_LAYER_IMPORT_RE = re.compile(
    r"^\s*from\s+agent\.modules\.(?P<module>[a-z_]+)\.(domain|application|infrastructure)\b"
)


def test_module_internal_layers_are_not_imported_cross_module() -> None:
    """Enforce imports through each module's public API.

    Files under agent/modules/<name>/ are allowed to import that same module's
    internal layers. External callers must import through agent.modules.<name>.public.
    """

    offenders: list[str] = []
    root = Path("agent")

    for path in root.rglob("*.py"):
        normalized = path.as_posix()
        content = path.read_text(encoding="utf-8")
        for idx, line in enumerate(content.splitlines(), start=1):
            match = INTERNAL_LAYER_IMPORT_RE.search(line)
            if not match:
                continue

            imported_module = match.group("module")
            owning_prefix = f"agent/modules/{imported_module}/"
            if owning_prefix not in normalized:
                offenders.append(f"{normalized}:{idx}: {line.strip()}")

    assert offenders == []

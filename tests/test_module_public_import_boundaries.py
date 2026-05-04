from __future__ import annotations

import re
from pathlib import Path


DEEP_MODULE_IMPORT_RE = re.compile(
    r"^\s*(?:from|import)\s+agent\.modules\.(?P<module>[a-z_]+)\.[a-zA-Z_][\w.]*"
)


def test_module_internals_are_not_imported_cross_module() -> None:
    """Enforce imports through each module package API.

    Files under agent/modules/<name>/ are allowed to import that same module's
    internals. External callers must import through agent.modules.<name>.
    """

    offenders: list[str] = []
    root = Path("agent")

    for path in root.rglob("*.py"):
        normalized = path.as_posix()
        content = path.read_text(encoding="utf-8")
        for idx, line in enumerate(content.splitlines(), start=1):
            match = DEEP_MODULE_IMPORT_RE.search(line)
            if not match:
                continue

            imported_module = match.group("module")
            owning_prefix = f"agent/modules/{imported_module}/"
            if owning_prefix not in normalized:
                offenders.append(f"{normalized}:{idx}: {line.strip()}")

    assert offenders == []

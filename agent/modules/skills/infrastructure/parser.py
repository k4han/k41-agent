"""SKILL.md parser.

Pure function that extracts YAML frontmatter and body content
from a SKILL.md file, following the agentskills.io specification.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from agent.modules.skills.domain.skill import Skill

logger = logging.getLogger(__name__)

# Regex to match YAML frontmatter between two --- delimiters.
_FRONTMATTER_RE = re.compile(
    r"\A\s*---\s*\n(.*?)\n---\s*\n?(.*)",
    re.DOTALL,
)

# Allowed name characters: lowercase a-z, digits 0-9, hyphens.
_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")


def _validate_name(name: str) -> bool:
    """Check name follows agentskills.io rules (warn but don't reject)."""
    if not name or len(name) > 64:
        return False
    if "--" in name:
        return False
    return bool(_NAME_RE.match(name))


def _list_resources(skill_dir: Path) -> list[str]:
    """List bundled resource files (scripts/, references/, assets/).

    Always uses forward slashes for cross-platform consistency.
    """
    resources: list[str] = []
    for sub in ("scripts", "references", "assets"):
        sub_dir = skill_dir / sub
        if sub_dir.is_dir():
            for file in sub_dir.rglob("*"):
                if file.is_file():
                    resources.append(file.relative_to(skill_dir).as_posix())
    return sorted(resources)


def parse_skill_md(content: str, skill_dir: Path) -> Skill | None:
    """Parse a SKILL.md file and return a ``Skill``, or ``None`` on failure.

    Follows lenient validation per agentskills.io:
    - Name doesn't match dir → warn, load anyway
    - Name exceeds 64 chars → warn, load anyway
    - Description missing → skip (return None)
    - YAML completely unparseable → skip (return None)
    """
    match = _FRONTMATTER_RE.match(content)
    if not match:
        logger.warning("SKILL.md in %s has no valid YAML frontmatter — skipping.", skill_dir)
        return None

    yaml_block = match.group(1)
    body = match.group(2).strip()

    try:
        import yaml

        data = yaml.safe_load(yaml_block)
    except Exception:
        logger.warning("SKILL.md in %s has unparseable YAML — skipping.", skill_dir)
        return None

    if not isinstance(data, dict):
        logger.warning("SKILL.md in %s frontmatter is not a mapping — skipping.", skill_dir)
        return None

    # --- required fields ---
    name = data.get("name")
    description = data.get("description")

    if not description:
        logger.warning("SKILL.md in %s is missing 'description' — skipping.", skill_dir)
        return None

    description = str(description).strip()
    if not description:
        logger.warning("SKILL.md in %s has empty 'description' — skipping.", skill_dir)
        return None

    # Name: use dir name as fallback
    if not name:
        name = skill_dir.name
        logger.debug("SKILL.md in %s missing 'name', using directory name '%s'.", skill_dir, name)
    else:
        name = str(name).strip()

    # Warn on invalid name format but load anyway
    if not _validate_name(name):
        logger.warning(
            "Skill name '%s' in %s doesn't follow naming rules — loading anyway.",
            name,
            skill_dir,
        )

    # Warn if name doesn't match directory
    if name != skill_dir.name:
        logger.warning(
            "Skill name '%s' doesn't match directory '%s' — loading anyway.",
            name,
            skill_dir.name,
        )

    # --- optional fields ---
    license_val = data.get("license")
    if license_val is not None:
        license_val = str(license_val).strip()

    compatibility = data.get("compatibility")
    if compatibility is not None:
        compatibility = str(compatibility).strip()

    raw_metadata = data.get("metadata")
    metadata: dict[str, str] = {}
    if isinstance(raw_metadata, dict):
        metadata = {str(k): str(v) for k, v in raw_metadata.items()}

    allowed_tools_raw = data.get("allowed-tools", "")
    allowed_tools: list[str] = []
    if isinstance(allowed_tools_raw, str) and allowed_tools_raw.strip():
        allowed_tools = allowed_tools_raw.strip().split()
    elif isinstance(allowed_tools_raw, list):
        allowed_tools = [str(t) for t in allowed_tools_raw]

    resources = _list_resources(skill_dir)

    return Skill(
        name=name,
        description=description,
        body=body,
        path=skill_dir,
        license=license_val,
        compatibility=compatibility,
        metadata=metadata,
        allowed_tools=allowed_tools,
        resources=resources,
    )


__all__ = ["parse_skill_md"]

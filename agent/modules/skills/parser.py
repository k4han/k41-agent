"""SKILL.md parser.

Pure function that extracts YAML frontmatter and body content
from a SKILL.md file, following the agentskills.io specification.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from pathlib import Path

from agent.modules.skills.models import Skill, SkillDiagnostic
from agent.shared.infrastructure.parsing import parse_string_or_list

logger = logging.getLogger(__name__)

# Regex to match YAML frontmatter between two --- delimiters.
_FRONTMATTER_RE = re.compile(
    r"\A\s*---\s*\n(.*?)\n---\s*\n?(.*)",
    re.DOTALL,
)

def _validate_name(name: str) -> bool:
    """Check name follows agentskills.io rules (warn but don't reject)."""
    if not name or len(name) > 64:
        return False
    if "--" in name or name.startswith("-") or name.endswith("-"):
        return False
    return all(character == "-" or character.isalnum() and character == character.lower() for character in name)


def parse_skill_md(
    content: str,
    skill_dir: Path,
    *,
    strict: bool = False,
    expected_name: str | None = None,
    resources: Sequence[str] = (),
) -> Skill | None:
    """Parse a SKILL.md file and return a ``Skill``, or ``None`` on failure.

    Follows lenient validation per agentskills.io when ``strict=False``:
    - Name doesn't match dir → warn, load anyway
    - Name exceeds 64 chars → warn, load anyway
    - Description missing → skip (return None)
    - YAML completely unparseable → skip (return None)

    When ``strict=True`` (used by dashboard CRUD), the same conditions
    raise ``ValueError`` instead so that explicit write operations
    reject malformed content rather than silently accepting it.
    ``expected_name`` lets strict writes validate an imported identifier
    instead of the directory name; missing names still use the actual directory.
    """
    match = _FRONTMATTER_RE.match(content)
    if not match:
        if strict:
            raise ValueError("SKILL.md has no valid YAML frontmatter.")
        logger.warning("SKILL.md in %s has no valid YAML frontmatter — skipping.", skill_dir)
        return None

    yaml_block = match.group(1)
    body = match.group(2).strip()

    try:
        import yaml

        data = yaml.safe_load(yaml_block)
    except Exception:
        if strict:
            raise ValueError("SKILL.md YAML is unparseable.") from None
        logger.warning("SKILL.md in %s has unparseable YAML — skipping.", skill_dir)
        return None

    if not isinstance(data, dict):
        if strict:
            raise ValueError("SKILL.md frontmatter is not a mapping.")
        logger.warning("SKILL.md in %s frontmatter is not a mapping — skipping.", skill_dir)
        return None

    diagnostics: list[SkillDiagnostic] = []
    limits = {"description": 1024, "compatibility": 500}
    for key, maximum in limits.items():
        value = data.get(key)
        if value is not None and (not isinstance(value, str) or len(value) > maximum):
            message = f"'{key}' must be a string with at most {maximum} characters."
            if strict:
                raise ValueError(message)
            diagnostics.append(SkillDiagnostic("invalid_field", message, key))
    for key in ("name", "license"):
        if key in data and data[key] is not None and not isinstance(data[key], str):
            if strict:
                raise ValueError(f"'{key}' must be a string.")
            diagnostics.append(SkillDiagnostic("invalid_field", f"'{key}' is not a string.", key))
    if data.get("metadata") is not None and not isinstance(data["metadata"], dict):
        if strict:
            raise ValueError("'metadata' must be a mapping.")
        diagnostics.append(SkillDiagnostic("invalid_metadata", "Metadata must be a mapping.", "metadata"))
    known = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
    diagnostics.extend(SkillDiagnostic("unsupported_field", f"Preserved unsupported field '{key}'.", str(key))
                       for key in data if key not in known)
    if not data.get("name"):
        diagnostics.append(SkillDiagnostic("missing_name", "Using the directory name as the skill name.", "name"))
    if isinstance(data.get("metadata"), dict) and any(
        not isinstance(key, str) or not isinstance(value, str) for key, value in data["metadata"].items()
    ):
        if strict:
            raise ValueError("'metadata' must map string keys to string values.")
        diagnostics.append(SkillDiagnostic("invalid_metadata", "Metadata values are not all strings.", "metadata"))

    # --- required fields ---
    name = data.get("name")
    description = data.get("description")

    if not description:
        if strict:
            raise ValueError("SKILL.md is missing 'description'.")
        logger.warning("SKILL.md in %s is missing 'description' — skipping.", skill_dir)
        return None

    description = str(description).strip()
    if not description:
        if strict:
            raise ValueError("SKILL.md has empty 'description'.")
        logger.warning("SKILL.md in %s has empty 'description' — skipping.", skill_dir)
        return None

    # Name: use dir name as fallback
    if not name:
        name = skill_dir.name
        logger.debug("SKILL.md in %s missing 'name', using directory name '%s'.", skill_dir, name)
    else:
        name = str(name).strip()

    # Reject invalid name format when strict; otherwise warn and load
    if not _validate_name(name):
        msg = (
            f"Skill name '{name}' in {skill_dir} doesn't follow naming rules "
            "(lowercase letters, numbers, single hyphens, 1-64 chars)."
        )
        if strict:
            raise ValueError(msg)
        logger.warning("%s — loading anyway.", msg)
        diagnostics.append(SkillDiagnostic("invalid_name", msg, "name"))

    if strict and expected_name is not None and name != expected_name:
        raise ValueError("SKILL.md frontmatter name must match the skill name.")

    # Imported skills may have an explicitly expected identifier.
    if name != skill_dir.name:
        msg = (
            f"Skill name '{name}' doesn't match directory '{skill_dir.name}'."
        )
        if strict and expected_name is None:
            raise ValueError(msg)
        logger.warning("%s — loading anyway.", msg)
        diagnostics.append(SkillDiagnostic("name_mismatch", msg, "name"))

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
    allowed_tools = parse_string_or_list(allowed_tools_raw, separator=" ")

    return Skill(
        name=name,
        description=description,
        body=body,
        path=skill_dir,
        license=license_val,
        compatibility=compatibility,
        metadata=metadata,
        allowed_tools=allowed_tools,
        resources=list(resources),
        frontmatter=data,
        diagnostics=tuple(diagnostics),
    )


__all__ = ["parse_skill_md"]

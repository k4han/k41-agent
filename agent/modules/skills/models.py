"""Domain entities for the skills module."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class SkillDiagnostic:
    code: str
    message: str
    field: str = ""
    severity: Literal["warning", "error"] = "warning"


@dataclass(frozen=True, slots=True)
class SkillSource:
    id: str
    scope: Literal["global", "project"]
    root: str
    directory: str
    workspace: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class SkillResource:
    path: str
    kind: str
    size: int = 0
    mime_type: str = "application/octet-stream"
    version: str = ""
    mode: int = 0o644


@dataclass(frozen=True, slots=True)
class SkillPackageSummary:
    id: str
    name: str
    description: str
    source: SkillSource
    version: str
    enabled: bool = True
    shadowed: bool = False
    diagnostics: tuple[SkillDiagnostic, ...] = ()


@dataclass(frozen=True, slots=True)
class ActivatedSkill:
    id: str
    name: str
    version: str
    agent_name: str
    workspace_key: str
    skill_root: str
    workspace_root: str
    content: str
    source: dict[str, Any]
    execution_mode: str = "snapshot"
    package_path: str = ""
    environment_variable: str = ""
    shell: str = ""


@dataclass(frozen=True, slots=True)
class SkillSummary:
    """Lightweight DTO for progressive disclosure (tier 1).

    Contains only the metadata needed at startup — name, description,
    and the path to the SKILL.md file.
    """

    name: str
    description: str
    path: Path


@dataclass(frozen=True, slots=True)
class Skill:
    """Full skill entity including body content (tier 2).

    Loaded on-demand when a task matches the skill's description.
    """

    name: str
    description: str
    body: str
    path: Path
    license: str | None = None
    compatibility: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    allowed_tools: list[str] = field(default_factory=list)
    resources: list[str] = field(default_factory=list)
    frontmatter: dict[str, Any] = field(default_factory=dict)
    diagnostics: tuple[SkillDiagnostic, ...] = ()

    def to_summary(self) -> SkillSummary:
        """Downcast to a lightweight summary."""
        return SkillSummary(name=self.name, description=self.description, path=self.path)


__all__ = ["Skill", "SkillSummary", "SkillSource", "SkillPackageSummary", "SkillResource", "SkillDiagnostic", "ActivatedSkill"]

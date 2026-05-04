"""Domain entities for the skills module."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


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

    def to_summary(self) -> SkillSummary:
        """Downcast to a lightweight summary."""
        return SkillSummary(name=self.name, description=self.description, path=self.path)


__all__ = ["Skill", "SkillSummary"]

"""Use case: load a specific skill's full content (progressive disclosure tier 2)."""

from __future__ import annotations

from agent.modules.skills.domain.ports import SkillRepository
from agent.modules.skills.domain.skill import Skill


def load_skill(repository: SkillRepository, name: str) -> Skill | None:
    """Load the full content of a skill by name.

    Returns ``None`` if the skill is not found.
    """
    return repository.load_skill(name)


__all__ = ["load_skill"]

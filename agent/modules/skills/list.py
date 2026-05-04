"""Use case: list available skills (progressive disclosure tier 1)."""

from __future__ import annotations

from agent.modules.skills.ports import SkillRepository
from agent.modules.skills.models import SkillSummary


def list_skills(repository: SkillRepository) -> list[SkillSummary]:
    """Return lightweight summaries of all discovered skills."""
    return repository.list_summaries()


__all__ = ["list_skills"]

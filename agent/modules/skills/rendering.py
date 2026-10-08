"""Prompt rendering for skill catalogs and loaded instructions."""

from __future__ import annotations

from collections.abc import Sequence
from xml.sax.saxutils import escape, quoteattr

from agent.modules.skills.models import Skill, SkillSummary


def skill_catalog_xml(skills: Sequence[Skill | SkillSummary]) -> str:
    if not skills:
        return "<available_skills/>"
    lines = ["<available_skills>"]
    for skill in skills:
        lines.extend([
            "  <skill>",
            f"    <name>{escape(skill.name)}</name>",
            f"    <description>{escape(skill.description)}</description>",
            f"    <location>{escape(str(skill.path / 'SKILL.md'))}</location>",
            "  </skill>",
        ])
    lines.append("</available_skills>")
    return "\n".join(lines)


def skill_content_xml(skill: Skill) -> str:
    lines = [
        f"<skill_content name={quoteattr(skill.name)}>",
        skill.body,
        "",
        f"Skill directory: {skill.path}",
        "Relative paths in this skill are relative to the skill directory.",
    ]
    if skill.compatibility:
        lines.append(f"Compatibility: {skill.compatibility}")
    if skill.allowed_tools:
        lines.append("Declared tools (subject to agent permissions): " + " ".join(skill.allowed_tools))
    if skill.resources:
        lines.append("<skill_resources>")
        lines.extend(f"  <file>{escape(resource)}</file>" for resource in skill.resources[:200])
        if len(skill.resources) > 200:
            lines.append("  Listing capped at 200 files. Use glob or bash to list more files in skill_root.")
        lines.append("</skill_resources>")
    lines.append("</skill_content>")
    return "\n".join(lines)

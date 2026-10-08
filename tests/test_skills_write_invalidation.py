"""Coverage for refreshing repo-local skills when a write touches the skills dir."""

from types import SimpleNamespace

import pytest

import agent.modules.skills as skills_module
import agent.modules.tools.builtin.filesystem.edit_file as edit_file_module
import agent.modules.tools.builtin.filesystem.write_file as write_file_module
from agent.shared.infrastructure.revisions import SKILLS_REVISION, get_revision

SKILL_MD = "---\nname: demo\ndescription: Demo skill.\n---\n\n# Demo\nBody.\n"


def _runtime(working_dir: str) -> SimpleNamespace:
    return SimpleNamespace(context={"working_dir": working_dir})


@pytest.mark.parametrize(
    "file_path",
    [
        ".agent/skills/demo/SKILL.md",
        "./.agent/skills/demo/SKILL.md",
        ".agent\\skills\\demo\\SKILL.md",
        "D:/repo/.agent/skills/demo/SKILL.md",
        "/home/user/repo/.agent/skills/demo/scripts/run.py",
        ".agent/skills",
    ],
)
def test_is_repository_skill_path_accepts_paths_inside_the_skills_dir(file_path) -> None:
    assert skills_module.is_repository_skill_path(file_path) is True


@pytest.mark.parametrize(
    "file_path",
    [
        "",
        "README.md",
        ".agent/agents/foo.md",
        "agent/skills/demo/SKILL.md",
        "skills/demo/SKILL.md",
    ],
)
def test_is_repository_skill_path_rejects_unrelated_paths(file_path) -> None:
    assert skills_module.is_repository_skill_path(file_path) is False


def test_is_repository_skill_path_honors_custom_repository_dir() -> None:
    assert skills_module.is_repository_skill_path(
        "custom/skills/demo/SKILL.md",
        repository_dir="custom/skills",
    )
    assert not skills_module.is_repository_skill_path(
        ".agent/skills/demo/SKILL.md",
        repository_dir="custom/skills",
    )


def test_invalidate_bumps_the_revision_only_for_skill_paths() -> None:
    before = get_revision(SKILLS_REVISION)

    assert skills_module.invalidate_repository_skills_for_path("README.md") is False
    assert get_revision(SKILLS_REVISION) == before

    assert (
        skills_module.invalidate_repository_skills_for_path(
            ".agent/skills/demo/SKILL.md"
        )
        is True
    )
    assert get_revision(SKILLS_REVISION) == before + 1


@pytest.mark.asyncio
async def test_invalidate_clears_the_discovery_cache(tmp_path) -> None:
    from agent.modules.skills.discovery import discover_repository_skills

    directory = tmp_path / ".agent" / "skills" / "demo"
    directory.mkdir(parents=True)
    file = directory / "SKILL.md"
    file.write_text(SKILL_MD, encoding="utf-8")
    kwargs = {"workspace": str(tmp_path), "repository_dir": ".agent/skills"}
    initial = await discover_repository_skills(**kwargs)
    file.write_text(SKILL_MD.replace("Body.", "Updated body."), encoding="utf-8")
    cached = await discover_repository_skills(**kwargs)
    assert cached["demo"].body == initial["demo"].body

    skills_module.invalidate_repository_skills_for_path(".agent/skills/demo/SKILL.md")

    refreshed = await discover_repository_skills(**kwargs)
    assert "Updated body." in refreshed["demo"].body


@pytest.mark.asyncio
async def test_write_file_refreshes_skills_when_writing_a_skill(tmp_path) -> None:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    before = get_revision(SKILLS_REVISION)

    result = await write_file_module.write_file.coroutine(
        file_path=".agent/skills/demo/SKILL.md",
        content=SKILL_MD,
        runtime=_runtime(str(sandbox)),
    )

    assert "[OK]" in result
    assert (sandbox / ".agent" / "skills" / "demo" / "SKILL.md").is_file()
    assert get_revision(SKILLS_REVISION) == before + 1


@pytest.mark.asyncio
async def test_write_file_outside_the_skills_dir_leaves_the_revision_alone(
    tmp_path,
) -> None:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    before = get_revision(SKILLS_REVISION)

    result = await write_file_module.write_file.coroutine(
        file_path="notes.md",
        content="Nothing to do with skills.\n",
        runtime=_runtime(str(sandbox)),
    )

    assert "[OK]" in result
    assert get_revision(SKILLS_REVISION) == before


@pytest.mark.asyncio
async def test_write_file_does_not_refresh_when_the_write_fails(tmp_path) -> None:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    before = get_revision(SKILLS_REVISION)

    result = await write_file_module.write_file.coroutine(
        file_path="../.agent/skills/demo/SKILL.md",
        content=SKILL_MD,
        runtime=_runtime(str(sandbox)),
    )

    assert "[error]" in result
    assert get_revision(SKILLS_REVISION) == before


@pytest.mark.asyncio
async def test_edit_file_refreshes_skills_when_editing_a_skill(tmp_path) -> None:
    sandbox = tmp_path / "sandbox"
    skill_md = sandbox / ".agent" / "skills" / "demo" / "SKILL.md"
    skill_md.parent.mkdir(parents=True)
    skill_md.write_text(SKILL_MD, encoding="utf-8")
    before = get_revision(SKILLS_REVISION)

    result = await edit_file_module.edit_file.coroutine(
        file_path=".agent/skills/demo/SKILL.md",
        old_string="Body.",
        new_string="Updated body.",
        runtime=_runtime(str(sandbox)),
    )

    assert "[OK]" in result
    assert "Updated body." in skill_md.read_text(encoding="utf-8")
    assert get_revision(SKILLS_REVISION) == before + 1


@pytest.mark.asyncio
async def test_edit_file_does_not_refresh_when_the_edit_fails(tmp_path) -> None:
    sandbox = tmp_path / "sandbox"
    skill_md = sandbox / ".agent" / "skills" / "demo" / "SKILL.md"
    skill_md.parent.mkdir(parents=True)
    skill_md.write_text(SKILL_MD, encoding="utf-8")
    before = get_revision(SKILLS_REVISION)

    result = await edit_file_module.edit_file.coroutine(
        file_path=".agent/skills/demo/SKILL.md",
        old_string="absent marker",
        new_string="whatever",
        runtime=_runtime(str(sandbox)),
    )

    assert "[error]" in result
    assert get_revision(SKILLS_REVISION) == before

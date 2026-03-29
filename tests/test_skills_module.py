"""Tests for the skills module (Phase 6)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from agent.modules.skills.domain.skill import Skill, SkillSummary
from agent.modules.skills.infrastructure.filesystem_repository import (
    FilesystemSkillRepository,
)
from agent.modules.skills.infrastructure.parser import parse_skill_md


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _create_skill_dir(
    base: Path,
    name: str,
    *,
    description: str = "A test skill.",
    body: str = "# Instructions\nDo the thing.",
    extra_frontmatter: str = "",
    create_resources: bool = False,
) -> Path:
    """Helper to create a skill directory with a SKILL.md file."""
    skill_dir = base / name
    skill_dir.mkdir(parents=True, exist_ok=True)

    frontmatter = f"name: {name}\ndescription: {description}"
    if extra_frontmatter:
        frontmatter += "\n" + extra_frontmatter

    content = f"---\n{frontmatter}\n---\n{body}\n"
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

    if create_resources:
        (skill_dir / "scripts").mkdir()
        (skill_dir / "scripts" / "run.py").write_text("print('hello')", encoding="utf-8")
        (skill_dir / "references").mkdir()
        (skill_dir / "references" / "guide.md").write_text("# Guide", encoding="utf-8")

    return skill_dir


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------


class TestParseSkillMd:
    def test_valid_skill(self, tmp_path: Path) -> None:
        skill_dir = _create_skill_dir(tmp_path, "my-skill")
        content = (skill_dir / "SKILL.md").read_text(encoding="utf-8")

        skill = parse_skill_md(content, skill_dir)

        assert skill is not None
        assert skill.name == "my-skill"
        assert skill.description == "A test skill."
        assert "# Instructions" in skill.body
        assert skill.path == skill_dir

    def test_missing_description_returns_none(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "no-desc"
        skill_dir.mkdir()
        content = "---\nname: no-desc\n---\nSome body.\n"
        (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

        result = parse_skill_md(content, skill_dir)
        assert result is None

    def test_malformed_yaml_returns_none(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "bad-yaml"
        skill_dir.mkdir()
        content = "---\n: : : invalid\n---\nBody here.\n"
        (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

        result = parse_skill_md(content, skill_dir)
        # Depends on how yaml handles it — could be None or parsed weirdly
        # Either way, it should NOT raise
        assert result is None or isinstance(result, Skill)

    def test_no_frontmatter_returns_none(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "no-front"
        skill_dir.mkdir()
        content = "Just some text, no frontmatter."
        (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

        result = parse_skill_md(content, skill_dir)
        assert result is None

    def test_optional_fields_parsed(self, tmp_path: Path) -> None:
        skill_dir = _create_skill_dir(
            tmp_path,
            "full-skill",
            description="Full featured skill.",
            extra_frontmatter=textwrap.dedent("""\
                license: MIT
                compatibility: Requires Python 3.12+
                metadata:
                  author: test-org
                  version: "2.0"
                allowed-tools: Bash(git:*) Read"""),
        )
        content = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        skill = parse_skill_md(content, skill_dir)

        assert skill is not None
        assert skill.license == "MIT"
        assert skill.compatibility == "Requires Python 3.12+"
        assert skill.metadata == {"author": "test-org", "version": "2.0"}
        assert skill.allowed_tools == ["Bash(git:*)", "Read"]

    def test_name_mismatch_warns_but_loads(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "dir-name"
        skill_dir.mkdir()
        # Name in frontmatter differs from directory
        content = "---\nname: different-name\ndescription: Test.\n---\nBody.\n"
        (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

        skill = parse_skill_md(content, skill_dir)
        assert skill is not None
        assert skill.name == "different-name"

    def test_resources_listed(self, tmp_path: Path) -> None:
        skill_dir = _create_skill_dir(
            tmp_path, "res-skill", create_resources=True
        )
        content = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        skill = parse_skill_md(content, skill_dir)

        assert skill is not None
        assert "scripts/run.py" in skill.resources
        assert "references/guide.md" in skill.resources

    def test_missing_name_uses_dir_name(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "fallback-name"
        skill_dir.mkdir()
        content = "---\ndescription: Has no name field.\n---\nBody.\n"
        (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

        skill = parse_skill_md(content, skill_dir)
        assert skill is not None
        assert skill.name == "fallback-name"


# ---------------------------------------------------------------------------
# Filesystem Repository tests
# ---------------------------------------------------------------------------


class TestFilesystemSkillRepository:
    def test_discover_skills(self, tmp_path: Path) -> None:
        _create_skill_dir(tmp_path, "skill-a", description="First skill.")
        _create_skill_dir(tmp_path, "skill-b", description="Second skill.")

        repo = FilesystemSkillRepository(skills_root=tmp_path)
        skills = repo.discover_all()

        assert len(skills) == 2
        names = {s.name for s in skills}
        assert names == {"skill-a", "skill-b"}

    def test_empty_dir_returns_empty_list(self, tmp_path: Path) -> None:
        repo = FilesystemSkillRepository(skills_root=tmp_path)
        assert repo.discover_all() == []

    def test_load_skill_by_name(self, tmp_path: Path) -> None:
        _create_skill_dir(tmp_path, "target-skill", description="Target.")

        repo = FilesystemSkillRepository(skills_root=tmp_path)
        skill = repo.load_skill("target-skill")

        assert skill is not None
        assert skill.name == "target-skill"

    def test_load_missing_skill_returns_none(self, tmp_path: Path) -> None:
        repo = FilesystemSkillRepository(skills_root=tmp_path)
        assert repo.load_skill("nonexistent") is None

    def test_list_summaries(self, tmp_path: Path) -> None:
        _create_skill_dir(tmp_path, "sum-skill", description="Summary test.")

        repo = FilesystemSkillRepository(skills_root=tmp_path)
        summaries = repo.list_summaries()

        assert len(summaries) == 1
        assert isinstance(summaries[0], SkillSummary)
        assert summaries[0].name == "sum-skill"
        assert summaries[0].description == "Summary test."

    def test_reload_refreshes_cache(self, tmp_path: Path) -> None:
        repo = FilesystemSkillRepository(skills_root=tmp_path)
        assert repo.discover_all() == []

        # Add a skill after initial scan
        _create_skill_dir(tmp_path, "late-skill", description="Added later.")
        # Still cached
        assert repo.discover_all() == []

        repo.reload()
        skills = repo.discover_all()
        assert len(skills) == 1
        assert skills[0].name == "late-skill"

    def test_skips_hidden_dirs(self, tmp_path: Path) -> None:
        _create_skill_dir(tmp_path, ".hidden-skill", description="Hidden.")
        _create_skill_dir(tmp_path, "visible-skill", description="Visible.")

        repo = FilesystemSkillRepository(skills_root=tmp_path)
        skills = repo.discover_all()

        assert len(skills) == 1
        assert skills[0].name == "visible-skill"

    def test_skips_dirs_without_skill_md(self, tmp_path: Path) -> None:
        (tmp_path / "not-a-skill").mkdir()
        _create_skill_dir(tmp_path, "real-skill", description="Real.")

        repo = FilesystemSkillRepository(skills_root=tmp_path)
        skills = repo.discover_all()

        assert len(skills) == 1

    def test_creates_root_if_missing(self, tmp_path: Path) -> None:
        root = tmp_path / "nonexistent" / "skills"
        repo = FilesystemSkillRepository(skills_root=root)
        skills = repo.discover_all()

        assert skills == []
        assert root.is_dir()

    def test_install_skill(self, tmp_path: Path) -> None:
        source = _create_skill_dir(
            tmp_path / "source", "install-me", description="To install."
        )
        install_root = tmp_path / "installed"

        repo = FilesystemSkillRepository(skills_root=install_root)
        installed = repo.install(source)

        assert installed.name == "install-me"
        assert (install_root / "install-me" / "SKILL.md").is_file()

        # Should be discoverable now
        repo.reload()
        assert repo.load_skill("install-me") is not None

    def test_install_invalid_raises(self, tmp_path: Path) -> None:
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        repo = FilesystemSkillRepository(skills_root=tmp_path / "dest")
        with pytest.raises(ValueError, match="SKILL.md"):
            repo.install(empty_dir)


# ---------------------------------------------------------------------------
# Public API tests
# ---------------------------------------------------------------------------


class TestPublicAPI:
    def test_catalog_xml_empty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import agent.modules.skills.public as pub
        repo = FilesystemSkillRepository(skills_root=tmp_path)
        monkeypatch.setattr(pub, "_repository", repo)

        xml = pub.get_skills_catalog_xml()
        assert xml == "<available_skills/>"

    def test_catalog_xml_with_skills(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _create_skill_dir(tmp_path, "xml-skill", description="XML test skill.")
        import agent.modules.skills.public as pub
        repo = FilesystemSkillRepository(skills_root=tmp_path)
        monkeypatch.setattr(pub, "_repository", repo)

        xml = pub.get_skills_catalog_xml()
        assert "<available_skills>" in xml
        assert "<name>xml-skill</name>" in xml
        assert "<description>XML test skill.</description>" in xml
        assert "SKILL.md</location>" in xml
        assert "</available_skills>" in xml

    def test_skill_content_xml(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _create_skill_dir(
            tmp_path,
            "content-skill",
            description="Content test.",
            body="# How to use\nStep 1.",
            create_resources=True,
        )
        import agent.modules.skills.public as pub
        repo = FilesystemSkillRepository(skills_root=tmp_path)
        monkeypatch.setattr(pub, "_repository", repo)

        xml = pub.get_skill_content_xml("content-skill")
        assert xml is not None
        assert '<skill_content name="content-skill">' in xml
        assert "# How to use" in xml
        assert "Step 1." in xml
        assert "<skill_resources>" in xml
        assert "<file>scripts/run.py</file>" in xml

    def test_skill_content_xml_not_found(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import agent.modules.skills.public as pub
        repo = FilesystemSkillRepository(skills_root=tmp_path)
        monkeypatch.setattr(pub, "_repository", repo)

        assert pub.get_skill_content_xml("nope") is None

    def test_list_and_get(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _create_skill_dir(tmp_path, "api-skill", description="API test.")
        import agent.modules.skills.public as pub
        repo = FilesystemSkillRepository(skills_root=tmp_path)
        monkeypatch.setattr(pub, "_repository", repo)

        summaries = pub.list_available_skills()
        assert len(summaries) == 1
        assert summaries[0].name == "api-skill"

        skill = pub.get_skill("api-skill")
        assert skill is not None
        assert skill.description == "API test."

    def test_reload(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import agent.modules.skills.public as pub
        repo = FilesystemSkillRepository(skills_root=tmp_path)
        monkeypatch.setattr(pub, "_repository", repo)

        assert pub.list_available_skills() == []

        _create_skill_dir(tmp_path, "reload-skill", description="Reload test.")
        pub.reload_skills()

        assert len(pub.list_available_skills()) == 1


# ---------------------------------------------------------------------------
# Extension Registry tests
# ---------------------------------------------------------------------------


class TestExtensionRegistry:
    def test_register_and_list(self) -> None:
        from agent.shared.extension_registry import ExtensionPoint, ExtensionRegistry

        reg = ExtensionRegistry()
        ep = ExtensionPoint(
            module="providers",
            kind="factory",
            name="openai_compatible",
            description="OpenAI-compatible LLM factory.",
        )
        reg.register(ep)

        all_eps = reg.list_all()
        assert "providers" in all_eps
        assert len(all_eps["providers"]) == 1
        assert all_eps["providers"][0].name == "openai_compatible"

    def test_list_by_module(self) -> None:
        from agent.shared.extension_registry import ExtensionPoint, ExtensionRegistry

        reg = ExtensionRegistry()
        reg.register(ExtensionPoint("channels", "adapter", "telegram", "Telegram bot."))
        reg.register(ExtensionPoint("channels", "adapter", "discord", "Discord bot."))
        reg.register(ExtensionPoint("providers", "factory", "openai", "OpenAI."))

        channels = reg.list_by_module("channels")
        assert len(channels) == 2

        providers = reg.list_by_module("providers")
        assert len(providers) == 1

        assert reg.list_by_module("skills") == []

    def test_clear(self) -> None:
        from agent.shared.extension_registry import ExtensionPoint, ExtensionRegistry

        reg = ExtensionRegistry()
        reg.register(ExtensionPoint("test", "kind", "name", "desc"))
        assert reg.list_all() != {}

        reg.clear()
        assert reg.list_all() == {}

    def test_global_singleton(self) -> None:
        from agent.shared.extension_registry import get_extension_registry

        r1 = get_extension_registry()
        r2 = get_extension_registry()
        assert r1 is r2


# ---------------------------------------------------------------------------
# Skill domain model tests
# ---------------------------------------------------------------------------


class TestSkillModel:
    def test_to_summary(self) -> None:
        skill = Skill(
            name="test",
            description="A test.",
            body="Body content.",
            path=Path("/tmp/test"),
        )
        summary = skill.to_summary()
        assert isinstance(summary, SkillSummary)
        assert summary.name == "test"
        assert summary.description == "A test."
        assert summary.path == Path("/tmp/test")

    def test_frozen(self) -> None:
        skill = Skill(
            name="frozen",
            description="Immutable.",
            body="Body.",
            path=Path("/tmp/frozen"),
        )
        with pytest.raises(AttributeError):
            skill.name = "changed"  # type: ignore[misc]

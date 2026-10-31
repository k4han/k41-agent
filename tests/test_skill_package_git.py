"""Git imports work with the application's selector loop and varied repository layouts."""

import asyncio
import os
import selectors
import shutil
import subprocess
from unittest.mock import MagicMock

import pytest

from agent.modules.skills.packages import SkillPackages
from agent.modules.skills.repository import FilesystemSkillRepository


@pytest.fixture
def packages(tmp_path, isolated_container):
    repository = FilesystemSkillRepository(tmp_path / "installed")
    isolated_container._skill_repository = repository
    service = SkillPackages(repository)
    isolated_container._skill_packages = service
    return service


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("git") is None, reason="Git is required.")
@pytest.mark.parametrize("layout,subdirectory", [
    ({".": "single-skill"}, ""),
    ({"skills/landing-page-design": "landing-page-design", "skills/design-to-motion": "design-to-motion"}, ""),
    ({".claude/skills/design": "design", "plugins/motion/skills/animate": "animate"}, ""),
    ({"skills/landing-page-design": "landing-page-design", "skills/design-to-motion": "design-to-motion"}, "skills/design-to-motion"),
])
def test_git_import_roundtrip_on_selector_loop(packages, tmp_path, monkeypatch, layout, subdirectory):
    source = tmp_path / "source"
    source.mkdir()
    documents = {}
    for directory, name in layout.items():
        skill = source / directory
        (skill / "assets").mkdir(parents=True)
        document = f'---\nname: {name}\ndescription: "Design instructions\n  spanning multiple lines."\ncustom-field: preserved\n---\n# Guide\n'
        (skill / "SKILL.md").write_text(document, encoding="utf-8")
        (skill / "assets" / "sample.bin").write_bytes(b"\x00\xff")
        documents[name] = document

    url = "https://example.invalid/design-skills"
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", f"url.{source.as_uri()}.insteadOf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", url)

    async def roundtrip():
        await packages._git(["init", str(source)])
        await packages._git(["-C", str(source), "add", "."])
        await packages._git(["-C", str(source), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                             "commit", "--no-gpg-sign", "-m", "Add skill fixtures"])
        commit = (await packages._git(["-C", str(source), "rev-parse", "HEAD"])).strip()
        preview = await packages.preview(kind="git", url=url, subdirectory=subdirectory)
        expected = {layout[subdirectory]} if subdirectory else set(layout.values())
        assert preview["commit"] == commit
        assert {item["name"] for item in preview["skills"]} == expected
        assert all(any(item["code"] == "unsupported_field" for item in skill["diagnostics"]) for skill in preview["skills"])
        result = await packages.import_preview(preview["preview_id"])
        assert set(result["names"]) == expected
        inventory = await packages.inventory(scope="global")
        assert {item.name for item in inventory if item.enabled} == expected
        for name in expected:
            installed = packages.repository.root / name
            assert (installed / "SKILL.md").read_text(encoding="utf-8") == documents[name]
            assert (installed / "assets" / "sample.bin").read_bytes() == b"\x00\xff"

    asyncio.run(roundtrip(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))


@pytest.mark.asyncio
async def test_missing_git_has_actionable_error(packages, monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "Popen", missing)
    with pytest.raises(ValueError, match="Git is not installed"):
        await packages._git(["--version"])


@pytest.mark.asyncio
async def test_git_timeout_terminates_process_tree(packages, monkeypatch):
    import agent.modules.tools as tools_module

    process = MagicMock()
    process.__enter__.return_value = process
    process.communicate.side_effect = [subprocess.TimeoutExpired("git", 120), (b"", b"")]
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)
    kill = MagicMock()
    monkeypatch.setattr(tools_module, "kill_process_tree", kill)
    with pytest.raises(ValueError, match="Git import timed out"):
        await packages._git(["--version"])
    kill.assert_called_once_with(process)
    assert process.communicate.call_count == 2


@pytest.mark.asyncio
async def test_git_failure_does_not_expose_credentials(packages, monkeypatch):
    process = MagicMock()
    process.__enter__.return_value = process
    process.returncode = 128
    process.communicate.return_value = (b"", b"fatal: secret-token")
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)
    with pytest.raises(ValueError, match="Check repository URL") as error:
        await packages._git(["fetch"])
    assert "secret-token" not in str(error.value)

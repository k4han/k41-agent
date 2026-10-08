"""Deterministic source identities and cross-client discovery conventions."""

import hashlib
import json
from pathlib import Path

from agent.modules.skills.repository import FilesystemSkillRepository
from agent.modules.workspaces import resolve_workspace_ref


def workspace_key(workspace) -> str:
    ref = resolve_workspace_ref(workspace)
    root = ref.locator if ref.backend == "local" else str(ref.metadata.get("root") or "")
    locator = ref.metadata.get("skill_workspace_id", ref.locator) if ref.backend == "modal" else ref.locator
    return hashlib.sha256(json.dumps([ref.backend, locator, root]).encode()).hexdigest()


def package_id(scope: str, root: str, directory: str, workspace=None) -> str:
    return hashlib.sha256(json.dumps([scope, root, directory, workspace_key(workspace) if workspace else ""]).encode()).hexdigest()[:32]


def global_repositories(repository):
    from agent.shared.config import get_config_service

    yield repository
    seen = {repository.root.resolve()}
    config = get_config_service()
    paths = [Path.home() / ".agents" / "skills", *(Path(str(value)).expanduser() for value in config.get("skills.additional_roots", []) or [])]
    for root in paths:
        root = root.resolve()
        if root in seen or not root.is_dir():
            continue
        seen.add(root)
        yield FilesystemSkillRepository(root)


def repository_roots(configured: str) -> list[str]:
    return list(dict.fromkeys([configured, ".agents/skills"]))


def is_enabled(scope: str, root: str, directory: str, workspace=None) -> bool:
    from agent.modules.skills.packages import get_skill_packages

    return package_id(scope, root, directory, workspace) not in get_skill_packages()._disabled()

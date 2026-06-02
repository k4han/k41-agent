from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent.modules.workspaces import WorkspaceRef
from agent.modules.workspaces.github_clone import (
    GitHubRepositorySelection,
    attach_github_repository_to_daytona_workspace,
    attach_github_repository_to_local_workspace,
    attach_github_repository_to_local_workspace_async,
    attach_github_repository_to_modal_workspace,
    attach_github_repository_to_workspace,
    is_github_workspace,
    normalize_github_workspace_ref,
)
import agent.modules.workspaces.github_clone as github_clone_module


@dataclass(frozen=True, slots=True)
class FakeBinding:
    repository_id: int = 0
    full_name: str = ""
    default_branch: str = ""
    installation_id: int = 0
    private: bool = False


class FakeGitHubAutomationService:
    def __init__(
        self,
        binding: FakeBinding | None,
        *,
        token: str = "install-token-abc",
    ) -> None:
        self.binding = binding
        self.token = token
        self.store = SimpleNamespace(
            get_binding_by_repository_id=self._get_binding,
        )
        self.client = SimpleNamespace(
            get_installation_token=self._get_token,
        )
        self.token_calls: list[int] = []

    async def _get_binding(self, repository_id: int):
        if self.binding is None or self.binding.repository_id != repository_id:
            return None
        return self.binding

    async def _get_token(self, installation_id: int) -> str:
        self.token_calls.append(installation_id)
        return self.token


def _patch_service(
    monkeypatch: pytest.MonkeyPatch,
    binding: FakeBinding | None,
    *,
    token: str = "install-token-abc",
) -> FakeGitHubAutomationService:
    service = FakeGitHubAutomationService(binding, token=token)
    monkeypatch.setattr(
        "agent.modules.github.get_github_automation_service",
        lambda: service,
    )
    return service


def test_is_github_workspace_detects_metadata_source() -> None:
    ref = WorkspaceRef(
        backend="daytona",
        locator="sb-1",
        label="owner/repo",
        metadata={"source": "github", "repository_full_name": "owner/repo"},
    )

    assert is_github_workspace(ref) is True


def test_is_github_workspace_handles_dict_payload() -> None:
    payload = {
        "backend": "modal",
        "locator": "sb-2",
        "metadata": {"source": "github", "repository_full_name": "owner/repo"},
    }

    assert is_github_workspace(payload) is True


def test_is_github_workspace_rejects_other_sources() -> None:
    ref = WorkspaceRef(
        backend="local",
        locator="C:/work",
        label="C:/work",
        metadata={"source": "local-folder"},
    )
    assert is_github_workspace(ref) is False
    assert is_github_workspace(None) is False
    assert is_github_workspace({"metadata": "not-a-dict"}) is False


def test_github_repository_selection_from_binding_defaults_branch() -> None:
    binding = FakeBinding(
        repository_id=42,
        full_name="acme/widgets",
        default_branch="",
        installation_id=7,
    )

    selection = GitHubRepositorySelection.from_binding(binding)

    assert selection.repository_id == 42
    assert selection.full_name == "acme/widgets"
    assert selection.default_branch == "main"
    assert selection.installation_id == 7
    assert selection.token == ""


@pytest.mark.asyncio
async def test_attach_github_to_local_workspace_async_uses_manager(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    checkout_path = tmp_path / "github-workspaces" / "acme" / "widgets"
    checkout_path.mkdir(parents=True)

    class FakeManager:
        async def ensure_shared_checkout(self, *, full_name: str, token: str) -> Path:
            assert full_name == "acme/widgets"
            assert token == "install-token-abc"
            return checkout_path

    binding = FakeBinding(
        repository_id=11,
        full_name="acme/widgets",
        default_branch="main",
        installation_id=99,
    )
    _patch_service(monkeypatch, binding)

    selection = GitHubRepositorySelection.from_binding(binding)

    ref = await attach_github_repository_to_local_workspace_async(
        selection,
        manager=FakeManager(),
        token="install-token-abc",
    )

    assert ref.backend == "local"
    assert ref.locator == str(checkout_path.resolve())
    assert ref.label == "acme/widgets"
    assert ref.metadata["source"] == "github"
    assert ref.metadata["repository_id"] == 11
    assert ref.metadata["repository_full_name"] == "acme/widgets"
    assert ref.metadata["default_branch"] == "main"
    assert ref.metadata["repository_path"] == str(checkout_path.resolve())


def test_attach_github_to_local_workspace_sync_wrapper(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    checkout_path = tmp_path / "acme" / "widgets"
    checkout_path.mkdir(parents=True)

    class FakeManager:
        async def ensure_shared_checkout(self, *, full_name: str, token: str) -> Path:
            return checkout_path

    selection = GitHubRepositorySelection(
        repository_id=5,
        full_name="acme/widgets",
        default_branch="main",
        token="direct-token",
    )

    ref = attach_github_repository_to_local_workspace(
        selection,
        manager=FakeManager(),
    )

    assert ref.backend == "local"
    assert ref.locator == str(checkout_path.resolve())
    assert ref.metadata["source"] == "github"
    assert ref.metadata["repository_id"] == 5


def test_attach_github_to_local_workspace_rejects_invalid_full_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeManager:
        async def ensure_shared_checkout(self, *, full_name: str, token: str) -> Path:
            raise AssertionError("should not be called")

    selection = GitHubRepositorySelection(
        repository_id=1,
        full_name="invalid-no-slash",
    )

    with pytest.raises(ValueError, match="Invalid GitHub repository full name"):
        attach_github_repository_to_local_workspace(
            selection,
            manager=FakeManager(),
        )


def test_attach_github_to_daytona_workspace_invokes_clone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = WorkspaceRef(
        backend="daytona",
        locator="sb-day-1",
        label="daytona:sb-day-1",
        metadata={"root": "workspace"},
    )
    binding = FakeBinding(
        repository_id=21,
        full_name="acme/widgets",
        default_branch="develop",
        installation_id=1,
    )
    _patch_service(monkeypatch, binding)

    captured: dict[str, Any] = {}

    def fake_clone(ref, *, owner, repo, default_branch, token):
        captured["backend"] = ref.backend
        captured["locator"] = ref.locator
        captured["owner"] = owner
        captured["repo"] = repo
        captured["default_branch"] = default_branch
        captured["token"] = token
        return f"{owner}/{repo}"

    monkeypatch.setattr(
        "agent.modules.workspaces.daytona_backend.clone_repository_in_daytona_sandbox",
        fake_clone,
    )

    selection = GitHubRepositorySelection.from_binding(binding)
    selection = GitHubRepositorySelection(
        repository_id=selection.repository_id,
        full_name=selection.full_name,
        default_branch=selection.default_branch,
        installation_id=selection.installation_id,
        token="install-token-abc",
    )
    ref = attach_github_repository_to_daytona_workspace(workspace, selection)

    assert captured == {
        "backend": "daytona",
        "locator": "sb-day-1",
        "owner": "acme",
        "repo": "widgets",
        "default_branch": "develop",
        "token": "install-token-abc",
    }
    assert ref.backend == "daytona"
    assert ref.locator == "sb-day-1"
    assert ref.label == "acme/widgets"
    assert ref.metadata["source"] == "github"
    assert ref.metadata["repository_full_name"] == "acme/widgets"
    assert ref.metadata["default_branch"] == "develop"
    assert ref.metadata["repository_path"] == "acme/widgets"
    assert ref.metadata["root"] == "workspace"


def test_attach_github_to_modal_workspace_invokes_clone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = WorkspaceRef(
        backend="modal",
        locator="sb-mod-1",
        label="modal:sb-mod-1",
        metadata={"root": "/workspace"},
    )
    binding = FakeBinding(
        repository_id=22,
        full_name="acme/widgets",
        default_branch="trunk",
        installation_id=0,
    )
    service = _patch_service(monkeypatch, binding)

    captured: dict[str, Any] = {}

    def fake_clone(ref, *, owner, repo, default_branch, token):
        captured["backend"] = ref.backend
        captured["owner"] = owner
        captured["repo"] = repo
        captured["default_branch"] = default_branch
        captured["token"] = token
        return f"{owner}/{repo}"

    monkeypatch.setattr(
        "agent.modules.workspaces.modal_backend.clone_repository_in_modal_sandbox",
        fake_clone,
    )

    selection = GitHubRepositorySelection(
        repository_id=22,
        full_name="acme/widgets",
        default_branch="trunk",
        token="explicit-token",
    )
    ref = attach_github_repository_to_modal_workspace(workspace, selection)

    assert captured["backend"] == "modal"
    assert captured["owner"] == "acme"
    assert captured["repo"] == "widgets"
    assert captured["default_branch"] == "trunk"
    assert captured["token"] == "explicit-token"
    assert service.token_calls == []
    assert ref.backend == "modal"
    assert ref.label == "acme/widgets"
    assert ref.metadata["source"] == "github"
    assert ref.metadata["repository_path"] == "acme/widgets"


@pytest.mark.asyncio
async def test_attach_github_to_workspace_dispatches_to_daytona(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = WorkspaceRef(
        backend="daytona",
        locator="sb-d-2",
        label="daytona:sb-d-2",
        metadata={"root": "workspace"},
    )
    binding = FakeBinding(
        repository_id=33,
        full_name="acme/widgets",
        default_branch="main",
        installation_id=0,
    )
    _patch_service(monkeypatch, binding)

    monkeypatch.setattr(
        "agent.modules.workspaces.daytona_backend.clone_repository_in_daytona_sandbox",
        lambda ref, **kwargs: "acme/widgets",
    )

    ref = await attach_github_repository_to_workspace(
        workspace,
        repository_id=33,
        install_token="explicit",
    )

    assert ref.backend == "daytona"
    assert ref.metadata["source"] == "github"
    assert ref.metadata["repository_full_name"] == "acme/widgets"


@pytest.mark.asyncio
async def test_attach_github_to_workspace_dispatches_to_modal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = WorkspaceRef(
        backend="modal",
        locator="sb-m-2",
        label="modal:sb-m-2",
        metadata={"root": "/workspace"},
    )
    binding = FakeBinding(
        repository_id=44,
        full_name="acme/widgets",
        default_branch="main",
        installation_id=0,
    )
    _patch_service(monkeypatch, binding)

    monkeypatch.setattr(
        "agent.modules.workspaces.modal_backend.clone_repository_in_modal_sandbox",
        lambda ref, **kwargs: "acme/widgets",
    )

    ref = await attach_github_repository_to_workspace(
        workspace,
        repository_id=44,
    )

    assert ref.backend == "modal"
    assert ref.metadata["source"] == "github"
    assert ref.metadata["repository_full_name"] == "acme/widgets"


@pytest.mark.asyncio
async def test_attach_github_to_workspace_dispatches_to_local(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = WorkspaceRef(
        backend="local",
        locator=str(tmp_path),
        label=str(tmp_path),
    )
    checkout_path = tmp_path / "acme" / "widgets"
    checkout_path.mkdir(parents=True)

    binding = FakeBinding(
        repository_id=55,
        full_name="acme/widgets",
        default_branch="main",
        installation_id=0,
    )
    _patch_service(monkeypatch, binding)

    class FakeManager:
        async def ensure_shared_checkout(self, *, full_name: str, token: str) -> Path:
            return checkout_path

    monkeypatch.setattr(
        github_clone_module,
        "_get_github_workspace_manager_cls",
        lambda: FakeManager,
    )

    ref = await attach_github_repository_to_workspace(
        workspace,
        repository_id=55,
    )

    assert ref.backend == "local"
    assert ref.metadata["source"] == "github"
    assert ref.metadata["repository_path"] == str(checkout_path.resolve())


@pytest.mark.asyncio
async def test_attach_github_to_workspace_missing_binding_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = WorkspaceRef(
        backend="local",
        locator="C:/work",
        label="C:/work",
    )
    _patch_service(monkeypatch, binding=None)

    with pytest.raises(KeyError, match="not synced"):
        await attach_github_repository_to_workspace(
            workspace,
            repository_id=9999,
        )


def test_normalize_github_workspace_ref_preserves_metadata() -> None:
    payload = {
        "backend": "daytona",
        "locator": "sb-1",
        "label": "daytona:sb-1",
        "metadata": {
            "source": "github",
            "repository_id": 7,
            "repository_full_name": "acme/widgets",
            "default_branch": "main",
            "repository_path": "acme/widgets",
        },
    }

    ref = normalize_github_workspace_ref(payload)

    assert ref.backend == "daytona"
    assert ref.locator == "sb-1"
    assert ref.label == "acme/widgets"
    assert ref.metadata["source"] == "github"
    assert ref.metadata["repository_id"] == 7
    assert ref.metadata["repository_full_name"] == "acme/widgets"


def test_normalize_github_workspace_ref_uses_repository_full_name_label() -> None:
    payload = {
        "backend": "modal",
        "locator": "sb-9",
        "label": "modal:sb-9",
        "metadata": {
            "source": "github",
            "repository_full_name": "team/project",
            "repository_id": 3,
        },
    }

    ref = normalize_github_workspace_ref(payload)

    assert ref.label == "team/project"


def test_normalize_github_workspace_ref_passes_through_non_github() -> None:
    payload = {
        "backend": "local",
        "locator": "C:/work",
        "label": "C:/work",
        "metadata": {"root": "C:/work"},
    }

    ref = normalize_github_workspace_ref(payload)

    assert ref.backend == "local"
    assert ref.locator == str(Path("C:/work").resolve())
    assert ref.metadata == {"root": "C:/work"}


@pytest.mark.asyncio
async def test_installation_token_resolved_via_service(monkeypatch: pytest.MonkeyPatch) -> None:
    binding = FakeBinding(
        repository_id=1,
        full_name="acme/widgets",
        default_branch="main",
        installation_id=1234,
    )
    service = _patch_service(monkeypatch, binding, token="fetched-token")

    workspace = WorkspaceRef(
        backend="daytona",
        locator="sb-1",
        label="daytona:sb-1",
        metadata={"root": "workspace"},
    )

    monkeypatch.setattr(
        "agent.modules.workspaces.daytona_backend.clone_repository_in_daytona_sandbox",
        lambda ref, **kwargs: "acme/widgets",
    )

    ref = await attach_github_repository_to_workspace(
        workspace,
        repository_id=1,
    )

    assert service.token_calls == [1234]
    assert ref.metadata["source"] == "github"
    assert ref.metadata["repository_full_name"] == "acme/widgets"

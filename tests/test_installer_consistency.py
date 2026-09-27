"""Regression tests for installer consistency.

These tests lock the fixes from the install review:
- shared exclude list across install.ps1, install.sh, release.yml, update.py
- pytest moved to dev dependency group (smaller end-user install)
- install.sh stage order matches install.ps1 (source before venv)
- uv version is pinned instead of floating on latest
- backup and fish PATH support exist in shell installer
"""

from __future__ import annotations

import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

REQUIRED_EXCLUDES = [
    ".git",
    ".github",
    ".venv",
    "__pycache__",
    "node_modules",
    "local-dev",
    "data",
    "*.egg-info",
    "*.pyc",
    ".env",
]


def _read(name: str) -> str:
    return (REPO_ROOT / name).read_text(encoding="utf-8")


def test_install_ps1_excludes_runtime_and_secrets() -> None:
    content = _read("install.ps1")
    for entry in REQUIRED_EXCLUDES:
        assert entry in content, f"install.ps1 missing exclude {entry}"


def test_install_sh_excludes_runtime_and_secrets() -> None:
    content = _read("install.sh")
    for entry in REQUIRED_EXCLUDES:
        assert entry in content, f"install.sh missing exclude {entry}"


def test_release_workflow_excludes_runtime_and_secrets() -> None:
    content = _read(".github/workflows/release.yml")
    for entry in REQUIRED_EXCLUDES:
        assert entry in content, f"release.yml missing exclude {entry}"


def test_update_copy_tree_excludes_data_and_env() -> None:
    content = (REPO_ROOT / "agent" / "bootstrap" / "update.py").read_text(
        encoding="utf-8"
    )
    assert '"data"' in content
    assert '"local-dev"' in content
    assert '".env"' in content


def test_pytest_is_dev_only() -> None:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    main_deps = " ".join(data["project"]["dependencies"])
    assert "pytest" not in main_deps
    dev_group = " ".join(data["dependency-groups"]["dev"])
    assert "pytest" in dev_group


def test_install_sh_stage_order_source_before_venv() -> None:
    content = _read("install.sh")
    # Compare the main-flow invocations, not function definitions.
    source_call = content.rindex("\ninstall_source")
    venv_call = content.rindex("\nensure_venv")
    assert source_call < venv_call, "install.sh must install source before venv"


def test_uv_version_pinned() -> None:
    assert "K41_AGENT_UV_VERSION" in _read("install.ps1")
    assert "K41_AGENT_UV_VERSION" in _read("install.sh")
    assert "UV_VERSION" in _read(".github/workflows/release.yml")


def test_uv_download_url_has_no_v_prefix() -> None:
    # Upstream uv tags carry no "v" prefix (e.g. 0.12.13).
    # A "/download/v..." URL returns 404 from GitHub.
    ps1 = _read("install.ps1")
    assert "releases/download/$pinned" in ps1
    assert "releases/download/v$pinned" not in ps1
    sh = _read("install.sh")
    assert "releases/download/$version" in sh
    assert "releases/download/v$version" not in sh


def test_backup_and_fish_support_in_shell_installer() -> None:
    content = _read("install.sh")
    assert "backup_app_source" in content
    assert "BACKUP_PATH" in content
    assert "k41-agent.fish" in content


def test_ps1_backup_and_registry_cleanup() -> None:
    content = _read("install.ps1")
    assert "Backup-AppSource" in content
    assert "k41-agent-tray" in content
    assert "Wait-ProcessExit" in content

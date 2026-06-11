from __future__ import annotations

import os
import shutil
import zipfile
from pathlib import Path

import pytest

from agent.bootstrap import update as update_module
from agent.bootstrap.update import ReleaseInfo, UpdateError, UpdateOptions


def write_project(root: Path, *, version: str, marker: str) -> None:
    (root / "agent" / "bootstrap").mkdir(parents=True, exist_ok=True)
    (root / "agent" / "bootstrap" / "update.py").write_text("", encoding="utf-8")
    static_dir = root / "agent" / "delivery" / "http" / "dashboard" / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    (static_dir / "index.html").write_text("<!doctype html>", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "k41-agent"\nversion = "{version}"\n',
        encoding="utf-8",
    )
    (root / "marker.txt").write_text(marker, encoding="utf-8")


def create_managed_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    agent_home = tmp_path / "k41-agent"
    app_dir = agent_home / "app"
    write_project(app_dir, version="0.1.2", marker="old")

    tools_dir = agent_home / "tools"
    tools_dir.mkdir(parents=True)
    uv_exe = tools_dir / ("uv.exe" if os.name == "nt" else "uv")
    uv_exe.write_text("", encoding="utf-8")

    if os.name == "nt":
        python_exe = agent_home / "envs" / "Scripts" / "python.exe"
    else:
        python_exe = agent_home / "envs" / "bin" / "python"
    python_exe.parent.mkdir(parents=True)
    python_exe.write_text("", encoding="utf-8")

    monkeypatch.setenv("K41_AGENT_HOME", str(agent_home))
    monkeypatch.delenv("AGENT_HOME", raising=False)
    return agent_home, app_dir, python_exe


def build_release_zip(tmp_path: Path, *, version: str, marker: str) -> Path:
    source_root = tmp_path / "release-source" / "k41-agent"
    write_project(source_root, version=version, marker=marker)
    zip_path = tmp_path / f"release-{version}.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        for file_path in source_root.rglob("*"):
            if file_path.is_file():
                archive.write(file_path, file_path.relative_to(source_root.parent))
    return zip_path


def patch_release(
    monkeypatch: pytest.MonkeyPatch,
    *,
    zip_path: Path | None = None,
    version: str = "0.1.3",
) -> None:
    monkeypatch.setattr(
        update_module,
        "fetch_latest_release",
        lambda **kwargs: ReleaseInfo(
            tag_name=f"v{version}",
            version=version,
            asset_url="https://example.test/k41-agent-release.zip",
            html_url="https://example.test/releases/latest",
        ),
    )
    if zip_path is not None:
        monkeypatch.setattr(
            update_module,
            "download_release_artifact",
            lambda url, destination: shutil.copyfile(zip_path, destination),
        )


def disable_server(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(update_module, "get_running_server_pid", lambda: None)
    monkeypatch.setattr(update_module, "start_server", lambda install: None)
    monkeypatch.setattr(update_module, "stop_running_server", lambda pid: None)


def test_update_check_reports_available(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_release(monkeypatch, version="0.1.3")
    messages: list[str] = []

    result = update_module.run_update(
        UpdateOptions(check_only=True, current_version="0.1.2"),
        echo=messages.append,
    )

    assert result.status == "available"
    assert any("Update available" in message for message in messages)


def test_update_check_reports_current(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_release(monkeypatch, version="0.1.2")

    result = update_module.run_update(
        UpdateOptions(check_only=True, current_version="0.1.2")
    )

    assert result.status == "current"


def test_update_check_surfaces_network_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_fetch(**kwargs):
        raise UpdateError("network unavailable")

    monkeypatch.setattr(update_module, "fetch_latest_release", fail_fetch)

    with pytest.raises(UpdateError, match="network unavailable"):
        update_module.run_update(UpdateOptions(check_only=True, current_version="0.1.2"))


def test_update_success_replaces_source_and_keeps_single_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_home, app_dir, python_exe = create_managed_install(tmp_path, monkeypatch)
    old_backup = agent_home / "backup" / "app-0.1.1-old"
    write_project(old_backup, version="0.1.1", marker="older")
    zip_path = build_release_zip(tmp_path, version="0.1.3", marker="new")
    patch_release(monkeypatch, zip_path=zip_path, version="0.1.3")
    disable_server(monkeypatch)

    calls: list[str] = []
    monkeypatch.setattr(update_module, "sync_app", lambda install: calls.append("sync"))
    monkeypatch.setattr(update_module, "initialize_app", lambda install: calls.append("init"))

    result = update_module.run_update(
        UpdateOptions(
            yes=True,
            current_version="0.1.2",
            module_file=app_dir / "agent" / "bootstrap" / "update.py",
            executable=str(python_exe),
        )
    )

    assert result.status == "updated"
    assert (app_dir / "marker.txt").read_text(encoding="utf-8") == "new"
    backups = list((agent_home / "backup").iterdir())
    assert len(backups) == 1
    assert backups[0] == result.backup_path
    assert (backups[0] / "marker.txt").read_text(encoding="utf-8") == "old"
    assert not old_backup.exists()
    assert calls == ["sync", "init"]
    assert not any((agent_home / "download").iterdir())


def test_update_rollback_restores_source_when_sync_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _agent_home, app_dir, python_exe = create_managed_install(tmp_path, monkeypatch)
    zip_path = build_release_zip(tmp_path, version="0.1.3", marker="new")
    patch_release(monkeypatch, zip_path=zip_path, version="0.1.3")
    disable_server(monkeypatch)

    sync_calls = 0

    def fail_first_sync(install):
        nonlocal sync_calls
        sync_calls += 1
        if sync_calls == 1:
            raise UpdateError("sync failed")

    monkeypatch.setattr(update_module, "sync_app", fail_first_sync)
    monkeypatch.setattr(update_module, "initialize_app", lambda install: None)

    with pytest.raises(UpdateError, match="sync failed"):
        update_module.run_update(
            UpdateOptions(
                yes=True,
                current_version="0.1.2",
                module_file=app_dir / "agent" / "bootstrap" / "update.py",
                executable=str(python_exe),
            )
        )

    assert (app_dir / "marker.txt").read_text(encoding="utf-8") == "old"
    assert sync_calls == 2


def test_update_restarts_server_when_it_was_running(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _agent_home, app_dir, python_exe = create_managed_install(tmp_path, monkeypatch)
    zip_path = build_release_zip(tmp_path, version="0.1.3", marker="new")
    patch_release(monkeypatch, zip_path=zip_path, version="0.1.3")

    events: list[str] = []
    monkeypatch.setattr(update_module, "get_running_server_pid", lambda: 123)
    monkeypatch.setattr(update_module, "stop_running_server", lambda pid: events.append(f"stop:{pid}"))
    monkeypatch.setattr(update_module, "start_server", lambda install: events.append("start"))
    monkeypatch.setattr(update_module, "sync_app", lambda install: None)
    monkeypatch.setattr(update_module, "initialize_app", lambda install: None)

    result = update_module.run_update(
        UpdateOptions(
            yes=True,
            current_version="0.1.2",
            module_file=app_dir / "agent" / "bootstrap" / "update.py",
            executable=str(python_exe),
        )
    )

    assert result.restarted is True
    assert events == ["stop:123", "start"]


def test_update_rejects_non_managed_layout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("K41_AGENT_HOME", str(tmp_path / "missing-install"))
    patch_release(monkeypatch, version="0.1.3")

    with pytest.raises(UpdateError, match="Managed app directory"):
        update_module.run_update(UpdateOptions(yes=True, current_version="0.1.2"))


def test_version_comparison_handles_multi_digit_versions() -> None:
    assert update_module.is_version_newer("0.1.10", "0.1.2") is True
    assert update_module.is_version_newer("v0.1.2", "0.1.10") is False

from __future__ import annotations

import os
import shutil
import zipfile
from pathlib import Path

import pytest

from agent.bootstrap import update as update_module
from agent.bootstrap.update import ReleaseInfo, UpdateError, UpdateOptions
from agent.bootstrap.update_state import read_update_status, update_lock, write_update_status


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
    monkeypatch.setattr(update_module, "PID_FILE", agent_home / "server.pid")
    monkeypatch.setattr(update_module, "TRAY_PID_FILE", agent_home / "tray.pid")
    monkeypatch.setattr(update_module, "SHUTDOWN_SIGNAL", agent_home / "shutdown.signal")
    monkeypatch.setattr(update_module, "is_systemd_service_active", lambda: False)
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


@pytest.fixture
def prepared_update(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, UpdateOptions]:
    agent_home, app_dir, python_exe = create_managed_install(tmp_path, monkeypatch)
    zip_path = build_release_zip(tmp_path, version="0.1.3", marker="new")
    patch_release(monkeypatch, zip_path=zip_path)
    disable_server(monkeypatch)
    monkeypatch.setattr(update_module, "sync_app", lambda install: None)
    monkeypatch.setattr(update_module, "initialize_app", lambda install: None)
    return agent_home, app_dir, UpdateOptions(
        yes=True, module_file=app_dir / "agent" / "bootstrap" / "update.py",
        executable=str(python_exe),
    )


def test_update_can_replace_source_when_launched_from_app_directory(prepared_update, monkeypatch) -> None:
    agent_home, app_dir, options = prepared_update
    monkeypatch.chdir(app_dir)
    replace = update_module.replace_app_source

    def checked_replace(install, source_root):
        assert Path.cwd() == agent_home
        replace(install, source_root)

    monkeypatch.setattr(update_module, "replace_app_source", checked_replace)
    result = update_module.run_update(options)

    assert result.status == "updated"
    assert Path.cwd() == app_dir
    assert (app_dir / "marker.txt").read_text() == "new"
    status = read_update_status(agent_home)
    assert status["status"] == "updated"
    assert status["latest_version"] == "0.1.3"


def test_update_rejects_mismatched_artifact_before_stopping_server(prepared_update, monkeypatch) -> None:
    agent_home, app_dir, options = prepared_update
    patch_release(monkeypatch, version="0.1.4")
    events = []
    monkeypatch.setattr(update_module, "get_running_server_pid", lambda: events.append("pid"))

    with pytest.raises(UpdateError, match="does not match"):
        update_module.run_update(options)

    assert events == []
    assert (app_dir / "marker.txt").read_text() == "old"
    assert read_update_status(agent_home)["status"] == "failed"


def test_update_records_download_failure(prepared_update, monkeypatch) -> None:
    agent_home, app_dir, options = prepared_update

    def failed_download(*args):
        raise UpdateError("download unavailable")

    monkeypatch.setattr(update_module, "download_release_artifact", failed_download)
    with pytest.raises(UpdateError, match="download unavailable"):
        update_module.run_update(options)
    assert read_update_status(agent_home)["error"] == "download unavailable"
    assert (app_dir / "marker.txt").read_text() == "old"


@pytest.mark.parametrize("rollback_fails", [False, True])
def test_update_attempts_restart_even_when_rollback_sync_fails(prepared_update, monkeypatch, rollback_fails) -> None:
    agent_home, app_dir, options = prepared_update
    events = []
    running_pid = 123

    def stop(pid):
        nonlocal running_pid
        events.append("stop")
        running_pid = None

    def sync(install):
        events.append("sync")
        if events.count("sync") == 1 or rollback_fails:
            raise UpdateError("sync failed")

    monkeypatch.setattr(update_module, "get_running_server_pid", lambda: running_pid)
    monkeypatch.setattr(update_module, "stop_running_server", stop)
    monkeypatch.setattr(update_module, "sync_app", sync)
    monkeypatch.setattr(update_module, "start_server", lambda install: events.append("start"))

    with pytest.raises(UpdateError, match="sync failed"):
        update_module.run_update(options)

    assert events == ["stop", "sync", "sync", "start"]
    assert (app_dir / "marker.txt").read_text() == "old"
    assert read_update_status(agent_home)["status"] == "failed"


def test_update_restarts_tray_after_server_stop_failure(prepared_update, monkeypatch) -> None:
    agent_home, app_dir, options = prepared_update
    events = []
    tray_pid = 456

    def stop_tray(pid):
        nonlocal tray_pid
        tray_pid = None
        events.append("stop-tray")
        return True

    def fail_stop(pid):
        raise UpdateError("server did not stop")

    monkeypatch.setattr(update_module, "get_running_tray_pid", lambda: tray_pid)
    monkeypatch.setattr(update_module, "stop_running_tray", stop_tray)
    monkeypatch.setattr(update_module, "get_running_server_pid", lambda: 123)
    monkeypatch.setattr(update_module, "stop_running_server", fail_stop)
    monkeypatch.setattr(update_module, "start_server", lambda install: events.append("start-server"))
    monkeypatch.setattr(update_module, "start_tray", lambda install: events.append("start-tray"))
    with pytest.raises(UpdateError, match="server did not stop"):
        update_module.run_update(options)

    assert events == ["stop-tray", "start-tray"]
    assert (app_dir / "marker.txt").read_text() == "old"
    assert read_update_status(agent_home)["status"] == "failed"


def test_update_does_not_replace_source_if_tray_cannot_stop(prepared_update, monkeypatch) -> None:
    _, app_dir, options = prepared_update
    monkeypatch.setattr(update_module, "get_running_tray_pid", lambda: 456)
    monkeypatch.setattr(update_module, "stop_running_tray", lambda pid: False)
    monkeypatch.setattr(update_module, "is_process_alive", lambda pid: True)
    with pytest.raises(UpdateError, match="Could not stop the tray"):
        update_module.run_update(options)
    assert (app_dir / "marker.txt").read_text() == "old"


def test_update_rejects_concurrent_cli_and_queued_dashboard_update(prepared_update) -> None:
    agent_home, app_dir, options = prepared_update
    with update_lock(agent_home):
        with pytest.raises(UpdateError, match="already in progress"):
            update_module.run_update(options)
    write_update_status(agent_home, {"update_id": "web", "status": "queued", "queued_at": update_module.time.time()})
    with pytest.raises(UpdateError, match="already in progress"):
        update_module.run_update(options)
    assert (app_dir / "marker.txt").read_text() == "old"


def test_update_worker_claims_only_its_queued_job(prepared_update) -> None:
    from dataclasses import replace

    agent_home, _, options = prepared_update
    write_update_status(agent_home, {"update_id": "web", "status": "queued", "queued_at": update_module.time.time()})
    with pytest.raises(UpdateError, match="no longer available"):
        update_module.run_update(replace(options, update_id="other"))
    assert read_update_status(agent_home)["status"] == "queued"
    update_module.run_update(replace(options, update_id="web"))
    assert read_update_status(agent_home)["update_id"] == "web"
    assert read_update_status(agent_home)["status"] == "updated"


def test_systemd_rollback_preserves_original_error_when_refresh_fails(prepared_update, monkeypatch) -> None:
    agent_home, app_dir, options = prepared_update
    events = []
    monkeypatch.setattr(update_module, "is_systemd_service_active", lambda: True)
    monkeypatch.setattr(update_module, "stop_systemd_service", lambda: True)
    monkeypatch.setattr(update_module, "restart_systemd_service", lambda: events.append("restart") or True)

    def fail_init(install):
        raise UpdateError("initialization failed")

    def fail_refresh(install):
        raise RuntimeError("refresh failed")

    monkeypatch.setattr(update_module, "initialize_app", fail_init)
    monkeypatch.setattr(update_module, "refresh_systemd_unit", fail_refresh)
    with pytest.raises(UpdateError, match="initialization failed"):
        update_module.run_update(options)
    assert events == ["restart"]
    assert (app_dir / "marker.txt").read_text() == "old"
    assert read_update_status(agent_home)["error"] == "initialization failed"


def test_update_aborts_if_systemd_stop_fails(prepared_update, monkeypatch) -> None:
    _, app_dir, options = prepared_update
    events = []
    monkeypatch.setattr(update_module, "is_systemd_service_active", lambda: True)
    monkeypatch.setattr(update_module, "stop_systemd_service", lambda: False)
    monkeypatch.setattr(update_module, "restart_systemd_service", lambda: events.append("restart"))
    with pytest.raises(UpdateError, match="Could not stop systemd"):
        update_module.run_update(options)
    assert events == []
    assert (app_dir / "marker.txt").read_text() == "old"


@pytest.mark.parametrize("stage", ["sync_app", "initialize_app"])
def test_repeated_update_failures_keep_only_latest_backup(prepared_update, monkeypatch, stage) -> None:
    agent_home, app_dir, options = prepared_update
    old_backup = agent_home / "backup" / "app-0.1.1-old"
    write_project(old_backup, version="0.1.1", marker="older")

    def fail(install):
        raise UpdateError("installation failed")

    monkeypatch.setattr(update_module, stage, fail)
    previous_backup = old_backup
    for _ in range(3):
        with pytest.raises(UpdateError, match="installation failed"):
            update_module.run_update(options)
        backups = list((agent_home / "backup").iterdir())
        assert len(backups) == 1
        assert backups[0] != previous_backup
        assert not previous_backup.exists()
        assert (backups[0] / "marker.txt").read_text() == "old"
        assert (app_dir / "marker.txt").read_text() == "old"
        previous_backup = backups[0]


def test_backup_cleanup_failure_does_not_hide_update_error(prepared_update, monkeypatch) -> None:
    agent_home, _, options = prepared_update

    def fail_init(install):
        raise UpdateError("initialization failed")

    def fail_cleanup(*args, **kwargs):
        raise PermissionError("backup locked")

    monkeypatch.setattr(update_module, "initialize_app", fail_init)
    monkeypatch.setattr(update_module, "prune_backups", fail_cleanup)
    messages = []
    with pytest.raises(UpdateError, match="initialization failed"):
        update_module.run_update(options, echo=messages.append)
    assert any("backup locked" in message for message in messages)
    assert len(list((agent_home / "backup").iterdir())) == 1
    assert read_update_status(agent_home)["error"] == "initialization failed"


@pytest.mark.parametrize("metadata", [None, "not valid TOML"])
def test_queued_worker_records_invalid_metadata_failure_immediately(prepared_update, metadata) -> None:
    from dataclasses import replace

    agent_home, app_dir, options = prepared_update
    write_update_status(agent_home, {"update_id": "job", "status": "queued", "queued_at": update_module.time.time()})
    if metadata is None:
        (app_dir / "pyproject.toml").unlink()
    else:
        (app_dir / "pyproject.toml").write_text(metadata)
    with pytest.raises(UpdateError, match="Could not (read|parse)"):
        update_module.run_update(replace(options, update_id="job"))
    status = read_update_status(agent_home)
    assert status["status"] == "failed"
    assert "pyproject.toml" in status["error"]


def test_version_read_failure_after_validation_is_recorded(prepared_update, monkeypatch) -> None:
    from dataclasses import replace

    agent_home, _, options = prepared_update
    write_update_status(agent_home, {"update_id": "job", "status": "queued", "queued_at": update_module.time.time()})

    def fail_read(root):
        raise UpdateError("version could not be read")

    monkeypatch.setattr(update_module, "read_project_version", fail_read)
    with pytest.raises(UpdateError, match="version could not be read"):
        update_module.run_update(replace(options, update_id="job"))
    status = read_update_status(agent_home)
    assert status["status"] == "failed"
    assert status["error"] == "version could not be read"


def test_validation_failure_does_not_overwrite_another_queued_job(prepared_update) -> None:
    from dataclasses import replace

    agent_home, app_dir, options = prepared_update
    write_update_status(agent_home, {"update_id": "other", "status": "queued", "queued_at": update_module.time.time()})
    (app_dir / "pyproject.toml").unlink()
    with pytest.raises(UpdateError):
        update_module.run_update(replace(options, update_id="job"))
    assert read_update_status(agent_home)["status"] == "queued"

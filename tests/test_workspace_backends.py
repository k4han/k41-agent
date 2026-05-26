import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text

import agent.modules.workspaces.local_backend as local_backend_module
from agent.modules.workspaces import (
    WorkspaceRef,
    resolve_workspace_ref,
    workspace_ref_from_local_path,
)
from agent.modules.workspaces.local_backend import LocalWorkspaceBackend
from agent.modules.workspaces.migrations import migrate_workspace_tables


def test_workspace_ref_normalizes_local_path(tmp_path):
    workspace = workspace_ref_from_local_path(str(tmp_path), label="repo")

    assert workspace.backend == "local"
    assert workspace.locator == str(tmp_path.resolve())
    assert workspace.label == "repo"
    assert workspace.metadata == {}
    assert resolve_workspace_ref(workspace.model_dump()) == workspace


def test_workspace_display_label_compacts_local_root(tmp_path):
    workspace = workspace_ref_from_local_path(str(tmp_path))

    assert workspace.display_label() == f"{tmp_path.name}/"


def test_workspace_display_label_keeps_custom_label(tmp_path):
    workspace = workspace_ref_from_local_path(str(tmp_path), label="octo/example")

    assert workspace.display_label() == "octo/example"


def test_workspace_ref_from_local_path_preserves_metadata(tmp_path):
    workspace = workspace_ref_from_local_path(
        str(tmp_path),
        label="repo",
        metadata={"source": "github", "branch": "main"},
    )

    assert workspace.metadata == {"source": "github", "branch": "main"}
    assert resolve_workspace_ref(workspace.model_dump()) == workspace


def test_workspace_ref_normalizes_model_instances(tmp_path):
    raw_workspace = WorkspaceRef(
        backend="local",
        locator=str(tmp_path / "nested" / ".."),
        label="repo",
        metadata={"source": "ui"},
    )

    workspace = resolve_workspace_ref(raw_workspace)

    assert workspace.locator == str(tmp_path.resolve())
    assert workspace.label == "repo"
    assert workspace.metadata == {"source": "ui"}


def test_local_workspace_backend_file_operations_and_path_guard(tmp_path):
    workspace = workspace_ref_from_local_path(str(tmp_path))
    backend = LocalWorkspaceBackend(workspace)

    result = backend.write_text("src/app.py", "print('hello')\n")

    assert result.startswith("[OK] Wrote file:")
    assert backend.read_text("src/app.py") == "print('hello')\n"
    assert backend.list_files("src") == "app.py"
    with pytest.raises(ValueError, match="Path escapes working directory"):
        backend.read_text("../secret.txt")


def test_local_workspace_backend_execute_uses_safe_workspace(
    monkeypatch,
    tmp_path,
):
    workspace = workspace_ref_from_local_path(str(tmp_path))
    backend = LocalWorkspaceBackend(workspace)
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return SimpleNamespace(stdout="ok", stderr="", returncode=0)

    monkeypatch.setattr(local_backend_module.subprocess, "run", fake_run)

    result = backend.execute("echo ok", timeout=12)

    assert result.output == "ok"
    assert result.exit_code == 0
    assert captured["command"] == "echo ok"
    assert captured["cwd"] == str(tmp_path.resolve())
    assert captured["timeout"] == 12


def test_workspace_migration_backfills_legacy_tables(tmp_path):
    db_path = tmp_path / "legacy.db"
    database_url = f"sqlite:///{db_path.as_posix()}"
    engine = create_engine(database_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE thread_workspaces ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "thread_id VARCHAR(512), "
                    "working_dir TEXT, "
                    "created_at TEXT, "
                    "updated_at TEXT)"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO thread_workspaces "
                    "(thread_id, working_dir, created_at, updated_at) "
                    "VALUES ('thread-1', :working_dir, '', '')"
                ),
                {"working_dir": str(tmp_path)},
            )
            conn.execute(
                text(
                    "CREATE TABLE background_tasks ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "thread_id VARCHAR(512), "
                    "working_dir TEXT, "
                    "created_at TEXT, "
                    "updated_at TEXT)"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO background_tasks "
                    "(thread_id, working_dir, created_at, updated_at) "
                    "VALUES ('task-1', :working_dir, '', '')"
                ),
                {"working_dir": str(tmp_path / "task")},
            )
    finally:
        engine.dispose()

    migrate_workspace_tables(database_url)

    engine = create_engine(database_url)
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT workspace_backend, workspace_locator, "
                    "workspace_label, workspace_metadata_json "
                    "FROM thread_workspaces WHERE thread_id = 'thread-1'"
                )
            ).one()
            task_row = conn.execute(
                text(
                    "SELECT workspace_backend, workspace_locator, "
                    "workspace_label, workspace_metadata_json "
                    "FROM background_tasks WHERE thread_id = 'task-1'"
                )
            ).one()
    finally:
        engine.dispose()

    assert row.workspace_backend == "local"
    assert row.workspace_locator == str(tmp_path)
    assert row.workspace_label == str(tmp_path)
    assert json.loads(row.workspace_metadata_json) == {}
    assert task_row.workspace_backend == "local"
    assert task_row.workspace_locator == str(tmp_path / "task")
    assert task_row.workspace_label == str(tmp_path / "task")
    assert json.loads(task_row.workspace_metadata_json) == {}

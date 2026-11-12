"""Exercise persistence snapshots against actual SQLite and encrypted secrets."""

import json
import sqlite3
from contextlib import closing

import pytest

from agent.bootstrap import update_backup as backup
from agent.shared.infrastructure.db import engine
from agent.shared.infrastructure.secret_cipher import SecretCipher


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    home = tmp_path / "runtime"
    (home / "data").mkdir(parents=True)
    app = tmp_path / "app"
    app.mkdir()
    directory = tmp_path / "backup"
    directory.mkdir()
    database = home / "data" / "state.db"
    monkeypatch.setattr(backup, "runtime_home", lambda: home)
    monkeypatch.setattr(
        engine, "DEFAULT_DATABASE_URL", f"sqlite+aiosqlite:///{database.as_posix()}"
    )
    return home, app, directory, database


def test_restore_recovers_database_configuration_and_matching_encryption_key(runtime):
    home, app, directory, database = runtime
    config = "host: 127.0.0.1\nsecurity:\n  jwt_secret: private-test-secret\n"
    (home / "config.yaml").write_text(config)
    (home / ".env").write_text("SERVER_SETTING=original")
    (app / ".env").write_text("APP_SETTING=original")
    cipher = SecretCipher(home / "data" / "runtime_config.key")
    encrypted = cipher.encrypt("original-provider-key")
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE conversations (message TEXT, credential TEXT)")
        connection.execute(
            "INSERT INTO conversations VALUES (?, ?)", ("saved chat", encrypted)
        )
        connection.commit()
        snapshot = backup.backup_runtime(app, directory)
        connection.execute("DROP TABLE conversations")
        connection.commit()
    (home / "config.yaml").write_text("host: 0.0.0.0")
    (home / "data" / "runtime_config.key").unlink()
    cipher.encrypt("replacement-key")
    (app / ".env").unlink()
    backup.restore_runtime(snapshot)
    with closing(sqlite3.connect(database)) as connection:
        message, recovered = connection.execute(
            "SELECT * FROM conversations"
        ).fetchone()
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    assert message == "saved chat"
    assert cipher.decrypt(recovered) == "original-provider-key"
    assert (home / "config.yaml").read_text() == config
    assert (home / ".env").read_text() == "SERVER_SETTING=original"
    assert (app / ".env").read_text() == "APP_SETTING=original"
    assert (
        json.loads((snapshot.directory / "manifest.json").read_text())["version"] == 1
    )


def test_failed_first_start_removes_new_state_and_key(runtime):
    home, app, directory, database = runtime
    snapshot = backup.backup_runtime(app, directory)
    database.write_bytes(b"new database")
    (home / "config.yaml").write_text("host: 0.0.0.0")
    (home / "data" / "runtime_config.key").write_bytes(b"new key")
    backup.restore_runtime(snapshot)
    assert not database.exists()
    assert not (home / "config.yaml").exists()
    assert not (home / "data" / "runtime_config.key").exists()


def test_postgres_snapshot_requires_backup_and_restore_clients(runtime, monkeypatch):
    home, app, directory, _ = runtime
    (home / "config.yaml").write_text(
        "database:\n  url: postgresql+asyncpg://user:secret@localhost/test\n"
    )
    monkeypatch.setattr(backup.shutil, "which", lambda command: None)
    with pytest.raises(RuntimeError, match="pg_dump and pg_restore"):
        backup.backup_runtime(app, directory)


@pytest.mark.parametrize(
    "database_target, expected_database",
    [
        ("test?sslmode=require", "test"),
        ("test?sslmode=require&dbname=restore_target", "restore_target"),
        ("?sslmode=require", ""),
    ],
)
def test_postgres_clients_receive_credentials_only_through_environment(
    runtime, monkeypatch, database_target, expected_database
):
    home, app, directory, _ = runtime
    monkeypatch.delenv("PGDATABASE", raising=False)
    (home / "config.yaml").write_text(
        f"database:\n  url: postgresql+asyncpg://user:private@db.example/{database_target}\n"
    )
    monkeypatch.setattr(backup.shutil, "which", lambda command: command)
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        assert "private" not in " ".join(command)
        assert kwargs["env"]["PGPASSWORD"] == "private"
        assert kwargs["env"]["PGSSLMODE"] == "require"
        if command[0] == "pg_dump":
            from pathlib import Path

            Path(command[-1]).write_bytes(b"custom dump")

    monkeypatch.setattr(backup.subprocess, "run", run)
    snapshot = backup.backup_runtime(app, directory)
    backup.restore_runtime(snapshot)
    assert calls[1][0][0] == "pg_restore"
    assert "--single-transaction" in calls[1][0]
    restore_command, restore_options = calls[1]
    assert "--dbname" in restore_command
    assert restore_command[restore_command.index("--dbname") + 1] == expected_database
    assert restore_options["env"].get("PGDATABASE", "") == expected_database


def test_malformed_config_cannot_snapshot_the_wrong_database(runtime):
    home, app, directory, _ = runtime
    (home / "config.yaml").write_text("database: [invalid")
    with pytest.raises(Exception):
        backup.backup_runtime(app, directory)
    assert not (directory / backup.BACKUP_DIRECTORY).exists()

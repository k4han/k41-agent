"""Private, consistent persistence snapshots used by the application updater."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.engine import make_url

from agent.shared.infrastructure.subprocess_utils import hidden_subprocess_kwargs

BACKUP_DIRECTORY = ".runtime-backup"


@dataclass(frozen=True)
class RuntimeBackup:
    directory: Path
    runtime_home: Path
    app_dir: Path
    database_url: str
    database_existed: bool
    files: tuple[tuple[Path, str, bool], ...]


def runtime_home() -> Path:
    from agent.shared.infrastructure.config_file import DEFAULT_CONFIG_PATH

    return DEFAULT_CONFIG_PATH.parent


def _private_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    destination.chmod(0o600)


def _postgres_environment(database_url: str) -> dict[str, str]:
    parsed = make_url(database_url)
    env = os.environ.copy()
    for name, value in {
        "PGHOST": parsed.host,
        "PGPORT": parsed.port,
        "PGUSER": parsed.username,
        "PGPASSWORD": parsed.password,
        "PGDATABASE": parsed.database,
    }.items():
        if value is not None:
            env[name] = str(value)
    query_variables = {
        "host": "PGHOST",
        "port": "PGPORT",
        "user": "PGUSER",
        "password": "PGPASSWORD",
        "dbname": "PGDATABASE",
        "ssl": "PGSSLMODE",
        "sslmode": "PGSSLMODE",
        "sslcert": "PGSSLCERT",
        "sslkey": "PGSSLKEY",
        "sslrootcert": "PGSSLROOTCERT",
        "connect_timeout": "PGCONNECT_TIMEOUT",
        "options": "PGOPTIONS",
        "application_name": "PGAPPNAME",
        "service": "PGSERVICE",
        "target_session_attrs": "PGTARGETSESSIONATTRS",
        "channel_binding": "PGCHANNELBINDING",
    }
    for name, value in parsed.query.items():
        if name not in query_variables or not isinstance(value, str):
            raise RuntimeError(
                "Unsupported database connection option for PostgreSQL backup."
            )
        env[query_variables[name]] = value
    return env


def _postgres_command(command: list[str], database_url: str) -> None:
    try:
        env = _postgres_environment(database_url)
        if command[0] == "pg_restore":
            # pg_restore requires --dbname to restore directly into a database.
            # An empty value preserves libpq defaults when no database is set.
            command = [command[0], "--dbname", env.get("PGDATABASE", ""), *command[1:]]
        subprocess.run(
            command,
            env=env,
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=300,
            **hidden_subprocess_kwargs(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        # Database clients can echo credentials or connection options in errors.
        raise RuntimeError(
            f"{command[0]} failed; the persistence snapshot was not completed."
        ) from exc


def backup_runtime(app_dir: Path, backup_path: Path) -> RuntimeBackup:
    """Snapshot before migrations; failure prevents replacing the application."""
    from agent.bootstrap.container import resolve_database_url
    from agent.shared.config.factory import create_config_service

    home = runtime_home()
    config = create_config_service(yaml_path=home / "config.yaml")
    # A malformed operator config must never silently select another database.
    if (home / "config.yaml").exists():
        import yaml

        data = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise RuntimeError("Cannot snapshot an invalid config.yaml.")
    database_url = resolve_database_url(config)
    parsed = make_url(database_url)
    directory = backup_path / BACKUP_DIRECTORY
    directory.mkdir(mode=0o700)
    sources = [
        home / "config.yaml",
        home / ".env",
        home / "data" / "runtime_config.key",
        app_dir / ".env",
    ]
    sources.extend(path for path in app_dir.glob(".env.*") if path.is_file())
    files = []
    for index, source in enumerate(dict.fromkeys(sources)):
        name = f"file-{index}"
        existed = source.is_file()
        files.append((source, name, existed))
        if existed:
            _private_copy(source, directory / name)

    if parsed.drivername.startswith("sqlite"):
        database = Path(parsed.database)
        existed = database.exists()
        if existed:
            with closing(
                sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
            ) as source:
                with closing(
                    sqlite3.connect(directory / "database.sqlite")
                ) as destination:
                    source.backup(destination)
                    if destination.execute("PRAGMA integrity_check").fetchone() != (
                        "ok",
                    ):
                        raise RuntimeError(
                            "The SQLite snapshot did not pass its integrity check."
                        )
            (directory / "database.sqlite").chmod(0o600)
    else:
        if not all(shutil.which(tool) for tool in ("pg_dump", "pg_restore")):
            raise RuntimeError(
                "PostgreSQL updates require pg_dump and pg_restore on PATH."
            )
        existed = True
        _postgres_command(
            ["pg_dump", "--format=custom", "--file", str(directory / "database.dump")],
            database_url,
        )
        (directory / "database.dump").chmod(0o600)

    snapshot = RuntimeBackup(
        directory, home, app_dir, database_url, existed, tuple(files)
    )
    manifest = {
        "version": 1,
        "database_url": database_url,
        "database_existed": existed,
        "files": [
            {"path": str(path), "backup": name, "existed": present}
            for path, name, present in files
        ],
    }
    manifest_path = directory / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    manifest_path.chmod(0o600)
    return snapshot


def restore_configuration(snapshot: RuntimeBackup) -> None:
    """Restore secrets omitted from the source archive before starting either version."""
    for path, name, existed in snapshot.files:
        if existed:
            _private_copy(snapshot.directory / name, path)
        else:
            path.unlink(missing_ok=True)


def restore_runtime(snapshot: RuntimeBackup) -> None:
    """Restore only after every process using the database has stopped."""
    parsed = make_url(snapshot.database_url)
    if parsed.drivername.startswith("sqlite"):
        database = Path(parsed.database)
        database.parent.mkdir(parents=True, exist_ok=True)
        if snapshot.database_existed:
            temporary = database.with_name(database.name + ".restore")
            _private_copy(snapshot.directory / "database.sqlite", temporary)
            temporary.replace(database)
        else:
            database.unlink(missing_ok=True)
        for suffix in ("-wal", "-shm", "-journal"):
            Path(str(database) + suffix).unlink(missing_ok=True)
    else:
        _postgres_command(
            [
                "pg_restore",
                "--clean",
                "--if-exists",
                "--no-owner",
                "--no-privileges",
                "--exit-on-error",
                "--single-transaction",
                str(snapshot.directory / "database.dump"),
            ],
            snapshot.database_url,
        )
    restore_configuration(snapshot)

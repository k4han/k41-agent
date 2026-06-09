import shutil
import sys
import uuid
from pathlib import Path

from typer.testing import CliRunner

import agent.bootstrap.cli as cli_module


runner = CliRunner()


def test_init_registers_orm_models_before_creating_tables(
    monkeypatch,
):
    temp_path = Path(".tmp_bootstrap_cli") / uuid.uuid4().hex
    temp_path.mkdir(parents=True, exist_ok=True)
    try:
        home_dir = temp_path / "home"
        home_dir.mkdir()

        captured: dict[str, object] = {}
        database_url = "sqlite:///ignored.sqlite3"

        def fake_create_tables(database_url: str, metadata=None) -> None:
            captured["database_url"] = database_url
            captured["tables"] = sorted(metadata.tables.keys())

        monkeypatch.setattr(cli_module.Path, "home", lambda: home_dir)
        monkeypatch.setattr(cli_module, "get_database_url", lambda: database_url)
        monkeypatch.setattr(cli_module, "create_tables", fake_create_tables)

        result = runner.invoke(cli_module.app, ["init"])

        assert result.exit_code == 0, result.output
        assert "Database tables created" in result.output
        assert captured["database_url"] == database_url
        assert {"admin_credentials", "users", "bot_settings", "user_preferences"} <= set(captured["tables"])
    finally:
        shutil.rmtree(temp_path, ignore_errors=True)


def test_version_flag():
    result = runner.invoke(cli_module.app, ["--version"])
    assert result.exit_code == 0
    assert "0.1.1" in result.output


def test_help_output():
    result = runner.invoke(cli_module.app, ["--help"])
    assert result.exit_code == 0
    assert "Kai Agent CLI" in result.output


def test_daemon_command_uses_module_entrypoint(monkeypatch):
    monkeypatch.setattr(cli_module.sys, "executable", "python.exe")
    monkeypatch.setattr(
        cli_module.sys,
        "argv",
        [r"C:\venv\Scripts\k41.exe", "--verbose"],
    )

    assert cli_module._daemon_command() == [
        "python.exe",
        "-m",
        "agent.bootstrap.cli",
        "--verbose",
    ]


def test_background_python_executable_prefers_pythonw_on_windows(tmp_path):
    scripts_dir = tmp_path / "Scripts"
    scripts_dir.mkdir()
    python = scripts_dir / "python.exe"
    pythonw = scripts_dir / "pythonw.exe"
    python.write_text("")
    pythonw.write_text("")

    assert (
        cli_module._background_python_executable(str(python), is_windows=True)
        == str(pythonw)
    )


def test_is_process_alive_uses_psutil_pid_exists(monkeypatch):
    class FakePsutil:
        @staticmethod
        def pid_exists(pid: int) -> bool:
            return pid == 123

    monkeypatch.setitem(sys.modules, "psutil", FakePsutil)

    assert cli_module._is_process_alive(123) is True
    assert cli_module._is_process_alive(456) is False


def test_health_url_uses_loopback_for_wildcard_hosts():
    assert cli_module._base_url("0.0.0.0", 8000) == "http://127.0.0.1:8000"
    assert cli_module._health_url("0.0.0.0", 8000) == "http://127.0.0.1:8000/health"
    assert cli_module._health_url("::", 8000) == "http://127.0.0.1:8000/health"


def test_health_url_brackets_ipv6_hosts():
    assert cli_module._health_url("::1", 8000) == "http://[::1]:8000/health"

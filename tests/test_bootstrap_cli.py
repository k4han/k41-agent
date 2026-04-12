import shutil
import uuid
from pathlib import Path

from click.testing import CliRunner

import agent.bootstrap.cli as cli_module


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

        runner = CliRunner()
        result = runner.invoke(cli_module.cli, ["init"])

        assert result.exit_code == 0, result.output
        assert "[OK] Database tables created" in result.output
        assert captured["database_url"] == database_url
        assert {"admin_credentials", "users", "bot_settings", "user_preferences"} <= set(captured["tables"])
    finally:
        shutil.rmtree(temp_path, ignore_errors=True)

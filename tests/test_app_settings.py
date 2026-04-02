from __future__ import annotations

import textwrap

from pytest import MonkeyPatch

from agent.bootstrap.settings import BootstrapConfig, load_bootstrap_config


def test_load_bootstrap_config_reads_env_overrides(monkeypatch: MonkeyPatch, tmp_path):
    monkeypatch.setenv("HOST", "127.0.0.1")
    monkeypatch.setenv("PORT", "9000")
    monkeypatch.setenv("ENABLE_WEB", "true")
    monkeypatch.setenv("ENABLE_API", "false")
    monkeypatch.setenv("ENABLE_DASHBOARD", "true")
    monkeypatch.setenv("ENABLE_TELEGRAM", "false")
    monkeypatch.setenv("ENABLE_DISCORD", "false")

    config = load_bootstrap_config(path=tmp_path / "missing.yml")

    assert config == BootstrapConfig(
        host="127.0.0.1",
        port=9000,
        enable_web=True,
        enable_api=False,
        enable_dashboard=True,
    )


def test_load_bootstrap_config_reads_file_and_ignores_runtime_keys(
    monkeypatch: MonkeyPatch,
    tmp_path,
):
    for var in ("HOST", "PORT", "ENABLE_WEB", "ENABLE_API", "ENABLE_DASHBOARD"):
        monkeypatch.delenv(var, raising=False)

    config_path = tmp_path / "config.yml"
    config_path.write_text(
        textwrap.dedent("""\
        host: 127.0.0.1
        port: 3000
        enable_web: false
        enable_api: true
        enable_dashboard: false
        channels:
          telegram:
            enabled: false
        """),
        encoding="utf-8",
    )

    config = load_bootstrap_config(path=config_path)

    assert config == BootstrapConfig(
        host="127.0.0.1",
        port=3000,
        enable_web=False,
        enable_api=True,
        enable_dashboard=False,
    )


def test_load_bootstrap_config_uses_defaults(monkeypatch: MonkeyPatch, tmp_path):
    for var in ("HOST", "PORT", "ENABLE_WEB", "ENABLE_API", "ENABLE_DASHBOARD"):
        monkeypatch.delenv(var, raising=False)

    config = load_bootstrap_config(path=tmp_path / "missing.yml")

    assert config == BootstrapConfig(
        host="0.0.0.0",
        port=8000,
        enable_web=True,
        enable_api=True,
        enable_dashboard=True,
    )

from __future__ import annotations

import textwrap
from pathlib import Path

from pytest import MonkeyPatch

from agent.bootstrap.settings import BootstrapConfig, load_bootstrap_config


def test_load_bootstrap_config_reads_config_file(monkeypatch: MonkeyPatch, tmp_path):
    """Test that load_bootstrap_config reads from ConfigService."""
    # Reset singleton
    import agent.shared.config.service as service_module
    import agent.shared.config.yaml_source as yaml_module
    monkeypatch.setattr(service_module, "_config_service", None)

    # Create test config
    k41_dir = tmp_path / ".k41-agent"
    k41_dir.mkdir()
    config_path = k41_dir / "config.yaml"
    config_path.write_text(
        textwrap.dedent("""\
        host: 127.0.0.1
        port: 3000
        enable_web: false
        enable_api: true
        enable_dashboard: false
        """),
        encoding="utf-8",
    )

    # Mock DEFAULT_CONFIG_PATH
    monkeypatch.setattr(yaml_module, "DEFAULT_CONFIG_PATH", config_path)

    config = load_bootstrap_config()

    assert config == BootstrapConfig(
        host="127.0.0.1",
        port=3000,
        enable_web=False,
        enable_api=True,
        enable_dashboard=False,
    )


def test_load_bootstrap_config_uses_defaults(monkeypatch: MonkeyPatch, tmp_path):
    """Test that load_bootstrap_config uses defaults when no config exists."""
    # Reset singleton
    import agent.shared.config.service as service_module
    import agent.shared.config.yaml_source as yaml_module
    monkeypatch.setattr(service_module, "_config_service", None)

    # Point to non-existent config
    config_path = tmp_path / "nonexistent.yaml"
    monkeypatch.setattr(yaml_module, "DEFAULT_CONFIG_PATH", config_path)

    config = load_bootstrap_config()

    assert config == BootstrapConfig(
        host="0.0.0.0",
        port=8000,
        enable_web=True,
        enable_api=True,
        enable_dashboard=True,
    )
    assert config_path.exists()
    assert "enable_dashboard: true" in config_path.read_text(encoding="utf-8")

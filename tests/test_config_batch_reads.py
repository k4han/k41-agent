"""Configuration batches should inspect each source once and retain live reloads."""

import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from agent.shared.config.default_source import DefaultConfigSource
from agent.shared.config.service import ConfigService
from agent.shared.config.yaml_source import YamlConfigSource


@pytest.mark.parametrize("method", ["list_all", "list_all_by_source", "get_runtime_settings",
                                   "get_settings_overview_and_sources"])
def test_runtime_batches_stat_yaml_once(tmp_path, monkeypatch, method):
    path = tmp_path / "config.yaml"
    path.write_text("host: 127.0.0.1\n", encoding="utf-8")
    source = YamlConfigSource(path)
    source.get_all()
    defaults = DefaultConfigSource()
    defaults.register_defaults({f"tools.extra_{index}.enabled": True for index in range(300)})
    service = ConfigService([defaults, source])
    original_stat = Path.stat
    checks = Mock()

    def counted_stat(target, *args, **kwargs):
        if target == path:
            checks()
        return original_stat(target, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", counted_stat)
    getattr(service, method)()
    assert checks.call_count == 1


def test_next_batch_observes_external_yaml_changes_immediately(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("host: first\n", encoding="utf-8")
    service = ConfigService([DefaultConfigSource(), YamlConfigSource(path)])
    assert service.list_all()["host"].value == "first"
    timestamp = path.stat()
    path.write_text("host: second\n", encoding="utf-8")
    os.utime(path, ns=(timestamp.st_atime_ns, timestamp.st_mtime_ns + 1_000_000_000))
    assert service.list_all()["host"].value == "second"

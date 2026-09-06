from __future__ import annotations

import os
import sys
from asyncio import run
from importlib.machinery import ModuleSpec

import importlib.util
import pytest

from agent.shared.integrations import (
    IntegrationDescriptor,
    IntegrationInstallResult,
    IntegrationUnavailableError,
    LazyIntegrationRegistry,
)


OPTIONAL_MODULES = ("aiogram", "discord", "modal", "daytona")


def build_demo_instance() -> object:
    return object()


def demo_loader() -> str:
    return "ok"


class _Config:
    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    def get_str(self, key: str, default: str = "") -> str:
        return self._values.get(key, default)


def _reset_channel_registry(name: str = "telegram") -> None:
    from agent.modules.channels.registry import get_channel_registry

    registry = get_channel_registry()
    registry.unregister(name)
    registry._lazy.clear_instances()


def test_public_imports_do_not_load_optional_integration_sdks() -> None:
    for module_name in OPTIONAL_MODULES:
        sys.modules.pop(module_name, None)

    import agent.bootstrap.runtime  # noqa: F401
    import agent.modules.channels  # noqa: F401
    import agent.modules.workspaces  # noqa: F401

    assert {
        module_name: module_name in sys.modules
        for module_name in OPTIONAL_MODULES
    } == {
        "aiogram": False,
        "discord": False,
        "modal": False,
        "daytona": False,
    }


def test_catalog_availability_does_not_auto_install_optional_dependency(monkeypatch) -> None:
    from agent.modules.channels import get_registered_channel_catalog

    _reset_channel_registry("telegram")

    original_find_spec = importlib.util.find_spec

    def fake_find_spec(name: str, package: str | None = None) -> ModuleSpec | None:
        if name == "aiogram":
            return None
        return original_find_spec(name, package)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)
    monkeypatch.setattr(
        "agent.shared.integrations.install_integration_extra",
        lambda extra: (_ for _ in ()).throw(AssertionError("unexpected install")),
    )

    catalog = get_registered_channel_catalog()

    telegram = next(item for item in catalog if item["name"] == "telegram")
    assert telegram["availability"] == {
        "available": False,
        "missing_import": "aiogram",
        "install_hint": "Install with: uv sync --extra channel-telegram",
    }


def test_lazy_registry_load_auto_installs_optional_dependency(monkeypatch) -> None:
    installed = False
    calls: list[str] = []
    original_find_spec = importlib.util.find_spec

    def fake_find_spec(name: str, package: str | None = None) -> ModuleSpec | None:
        if name == "demo_sdk":
            return ModuleSpec(name, loader=None) if installed else None
        return original_find_spec(name, package)

    def fake_install(extra: str) -> IntegrationInstallResult:
        nonlocal installed
        calls.append(extra)
        installed = True
        return IntegrationInstallResult(
            attempted=True,
            command=("uv", "sync", "--inexact", "--locked", "--extra", extra),
        )

    registry = LazyIntegrationRegistry("demo")
    registry.register(
        IntegrationDescriptor(
            kind="demo",
            name="sample",
            title="Sample",
            config_prefix="demo.sample",
            loader=f"{__name__}:build_demo_instance",
            dependency_imports=("demo_sdk",),
            install_extra="demo-extra",
        )
    )
    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)
    monkeypatch.setattr("agent.shared.integrations.install_integration_extra", fake_install)

    assert registry.load("sample") is registry.load("sample")
    assert calls == ["demo-extra"]


def test_lazy_registry_reports_auto_install_failure(monkeypatch) -> None:
    original_find_spec = importlib.util.find_spec

    def fake_find_spec(name: str, package: str | None = None) -> ModuleSpec | None:
        if name == "demo_sdk":
            return None
        return original_find_spec(name, package)

    def fake_install(extra: str) -> IntegrationInstallResult:
        return IntegrationInstallResult(
            attempted=True,
            command=("uv", "sync", "--inexact", "--locked", "--extra", extra),
            error="network unavailable",
        )

    registry = LazyIntegrationRegistry("demo")
    registry.register(
        IntegrationDescriptor(
            kind="demo",
            name="sample",
            title="Sample",
            config_prefix="demo.sample",
            loader=f"{__name__}:build_demo_instance",
            dependency_imports=("demo_sdk",),
            install_extra="demo-extra",
        )
    )
    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)
    monkeypatch.setattr("agent.shared.integrations.install_integration_extra", fake_install)

    with pytest.raises(IntegrationUnavailableError) as exc_info:
        registry.load("sample")

    details = exc_info.value.to_dict()
    assert details["status"] == "missing_dependency"
    assert details["missing_import"] == "demo_sdk"
    assert details["install_attempted"] is True
    assert details["install_command"] == [
        "uv",
        "sync",
        "--inexact",
        "--locked",
        "--extra",
        "demo-extra",
    ]
    assert details["install_error"] == "network unavailable"


def test_workspace_backend_resolve_loader_auto_installs_optional_dependency(
    monkeypatch,
) -> None:
    from agent.modules.workspaces.registry import (
        WorkspaceBackendDescriptor,
        WorkspaceBackendRegistry,
    )

    installed = False
    calls: list[str] = []
    original_find_spec = importlib.util.find_spec

    def fake_find_spec(name: str, package: str | None = None) -> ModuleSpec | None:
        if name == "workspace_sdk":
            return ModuleSpec(name, loader=None) if installed else None
        return original_find_spec(name, package)

    def fake_install(extra: str) -> IntegrationInstallResult:
        nonlocal installed
        calls.append(extra)
        installed = True
        return IntegrationInstallResult(
            attempted=True,
            command=("uv", "sync", "--inexact", "--locked", "--extra", extra),
        )

    registry = WorkspaceBackendRegistry()
    registry.register(
        WorkspaceBackendDescriptor(
            kind="workspace_backend",
            name="cloud",
            title="Cloud",
            config_prefix="workspace.cloud",
            loader=f"{__name__}:build_demo_instance",
            dependency_imports=("workspace_sdk",),
            install_extra="workspace-extra",
        )
    )
    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)
    monkeypatch.setattr("agent.shared.integrations.install_integration_extra", fake_install)

    resolved = registry.resolve_loader("cloud", f"{__name__}:demo_loader")

    assert resolved() == "ok"
    assert calls == ["workspace-extra"]


def test_channel_connection_auto_installs_then_tests_provider(monkeypatch) -> None:
    from agent.modules.channels import diagnostics
    from agent.modules.channels.diagnostics import test_channel_connection

    _reset_channel_registry("telegram")
    installed = False
    install_calls: list[str] = []
    requested_urls: list[str] = []
    original_find_spec = importlib.util.find_spec

    def fake_find_spec(name: str, package: str | None = None) -> ModuleSpec | None:
        if name == "aiogram":
            return ModuleSpec(name, loader=None) if installed else None
        return original_find_spec(name, package)

    def fake_install(extra: str) -> IntegrationInstallResult:
        nonlocal installed
        install_calls.append(extra)
        installed = True
        return IntegrationInstallResult(
            attempted=True,
            command=("uv", "sync", "--inexact", "--locked", "--extra", extra),
        )

    class FakeResponse:
        status_code = 200
        is_success = True

        def json(self):
            return {
                "ok": True,
                "result": {
                    "id": 123,
                    "username": "demo_bot",
                    "first_name": "Demo",
                },
            }

    class FakeAsyncClient:
        def __init__(self, *, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(self, url: str, **kwargs):
            del kwargs
            requested_urls.append(url)
            return FakeResponse()

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)
    monkeypatch.setattr("agent.shared.integrations.install_integration_extra", fake_install)
    monkeypatch.setattr(
        diagnostics,
        "get_config_service",
        lambda: _Config({"channels.telegram.bot_token": "token"}),
    )
    monkeypatch.setattr(diagnostics.httpx, "AsyncClient", FakeAsyncClient)

    result = run(test_channel_connection("telegram"))

    assert result.ok is True
    assert install_calls == ["channel-telegram"]
    assert requested_urls == ["https://api.telegram.org/bottoken/getMe"]


def test_channel_connection_reports_auto_install_failure(monkeypatch) -> None:
    from agent.modules.channels import diagnostics
    from agent.modules.channels.diagnostics import test_channel_connection

    _reset_channel_registry("telegram")
    original_find_spec = importlib.util.find_spec

    def fake_find_spec(name: str, package: str | None = None) -> ModuleSpec | None:
        if name == "aiogram":
            return None
        return original_find_spec(name, package)

    def fake_install(extra: str) -> IntegrationInstallResult:
        return IntegrationInstallResult(
            attempted=True,
            command=("uv", "sync", "--inexact", "--locked", "--extra", extra),
            error="network unavailable",
        )

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)
    monkeypatch.setattr("agent.shared.integrations.install_integration_extra", fake_install)
    monkeypatch.setattr(
        diagnostics,
        "get_config_service",
        lambda: _Config({"channels.telegram.bot_token": "token"}),
    )

    result = run(test_channel_connection("telegram"))

    assert result.ok is False
    assert result.details is not None
    assert result.details["status"] == "missing_dependency"
    assert result.details["name"] == "telegram"
    assert result.details["missing_import"] == "aiogram"
    assert result.details["install_hint"] == "Install with: uv sync --extra channel-telegram"
    assert result.details["install_attempted"] is True
    assert result.details["install_command"] == [
        "uv",
        "sync",
        "--inexact",
        "--locked",
        "--extra",
        "channel-telegram",
    ]
    assert result.details["install_error"] == "network unavailable"


def test_build_install_env_sets_virtual_env_and_path(monkeypatch) -> None:
    from agent.shared.integrations import _build_install_env

    monkeypatch.setattr(sys, "prefix", "/test/custom/prefix")
    monkeypatch.setattr(sys, "base_prefix", "/usr")
    monkeypatch.setattr(sys, "executable", "/test/custom/prefix/bin/python")

    env = _build_install_env("/opt/tools/uv")
    assert env["VIRTUAL_ENV"] == "/test/custom/prefix"
    assert os.path.normpath("/test/custom/prefix/bin") in env["PATH"]
    assert os.path.normpath("/opt/tools") in env["PATH"]


def test_find_uv_executable_falls_back_to_tools_dir(monkeypatch, tmp_path) -> None:
    from agent.shared.integrations import _find_uv_executable

    monkeypatch.setattr("shutil.which", lambda name: None)

    agent_home = tmp_path / "k41-agent"
    tools_dir = agent_home / "tools"
    tools_dir.mkdir(parents=True)
    exe_name = "uv.exe" if os.name == "nt" else "uv"
    fake_uv = tools_dir / exe_name
    fake_uv.write_text("fake uv")

    envs_dir = agent_home / "envs" / ("Scripts" if os.name == "nt" else "bin")
    envs_dir.mkdir(parents=True)
    fake_python = envs_dir / ("python.exe" if os.name == "nt" else "python")
    fake_python.write_text("fake python")

    monkeypatch.setattr(sys, "executable", str(fake_python))

    found = _find_uv_executable()
    assert found == str(fake_uv)


def test_install_integration_extra_passes_env_to_subprocess(monkeypatch) -> None:
    from agent.shared.integrations import install_integration_extra

    captured_kwargs: dict = {}

    def fake_subprocess_run(cmd, **kwargs):
        captured_kwargs.update(kwargs)
        captured_kwargs["cmd"] = cmd

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return Result()

    monkeypatch.setattr("subprocess.run", fake_subprocess_run)
    monkeypatch.setattr("agent.shared.integrations._find_uv_executable", lambda: "/bin/uv")
    monkeypatch.setattr("agent.shared.integrations._has_active_virtualenv", lambda: True)
    monkeypatch.setattr(sys, "prefix", "/env/active")
    monkeypatch.setattr(sys, "executable", "/env/active/bin/python")

    result = install_integration_extra("channel-telegram")
    assert result.attempted is True
    assert result.error == ""
    assert "env" in captured_kwargs
    assert captured_kwargs["env"]["VIRTUAL_ENV"] == "/env/active"

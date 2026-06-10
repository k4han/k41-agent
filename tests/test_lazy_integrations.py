from __future__ import annotations

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


OPTIONAL_MODULES = ("aiogram", "discord", "modal", "daytona", "openshell")


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
        "openshell": False,
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

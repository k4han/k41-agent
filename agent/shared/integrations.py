"""Shared lazy integration registry primitives."""

from __future__ import annotations

import importlib
import importlib.util
import os
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.shared.infrastructure.subprocess_utils import hidden_subprocess_kwargs


INSTALL_TIMEOUT_SECONDS = 600


@dataclass(frozen=True, slots=True)
class IntegrationInstallResult:
    attempted: bool
    command: tuple[str, ...] = ()
    error: str = ""

    def with_error(self, error: str) -> "IntegrationInstallResult":
        return IntegrationInstallResult(
            attempted=self.attempted,
            command=self.command,
            error=error,
        )


@dataclass(frozen=True, slots=True)
class IntegrationAvailability:
    available: bool
    missing_import: str = ""
    install_hint: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "available": self.available,
            "missing_import": self.missing_import,
            "install_hint": self.install_hint,
        }


class IntegrationUnavailableError(RuntimeError):
    def __init__(
        self,
        *,
        kind: str,
        name: str,
        missing_import: str,
        install_hint: str = "",
        install_attempted: bool = False,
        install_command: tuple[str, ...] = (),
        install_error: str = "",
    ) -> None:
        self.kind = kind
        self.name = name
        self.missing_import = missing_import
        self.install_hint = install_hint
        self.install_attempted = install_attempted
        self.install_command = install_command
        self.install_error = install_error
        message = f"{kind} integration '{name}' is unavailable"
        if missing_import:
            message = f"{message}: missing import '{missing_import}'"
        if install_hint:
            message = f"{message}. {install_hint}"
        if install_error:
            message = f"{message}. Auto-install failed: {install_error}"
        super().__init__(message)

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "status": "missing_dependency",
            "kind": self.kind,
            "name": self.name,
            "missing_import": self.missing_import,
            "install_hint": self.install_hint,
            "message": str(self),
        }
        if self.install_attempted:
            payload["install_attempted"] = True
            payload["install_command"] = list(self.install_command)
            payload["install_error"] = self.install_error
        return payload


@dataclass(frozen=True, slots=True)
class IntegrationDescriptor:
    kind: str
    name: str
    title: str
    config_prefix: str
    loader: str
    capabilities: frozenset[str] = frozenset()
    dependency_imports: tuple[str, ...] = ()
    install_extra: str = ""
    summary: str = ""
    tagline: str = ""
    settings_schema: tuple[Any, ...] = ()
    settings_sections: tuple[Any, ...] = ()

    @property
    def install_hint(self) -> str:
        if not self.install_extra:
            return ""
        return f"Install with: uv sync --extra {self.install_extra}"


_install_locks: dict[str, threading.Lock] = {}
_install_locks_guard = threading.Lock()


def install_integration_extra(extra: str) -> IntegrationInstallResult:
    normalized = str(extra or "").strip()
    if not normalized:
        return IntegrationInstallResult(attempted=False)

    with _install_locks_guard:
        lock = _install_locks.setdefault(normalized, threading.Lock())

    with lock:
        command = _build_uv_sync_command(normalized)
        project_root = _find_project_root()
        if project_root is None:
            return IntegrationInstallResult(
                attempted=True,
                command=command,
                error="Unable to find pyproject.toml for the current project.",
            )

        uv_executable = shutil.which("uv")
        if uv_executable is None:
            return IntegrationInstallResult(
                attempted=True,
                command=command,
                error="uv executable was not found on PATH.",
            )

        run_command = (uv_executable, *command[1:])
        try:
            completed = subprocess.run(
                run_command,
                cwd=str(project_root),
                capture_output=True,
                text=True,
                timeout=INSTALL_TIMEOUT_SECONDS,
                check=False,
                **hidden_subprocess_kwargs(),
            )
        except subprocess.TimeoutExpired as exc:
            return IntegrationInstallResult(
                attempted=True,
                command=command,
                error=f"uv sync timed out after {INSTALL_TIMEOUT_SECONDS} seconds: {exc}",
            )
        except OSError as exc:
            return IntegrationInstallResult(
                attempted=True,
                command=command,
                error=str(exc),
            )

        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            return IntegrationInstallResult(
                attempted=True,
                command=command,
                error=detail or f"uv sync exited with code {completed.returncode}.",
            )

        return IntegrationInstallResult(attempted=True, command=command)


def _build_uv_sync_command(extra: str) -> tuple[str, ...]:
    command = ["uv", "sync"]
    if _has_active_virtualenv():
        command.append("--active")
    command.extend(["--inexact", "--locked", "--extra", extra])
    return tuple(command)


def _has_active_virtualenv() -> bool:
    return bool(os.environ.get("VIRTUAL_ENV")) or sys.prefix != sys.base_prefix


def _find_project_root() -> Path | None:
    candidates = [Path.cwd(), Path(__file__).resolve()]
    seen: set[Path] = set()
    for candidate in candidates:
        current = candidate if candidate.is_dir() else candidate.parent
        for path in (current, *current.parents):
            if path in seen:
                continue
            seen.add(path)
            if (path / "pyproject.toml").is_file():
                return path
    return None


class LazyIntegrationRegistry:
    """Catalog integration descriptors and load implementations on demand."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self._descriptors: dict[str, IntegrationDescriptor] = {}
        self._instances: dict[str, Any] = {}

    def register(
        self,
        descriptor: IntegrationDescriptor,
        *,
        replace: bool = False,
    ) -> None:
        name = descriptor.name.strip().lower()
        if not name:
            raise ValueError("Integration name is required.")
        if descriptor.kind != self.kind:
            raise ValueError(
                f"Cannot register {descriptor.kind!r} descriptor in {self.kind!r} registry."
            )
        if name in self._descriptors and not replace:
            raise ValueError(f"{self.kind} integration '{name}' is already registered.")
        self._descriptors[name] = descriptor
        if replace:
            self._instances.pop(name, None)

    def get_descriptor(self, name: str) -> IntegrationDescriptor | None:
        return self._descriptors.get(name.strip().lower())

    def require_descriptor(self, name: str) -> IntegrationDescriptor:
        descriptor = self.get_descriptor(name)
        if descriptor is None:
            raise KeyError(
                f"{self.kind} integration '{name}' is not registered. "
                f"Available: {sorted(self._descriptors)}"
            )
        return descriptor

    def list_descriptors(self) -> list[IntegrationDescriptor]:
        return list(self._descriptors.values())

    def names(self) -> list[str]:
        return list(self._descriptors.keys())

    def availability(self, name: str) -> IntegrationAvailability:
        descriptor = self.require_descriptor(name)
        for import_name in descriptor.dependency_imports:
            if importlib.util.find_spec(import_name) is None:
                return IntegrationAvailability(
                    available=False,
                    missing_import=import_name,
                    install_hint=descriptor.install_hint,
                )
        return IntegrationAvailability(available=True)

    def ensure_available(self, name: str) -> IntegrationAvailability:
        descriptor = self.require_descriptor(name)
        availability, install_result = self._ensure_available(descriptor)
        if not availability.available:
            self._raise_unavailable(descriptor, availability, install_result)
        return availability

    def load(self, name: str) -> Any:
        normalized = name.strip().lower()
        if normalized in self._instances:
            return self._instances[normalized]

        factory = self.resolve(normalized)
        instance = factory()
        self._instances[normalized] = instance
        return instance

    def resolve(self, name: str) -> Any:
        normalized = name.strip().lower()
        descriptor = self.require_descriptor(normalized)
        return self.resolve_loader(normalized, descriptor.loader)

    def resolve_loader(self, name: str, loader: str) -> Any:
        normalized = name.strip().lower()
        descriptor = self.require_descriptor(normalized)
        availability, install_result = self._ensure_available(descriptor)
        if not availability.available:
            self._raise_unavailable(descriptor, availability, install_result)

        module_name, separator, attr_name = loader.partition(":")
        if not separator or not module_name or not attr_name:
            raise ValueError(
                f"Invalid loader for {descriptor.kind} integration '{descriptor.name}': "
                f"{loader!r}"
            )

        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            missing = exc.name or module_name
            raise IntegrationUnavailableError(
                kind=descriptor.kind,
                name=descriptor.name,
                missing_import=missing,
                install_hint=descriptor.install_hint,
                install_attempted=bool(
                    install_result and install_result.attempted
                ),
                install_command=install_result.command if install_result else (),
                install_error=install_result.error if install_result else "",
            ) from exc
        return getattr(module, attr_name)

    def clear_instances(self) -> None:
        self._instances.clear()

    def _ensure_available(
        self,
        descriptor: IntegrationDescriptor,
    ) -> tuple[IntegrationAvailability, IntegrationInstallResult | None]:
        availability = self.availability(descriptor.name)
        if availability.available:
            return availability, None
        if not descriptor.install_extra:
            return availability, None

        install_result = install_integration_extra(descriptor.install_extra)
        importlib.invalidate_caches()
        availability = self.availability(descriptor.name)
        if availability.available:
            return availability, install_result
        if install_result.attempted and not install_result.error:
            install_result = install_result.with_error(
                (
                    "uv sync completed but import "
                    f"'{availability.missing_import}' is still unavailable."
                )
            )
        return availability, install_result

    def _raise_unavailable(
        self,
        descriptor: IntegrationDescriptor,
        availability: IntegrationAvailability,
        install_result: IntegrationInstallResult | None = None,
    ) -> None:
        raise IntegrationUnavailableError(
            kind=descriptor.kind,
            name=descriptor.name,
            missing_import=availability.missing_import,
            install_hint=availability.install_hint,
            install_attempted=bool(install_result and install_result.attempted),
            install_command=install_result.command if install_result else (),
            install_error=install_result.error if install_result else "",
        )


__all__ = [
    "IntegrationAvailability",
    "IntegrationDescriptor",
    "IntegrationInstallResult",
    "IntegrationUnavailableError",
    "LazyIntegrationRegistry",
    "install_integration_extra",
]

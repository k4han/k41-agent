"""Exercise release verification and ownership of external ACP installations."""

import hashlib
import io
import json
import os
import threading
import zipfile

import httpx
import pytest

from agent.modules.acp import installations, management, releases
from agent.modules.acp.agents import ANTIGRAVITY_CLI_AGENT_ID, OPENCODE_AGENT_ID
from agent.modules.acp.errors import AcpError


@pytest.mark.parametrize("source,isolated_group,extended_path", [
    ("npm", False, False),
    ("pnpm", False, False),
    ("pnpm", True, False),
    pytest.param("pnpm", True, True, marks=pytest.mark.skipif(os.name != "nt", reason="Windows paths")),
])
def test_external_opencode_changes_only_verified_package(tmp_path, monkeypatch, source, isolated_group, extended_path):
    monkeypatch.setenv("K41_AGENT_HOME", str(tmp_path / "k41"))
    prefix = tmp_path / source
    if isolated_group:
        root, bin_dir = prefix / "install-group" / "node_modules", tmp_path / "bin"
    elif source == "npm" and os.name != "nt":
        root, bin_dir = prefix / "lib/node_modules", prefix / "bin"
    else:
        root, bin_dir = prefix / "node_modules", prefix
    name = "@opencode/cli"
    package = root / name
    binary = package / "bin" / "opencode"
    binary.parent.mkdir(parents=True)
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / "opencode.cmd"
    target = os.path.relpath(binary, bin_dir)
    shim.write_text(f'"%dp0%/{target}" %*', encoding="utf-8")
    metadata = package / "package.json"
    dependencies = {name: "2.4.0"}
    reported_path = f"\\\\?\\{package}" if extended_path else str(package)
    group = {name: {"version": "2.4.0", "path": reported_path}}
    if isolated_group:
        group["unrelated-global"] = {"version": "1.0.0"}
    group_manifest = root.parent / "package.json"
    commands = []

    def publish(version):
        binary.write_text(version, encoding="utf-8")
        metadata.write_text(json.dumps({"name": name, "version": version, "bin": {"opencode": "bin/opencode"}}), encoding="utf-8")

    def runner(_executable, command, *args, stop=None):
        if command == "prefix":
            return str(prefix)
        if command == "list":
            return json.dumps([{"path": str(prefix), "dependencies": group}])
        if command == "bin":
            return str(bin_dir)
        if command == "view":
            assert args == (f"{name}@2", "version", "--json")
            return json.dumps(["1.9.9", "2.4.0", "2.4.2", "2.5.0-beta.1", "3.0.0"])
        commands.append((command, *args))
        if command in {"install", "add"}:
            publish("2.4.2")
        else:
            group.pop(name)
            if not isolated_group:
                metadata.unlink()
            shim.unlink()
        return ""

    def which(command):
        if command == "opencode" and shim.is_file():
            return str(shim)
        return str(tmp_path / source) if command == source else None

    publish("2.4.0")
    if isolated_group:
        group_manifest.write_text(json.dumps({"dependencies": dependencies}), encoding="utf-8")
    monkeypatch.setattr(installations.shutil, "which", which)
    monkeypatch.setattr(installations, "run_package_manager", runner)
    monkeypatch.setattr(management, "run_package_manager", runner)
    monkeypatch.setattr(management, "inspect_executable", lambda *args, **kwargs: (binary.read_text(), None))
    agent_id = OPENCODE_AGENT_ID
    installation = installations.discover_installation(agent_id)
    assert installation.source == source
    assert management.apply_operation(agent_id, "update", installation.revision(), threading.Event()) == "2.4.2"
    updated = installations.discover_installation(agent_id)
    assert updated.package_version == "2.4.2"

    if source == "pnpm":
        if isolated_group:
            group_manifest.write_text(json.dumps({"dependencies": {**dependencies, "other-tool": "1.0.0"}}), encoding="utf-8")
        else:
            group["other-tool"] = {"version": "1.0.0"}
        assert installations.discover_installation(agent_id).source == "unknown"
        with pytest.raises(AcpError, match="installation changed"):
            management.apply_operation(agent_id, "uninstall", updated.revision(), threading.Event())
        if isolated_group:
            group_manifest.write_text(json.dumps({"dependencies": dependencies}), encoding="utf-8")
        else:
            del group["other-tool"]

    management.apply_operation(agent_id, "uninstall", updated.revision(), threading.Event())
    assert installations.discover_installation(agent_id).path is None
    assert metadata.is_file() is isolated_group
    assert commands == [
        ("install" if source == "npm" else "add", "--global", f"{name}@2.4.2"),
        ("uninstall" if source == "npm" else "remove", "--global", name),
    ]


def test_google_default_update_and_uninstall_preserve_other_files(tmp_path, monkeypatch):
    monkeypatch.setenv("K41_AGENT_HOME", str(tmp_path / "k41"))
    executable = tmp_path / "agy"
    config = tmp_path / "credentials"
    executable.write_text("1.5.10")
    config.write_text("credentials")
    monkeypatch.setattr(installations, "google_default_executable", lambda: executable)
    monkeypatch.setattr(installations.shutil, "which", lambda name: str(executable) if name == "agy" and executable.is_file() else None)
    monkeypatch.setattr(management, "latest_release", lambda _id: releases.Release(
        "1.5.12", "https://storage.googleapis.com/example", "cf", "sha512", "binary", "antigravity",
    ))
    monkeypatch.setattr(management, "download_executable", lambda release, target, _stop: target.write_text(release.version))
    monkeypatch.setattr(management, "inspect_executable", lambda agent_id, path, **kwargs: (path.read_text(), None))

    agent_id = ANTIGRAVITY_CLI_AGENT_ID
    installation = installations.discover_installation(agent_id)
    assert installation.source == "google"
    management.apply_operation(agent_id, "update", installation.revision(), threading.Event())
    assert executable.read_text() == "1.5.12"
    updated = installations.discover_installation(agent_id)
    management.apply_operation(agent_id, "uninstall", updated.revision(), threading.Event())
    assert not executable.exists() and config.read_text() == "credentials"


@pytest.mark.parametrize("scenario", ["success", "checksum", "redirect", "disconnect", "interrupted"])
def test_release_bytes_are_verified_before_extraction(tmp_path, monkeypatch, scenario):
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, mode="w") as archive:
        archive.writestr("bin/opencode.exe", b"executable data")
        archive.writestr("../outside", b"unrelated data")
    content = payload.getvalue()
    digest = hashlib.sha256(b"bad" if scenario == "checksum" else content).hexdigest()
    url = "https://github.com/anomalyco/opencode/releases/download/v2.4.2/opencode-windows-x64.zip"
    release = releases.Release("2.4.2", url, digest, "sha256", "zip", "opencode.exe")

    def respond(request):
        if scenario == "redirect":
            return httpx.Response(302, headers={"Location": "https://invalid.example/file"})
        if scenario == "disconnect":
            raise httpx.ReadError("connection reset")
        return httpx.Response(200, content=content)

    client = httpx.Client(transport=httpx.MockTransport(respond))
    monkeypatch.setattr(releases.httpx, "Client", lambda **_kwargs: client)
    target = tmp_path / "opencode.exe"
    stop = threading.Event()
    if scenario == "success":
        releases.download_executable(release, target, stop)
        assert target.read_bytes() == b"executable data"
        assert not (tmp_path.parent / "outside").exists()
        return
    if scenario == "interrupted":
        stop.set()
    with pytest.raises(AcpError):
        releases.download_executable(release, target, stop)
    assert not target.exists()

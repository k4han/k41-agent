"""CLI discovery, launch isolation and backward-compatible ACP card settings."""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent.modules.acp import launcher
from agent.modules.acp.agents import AGENT_DEFINITIONS, ANTIGRAVITY_CLI_AGENT_ID
from agent.modules.acp.auth import AcpAuthManager
from agent.modules.acp.errors import AcpError, AcpNotInstalled
from agent.modules.acp.process import AgentCommand
from agent.modules.acp.service import AcpService, TurnRequest
from agent.modules.acp.tool_bridge import ToolOutput
from agent.modules.agents.models import AcpAgentSettings, AgentConfig
from agent.modules.agents.parser import parse_agent_markdown_content, serialize_agent_config

CLI = ANTIGRAVITY_CLI_AGENT_ID
FAKE_AGENT = Path(__file__).parent / "fixtures" / "acp" / "fake_agent.py"


def test_cli_card_round_trip_and_legacy_defaults():
    settings = AcpAgentSettings(agent=CLI, agent_name="  code-reviewer  ", config_options={"model": "custom"})
    card = AgentConfig(name="cli", graph_type="acp_agent", permission_mode="ask", acp=settings)
    assert parse_agent_markdown_content(serialize_agent_config(card)).acp == settings
    assert settings.agent_name == "code-reviewer"
    assert AcpAgentSettings().agent == CLI
    assert AcpAgentSettings(agent=CLI, agent_name="  ").agent_name is None


@pytest.mark.parametrize("name", ["--help", "bad\nname", "x" * 129])
def test_invalid_agent_names_are_rejected(name):
    with pytest.raises(ValueError):
        AcpAgentSettings(agent=CLI, agent_name=name)


@pytest.mark.parametrize("agent_id", [None, "", "  ", "antigravity-acp", " antigravity-acp ", CLI])
def test_legacy_runtime_settings_normalize_to_cli(agent_id):
    settings = AcpAgentSettings(agent=agent_id, agent_name="reviewer", config_options={"model": "custom"})
    assert settings.agent == CLI and settings.agent_name == "reviewer"
    card = AgentConfig(name="legacy", graph_type="acp_agent", permission_mode="auto", acp=settings,
                       system_prompt="Preserved instructions.", tools=["read"],
                       tool_configs={"read": {"limit": 3}}, tool_permissions=[{"action": "*", "effect": "deny"}])
    serialized = serialize_agent_config(card)
    assert "antigravity-acp" not in serialized
    assert parse_agent_markdown_content(serialized) == card


def test_unknown_runtimes_are_rejected_and_only_cli_is_registered():
    assert list(AGENT_DEFINITIONS) == [CLI]
    with pytest.raises(ValueError):
        AcpAgentSettings(agent="unknown")
    with pytest.raises(AcpError, match="Unknown ACP agent"):
        launcher.runtime_key("antigravity-acp")


def test_tools_only_card_round_trip_and_compatible_defaults():
    settings = AcpAgentSettings(agent=CLI, use_default_tools=False)
    card = AgentConfig(name="tools-only", graph_type="acp_agent", permission_mode="ask", acp=settings,
                       system_prompt="Use the card tools.", tools=["read"])
    parsed = parse_agent_markdown_content(serialize_agent_config(card))
    assert parsed.acp == settings and parsed.system_prompt == card.system_prompt
    assert parsed.tools == ["read"]
    assert AcpAgentSettings().use_default_tools is True
    assert AcpAgentSettings(agent=CLI, agent_name="reviewer").use_default_tools is True
    assert AcpAgentSettings(use_default_tools=False).agent == CLI
    with pytest.raises(ValueError, match="Clear acp.agent_name"):
        AcpAgentSettings(agent=CLI, agent_name="reviewer", use_default_tools=False)


def test_tools_only_launch_uses_managed_profile_without_changing_user_files(tmp_path, monkeypatch):
    import yaml

    monkeypatch.setenv("K41_AGENT_HOME", str(tmp_path / "k41"))
    monkeypatch.setenv("GEMINI_HOME", str(tmp_path / "gemini"))
    monkeypatch.setattr(launcher, "cli_details", lambda: {"executable_path": "agy", "version": "1.5.9", "error": None})
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    command = launcher.resolve_cli_command(cwd=str(workspace), use_default_tools=False)
    profile = launcher.tools_only_profile()
    assert command.argv == ["agy", "acp", "--process-per-session", "--agent", profile.stem,
                            "--add-dir", str(profile.parent.parent.parent), "--include-default-customizations=false"]
    assert profile.is_relative_to(tmp_path / "k41" / "acp" / CLI)
    metadata = yaml.safe_load(profile.read_text(encoding="utf-8").split("---")[1])
    assert metadata["excludeDefaultComponents"] is True and metadata["tools"] == []
    assert metadata["inheritMcp"] is False and metadata["inheritCustomizations"] is False
    assert metadata["mainAgent"] is True and metadata["subagent"] is False
    assert metadata["name"] == profile.stem
    assert command.env == {"GEMINI_HOME": str(tmp_path / "gemini")}
    assert not (workspace / ".agents").exists() and not (tmp_path / "gemini").exists()
    assert launcher.resolve_cli_command(cwd=str(workspace), use_default_tools=False) == command
    with pytest.raises(AcpError, match="Clear acp.agent_name"):
        launcher.resolve_cli_command(cwd=str(workspace), agent_name="reviewer", use_default_tools=False)


@pytest.mark.parametrize("version", [None, "unknown", "1.2.0", "1.1.26", "1.3.2"])
def test_tools_only_requires_supported_cli_version(tmp_path, monkeypatch, version):
    monkeypatch.setattr(launcher, "cli_details", lambda: {"executable_path": "agy", "version": version, "error": None})
    with pytest.raises(AcpError, match="agy update"):
        launcher.resolve_cli_command(cwd=str(tmp_path), use_default_tools=False)


def test_cli_launch_preserves_arguments_workspace_and_user_home(tmp_path, monkeypatch):
    executable = str(tmp_path / "Program Files" / "agy.exe")
    monkeypatch.setattr(launcher, "cli_details", lambda: {"executable_path": executable, "error": None})
    monkeypatch.setenv("GEMINI_HOME", str(tmp_path / "user settings"))
    command = launcher.resolve_cli_command(agent_name="reviewer", cwd=str(tmp_path))
    assert command.argv == [executable, "acp", "--process-per-session", "--agent", "reviewer"]
    assert command.cwd == tmp_path.resolve()
    assert command.env == {"GEMINI_HOME": str(tmp_path / "user settings")}
    monkeypatch.delenv("GEMINI_HOME")
    default = launcher.resolve_cli_command(cwd=str(tmp_path))
    assert default.argv == [executable, "acp", "--process-per-session"] and default.env == {}


@pytest.mark.parametrize("agent_name", [None, "reviewer"])
def test_cli_keeps_existing_profiles_and_does_not_generate_one(tmp_path, monkeypatch, agent_name):
    monkeypatch.setattr(launcher, "cli_details", lambda: {"executable_path": "agy", "version": "1.1.26", "error": None})
    profiles = tmp_path / ".agents" / "agents"
    profiles.mkdir(parents=True)
    existing = profiles / "reviewer.md"
    existing.write_text("User-owned profile.", encoding="utf-8")
    command = launcher.resolve_cli_command(agent_name=agent_name, cwd=str(tmp_path))
    expected = ["agy", "acp", "--process-per-session"]
    assert command.argv == expected + (["--agent", agent_name] if agent_name else [])
    assert list(profiles.iterdir()) == [existing]
    assert existing.read_text(encoding="utf-8") == "User-owned profile."


def test_missing_or_incompatible_cli_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher.shutil, "which", lambda _name: None)
    with pytest.raises(AcpNotInstalled, match="PATH"):
        launcher.resolve_cli_command(cwd=str(tmp_path))
    monkeypatch.setattr(launcher, "cli_details", lambda: {"executable_path": "agy", "error": "Run agy update"})
    with pytest.raises(AcpError, match="agy update"):
        launcher.resolve_cli_command(cwd=str(tmp_path))


def test_cli_capability_check_is_cached_and_invalidated_on_update(tmp_path, monkeypatch):
    executable = tmp_path / "agy.exe"
    executable.write_bytes(b"fake")
    monkeypatch.setattr(launcher.shutil, "which", lambda _name: str(executable))
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        assert kwargs["stdin"] == subprocess.DEVNULL and kwargs["timeout"] == 10
        output = b"1.5.9" if argv[-1] == "--version" else b"--agent --process-per-session"
        return subprocess.CompletedProcess(argv, 0, stdout=output, stderr=b"")

    monkeypatch.setattr(launcher.subprocess, "run", run)
    assert launcher.cli_details(refresh=True)["version"] == "1.5.9"
    assert launcher.cli_details()["error"] is None and len(calls) == 2
    executable.write_bytes(b"updated executable")
    assert launcher.cli_details()["version"] == "1.5.9" and len(calls) == 4
    launcher.cli_details(refresh=True)
    assert len(calls) == 6
    launcher._inspect_cli.cache_clear()


def test_cli_without_required_acp_flags_is_incompatible(tmp_path, monkeypatch):
    executable = tmp_path / "old-agy.exe"
    executable.write_bytes(b"old")
    monkeypatch.setattr(launcher.shutil, "which", lambda _name: str(executable))
    monkeypatch.setattr(launcher.subprocess, "run", lambda argv, **_kw: subprocess.CompletedProcess(
        argv, 0, stdout=b"--agent", stderr=b"",
    ))
    assert "agy update" in launcher.cli_details(refresh=True)["error"]
    launcher._inspect_cli.cache_clear()


def test_runtime_keys_normalize_context_and_separate_cli_agents(tmp_path):
    key = launcher.runtime_key(CLI, "reviewer", str(tmp_path))
    assert key == launcher.runtime_key(CLI, " reviewer ", str(tmp_path / "."))
    assert key != launcher.runtime_key(CLI, "coder", str(tmp_path))
    assert key != launcher.runtime_key(CLI, "reviewer", str(tmp_path / "other"))
    assert launcher.runtime_key(CLI, cwd=str(tmp_path)) != launcher.runtime_key(CLI, cwd=str(tmp_path), use_default_tools=False)


@pytest.mark.asyncio
async def test_runtime_processes_and_idle_cleanup_are_isolated(tmp_path):
    def command(_agent):
        return AgentCommand(argv=[sys.executable, "-I", "-u", str(FAKE_AGENT), str(tmp_path)], cwd=tmp_path)

    service = AcpService(command_resolver=command)
    try:
        first = await service.client_for(CLI, agent_name="one", cwd=str(tmp_path))
        assert first is await service.client_for(CLI, agent_name="one", cwd=str(tmp_path))
        second = await service.client_for(CLI, agent_name="two", cwd=str(tmp_path))
        third = await service.client_for(CLI, agent_name="one", cwd=str(tmp_path / "other"))
        tools_only = await service.client_for(CLI, agent_name="one", cwd=str(tmp_path), use_default_tools=False)
        assert len({client.process.pid for client in (first, second, third, tools_only)}) == 4
        key = launcher.runtime_key(CLI, "one", str(tmp_path))
        service.hold(key)
        await service._reap(key)
        assert not first.closed and service.agent_is_busy(CLI)
        service.release(key)
        await service._reap(key)
        assert first.closed and not second.closed and not third.closed and not tools_only.closed
        await service.restart_client(CLI)
        assert second.closed and third.closed and tools_only.closed
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_native_auth_probes_are_scoped_and_credentials_stay_external(tmp_path, monkeypatch):
    credentials = tmp_path / "user-config" / "settings.json"
    credentials.parent.mkdir()
    credentials.write_text('{"existing": true}', encoding="utf-8")
    monkeypatch.setenv("GEMINI_HOME", str(credentials.parent))
    monkeypatch.setattr("agent.modules.acp.auth.cli_details", lambda **_kw: {"executable_path": "agy", "error": None})
    service = AcpService(command_resolver=lambda _agent: AgentCommand(
        argv=[sys.executable, "-I", "-u", str(FAKE_AGENT), str(tmp_path)], cwd=tmp_path,
    ))
    auth = AcpAuthManager(service)
    try:
        first = await auth.status(CLI, agent_name="one", cwd=str(tmp_path))
        assert first["signed_in"] and not ({"methods", "can_sign_out", "uses_api_key"} & first.keys())
        assert auth._probes[launcher.runtime_key(CLI, "one", str(tmp_path))][1] is first
        await auth.status(CLI, agent_name="two", cwd=str(tmp_path))
        assert len(auth._probes) == 2
        key = launcher.runtime_key(CLI, "one", str(tmp_path))
        previous = service._clients[key]
        await auth.status(CLI, agent_name="one", cwd=str(tmp_path), refresh=True)
        assert previous.closed and len(auth._probes) == 1
        current = service._clients[key]
        service.hold(key)
        await auth.status(CLI, agent_name="one", cwd=str(tmp_path), refresh=True)
        assert service._clients[key] is current and not current.closed
        service.release(key)
        auth.invalidate(CLI)
        assert not auth._probes
        assert not (tmp_path / CLI).exists()
        assert credentials.read_text(encoding="utf-8") == '{"existing": true}'
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_default_service_resolves_cli_and_forwards_startup_context(tmp_path, monkeypatch):
    calls = []

    def command(**kwargs):
        calls.append(kwargs)
        return AgentCommand(argv=[sys.executable, "-I", "-u", str(FAKE_AGENT), str(tmp_path)], cwd=tmp_path)

    monkeypatch.setattr("agent.modules.acp.service.resolve_cli_command", command)
    service = AcpService()
    try:
        await service.client_for(CLI, agent_name="reviewer", cwd=str(tmp_path), use_default_tools=False)
        assert calls == [{"agent_name": "reviewer", "cwd": str(tmp_path), "use_default_tools": False}]
        with pytest.raises(AcpError, match="Unknown ACP agent"):
            await service.client_for("antigravity-acp", cwd=str(tmp_path))
        assert len(calls) == 1
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("details,state", [
    ({"executable_path": None, "error": None}, "not_installed"),
    ({"executable_path": "agy", "error": "Run agy update"}, "error"),
])
async def test_status_discovery_errors_do_not_start_the_agent(monkeypatch, details, state):
    monkeypatch.setattr("agent.modules.acp.auth.cli_details", lambda **_kw: details)
    service = AcpService(command_resolver=lambda _agent: pytest.fail("must not start"))
    try:
        status = await AcpAuthManager(service).status(CLI)
        assert status["state"] == state and not status["signed_in"]
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_status_observes_external_sign_in_on_refresh(tmp_path, monkeypatch):
    monkeypatch.setattr("agent.modules.acp.auth.cli_details", lambda **_kw: {"executable_path": "agy", "error": None})
    service = AcpService(command_resolver=lambda _agent: AgentCommand(
        argv=[sys.executable, "-I", "-u", str(FAKE_AGENT), str(tmp_path), "--require-auth"], cwd=tmp_path,
    ))
    auth = AcpAuthManager(service)
    try:
        assert (await auth.status(CLI, cwd=str(tmp_path)))["state"] == "signed_out"
        (tmp_path / "authenticated").write_text("yes", encoding="utf-8")
        status = await auth.status(CLI, cwd=str(tmp_path), refresh=True)
        assert status["state"] == "signed_in" and status["config_options"]
        assert all(option["category"] != "mode" for option in status["config_options"])
    finally:
        await service.close()


@pytest.mark.live
@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("K41_LIVE_ACP_CLI") != "1", reason="Set K41_LIVE_ACP_CLI=1 with agy installed and signed in.")
async def test_live_cli_tools_only_uses_card_prompt_and_bridge(tmp_path, monkeypatch):
    """Catch AGY silently falling back to its default agent when a profile is not found."""
    monkeypatch.setenv("K41_AGENT_HOME", str(tmp_path / "k41"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    calls = []

    class Provider:
        async def tool_names(self):
            return frozenset({"k41_probe_echo"})

        async def list_tools(self):
            return [{"name": "k41_probe_echo", "description": "Return a fixed probe value without side effects.",
                     "inputSchema": {"type": "object", "properties": {}}}]

        async def call_tool(self, name, arguments):
            calls.append(name)
            return ToolOutput("K41_TOOL_OK")

    async def instructions():
        return "Always begin your final response with K41_CUSTOM_PROMPT_OK."

    service = AcpService()
    events = []
    try:
        async with asyncio.timeout(120):
            turn = await service.begin_turn(TurnRequest(
                turn_id="probe", thread_id="probe", agent_id=CLI, cwd=str(workspace),
                use_default_tools=False, tools=Provider(), system_prompt=instructions,
                blocks=[{"type": "text", "text": "Call k41_probe_echo and report its result. "
                         "If a run_command tool is available, call it with echo K41_NATIVE_PROBE. "
                         "Do not substitute another tool for run_command."}],
            ))
            while not turn.done:
                events.extend(turn.take_events())
                await turn.wait_for_progress()
            events.extend(turn.take_events())
        assert turn.error is None
        assert calls == ["k41_probe_echo"]
        response = turn.final_text(turn.result_notice())
        assert "K41_CUSTOM_PROMPT_OK" in response and "K41_TOOL_OK" in response
        assert all(event.get("name") != "acp_execute" for event in events)
        assert not (workspace / ".agents").exists()
    finally:
        await service.close()

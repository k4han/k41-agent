"""ACP install, sign-in relay, dashboard routes, and non-model guards."""

import asyncio
import hashlib
import io
import json
import stat
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.modules.acp.agents import ANTIGRAVITY_AGENT_ID, get_agent_definition
from agent.modules.acp.auth import AcpAuthError, AcpAuthManager, CallbackRejected, parse_authorization_link, validate_callback
from agent.modules.acp.errors import AcpError, AcpNotInstalled
from agent.modules.acp.installer import AcpInstaller, agent_home, is_installed, read_api_key, resolve_installed_command, write_api_key
from agent.modules.acp.process import AgentCommand
from agent.modules.acp.registry import AgentRelease, current_target, parse_release
from agent.modules.acp.service import AcpService
from agent.modules.agents.models import AgentConfig

AGENT = ANTIGRAVITY_AGENT_ID
DEFINITION = get_agent_definition(AGENT)
FAKE_AGENT = Path(__file__).parent / "fixtures" / "acp" / "fake_agent.py"


def make_zip(files: dict[str, bytes], *, symlink: str | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            info = zipfile.ZipInfo(name)
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, content)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, "/etc/passwd")
    return buffer.getvalue()


def release(payload: bytes, *, version="1.0.0", sha256=None, command="server.exe") -> AgentRelease:
    return AgentRelease(
        agent_id=AGENT, version=version, license="proprietary",
        archive_url="https://dl.google.com/x.zip", sha256=sha256 or hashlib.sha256(payload).hexdigest(),
        command=command, args=("--flag",),
    )


def installer_for(tmp_path, payload, **release_args) -> AcpInstaller:
    def download(_url):
        yield payload, len(payload)

    return AcpInstaller(data_root=tmp_path, release_fetcher=lambda _definition: release(payload, **release_args), downloader=download)


def run_install(installer: AcpInstaller) -> dict:
    installer.start(AGENT)
    deadline = time.monotonic() + 20
    while installer.is_running(AGENT):
        assert time.monotonic() < deadline
        time.sleep(0.01)
    return installer.status(AGENT)


# Registry ---------------------------------------------------------------------


def registry_entry(**target):
    selected = {"archive": "https://dl.google.com/a.zip", "cmd": "./agy", "sha256": "a" * 64, **target}
    return {"id": AGENT, "version": "1.4.5", "license": "proprietary", "distribution": {"binary": {"linux-x86_64": selected}}}


def test_release_is_parsed_for_the_requested_target():
    parsed = parse_release(registry_entry(args=["--uid="]), DEFINITION, "linux-x86_64")
    assert parsed.command == "agy" and parsed.args == ("--uid=",) and parsed.version == "1.4.5"


@pytest.mark.parametrize("archive", [
    "https://evil.example/a.zip",
    "http://dl.google.com/a.zip",
    "https://dl.google.com.evil.example/a.zip",
    "https://user:pw@dl.google.com/a.zip",
    "file:///etc/passwd",
])
def test_release_from_an_unexpected_location_is_refused(archive):
    with pytest.raises(AcpError):
        parse_release(registry_entry(archive=archive), DEFINITION, "linux-x86_64")


@pytest.mark.parametrize("fields", [
    {"sha256": ""}, {"sha256": "abc"}, {"cmd": "../../evil"}, {"cmd": "/bin/sh"}, {"cmd": "C:\\evil.exe"}, {"cmd": ""}, {"args": "--x"},
])
def test_malformed_release_is_refused(fields):
    with pytest.raises(AcpError):
        parse_release(registry_entry(**fields), DEFINITION, "linux-x86_64")


def test_missing_target_is_reported():
    with pytest.raises(AcpError, match="no release"):
        parse_release(registry_entry(), DEFINITION, "windows-x86_64")


def test_current_target_uses_registry_names():
    system, machine = current_target().split("-")
    assert system in {"windows", "linux", "darwin"} and machine in {"x86_64", "aarch64"}


# Installer --------------------------------------------------------------------


def test_install_activates_and_resolves_with_an_isolated_home(tmp_path):
    payload = make_zip({"server.exe": b"binary", "helper.exe": b"helper"})
    job = run_install(installer_for(tmp_path, payload))
    assert job["state"] == "succeeded" and job["phase"] == "installed" and job["downloaded_bytes"] == len(payload)
    command = resolve_installed_command(AGENT, tmp_path)
    assert Path(command.argv[0]).read_bytes() == b"binary" and command.argv[1:] == ["--flag"]
    assert command.env["GEMINI_HOME"] == str(agent_home(AGENT, tmp_path))
    assert "GEMINI_API_KEY" not in command.env
    assert not list((tmp_path / AGENT).glob(".install-*"))


def test_wrong_checksum_installs_nothing(tmp_path):
    job = run_install(installer_for(tmp_path, make_zip({"server.exe": b"x"}), sha256="0" * 64))
    assert job["state"] == "failed" and "sha256" in job["error"]
    assert not is_installed(AGENT, tmp_path)
    assert not list((tmp_path / AGENT).glob(".install-*"))


@pytest.mark.parametrize("files,symlink", [
    ({"../evil.exe": b"x", "server.exe": b"x"}, None),
    ({"..\\evil.exe": b"x", "server.exe": b"x"}, None),
    ({"/abs.exe": b"x", "server.exe": b"x"}, None),
    ({"server.exe": b"x"}, "link"),
])
def test_unsafe_archives_are_refused(tmp_path, files, symlink):
    job = run_install(installer_for(tmp_path, make_zip(files, symlink=symlink)))
    assert job["state"] == "failed" and not is_installed(AGENT, tmp_path)
    assert not (tmp_path / "evil.exe").exists()


def test_archive_without_the_program_is_refused(tmp_path):
    job = run_install(installer_for(tmp_path, make_zip({"other.exe": b"x"})))
    assert job["state"] == "failed" and "expected program" in job["error"]


def test_garbage_archive_fails_cleanly(tmp_path):
    assert run_install(installer_for(tmp_path, b"not a zip"))["state"] == "failed"


def test_reinstalling_the_same_version_downloads_nothing(tmp_path):
    payload = make_zip({"server.exe": b"x"})
    assert run_install(installer_for(tmp_path, payload))["phase"] == "installed"

    def fail_download(_url):
        raise AssertionError("must not download")
        yield

    installer = AcpInstaller(data_root=tmp_path, release_fetcher=lambda _d: release(payload), downloader=fail_download)
    assert run_install(installer)["phase"] == "up_to_date"


def test_update_switches_version_and_removes_the_old_one(tmp_path):
    run_install(installer_for(tmp_path, make_zip({"server.exe": b"old"})))
    run_install(installer_for(tmp_path, make_zip({"server.exe": b"new"}), version="2.0.0"))
    assert Path(resolve_installed_command(AGENT, tmp_path).argv[0]).read_bytes() == b"new"
    assert [path.name for path in (tmp_path / AGENT / "versions").iterdir()] == ["2.0.0"]


def test_failed_update_keeps_the_working_version(tmp_path):
    run_install(installer_for(tmp_path, make_zip({"server.exe": b"old"})))
    run_install(installer_for(tmp_path, make_zip({"server.exe": b"new"}), version="2.0.0", sha256="0" * 64))
    assert Path(resolve_installed_command(AGENT, tmp_path).argv[0]).read_bytes() == b"old"


def test_too_little_disk_space_stops_before_downloading_everything(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.disk_usage", lambda _path: SimpleNamespace(free=1))
    job = run_install(installer_for(tmp_path, make_zip({"server.exe": b"x"})))
    assert job["state"] == "failed" and "disk space" in job["error"]


def test_second_install_while_running_is_refused(tmp_path):
    from agent.modules.acp.installer import InstallBusyError

    gate = []

    def slow_fetch(_definition):
        while not gate:
            time.sleep(0.01)
        raise AcpError("stopped")

    installer = AcpInstaller(data_root=tmp_path, release_fetcher=slow_fetch)
    installer.start(AGENT)
    with pytest.raises(InstallBusyError):
        installer.start(AGENT)
    gate.append(True)


def test_uninstall_removes_binary_and_credentials(tmp_path):
    installer = installer_for(tmp_path, make_zip({"server.exe": b"x"}))
    run_install(installer)
    write_api_key(AGENT, "secret", tmp_path)
    installer.uninstall(AGENT)
    assert not is_installed(AGENT, tmp_path) and read_api_key(AGENT, tmp_path) == ""


def test_missing_install_raises_a_friendly_error(tmp_path):
    with pytest.raises(AcpNotInstalled, match="not installed"):
        resolve_installed_command(AGENT, tmp_path)


def test_api_key_reaches_only_the_agent_environment(tmp_path):
    run_install(installer_for(tmp_path, make_zip({"server.exe": b"x"})))
    write_api_key(AGENT, "secret", tmp_path)
    assert resolve_installed_command(AGENT, tmp_path).env["GEMINI_API_KEY"] == "secret"


# Callback validation ----------------------------------------------------------


GOOD = {"port": 5000, "path": "/", "state": "abc"}


def test_valid_callback_is_rebuilt_against_loopback():
    assert validate_callback("http://localhost:5000/?state=abc&code=xyz", **GOOD) == "http://127.0.0.1:5000/?state=abc&code=xyz"


@pytest.mark.parametrize("url", [
    "http://evil.example:5000/?state=abc&code=x",
    "https://127.0.0.1:5000/?state=abc&code=x",
    "http://127.0.0.1:5001/?state=abc&code=x",
    "http://127.0.0.1:5000/other?state=abc&code=x",
    "http://127.0.0.1:5000/?state=wrong&code=x",
    "http://127.0.0.1:5000/?code=x",
    "http://127.0.0.1:5000/?state=abc",
    "http://user:pw@127.0.0.1:5000/?state=abc&code=x",
    "http://169.254.169.254:5000/?state=abc&code=x",
    "http://127.0.0.1.evil.example:5000/?state=abc&code=x",
    "http://127.0.0.1:notaport/?state=abc&code=x",
    "not a url",
    "",
])
def test_bad_callbacks_are_rejected_before_forwarding(url):
    with pytest.raises(CallbackRejected):
        validate_callback(url, **GOOD)


def test_authorization_link_must_point_at_an_allowed_host():
    redirect = urllib.parse.quote("http://127.0.0.1:5000/", safe="")
    good = f"https://accounts.google.com/auth?redirect_uri={redirect}&state=abc"
    assert parse_authorization_link(good, ("accounts.google.com",))[1:] == (5000, "/", "abc")
    assert parse_authorization_link(good.replace("accounts.google.com", "evil.example"), ("accounts.google.com",)) is None
    assert parse_authorization_link("https://accounts.google.com/auth?state=abc", ("accounts.google.com",)) is None


# Sign-in against the fake agent ----------------------------------------------


@pytest_asyncio.fixture
async def auth_env(tmp_path):
    (tmp_path / AGENT / "versions" / "1.0.0").mkdir(parents=True)
    (tmp_path / AGENT / "versions" / "1.0.0" / "fake").write_bytes(b"x")
    (tmp_path / AGENT / "current.json").write_text(json.dumps({"version": "1.0.0", "command": "fake"}), encoding="utf-8")
    state = tmp_path / "fake-state"

    def resolver(_agent_id):
        env = {"GEMINI_API_KEY": read_api_key(AGENT, tmp_path)} if read_api_key(AGENT, tmp_path) else {}
        return AgentCommand(
            argv=[sys.executable, "-I", "-u", str(FAKE_AGENT), str(state), "--require-auth", "--oauth"],
            cwd=state, env=env,
        )

    service = AcpService(command_resolver=resolver)
    yield SimpleNamespace(service=service, auth=AcpAuthManager(service, data_root=tmp_path), state=state, root=tmp_path)
    await service.close()


async def start_oauth(auth_env):
    attempt = await auth_env.auth.start_sign_in(AGENT, "oauth-personal")
    assert attempt["status"] == "pending" and attempt["authorization_url"].startswith("https://accounts.google.com/")
    return attempt, auth_env.auth._attempts[AGENT]


def get_url(url):
    urllib.request.urlopen(url, timeout=5).read()


@pytest.mark.asyncio
async def test_status_reports_signed_out_then_signed_in_with_options(auth_env):
    status = await auth_env.auth.status(AGENT)
    assert status["state"] == "signed_out"
    assert [method["id"] for method in status["methods"]] == ["oauth-personal", "gemini-api-key"]
    attempt, internal = await start_oauth(auth_env)
    url = f"http://127.0.0.1:{internal.redirect_port}/?state={internal.state}&code=abc"
    result = await auth_env.auth.complete_sign_in(AGENT, attempt["attempt_id"], url)
    assert result["status"] == "succeeded" and result["authorization_url"] == ""
    status = await auth_env.auth.status(AGENT)
    assert status["signed_in"] is True
    assert {option["id"] for option in status["config_options"]} == {"model", "thinking_level"}


@pytest.mark.asyncio
async def test_direct_browser_callback_also_finishes_the_attempt(auth_env):
    attempt, internal = await start_oauth(auth_env)
    await asyncio.to_thread(get_url, f"http://127.0.0.1:{internal.redirect_port}/?state={internal.state}&code=abc")
    await asyncio.wait_for(internal.task, 10)
    assert auth_env.auth.attempt_status(AGENT)["status"] == "succeeded"


@pytest.mark.asyncio
async def test_wrong_state_is_never_forwarded_and_attempt_stays_usable(auth_env):
    attempt, internal = await start_oauth(auth_env)
    with pytest.raises(CallbackRejected):
        await auth_env.auth.complete_sign_in(AGENT, attempt["attempt_id"], f"http://127.0.0.1:{internal.redirect_port}/?state=wrong&code=abc")
    assert auth_env.auth.attempt_status(AGENT)["status"] == "pending"
    url = f"http://127.0.0.1:{internal.redirect_port}/?state={internal.state}&code=abc"
    assert (await auth_env.auth.complete_sign_in(AGENT, attempt["attempt_id"], url))["status"] == "succeeded"


@pytest.mark.asyncio
async def test_callback_for_another_attempt_id_is_rejected(auth_env):
    await start_oauth(auth_env)
    with pytest.raises(CallbackRejected):
        await auth_env.auth.complete_sign_in(AGENT, "other", "http://127.0.0.1:1/?state=x&code=y")


@pytest.mark.asyncio
async def test_only_one_attempt_at_a_time(auth_env):
    await start_oauth(auth_env)
    with pytest.raises(AcpAuthError, match="already in progress"):
        await auth_env.auth.start_sign_in(AGENT, "oauth-personal")


@pytest.mark.asyncio
async def test_cancel_ends_the_attempt_and_allows_a_new_one(auth_env):
    attempt, _internal = await start_oauth(auth_env)
    result = await auth_env.auth.cancel_sign_in(AGENT, attempt["attempt_id"])
    assert result["status"] == "cancelled" and result["authorization_url"] == ""
    await start_oauth(auth_env)


@pytest.mark.asyncio
async def test_authorization_details_are_not_exposed_after_the_attempt(auth_env):
    attempt, internal = await start_oauth(auth_env)
    await auth_env.auth.cancel_sign_in(AGENT, attempt["attempt_id"])
    assert internal.state == "" and internal.authorization_url == ""


@pytest.mark.asyncio
async def test_api_key_is_verified_and_a_rejected_key_is_not_kept(auth_env):
    with pytest.raises(AcpAuthError, match="did not accept"):
        await auth_env.auth.set_api_key(AGENT, "bad-key")
    assert read_api_key(AGENT, auth_env.root) == ""
    status = await auth_env.auth.set_api_key(AGENT, "good-key")
    assert status["signed_in"] is True and status["uses_api_key"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["", "   ", "two words", "x" * 5000])
async def test_malformed_api_key_is_refused_without_starting_anything(auth_env, key):
    with pytest.raises(AcpAuthError):
        await auth_env.auth.set_api_key(AGENT, key)
    assert read_api_key(AGENT, auth_env.root) == ""


@pytest.mark.asyncio
async def test_sign_out_clears_the_key_and_the_session(auth_env):
    await auth_env.auth.set_api_key(AGENT, "good-key")
    status = await auth_env.auth.sign_out(AGENT)
    assert status["state"] == "signed_out" and read_api_key(AGENT, auth_env.root) == ""


@pytest.mark.asyncio
async def test_api_key_method_cannot_start_a_browser_flow(auth_env):
    with pytest.raises(AcpAuthError):
        await auth_env.auth.start_sign_in(AGENT, "gemini-api-key")


@pytest.mark.asyncio
async def test_status_without_install_does_not_start_anything(tmp_path):
    service = AcpService(command_resolver=lambda _agent: pytest.fail("must not start"))
    status = await AcpAuthManager(service, data_root=tmp_path).status(AGENT)
    assert status["state"] == "not_installed"


# Routes -----------------------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch, isolated_container):
    from agent.delivery.http.dashboard.routes import acp as routes
    from agent.modules.admin_auth import get_current_admin

    monkeypatch.setattr(routes, "get_app_container", lambda _request: isolated_container)
    isolated_container._acp_service = AcpService(command_resolver=lambda _agent: pytest.fail("must not start"))
    isolated_container._acp_auth_manager = AcpAuthManager(isolated_container._acp_service, data_root=tmp_path)
    isolated_container._acp_installer = installer_for(tmp_path, make_zip({"server.exe": b"x"}))
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_admin] = lambda: "admin"
    return TestClient(app)


def test_routes_require_admin(tmp_path, monkeypatch, isolated_container):
    from agent.delivery.http.dashboard.routes import acp as routes

    app = FastAPI()
    app.include_router(routes.router)
    unauthenticated = TestClient(app)
    assert unauthenticated.get("/dashboard-api/acp/agents").status_code == 401
    assert unauthenticated.post(f"/dashboard-api/acp/agents/{AGENT}/install", json={"accept_license": True}).status_code == 401


def test_list_reports_a_missing_agent_with_its_terms(client):
    agent = client.get("/dashboard-api/acp/agents").json()["agents"][0]
    assert agent["id"] == AGENT and agent["installed"] is False and agent["auth"]["state"] == "not_installed"
    assert agent["terms_url"].startswith("https://") and agent["license"] == "proprietary"


def test_install_requires_accepting_the_license(client):
    assert client.post(f"/dashboard-api/acp/agents/{AGENT}/install", json={}).status_code == 400


def test_unknown_agent_is_not_found(client):
    assert client.get("/dashboard-api/acp/agents/nope").status_code == 404
    assert client.post("/dashboard-api/acp/agents/nope/install", json={"accept_license": True}).status_code == 404


def test_responses_with_sign_in_details_are_not_cached(client):
    assert client.get("/dashboard-api/acp/agents").headers["cache-control"] == "no-store"


# Guards -----------------------------------------------------------------------


def catalog_with(tmp_path):
    """Always a temporary directory: the default repository is the user's real one."""
    from agent.modules.agents.repository import FilesystemAgentRepository
    from agent.modules.agents.service import AgentCatalogService

    repository = FilesystemAgentRepository(tmp_path / "agents")
    repository.load()
    catalog = AgentCatalogService()
    catalog._repository = repository
    catalog.create_agent_card(AgentConfig(permission_mode="ask", name="ext", graph_type="acp_agent"))
    return catalog


def test_acp_cards_are_not_delegation_targets(tmp_path):
    catalog = catalog_with(tmp_path)
    caller = AgentConfig(name="caller", graph_type="react_agent", sub_agents=["ext"])
    catalog.create_agent_card(caller)
    assert catalog.validate_call("caller", "ext") is False
    assert catalog.get_callable_agents("caller") == []


def test_acp_cards_are_not_plan_approval_targets(tmp_path):
    catalog = catalog_with(tmp_path)
    with pytest.raises(ValueError, match="plan approval target"):
        catalog.create_agent_card(AgentConfig(name="planner", graph_type="react_agent", plan_approval_targets=["ext"]))


def test_acp_graph_is_not_routeable():
    from langgraph.checkpoint.memory import InMemorySaver

    from agent.modules.workflows.graphs.acp_agent import build_acp_agent_graph
    from agent.modules.workflows.registry import GraphRegistry

    build_acp_agent_graph(InMemorySaver(), graph_name="acp_agent_test")
    assert "acp_agent_test" not in GraphRegistry.routeable_workflows()


@pytest.mark.asyncio
async def test_thread_cannot_switch_across_the_acp_boundary(monkeypatch):
    from agent.modules.agent_runtime import runner

    async def thread_record(_thread_id):
        return {"agent_name": "previous"}

    catalog_agent = {}
    monkeypatch.setattr("agent.modules.conversations.get_conversation_thread", thread_record)
    monkeypatch.setattr("agent.modules.agents.get_catalog_service", lambda: SimpleNamespace(get_agent=lambda _name: catalog_agent["previous"]))
    acp, react = SimpleNamespace(name="ext", graph_type="acp_agent"), SimpleNamespace(name="model", graph_type="react_agent")

    catalog_agent["previous"] = react
    await runner._assert_thread_graph_family("t", "react_agent", react)
    with pytest.raises(ValueError, match="cannot switch"):
        await runner._assert_thread_graph_family("t", "acp_agent", acp)
    catalog_agent["previous"] = acp
    await runner._assert_thread_graph_family("t", "acp_agent", acp)
    with pytest.raises(ValueError, match="cannot switch"):
        await runner._assert_thread_graph_family("t", "react_agent", react)


ACP_CARD = SimpleNamespace(name="ext", graph_type="acp_agent", tools=[], context_compact_threshold=75)
MODEL_CARD = SimpleNamespace(name="model", graph_type="react_agent", tools=[], context_compact_threshold=75)


def _stored_thread(monkeypatch, record, cards=None):
    cards = cards or {"ext": ACP_CARD, "model": MODEL_CARD}

    async def thread_record(_thread_id):
        return record

    monkeypatch.setattr("agent.modules.conversations.get_conversation_thread", thread_record)
    monkeypatch.setattr("agent.modules.agents.get_catalog_service", lambda: SimpleNamespace(get_agent=cards.get))


@pytest.mark.asyncio
async def test_stored_family_outlives_the_agent_name_and_its_card(monkeypatch):
    from agent.modules.agent_runtime import runner

    # A later writer renamed the agent, and the card it names has since been deleted.
    _stored_thread(monkeypatch, {"agent_name": "gone", "graph_family": "acp"}, cards={})
    await runner._assert_thread_graph_family("t", "acp_agent", ACP_CARD)
    with pytest.raises(ValueError, match="cannot switch"):
        await runner._assert_thread_graph_family("t", "react_agent", MODEL_CARD)


@pytest.mark.asyncio
async def test_thread_without_a_family_or_agent_can_be_claimed_by_either_side(monkeypatch):
    from agent.modules.agent_runtime import runner

    _stored_thread(monkeypatch, {"agent_name": "", "graph_family": ""})
    await runner._assert_thread_graph_family("t", "acp_agent", ACP_CARD)
    await runner._assert_thread_graph_family("t", "react_agent", MODEL_CARD)


@pytest.mark.asyncio
async def test_legacy_thread_with_a_deleted_card_counts_as_a_model_thread(monkeypatch):
    from agent.modules.agent_runtime import runner

    _stored_thread(monkeypatch, {"agent_name": "gone", "graph_family": ""}, cards={})
    await runner._assert_thread_graph_family("t", "react_agent", MODEL_CARD)
    with pytest.raises(ValueError, match="cannot switch"):
        await runner._assert_thread_graph_family("t", "acp_agent", ACP_CARD)


@pytest.mark.asyncio
@pytest.mark.parametrize("workflow, card", [("acp_agent", MODEL_CARD), ("react_agent", ACP_CARD)])
async def test_workflow_override_cannot_cross_the_card_family(monkeypatch, workflow, card):
    from agent.modules.agent_runtime import runner

    _stored_thread(monkeypatch, None)
    with pytest.raises(ValueError, match="cannot run agent"):
        await runner._assert_thread_graph_family("t", workflow, card)


@pytest.mark.asyncio
async def test_guard_fails_closed_when_the_thread_cannot_be_loaded(monkeypatch):
    from agent.modules.agent_runtime import runner

    async def broken(_thread_id):
        raise RuntimeError("database is unavailable")

    monkeypatch.setattr("agent.modules.conversations.get_conversation_thread", broken)
    with pytest.raises(RuntimeError, match="database is unavailable"):
        await runner._assert_thread_graph_family("t", "react_agent", MODEL_CARD)


def _unexpected_graph(_name):
    pytest.fail("A run on the wrong side of the ACP boundary must not reach a graph.")


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["run_agent", "run_agent_stream"])
@pytest.mark.parametrize("override", [{"resume": True}, {"workflow": "react_agent"}])
async def test_acp_thread_rejects_resume_and_overrides_under_a_model_agent(monkeypatch, entry, override):
    from agent.modules.agent_runtime import runner

    _stored_thread(monkeypatch, {"agent_name": "ext", "graph_family": "acp"})
    monkeypatch.setattr(runner, "get_workflow_graph", _unexpected_graph)
    with pytest.raises(ValueError, match="cannot switch"):
        async for _ in getattr(runner, entry)(user_input="go", thread_id="t", agent_name="model", **override):
            pass


@pytest.mark.asyncio
async def test_edit_cannot_fork_an_acp_thread_under_a_model_agent(monkeypatch):
    from agent.modules.agent_runtime import runner

    _stored_thread(monkeypatch, {"agent_name": "ext", "graph_family": "acp"})
    monkeypatch.setattr(runner, "get_workflow_graph", _unexpected_graph)
    with pytest.raises(ValueError, match="cannot switch"):
        async for _ in runner.run_agent_edit_stream(
            "edited", "t", "model", message_index=0, source_checkpoint_id="c",
        ):
            pass


@pytest.mark.asyncio
async def test_edit_never_runs_the_acp_graph(monkeypatch):
    from agent.modules.agent_runtime import runner

    _stored_thread(monkeypatch, None)
    monkeypatch.setattr(runner, "get_workflow_graph", _unexpected_graph)
    with pytest.raises(ValueError, match="cannot be edited"):
        async for _ in runner.run_agent_edit_stream(
            "edited", "t", "ext", message_index=0, source_checkpoint_id="c",
        ):
            pass


def test_existing_database_gains_the_graph_family_column(tmp_path):
    from sqlalchemy import create_engine, inspect, text

    from agent.modules.conversations.migrations import migrate_conversation_tables

    url = f"sqlite:///{(tmp_path / 'old.db').as_posix()}"
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE conversation_threads (id INTEGER PRIMARY KEY, thread_id VARCHAR(512) NOT NULL)"))
        conn.execute(text("INSERT INTO conversation_threads (thread_id) VALUES ('old')"))

    migrate_conversation_tables(url)
    migrate_conversation_tables(url)

    with engine.connect() as conn:
        assert "graph_family" in {column["name"] for column in inspect(conn).get_columns("conversation_threads")}
        assert conn.execute(text("SELECT graph_family FROM conversation_threads")).scalar_one() == ""
    engine.dispose()


def test_conflict_reaches_the_user_with_the_way_out():
    from agent.shared.infrastructure.errors import ThreadGraphConflictError, classify_agent_error

    error = classify_agent_error(ThreadGraphConflictError("Send /clear."))
    assert error.code == "thread_graph_conflict"
    assert error.message == "Send /clear."


@pytest.mark.asyncio
async def test_first_run_owns_the_thread_until_it_is_released(isolated_container):
    from agent.modules.conversations import (
        get_conversation_thread,
        get_conversation_thread_repository,
        release_conversation_thread_agent,
        upsert_conversation_thread,
    )
    from agent.modules.workflows import thread_graph_family

    await isolated_container.initialize_persistence()
    try:
        await upsert_conversation_thread(thread_id="owned", agent_name="ext", graph_family="acp")
        await upsert_conversation_thread(thread_id="owned", agent_name="other", graph_family="model")
        assert (await get_conversation_thread("owned"))["graph_family"] == "acp"

        await release_conversation_thread_agent("owned")
        stored = await get_conversation_thread("owned")
        assert (stored["agent_name"], stored["graph_family"]) == ("", "")
        assert await thread_graph_family("owned") == ""

        await upsert_conversation_thread(thread_id="owned", agent_name="model", graph_family="model")
        assert await thread_graph_family("owned") == "model"

        repository = get_conversation_thread_repository()
        await repository.mark_deleted("owned")
        await upsert_conversation_thread(thread_id="owned", title="revived")
        assert await thread_graph_family("owned") == ""
    finally:
        await isolated_container.close_persistence()


@pytest.mark.asyncio
async def test_clearing_a_channel_thread_lets_the_other_family_run(isolated_container, monkeypatch):
    from agent.modules.agent_runtime import runner
    from agent.modules.conversations import upsert_conversation_thread
    from agent.shared.thread_ids import SessionManager

    async def nothing(*_args, **_kwargs):
        return None

    await isolated_container.initialize_persistence()
    monkeypatch.setattr("agent.modules.tools.stop_thread_processes", lambda _thread_id: None)
    monkeypatch.setattr("agent.modules.workspaces.delete_thread_workspace", nothing)
    monkeypatch.setattr("agent.modules.workflows.delete_workflow_thread_tree", nothing)
    monkeypatch.setattr("agent.modules.agents.get_catalog_service", lambda: SimpleNamespace(get_agent=lambda _name: ACP_CARD))
    thread_id = SessionManager.make_thread_id("telegram", "1", "1")
    try:
        await upsert_conversation_thread(thread_id=thread_id, agent_name="ext", graph_family="acp")
        with pytest.raises(ValueError, match="/clear"):
            await runner._assert_thread_graph_family(thread_id, "react_agent", MODEL_CARD)

        await runner.clear_agent_session(platform="telegram", user_id="1", channel_id="1")
        await runner._assert_thread_graph_family(thread_id, "react_agent", MODEL_CARD)
    finally:
        await isolated_container.close_persistence()


@pytest.mark.asyncio
async def test_scheduled_task_keeps_the_acp_thread_and_skips_injection(isolated_container, monkeypatch):
    from agent.modules.agent_runtime import runner
    from agent.modules.conversations import get_conversation_thread, upsert_conversation_thread
    from agent.modules.scheduler import service as scheduler_service
    from agent.shared.thread_ids import SessionManager

    async def fake_run_agent_full(**_kwargs):
        return "done"

    async def fake_send_notification(*_args, **_kwargs):
        return True

    await isolated_container.initialize_persistence()
    monkeypatch.setattr(scheduler_service, "run_agent_full", fake_run_agent_full)
    monkeypatch.setattr(scheduler_service, "_send_notification", fake_send_notification)
    monkeypatch.setattr("agent.modules.workflows.get_workflow_graph", lambda _name: pytest.fail("The ACP thread must not be written to."))
    monkeypatch.setattr("agent.modules.agents.get_catalog_service", lambda: SimpleNamespace(get_agent=lambda _name: MODEL_CARD))
    thread_id = SessionManager.make_thread_id("telegram", "1", "1")
    try:
        await upsert_conversation_thread(thread_id=thread_id, agent_name="ext", graph_family="acp")
        await scheduler_service.execute_scheduled_task("telegram", "1", "daily summary")

        stored = await get_conversation_thread(thread_id)
        assert (stored["agent_name"], stored["graph_family"]) == ("ext", "acp")
        await runner._assert_thread_graph_family(thread_id, "acp_agent", ACP_CARD)
    finally:
        await isolated_container.close_persistence()


@pytest.mark.parametrize(
    "stored_family, payload",
    [
        ("acp", {"agent_name": "model"}),
        ("", {"agent_name": "ext"}),
        ("", {"agent_name": "model", "workflow": "acp_agent"}),
    ],
)
def test_edit_route_rejects_acp_whichever_agent_the_request_names(monkeypatch, stored_family, payload):
    from importlib import import_module

    from agent.modules.admin_auth import get_current_admin

    router_module = import_module("agent.delivery.http.api.router")

    async def thread_is_acp(_thread_id):
        return stored_family == "acp"

    cards = {"ext": ACP_CARD, "model": MODEL_CARD}
    monkeypatch.setattr(router_module, "thread_is_acp", thread_is_acp)
    monkeypatch.setattr(router_module, "_ensure_chat_agent_available", lambda _name: None)
    monkeypatch.setattr(router_module, "get_catalog_service", lambda: SimpleNamespace(get_agent_card=cards.get))
    monkeypatch.setattr(router_module, "build_run_params", lambda **_params: pytest.fail("The edit must be rejected first."))
    app = FastAPI()
    app.include_router(router_module.router)
    app.dependency_overrides[get_current_admin] = lambda: "admin"

    response = TestClient(app).post("/v1/api/chat/events/edit", json={
        "message": "edited", "thread_id": "t", "user_id": "u", "message_index": 0, "source_checkpoint_id": "c", **payload,
    })
    assert response.status_code == 409


def test_acp_history_exposes_no_edit_or_fork_points():
    from langchain_core.messages import AIMessage, HumanMessage

    from agent.modules.conversations.history import _serialize_thread_messages

    messages = [HumanMessage(content="q", id="u"), AIMessage(content="a", id="a", additional_kwargs={"acp_live": True})]
    rows = _serialize_thread_messages(messages, sources={0: ("c0", "p0"), 1: ("c1", "p1")})
    assert all("source_checkpoint_id" not in row and "message_index" not in row for row in rows)
    assert [row["content"] for row in rows] == ["q", "a"]


@pytest.mark.asyncio
async def test_plan_cannot_be_handed_to_an_acp_agent():
    from agent.modules.agent_runtime import runner

    catalog = SimpleNamespace(
        get_agent=lambda _name: SimpleNamespace(graph_type="acp_agent"),
        get_agent_card=lambda _name: SimpleNamespace(internal=False),
    )
    payload = SimpleNamespace(action="approve", target_agent="ext")
    with pytest.raises(ValueError, match="ACP agent"):
        runner._resolve_agent_name_for_resume(catalog, "planner", payload, source_agent_name="planner")


def test_settings_page_can_be_loaded_directly():
    from agent.delivery.http.dashboard.routes import spa

    assert "/settings/acp" in {route.path for route in spa.router.routes}

"""ACP dashboard status, management routes, and non-model guards."""

import threading
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.modules.acp.agents import ANTIGRAVITY_CLI_AGENT_ID, OPENCODE_AGENT_ID
from agent.modules.acp.auth import AcpAuthManager
from agent.modules.acp.service import AcpService
from agent.modules.agents.models import AgentConfig
from agent.shared.infrastructure.csrf_protection import (
    CSRF_HEADER_NAME,
    CSRFProtectionMiddleware,
)

AGENT = ANTIGRAVITY_CLI_AGENT_ID


@pytest.fixture
def client(tmp_path, monkeypatch, isolated_container):
    from agent.delivery.http.dashboard.routes import acp as routes
    from agent.modules.admin_auth import get_current_admin

    monkeypatch.setattr(routes, "get_app_container", lambda _request: isolated_container)
    monkeypatch.setenv("K41_AGENT_HOME", str(tmp_path))
    monkeypatch.setattr("agent.modules.acp.installations.shutil.which", lambda _name: None)
    isolated_container._acp_service = AcpService(command_resolver=lambda _agent: pytest.fail("must not start"))
    isolated_container._acp_auth_manager = AcpAuthManager(isolated_container._acp_service)
    @asynccontextmanager
    async def lifespan(_app):
        yield
        if isolated_container._acp_install_manager is not None:
            await isolated_container._acp_install_manager.close()

    app = FastAPI(lifespan=lifespan)
    app.add_middleware(CSRFProtectionMiddleware)
    app.include_router(routes.router)
    app.dependency_overrides[get_current_admin] = lambda: "admin"
    with TestClient(app) as client:
        yield client


def test_routes_require_admin():
    from agent.delivery.http.dashboard.routes import acp as routes

    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    assert client.get("/dashboard-api/acp/agents").status_code == 401
    assert client.get(f"/dashboard-api/acp/agents/{AGENT}").status_code == 401
    assert client.post(f"/dashboard-api/acp/agents/{AGENT}/install").status_code == 401
    assert client.post(f"/dashboard-api/acp/agents/{AGENT}/update").status_code == 401
    assert client.delete(f"/dashboard-api/acp/agents/{AGENT}").status_code == 401


def test_list_reports_only_cli_and_missing_installation(client):
    response = client.get("/dashboard-api/acp/agents")
    assert response.headers["cache-control"] == "no-store"
    agents = response.json()["agents"]
    assert len(agents) == 2
    by_id = {agent["id"]: agent for agent in agents}
    assert AGENT in by_id and OPENCODE_AGENT_ID in by_id
    agent = by_id[AGENT]
    assert agent["id"] == AGENT and agent["installed"] is False
    assert agent["installation_source"] == "path" and agent["executable_path"] is None
    assert agent["version"] is None and agent["auth"]["state"] == "not_installed"
    assert not ({"job", "sign_in"} & agent.keys())
    assert not ({"methods", "uses_api_key", "can_sign_out"} & agent["auth"].keys())
    opencode = by_id[OPENCODE_AGENT_ID]
    assert opencode["id"] == OPENCODE_AGENT_ID and opencode["installed"] is False
    assert opencode["installation_source"] == "path" and opencode["executable_path"] is None
    assert opencode["version"] is None and opencode["auth"]["state"] == "not_installed"


@pytest.mark.parametrize("agent_id", ["unknown", "antigravity-acp"])
def test_unknown_and_removed_runtimes_are_not_found(client, agent_id):
    assert client.get(f"/dashboard-api/acp/agents/{agent_id}").status_code == 404


def test_cli_status_forwards_agent_workspace_and_refresh(client, monkeypatch, isolated_container, tmp_path):
    from agent.modules.acp import launcher
    from agent.modules.acp.installations import managed_executable

    calls = []

    async def status(agent_id, **kwargs):
        calls.append((agent_id, kwargs))
        return {"signed_in": True, "state": "signed_in", "config_options": []}

    executable = managed_executable(AGENT)
    executable.parent.mkdir(parents=True)
    executable.touch()
    monkeypatch.setattr(launcher, "inspect_executable", lambda *_args, **_kwargs: ("1.5.10", None))
    monkeypatch.setattr(isolated_container._acp_auth_manager, "status", status)
    response = client.get(f"/dashboard-api/acp/agents/{AGENT}", params={
        "agent_name": " reviewer ", "cwd": str(tmp_path), "refresh": True,
    })
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert response.json()["version"] == "1.5.10" and response.json()["installed"]
    assert calls == [(AGENT, {"refresh": True, "agent_name": "reviewer", "cwd": str(tmp_path)})]


@pytest.mark.parametrize("method,path", [
    ("post", "/sign-in"),
    ("post", "/sign-in/attempt/complete"), ("post", "/sign-in/attempt/cancel"),
    ("post", "/api-key"), ("post", "/sign-out"),
])
def test_credential_routes_are_removed(client, method, path):
    status = client.get(f"/dashboard-api/acp/agents/{AGENT}")
    response = getattr(client, method)(f"/dashboard-api/acp/agents/{AGENT}{path}", headers={
        CSRF_HEADER_NAME: status.headers[CSRF_HEADER_NAME],
    })
    assert response.status_code == 404


def _management_status(client, url):
    response = client.get(url)
    response.raise_for_status()
    agent = response.json()
    return agent, {CSRF_HEADER_NAME: response.headers[CSRF_HEADER_NAME], "If-Match": agent["installation_revision"]}


def _wait_for_operation(client, url):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        agent, headers = _management_status(client, url)
        if agent["operation"]["state"] != "running":
            return agent, headers
        time.sleep(0.01)
    pytest.fail("ACP operation did not finish")


@pytest.mark.parametrize("replace_during", ["version", "auth"])
def test_status_retries_when_executable_is_replaced(client, monkeypatch, isolated_container, replace_during):
    from agent.modules.acp import launcher
    from agent.modules.acp.installations import discover_installation, managed_executable

    path = managed_executable(AGENT)
    path.parent.mkdir(parents=True)
    path.write_text("1.5.10", encoding="utf-8")
    replacement = path.with_name("replacement")

    def inspect(_agent_id, executable, **_kwargs):
        version = executable.read_text(encoding="utf-8")
        if replace_during == "version" and replacement.exists():
            replacement.replace(path)
        return version, None

    async def status(_agent_id, **_kwargs):
        if replace_during == "auth" and replacement.exists():
            replacement.replace(path)
        return {"signed_in": False, "state": "signed_out", "config_options": []}

    monkeypatch.setattr(launcher, "inspect_executable", inspect)
    monkeypatch.setattr(isolated_container._acp_auth_manager, "status", status)
    url = f"/dashboard-api/acp/agents/{AGENT}"
    initial, stale_headers = _management_status(client, url)
    assert initial["version"] == "1.5.10"

    replacement.write_text("1.5.11", encoding="utf-8")
    updated, _ = _management_status(client, url)
    assert updated["version"] == "1.5.11"
    assert updated["installation_revision"] == discover_installation(AGENT).revision()
    assert client.delete(url, headers=stale_headers).status_code == 409
    assert path.is_file()


def test_managed_install_update_uninstall(client, monkeypatch, isolated_container):
    from agent.modules.acp import launcher, management
    from agent.modules.acp.installations import managed_executable

    version = "1.5.10"
    download_started = threading.Event()
    release_download = threading.Event()
    fail_download = False

    def download(release, candidate, stop):
        download_started.set()
        assert release_download.wait(3)
        assert not stop.is_set()
        candidate.write_text(release.version, encoding="utf-8")
        if fail_download:
            raise management.AcpError("The download failed its checksum verification.")

    def inspect(_agent_id, executable, **_kwargs):
        return executable.read_text(encoding="utf-8"), None

    async def auth_status(_agent_id, **_kwargs):
        return {"signed_in": False, "state": "signed_out", "config_options": []}

    monkeypatch.setattr(management, "latest_release", lambda _agent_id: SimpleNamespace(version=version))
    monkeypatch.setattr(management, "download_executable", download)
    monkeypatch.setattr(management, "inspect_executable", inspect)
    monkeypatch.setattr(launcher, "inspect_executable", inspect)
    monkeypatch.setattr(isolated_container._acp_auth_manager, "status", auth_status)
    path = managed_executable(AGENT)
    url = f"/dashboard-api/acp/agents/{AGENT}"

    missing, headers = _management_status(client, url)
    assert missing["actions"] == {"install": True, "update": False, "uninstall": False}
    assert client.post(f"{url}/install", headers={"If-Match": missing["installation_revision"]}).status_code == 403
    assert client.post(f"{url}/install", headers={CSRF_HEADER_NAME: headers[CSRF_HEADER_NAME]}).status_code == 428
    response = client.post(f"{url}/install", headers=headers)
    assert response.status_code == 202 and response.headers["cache-control"] == "no-store"
    assert download_started.wait(3)
    pending, _ = _management_status(client, url)
    assert pending["operation"] == response.json()["operation"]
    assert not path.exists()
    assert client.post(f"{url}/install", headers=headers).status_code == 409
    release_download.set()

    installed, installed_headers = _wait_for_operation(client, url)
    assert installed["operation"]["state"] == "succeeded"
    assert installed["management_source"] == "managed" and installed["version"] == version
    assert installed["latest_version"] == version
    assert str(path) in installed["login_command"] and installed["login_command"].endswith("acp --login")
    assert launcher.resolve_cli_command(AGENT, cwd=str(path.parent)).argv[0] == str(path)
    assert client.delete(url, headers=headers).status_code == 409

    turn = SimpleNamespace(agent_id=AGENT, has_unresolved_work=lambda: True)
    service = isolated_container._acp_service
    service._turns["pending-approval"] = turn
    try:
        assert client.post(f"{url}/update", headers=installed_headers).status_code == 409
        assert client.delete(url, headers=installed_headers).status_code == 409
    finally:
        del service._turns["pending-approval"]

    version = "1.5.12"
    fail_download = True
    assert client.post(f"{url}/update", headers=installed_headers).status_code == 202
    failed, retry_headers = _wait_for_operation(client, url)
    assert failed["version"] == "1.5.10" and "checksum" in failed["operation"]["error"]
    assert list(path.parent.iterdir()) == [path]

    fail_download = False
    assert client.post(f"{url}/update", headers=retry_headers).status_code == 202
    updated, updated_headers = _wait_for_operation(client, url)
    assert updated["version"] == version and updated["operation"]["state"] == "succeeded"

    external = path.parent / "external-agy"
    external.write_text("1.5.8", encoding="utf-8")
    monkeypatch.setattr("agent.modules.acp.installations.shutil.which", lambda name: str(external) if name == "agy" else None)
    assert client.delete(url, headers=updated_headers).status_code == 202
    uninstalled, external_headers = _wait_for_operation(client, url)
    assert uninstalled["operation"]["state"] == "succeeded" and not path.exists()
    assert uninstalled["management_source"] == "unknown" and uninstalled["version"] == "1.5.8"
    assert client.delete(url, headers=external_headers).status_code == 422
    assert external.is_file()


def test_status_rejects_invalid_agent_name_parameters(client):
    assert client.get(f"/dashboard-api/acp/agents/{AGENT}", params={"agent_name": "--help"}).status_code == 422


@pytest.mark.parametrize("executable", [None, "C:/Program Files/agy/agy.exe"])
def test_dashboard_onboarding_recognizes_cli_on_path(monkeypatch, executable):
    from agent.delivery.http.dashboard.routes import dashboard

    monkeypatch.setattr("agent.modules.acp.installations.shutil.which", lambda _name: executable)
    onboarding = dashboard._build_onboarding(services=[], providers=[], agents_total=1)
    assert onboarding["needs_provider"] is (executable is None)


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
        conn.execute(text("""
            CREATE TABLE conversation_threads (
                id INTEGER PRIMARY KEY, thread_id VARCHAR(512) NOT NULL,
                kind VARCHAR(50) NOT NULL DEFAULT 'user',
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                deleted_at DATETIME
            )
        """))
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

    async def fake_run(**_kwargs):
        return "done"

    async def fake_send_notification(*_args, **_kwargs):
        return True

    await isolated_container.initialize_persistence()
    monkeypatch.setattr(scheduler_service, "_run_scheduled_agent", fake_run)
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
    from agent.delivery.http.api.events_v2 import router as events_router
    app.include_router(events_router)
    app.dependency_overrides[get_current_admin] = lambda: "admin"

    response = TestClient(app).post("/v2/api/chat/events/edit", json={
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
    from agent.delivery.http.dashboard.router import router
    from agent.modules.admin_auth import get_current_admin

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_admin] = lambda: "test_admin"
    client = TestClient(app)

    response = client.get("/settings/acp", headers={"Accept": "text/html"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert '<div id="root">' in response.text

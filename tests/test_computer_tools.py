"""Contract tests for the backend-neutral computer layer, run against a fake desktop backend."""

from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import ToolMessage

from agent.modules.tools.result import ToolError, ToolErrorCode
from agent.modules.workspaces import WorkspaceRef
from agent.modules.workspaces.computer import (
    ComputerControl,
    ComputerError,
    ComputerScreenshot,
    ComputerStep,
    ViewerEndpoint,
    normalize_keys,
    xdotool_input,
)

BACKEND = "fakedesk"
LOCATOR = "box-1"


def png(width: int, height: int) -> bytes:
    from PIL import Image

    output = io.BytesIO()
    Image.new("RGB", (width, height), (40, 80, 120)).save(output, format="PNG")
    return output.getvalue()


class FakeComputer:
    """In-memory ``WorkspaceComputer``."""

    def __init__(self, width: int = 1280, height: int = 800) -> None:
        self.size = (width, height)
        self.steps: list[ComputerStep] = []
        self.fail_on: str | None = None
        self.screenshot_error: ComputerError | None = None
        self.human_control: list[bool] = []
        self.invalidated: list[str] = []

    async def status(self, locator):
        return {"running": True, "locator": locator}

    async def start(self, locator, *, width, height):
        self.size = (width, height)
        return {"running": True}

    async def screenshot(self, locator):
        if self.screenshot_error is not None:
            raise self.screenshot_error
        return ComputerScreenshot.from_png(png(*self.size))

    async def act(self, locator, step):
        if step.action == self.fail_on:
            raise ComputerError("upstream", f"{step.action} broke")
        self.steps.append(step)
        return {"ok": True}

    async def set_human_control(self, locator, active):
        self.human_control.append(active)
        return {"controller": "human" if active else "agent"}

    async def viewer_endpoint(self, locator):
        return ViewerEndpoint("ws://desktop.invalid/vnc", {"Authorization": "secret"})

    def invalidate(self, locator):
        self.invalidated.append(locator)


_current = FakeComputer()


def fake_computer() -> FakeComputer:
    return _current


@pytest.fixture
def computer(isolated_container):
    global _current
    from agent.modules.workspaces import WorkspaceBackendDescriptor, get_workspace_backend_registry

    _current = FakeComputer()
    get_workspace_backend_registry().register(WorkspaceBackendDescriptor(
        kind="workspace_backend", name=BACKEND, title="Fake desktop", config_prefix="",
        loader=f"{__name__}:FakeComputer",
        computer_loader=f"{__name__}:fake_computer",
    ), replace=True)
    return _current


@pytest.fixture
def tools(computer, tmp_path, monkeypatch):
    """The tool module aimed at the fake desktop, with a local main workspace and permissive rules."""
    from agent.modules.tools.builtin import computer as module
    from agent.modules.tools.coding.models import InvocationContext
    from agent.modules.tools.coding.permissions import Permissions
    from agent.modules.tools.coding.storage import OutputStore
    from agent.modules.tools.runtime.targets import ExecutionTarget

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    module.rules = []
    module.secrets = {}

    def context(runtime):
        return InvocationContext("default", str(workspace), "t1", tool_call_id=runtime.tool_call_id,
                                 permission_rules=tuple(module.rules))

    async def resolve_project_secrets(ctx):
        return dict(module.secrets)

    service = SimpleNamespace(permissions=Permissions(OutputStore(tmp_path / "permissions")),
                              resolve_project_secrets=resolve_project_secrets)
    monkeypatch.setattr("agent.modules.tools.coding.adapter.invocation_context", context)
    monkeypatch.setattr("agent.modules.tools.coding.service.get_coding_service", lambda: service)

    async def resolve_target(runtime, target):
        return ExecutionTarget(BACKEND, LOCATOR, "/", False)

    monkeypatch.setattr(module, "resolve_target", resolve_target)
    monkeypatch.setattr(module, "SETTLE_SECONDS", 0)
    monkeypatch.setattr(module, "OPEN_URL_SETTLE_SECONDS", 0)
    module.workspace = workspace
    return module


def runtime(workspace: Path, call_id: str = "call-1"):
    return SimpleNamespace(context={"workspace": WorkspaceRef(backend="local", locator=str(workspace)), "agent_name": "default"},
                           config={"configurable": {"thread_id": "t1"}}, tool_call_id=call_id, state={})


def allow(tools, *actions):
    from agent.modules.tools.coding.models import PermissionRule

    tools.rules.extend(PermissionRule(action=action, effect="allow") for action in actions)


# Vocabulary ----------------------------------------------------------------- #


def test_key_names_are_normalized_and_mapped_for_x11():
    assert normalize_keys(["Control", "Shift", "T"]) == ("ctrl", "shift", "t")
    assert normalize_keys("Return") == ("enter",)
    assert normalize_keys(["cmd", "PageDown"]) == ("super", "pagedown")
    with pytest.raises(ComputerError, match="Unknown key"):
        normalize_keys(["hyperdrive"])
    command = xdotool_input(ComputerStep.parse({"action": "key", "keys": ["ctrl", "+"]})).command
    assert command == "xdotool key -- ctrl+plus"
    assert xdotool_input(ComputerStep.parse({"action": "key", "keys": ["esc"]})).command == "xdotool key -- Escape"


def test_step_parsing_drops_unused_settings_and_refuses_misplaced_intent():
    step = ComputerStep.parse({"action": "click", "x": 3, "y": 4, "direction": "up", "seconds": 2, "text": "",
                               "keys": [], "modifiers": ["shift"]})
    assert (step.x, step.y, step.text, step.direction, step.modifiers) == (3, 4, None, "down", ("shift",))
    # A click that also carries text would silently skip the typing.
    with pytest.raises(ComputerError, match="click does not use text; add a separate type step"):
        ComputerStep.parse({"action": "click", "x": 3, "y": 4, "text": "hello"})
    with pytest.raises(ComputerError, match="type does not use keys; add a separate key step"):
        ComputerStep.parse({"action": "type", "text": "hello", "keys": ["enter"]})
    with pytest.raises(ComputerError, match="key does not use url"):
        ComputerStep.parse({"action": "key", "keys": ["enter"], "url": "https://a.test/"})
    with pytest.raises(ComputerError, match="exactly one of text or secret"):
        ComputerStep.parse({"action": "type", "text": "a", "secret": "TOKEN"})
    with pytest.raises(ComputerError, match="modifiers"):
        ComputerStep.parse({"action": "click", "x": 1, "y": 1, "modifiers": ["enter"]})
    with pytest.raises(ComputerError, match="seconds"):
        ComputerStep.parse({"action": "wait", "seconds": 60})
    assert ComputerStep.parse({"action": "triple_click", "x": 1, "y": 1}).button == "left"


def test_x11_commands_release_modifiers_and_scale_typing_timeouts():
    held = xdotool_input(ComputerStep.parse({"action": "click", "x": 1, "y": 2, "modifiers": ["ctrl"]})).command
    assert held.startswith("xdotool keydown ctrl; xdotool mousemove 1 2 click 1; status=$?")
    assert held.endswith("xdotool keyup ctrl; exit $status")
    assert xdotool_input(ComputerStep.parse({"action": "triple_click", "x": 1, "y": 2})).command.endswith("click --repeat 3 1")
    short = xdotool_input(ComputerStep.parse({"action": "type", "text": "hi"}))
    long = xdotool_input(ComputerStep.parse({"action": "type", "text": "x" * 4000}))
    assert short.timeout == 30
    # 4000 characters at 12 ms each take 48 s; the command must outlive them.
    assert long.timeout > 48 * 1.4


def test_backends_get_the_desktop_capability_from_their_computer_loader(computer):
    from agent.modules.workspaces import get_workspace_backend_registry, has_workspace_computer

    registry = get_workspace_backend_registry()
    assert "desktop" in registry.descriptor(BACKEND).capabilities
    assert "desktop" in registry.descriptor("instabox").capabilities
    assert "desktop" not in registry.descriptor("local").capabilities
    assert has_workspace_computer(BACKEND) and not has_workspace_computer("local")


# Control -------------------------------------------------------------------- #


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_human_control_blocks_agents_and_expires():
    clock = Clock()
    control = ComputerControl(clock=clock)
    control.take(BACKEND, LOCATOR, "admin:alice")
    with pytest.raises(ComputerError) as other:
        control.take(BACKEND, LOCATOR, "admin:bob")
    assert other.value.code == "controlled_by_other"
    with pytest.raises(ComputerError) as blocked:
        control.assert_agent_may_act(BACKEND, LOCATOR)
    assert blocked.value.code == "human_in_control"
    control.assert_agent_may_act(BACKEND, "box-2")

    clock.now += 31
    control.assert_agent_may_act(BACKEND, LOCATOR)
    with pytest.raises(ComputerError) as lost:
        control.renew(BACKEND, LOCATOR, "admin:alice")
    assert lost.value.code == "control_lost"


def test_viewer_tickets_are_single_use_and_expire():
    clock = Clock()
    control = ComputerControl(clock=clock)
    ticket = control.issue_ticket(BACKEND, LOCATOR, "admin:a")
    assert control.consume_ticket(ticket).backend == BACKEND
    with pytest.raises(PermissionError):
        control.consume_ticket(ticket)
    late = control.issue_ticket(BACKEND, LOCATOR, "admin:a")
    clock.now += 61
    with pytest.raises(PermissionError):
        control.consume_ticket(late)


@pytest.mark.asyncio
async def test_invalidating_a_machine_closes_viewers_and_forgets_state(computer):
    from agent.modules.workspaces import ScreenInfo, get_computer_control, invalidate_workspace_computer

    control = get_computer_control()
    closer = control.register_viewer(BACKEND, LOCATOR)
    control.take(BACKEND, LOCATOR, "admin:a")
    control.remember_screen(BACKEND, LOCATOR, ScreenInfo(10, 10))
    await invalidate_workspace_computer(BACKEND, LOCATOR)
    assert closer.is_set() and not control.human_in_control(BACKEND, LOCATOR)
    assert control.screen(BACKEND, LOCATOR) is None and computer.invalidated == [LOCATOR]


# Tools ---------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_screenshot_reports_size_and_keeps_only_recent_images(tools, monkeypatch):
    monkeypatch.setattr(tools, "KEEP_SCREENSHOTS", 3)
    for _ in range(5):
        message = await tools._computer_screenshot(runtime(tools.workspace))
    assert isinstance(message, ToolMessage)
    assert "Screen: 1280x800 px" in message.content[0]["text"]
    assert message.content[1]["type"] == "image"
    assert message.artifact["computer"]["width"] == 1280
    saved = sorted((tools.workspace / tools.SCREENSHOT_DIR).glob("*.png"))
    assert len(saved) == 3
    assert message.artifact["computer"]["workspace_path"].endswith(saved[-1].name)


@pytest.mark.asyncio
async def test_large_screens_are_scaled_and_coordinates_mapped_back(tools, computer):
    computer.size = (2560, 1600)
    message = await tools._computer_screenshot(runtime(tools.workspace))
    width, height = message.artifact["computer"]["width"], message.artifact["computer"]["height"]
    assert width < 2560 and width * height <= 1_150_000
    assert "Scaled from 2560x1600" in message.content[0]["text"]

    await tools._computer_action(runtime(tools.workspace), [{"action": "click", "x": width - 1, "y": height - 1}])
    clicked = computer.steps[-1]
    assert 2550 <= clicked.x < 2560 and 1590 <= clicked.y < 1600
    with pytest.raises(ToolError, match="outside the last screenshot"):
        await tools._computer_action(runtime(tools.workspace), [{"action": "move", "x": width + 5, "y": 1}])


@pytest.mark.asyncio
async def test_coordinates_are_scaled_when_the_remembered_screen_is_lost(tools, computer):
    from agent.modules.workspaces import get_computer_control

    computer.size = (2560, 1600)
    message = await tools._computer_screenshot(runtime(tools.workspace))
    width, height = message.artifact["computer"]["width"], message.artifact["computer"]["height"]
    # A restart or a restored machine forgets the screen, but the conversation keeps its coordinates.
    get_computer_control().invalidate(BACKEND, LOCATOR)
    await tools._computer_action(runtime(tools.workspace), [{"action": "click", "x": width - 1, "y": height - 1}],
                                 screenshot=False)
    clicked = computer.steps[-1]
    assert 2550 <= clicked.x < 2560 and 1590 <= clicked.y < 1600
    get_computer_control().invalidate(BACKEND, LOCATOR)
    with pytest.raises(ToolError, match="outside the last screenshot"):
        await tools._computer_action(runtime(tools.workspace), [{"action": "move", "x": width + 5, "y": 1}])
    # Steps without coordinates need no measurement.
    get_computer_control().invalidate(BACKEND, LOCATOR)
    computer.screenshot_error = ComputerError("timeout", "slow")
    await tools._computer_action(runtime(tools.workspace), [{"action": "key", "keys": ["tab"]}], screenshot=False)
    assert computer.steps[-1].action == "key"


@pytest.mark.asyncio
async def test_several_steps_run_in_order_and_wait_stays_local(tools, computer):
    allow(tools, "computer_type")
    result = json.loads(await tools._computer_action(runtime(tools.workspace), [
        {"action": "click", "x": 10, "y": 10},
        {"action": "wait", "seconds": 0},
        {"action": "type", "text": "hello"},
        {"action": "key", "keys": ["Return"]},
    ], screenshot=False))
    assert result["completed"] == 4 and result["ok"] is True
    assert [step.action for step in computer.steps] == ["click", "type", "key"]
    assert computer.steps[-1].keys == ("enter",)


@pytest.mark.asyncio
async def test_action_shows_the_desktop_by_default(tools, computer):
    message = await tools._computer_action(runtime(tools.workspace), [{"action": "click", "x": 10, "y": 10}])
    assert isinstance(message, ToolMessage) and message.content[1]["type"] == "image"
    assert "Desktop steps done (click)" in message.content[0]["text"]
    assert message.artifact["computer"]["completed"] == 1


def test_steps_tolerate_empty_values_for_unused_fields(tools):
    # Some models fill in every field of the schema.
    filled = {"action": "click", "x": 1, "y": 2, "x2": None, "button": "", "modifiers": [], "direction": "",
              "amount": 0, "text": "", "secret": None, "keys": [], "url": "", "seconds": None}
    step = tools._parse_steps([tools.ComputerStepInput.model_validate(filled)])[0]
    assert (step.action, step.x, step.y, step.button, step.text) == ("click", 1, 2, "left", None)
    with pytest.raises(ToolError, match="Step 1: Action click does not use text"):
        tools._parse_steps([{"action": "click", "x": 1, "y": 2, "text": "hello"}])


@pytest.mark.asyncio
async def test_partial_failure_reports_the_steps_that_ran(tools, computer):
    computer.fail_on = "key"
    message = await tools._computer_action(runtime(tools.workspace), [
        {"action": "click", "x": 1, "y": 1}, {"action": "key", "keys": ["tab"]}, {"action": "move", "x": 2, "y": 2}])
    assert isinstance(message, ToolMessage) and message.status == "error"
    assert "Steps 1-1 (click) ran; step 2 (key) failed" in message.content
    assert "Do not repeat" in message.content
    assert [step.action for step in computer.steps] == ["click"]

    computer.fail_on = "click"
    with pytest.raises(ToolError, match="Step 1 \\(click\\) failed"):
        await tools._computer_action(runtime(tools.workspace), [{"action": "click", "x": 1, "y": 1}])


@pytest.mark.asyncio
async def test_failed_screenshot_after_input_does_not_hide_the_input(tools, computer):
    await tools._computer_screenshot(runtime(tools.workspace))
    computer.screenshot_error = ComputerError("timeout", "slow")
    result = json.loads(await tools._computer_action(runtime(tools.workspace), [{"action": "click", "x": 1, "y": 1}]))
    assert result["ok"] is True and result["completed"] == 1
    assert "screenshot failed" in result["screenshot_error"] and len(computer.steps) == 1


@pytest.mark.asyncio
async def test_human_control_refuses_agent_input(tools, computer):
    from agent.modules.workspaces import get_computer_control

    get_computer_control().take(BACKEND, LOCATOR, "admin:alice")
    with pytest.raises(ToolError) as refused:
        await tools._computer_action(runtime(tools.workspace), [{"action": "click", "x": 1, "y": 1}])
    assert refused.value.code == ToolErrorCode.PERMISSION_DENIED
    assert "Do not retry right away" in refused.value.message
    assert refused.value.details["computer_code"] == "human_in_control"
    assert computer.steps == []


@pytest.mark.asyncio
async def test_open_url_rules_match_the_url(tools, computer):
    from agent.modules.tools.coding.models import PermissionRule

    tools.rules.append(PermissionRule(action="computer_open_url", resource="https://github.com/*", effect="allow"))
    await tools._computer_action(runtime(tools.workspace), [{"action": "open_url", "url": "https://github.com/k41"}])
    assert computer.steps[-1].url == "https://github.com/k41"
    with pytest.raises(ToolError) as asked:
        await tools._computer_action(runtime(tools.workspace), [{"action": "open_url", "url": "https://evil.test/"}])
    assert asked.value.code == ToolErrorCode.PERMISSION_DENIED


@pytest.mark.asyncio
async def test_secrets_are_typed_on_the_server_and_never_returned(tools, computer):
    tools.secrets = {"SITE_PASSWORD": "hunter2"}
    with pytest.raises(ToolError):
        # Typing a secret asks by default; this channel cannot ask.
        await tools._computer_action(runtime(tools.workspace), [{"action": "type", "secret": "SITE_PASSWORD"}])
    allow(tools, "computer_type_secret")
    result = await tools._computer_action(runtime(tools.workspace), [{"action": "type", "secret": "SITE_PASSWORD"}],
                                          screenshot=False)
    assert computer.steps[-1].text == "hunter2" and "hunter2" not in result
    with pytest.raises(ToolError, match="MISSING"):
        await tools._computer_action(runtime(tools.workspace), [{"action": "type", "secret": "MISSING"}])


def test_conversation_grants_ignore_coordinates(tmp_path, monkeypatch):
    import langgraph.types

    from agent.modules.tools.coding.models import InvocationContext
    from agent.modules.tools.coding.permissions import Permissions
    from agent.modules.tools.coding.storage import OutputStore

    asked = []

    def interrupt(payload):
        asked.append(payload)
        return {"action": "permission", "request_id": payload["request_id"], "decision": "allow_thread"}

    monkeypatch.setattr(langgraph.types, "interrupt", interrupt)
    permissions = Permissions(OutputStore(tmp_path))
    for call_id, x in (("call-1", 10), ("call-2", 500)):
        context = InvocationContext("default", str(tmp_path), "t1", tool_call_id=call_id, approval_supported=True)
        permissions.assert_allowed(context, "computer_open_url", "https://a.test/", details={"x": x})
    assert len(asked) == 1 and asked[0]["metadata"] == {"x": 10}


# Tool exposure -------------------------------------------------------------- #


def test_computer_tools_are_hidden_without_any_desktop(computer):
    from agent.modules.tools import get_tool_by_name
    from agent.modules.tools.resolver import ToolResolver

    tools = [get_tool_by_name(name) for name in ("computer_action", "computer_screenshot", "read")]
    names = lambda backend, extra=(): {tool.name for tool in ToolResolver().for_workspace(  # noqa: E731
        [*tools, *extra], WorkspaceRef(backend=backend, locator="/w" if backend == "local" else LOCATOR))}
    assert names("local") == {"read"}
    assert names(BACKEND) == {"computer_action", "computer_screenshot", "read"}
    # An agent that reaches auxiliary machines may drive their desktops.
    assert "computer_action" in names("local", [get_tool_by_name("copy_files")])


def test_batch_rule_groups_calls_by_machine():
    from agent.modules.tools.policy import browser_batch_error

    same = [{"name": "computer_action", "args": {"target": "m_1"}}, {"name": "computer_screenshot", "args": {"target": "m_1"}}]
    assert browser_batch_error(same) is not None
    other = [{"name": "computer_action", "args": {"target": "m_1"}}, {"name": "computer_action", "args": {}}]
    assert browser_batch_error(other) is None


# Dashboard ------------------------------------------------------------------ #


def _client(isolated_container):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from agent.delivery.http.dashboard.routes.computers import router, viewer_router
    from agent.modules.admin_auth import get_current_admin
    from agent.shared.config import ConfigService
    from agent.shared.config.default_source import DefaultConfigSource

    app = FastAPI()
    app.state.container = isolated_container
    isolated_container.config_service = ConfigService(sources=[DefaultConfigSource()])
    app.include_router(router)
    app.include_router(viewer_router)

    async def admin(_request=None):
        return "alice"

    app.dependency_overrides[get_current_admin] = admin
    return TestClient(app)


def test_dashboard_control_routes_work_for_any_backend(computer, isolated_container):
    from agent.modules.workspaces import get_computer_control

    client = _client(isolated_container)
    base = f"/dashboard-api/computers/{BACKEND}/{LOCATOR}"
    assert client.post(f"{base}/start", json={}).json()["running"] is True
    taken = client.post(f"{base}/takeover")
    assert taken.status_code == 200 and taken.json()["app_controller"] == "admin:alice"
    assert get_computer_control().human_in_control(BACKEND, LOCATOR)
    assert client.post(f"{base}/heartbeat").status_code == 200
    assert client.post(f"{base}/handback").json()["app_controller"] is None
    assert computer.human_control == [True, True, False]
    assert client.post(f"{base}/heartbeat").json()["detail"]["code"] == "control_lost"
    ticket = client.post(f"{base}/viewer-ticket").json()["ticket"]
    assert get_computer_control().consume_ticket(ticket).locator == LOCATOR
    missing = client.get("/dashboard-api/computers/local/x")
    assert missing.status_code == 404 and missing.json()["detail"]["code"] == "unsupported"

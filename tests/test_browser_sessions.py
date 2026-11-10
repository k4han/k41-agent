import asyncio
import base64
import hashlib
import json
import shutil
from types import SimpleNamespace

import pytest
import pytest_asyncio
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, END, StateGraph
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.types import Command
from sqlalchemy import MetaData, select
from sqlalchemy.ext.asyncio import AsyncSession
from stagehand.rpc_client import RPCError
from stagehand_middleware.facade import StagehandFacadeExecutionError, StagehandFacadeInputError, contract_error
from stagehand_middleware.facade.tools import RUN_BATCH_TIMEOUT_MS

from agent.modules.browser.models import BrowserAccountProfile, BrowserArtifact, BrowserBinding, BrowserHandoffResume, BrowserInvocation, BrowserProfile, BrowserProjectProfile
from agent.modules.browser import service as browser_service
from agent.modules.browser.service import ACTION_OBSERVE_FAILED, MAX_RUN_RESULT_BYTES, MIN_OPERATION_TIMEOUT_SECONDS, BrowserError, BrowserService, storage_namespace, storage_root
from agent.modules.browser.snapshot import NO_CHANGES
from agent.modules.projects.models import Project
from agent.modules.tools.builtin.browser import BROWSER_TOOLS, browser_batch_error
from agent.modules.workspaces.models import ThreadWorkspace
from agent.shared.infrastructure.db.engine import initialize_async_engine, close_async_engine


class FakeFacade:
    def __init__(self):
        self.submits = 0
        self.failure = False
        self.snapshots = 0
        self.snapshot_text = "button Login [1-1]"

    async def run(self, **values):
        self.submits += 1
        if self.failure:
            raise ConnectionError("Lost response after submit")
        return {"submitted": self.submits}

    async def snapshot(self, **values):
        self.snapshots += 1
        return self.snapshot_text

    async def screenshot(self, **values):
        return {"data": base64.b64encode(b"image").decode(), "mimeType": f"image/{values.get('type', 'jpeg')}"}


class FakeDriver:
    def __init__(self):
        self.facade = FakeFacade()
        self.closed = False
        self.root = self
        self.children = []
        self.tab_id = "page"

    async def create_session(self, artifacts):
        child = type(self)()
        child.root = self
        child.tab_id = "page" if not self.children else f"page-{len(self.children) + 1}"
        self.children.append(child)
        return child

    async def close(self):
        self.closed = True
        for child in self.children:
            child.closed = True

    async def tabs(self):
        if self.closed:
            raise ConnectionError("closed")
        return [{"id": self.tab_id, "url": "http://localhost", "title": "Login"}]


def make_browser_metadata():
    from agent.modules.admin_auth.models import AdminCredential

    metadata = MetaData()
    for model in (BrowserAccountProfile, BrowserArtifact, BrowserBinding, BrowserInvocation, BrowserProfile, BrowserProjectProfile,
                  Project, ThreadWorkspace, AdminCredential):
        model.__table__.to_metadata(metadata)
    return metadata


@pytest_asyncio.fixture
async def browser(isolated_container, tmp_path):
    await initialize_async_engine(metadata=make_browser_metadata(), container=isolated_container)

    async def launch(*args):
        return FakeDriver()

    service = BrowserService(isolated_container, root=tmp_path / "browser", launcher=launch)
    isolated_container._browser_service = service
    yield service
    await service.shutdown()
    await close_async_engine(container=isolated_container)


@pytest.mark.asyncio
async def test_launch_failure_reports_setup_command_and_logs_original_error(browser, caplog):
    async def fail(*args):
        raise FileNotFoundError("Chrome installation not found")

    browser.launcher = fail
    with pytest.raises(BrowserError, match="k41 browser install") as caught:
        await browser.ensure("missing-browser", "admin:1")
    assert caught.value.code == "browser_unavailable"
    assert "Chrome installation not found" in caplog.text
    assert "missing-browser" not in browser.sessions


@pytest.mark.asyncio
async def test_session_observer_pushes_revisions_and_stops_with_session(browser):
    started = asyncio.Event()
    stopped = asyncio.Event()

    class ObservedDriver(FakeDriver):
        async def watch_tabs(self, notify):
            try:
                notify()
                started.set()
                await asyncio.Event().wait()
            finally:
                stopped.set()

    async def launch(*args):
        return ObservedDriver()

    browser.launcher = launch
    current = await browser.ensure("observed", "admin:1")
    await asyncio.wait_for(started.wait(), 1)
    changed = asyncio.Event()
    current.subscribers.add(changed)
    previous = current.revision
    await browser.execute("observed", "admin:1", "snapshot", "browser_snapshot", {})
    assert changed.is_set() and current.revision > previous
    status = await browser.status("observed", "admin:1")
    assert status["revision"] == current.revision
    await browser.close("observed", "admin:1")
    assert stopped.is_set() and current.observer is None


@pytest.mark.asyncio
async def test_viewer_status_remains_available_during_an_agent_operation(browser):
    current = await browser.ensure("busy-viewer", "admin:1")
    async with browser.thread_scope(current.thread_id), current.lock:
        status = await asyncio.wait_for(browser.viewer_status(current), 0.5)
    assert status["open"] and status["generation"] == current.generation


@pytest.mark.asyncio
async def test_shared_profile_sessions_and_ownership(browser):
    first = await browser.ensure("first", "admin:1")
    second = await browser.ensure("second", "admin:1")
    assert first.profile_key == second.profile_key
    assert first.driver.root is second.driver.root
    assert first.driver.facade is not second.driver.facade
    assert await first.driver.tabs() != await second.driver.tabs()
    with pytest.raises(PermissionError):
        await browser.ensure("first", "admin:2")
    profile = await browser.repository.create_profile("admin:1", "Personal")
    await browser.close("first", "admin:1")
    await browser.close("second", "admin:1")
    for thread in ["first", "second"]:
        await browser.select_profile(thread, "admin:1", profile["profile_id"])
    active = await browser.ensure("first", "admin:1")
    second = await browser.ensure("second", "admin:1")
    assert active.driver.root is second.driver.root
    with pytest.raises(BrowserError):
        await browser.delete_profile("admin:1", profile["profile_id"])
    await browser.close("first", "admin:1")
    assert not second.driver.closed
    assert (await browser.ensure("second", "admin:1")).profile_key == active.profile_key


async def add_project_thread(browser, thread_id, project_id=None):
    async with browser.repository.session() as session:
        if project_id is None:
            project = Project(key=f"local:{thread_id}", label=thread_id, workspace_json="{}")
            session.add(project)
            await session.flush()
            project_id = project.id
        session.add(ThreadWorkspace(thread_id=thread_id, project_id=project_id))
        await session.commit()
    return project_id


@pytest.mark.asyncio
async def test_project_profile_applies_to_inherited_conversations_on_reopen(browser):
    profile = await browser.create_profile("admin:1", "Work")
    project_id = await add_project_thread(browser, "existing")
    await browser.bind("existing", "admin:1")
    await browser.repository.set_project_profile(project_id, "admin:1", profile["profile_id"])
    await add_project_thread(browser, "new", project_id)
    await add_project_thread(browser, "isolated", project_id)
    await add_project_thread(browser, "other-owner", project_id)
    await browser.repository.bind("other-owner", "admin:2")
    await add_project_thread(browser, "unrelated", await add_project_thread(browser, "unrelated-seed"))

    assert (await browser.status("new", "admin:1"))["profile_id"] == profile["profile_id"]
    assert (await browser.ensure("new", "admin:1")).profile_key == profile["profile_id"]
    await browser.close("new", "admin:1")
    assert (await browser.ensure("existing", "admin:1")).profile_key == profile["profile_id"]
    await browser.close("existing", "admin:1")
    assert (await browser.repository.binding("other-owner"))["profile_id"] is None
    assert (await browser.ensure("unrelated", "admin:1")).profile_key != profile["profile_id"]

    await browser.bind("isolated", "admin:1")
    personal = await browser.create_profile("admin:1", "Personal")
    await browser.select_profile("isolated", "admin:1", personal["profile_id"])
    await browser.repository.set_project_profile(project_id, "admin:1", profile["profile_id"])
    assert (await browser.ensure("isolated", "admin:1")).profile_key == personal["profile_id"]


@pytest.mark.asyncio
async def test_concurrent_project_profile_selection_keeps_one_default(browser, monkeypatch):
    first = await browser.create_profile("admin:1", "Work")
    second = await browser.create_profile("admin:1", "Personal")
    project_id = await add_project_thread(browser, "concurrent-default")
    both_read = asyncio.Event()
    reads = 0
    original_scalar = AsyncSession.scalar

    async def synchronized_scalar(session, statement, *args, **kwargs):
        nonlocal reads
        result = await original_scalar(session, statement, *args, **kwargs)
        # Force both legacy read-then-insert requests to observe the missing row.
        if statement.column_descriptions[0].get("entity") is BrowserProjectProfile:
            reads += 1
            if reads == 2:
                both_read.set()
            await asyncio.wait_for(both_read.wait(), 5)
        return result

    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "scalar", synchronized_scalar)
        await asyncio.gather(*(browser.repository.set_project_profile(project_id, "admin:1", profile["profile_id"])
                               for profile in (first, second)))

    async with browser.repository.session() as session:
        rows = (await session.scalars(select(BrowserProjectProfile).where(
            BrowserProjectProfile.project_id == project_id, BrowserProjectProfile.owner_id == "admin:1"))).all()
    assert len(rows) == 1
    assert rows[0].profile_id in {first["profile_id"], second["profile_id"]}
    await browser.repository.set_project_profile(project_id, "admin:1", second["profile_id"])
    assert await browser.repository.project_profile(project_id, "admin:1") == second["profile_id"]


@pytest.mark.asyncio
async def test_project_and_temporary_defaults_are_independent_and_update_on_reopen(browser):
    first = await browser.create_profile("admin:1", "First")
    second = await browser.create_profile("admin:1", "Second")
    await browser.bind("existing-standalone", "admin:1")
    project_id = await add_project_thread(browser, "existing-project")
    await browser.bind("existing-project", "admin:1")
    await browser.repository.set_default_profile("admin:1", first["profile_id"])

    builtin = browser.repository.builtin_profile_id("admin:1")
    assert (await browser.status("standalone", "admin:1"))["profile_id"] == builtin
    assert (await browser.bind("existing-standalone", "admin:1"))["profile_id"] is None
    assert (await browser.status("other-owner", "admin:2"))["profile_id"] == browser.repository.builtin_profile_id("admin:2")
    await add_project_thread(browser, "legacy-project", project_id)
    assert (await browser.status("legacy-project", "admin:1"))["profile_id"] == first["profile_id"]

    await browser.repository.set_project_profile(project_id, "admin:1", None, "inherit")
    await add_project_thread(browser, "inherits-first", project_id)
    assert (await browser.status("inherits-first", "admin:1"))["profile_id"] == first["profile_id"]
    active = await browser.ensure("inherits-first", "admin:1")
    assert active.profile_key == first["profile_id"]
    assert (await browser.bind("existing-project", "admin:1"))["profile_id"] is None
    await browser.repository.set_default_profile("admin:1", second["profile_id"])
    assert (await browser.status("inherits-first", "admin:1"))["profile_id"] == first["profile_id"]
    await browser.close("inherits-first", "admin:1")
    assert (await browser.ensure("inherits-first", "admin:1")).profile_key == second["profile_id"]
    await add_project_thread(browser, "inherits-second", project_id)
    assert (await browser.status("inherits-second", "admin:1"))["profile_id"] == second["profile_id"]

    await browser.repository.set_project_profile(project_id, "admin:1", first["profile_id"], "profile")
    await add_project_thread(browser, "specific", project_id)
    assert (await browser.status("specific", "admin:1"))["profile_id"] == first["profile_id"]
    await browser.select_profile("specific", "admin:1", second["profile_id"])
    assert (await browser.status("specific", "admin:1"))["profile_id"] == second["profile_id"]
    await browser.select_profile("specific", "admin:1", None)
    assert (await browser.status("specific", "admin:1"))["profile_id"] == first["profile_id"]
    await browser.repository.set_default_profile("admin:1", second["profile_id"], temporary=True)
    assert (await browser.status("existing-standalone", "admin:1"))["profile_id"] == second["profile_id"]
    assert (await browser.status("specific", "admin:1"))["profile_id"] == first["profile_id"]


@pytest.mark.asyncio
async def test_default_profile_deletion_clears_defaults_without_rebinding_conversations(browser):
    profile = await browser.create_profile("admin:1", "Custom default")
    await browser.repository.set_default_profile("admin:1", profile["profile_id"])
    project_id = await add_project_thread(browser, "inherited")
    await browser.repository.set_project_profile(project_id, "admin:1", None, "inherit")
    await browser.bind("inherited", "admin:1")
    await browser.delete_profile("admin:1", profile["profile_id"])
    assert await browser.repository.default_profile("admin:1") == browser.repository.builtin_profile_id("admin:1")
    assert (await browser.repository.project_profile_settings(project_id, "admin:1"))["mode"] == "inherit"
    replacement = await browser.create_profile("admin:1", "Replacement")
    await browser.repository.set_default_profile("admin:1", replacement["profile_id"])
    assert (await browser.bind("inherited", "admin:1"))["profile_id"] is None
    await add_project_thread(browser, "new-inherited", project_id)
    assert (await browser.status("new-inherited", "admin:1"))["profile_id"] == replacement["profile_id"]
    assert (await browser.status("inherited", "admin:1"))["profile_id"] == replacement["profile_id"]


@pytest.mark.asyncio
async def test_sub_agent_shares_temporary_profile_with_other_conversations(browser):
    profile = await browser.create_profile("admin:1", "Temporary")
    await browser.repository.set_default_profile("admin:1", profile["profile_id"], temporary=True)
    child = await browser.ensure("parent:sub:helper:1", "admin:1")
    assert child.profile_key == profile["profile_id"] and child.inherited_profile
    other = await browser.ensure("another", "admin:1")
    assert other.driver.root is child.driver.root
    await browser.release_inherited_profile("parent:sub:helper:1")
    assert (await browser.ensure("parent", "admin:1")).profile_key == profile["profile_id"]


@pytest.mark.asyncio
async def test_project_profile_requires_owned_profile_and_existing_project(browser):
    profile = await browser.create_profile("admin:1", "Work")
    project_id = await add_project_thread(browser, "chat")
    with pytest.raises(PermissionError):
        await browser.repository.set_project_profile(project_id, "admin:2", profile["profile_id"])
    with pytest.raises(LookupError):
        await browser.repository.set_project_profile(project_id + 1, "admin:1", profile["profile_id"])
    await browser.repository.set_project_profile(project_id, "admin:1", profile["profile_id"])
    await browser.repository.set_project_profile(project_id, "admin:1", None)
    assert await browser.repository.project_profile(project_id, "admin:1") is None


@pytest.mark.asyncio
async def test_deleting_profile_clears_project_default(browser):
    profile = await browser.create_profile("admin:1", "Work")
    project_id = await add_project_thread(browser, "chat")
    await browser.repository.set_project_profile(project_id, "admin:1", profile["profile_id"])
    await browser.delete_profile("admin:1", profile["profile_id"])
    assert await browser.repository.project_profile(project_id, "admin:1") is None
    assert (await browser.ensure("chat", "admin:1")).profile_key == browser.repository.builtin_profile_id("admin:1")


@pytest.mark.asyncio
async def test_sub_agent_borrows_parent_profile_until_delegation_ends(browser):
    profile = await browser.create_profile("admin:1", "Work")
    await browser.select_profile("chat", "admin:1", profile["profile_id"])
    child = await browser.ensure("chat:sub:helper:1", "admin:1")
    assert child.profile_key == profile["profile_id"] and child.inherited_profile
    assert next(p for p in await browser.profiles("admin:1") if p["profile_id"] == profile["profile_id"])["open_thread_id"] == "chat:sub:helper:1"
    await browser.release_inherited_profile("chat:sub:helper:1")
    assert child.driver.closed and "chat:sub:helper:1" not in browser.sessions
    assert (await browser.ensure("chat", "admin:1")).profile_key == profile["profile_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("fails", [False, True])
async def test_delegation_returns_borrowed_profile_when_it_ends(browser, monkeypatch, fails):
    from agent.modules.tools.builtin.delegation.delegate_task import delegate_task
    from agent.modules.tools.result import ToolError

    profile = await browser.create_profile("admin:1", "Work")
    await browser.select_profile("chat", "admin:1", profile["profile_id"])
    borrowed = []

    async def run_agent(*, thread_id, browser_owner_id, **_values):
        borrowed.append(await browser.ensure(thread_id, browser_owner_id))
        if fails:
            raise RuntimeError("helper failed")
        return "done"

    catalog = SimpleNamespace(validate_call=lambda *args: True, get_agent=lambda name: object())
    monkeypatch.setattr("agent.modules.agent_runtime.run_agent_full", run_agent)
    monkeypatch.setattr("agent.modules.agents.get_catalog_service", lambda: catalog)
    runtime = SimpleNamespace(config={"configurable": {"thread_id": "chat"}},
                              context={"browser_owner_id": "admin:1", "agent_name": "default"})
    if fails:
        with pytest.raises(ToolError):
            await delegate_task.coroutine(task="work", sub_agent="helper", runtime=runtime)
    else:
        assert await delegate_task.coroutine(task="work", sub_agent="helper", runtime=runtime) == "done"
    assert borrowed[0].profile_key == profile["profile_id"] and borrowed[0].driver.closed
    assert (await browser.ensure("chat", "admin:1")).profile_key == profile["profile_id"]


@pytest.mark.asyncio
async def test_failed_release_does_not_fail_delegation(browser, monkeypatch):
    from agent.modules.browser import release_delegated_browser

    async def fail(thread_id):
        raise RuntimeError("cleanup failed")

    monkeypatch.setattr(browser, "release_inherited_profile", fail)
    await release_delegated_browser("chat:sub:helper:1")


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["completed", "failed", "cancelled"])
@pytest.mark.parametrize("shared", [False, True])
async def test_scheduled_run_releases_browser_before_reporting(browser, monkeypatch, outcome, shared):
    from unittest.mock import AsyncMock

    import agent.modules.conversations as conversations
    import agent.modules.workspaces as workspaces
    from agent.modules.scheduler import service as scheduler

    other = await browser.ensure("user-chat", "admin:1") if shared else None
    opened = []
    artifacts = []
    started = asyncio.Event()

    async def run(*, thread_id, browser_owner_id, **kwargs):
        current = await browser.ensure(thread_id, browser_owner_id)
        opened.append(current)
        result = await browser.execute(thread_id, browser_owner_id, "capture", "browser_screenshot", {})
        artifacts.append(result["artifact"])
        await browser.issue_ticket(thread_id, browser_owner_id)
        started.set()
        if outcome == "failed":
            raise RuntimeError("scheduled execution failed")
        if outcome == "cancelled":
            await asyncio.Event().wait()
        return "done"

    async def assert_released(*args, **kwargs):
        assert opened[0].closed and opened[0].driver.closed
        assert opened[0].thread_id not in browser.sessions

    finish = AsyncMock(side_effect=assert_released)
    notify = AsyncMock(side_effect=assert_released)
    workspace = workspaces.WorkspaceRef(locator=str(browser.root.parent), metadata={"no_workspace": True})
    monkeypatch.setattr(scheduler, "run_agent_full", run)
    monkeypatch.setattr(scheduler, "_record_run_start", AsyncMock(return_value=True))
    monkeypatch.setattr(scheduler, "_record_run_finish", finish)
    monkeypatch.setattr(scheduler, "_send_notification", notify)
    monkeypatch.setattr(scheduler, "describe_executor_model", lambda: {"provider": "test", "model": "test"})
    monkeypatch.setattr(conversations, "upsert_conversation_thread", AsyncMock())
    monkeypatch.setattr(workspaces, "create_no_workspace", AsyncMock(return_value=workspace))
    monkeypatch.setattr(workspaces, "remember_thread_workspace_ref", AsyncMock())

    execution = asyncio.create_task(scheduler.execute_scheduled_task(
        "api", "1", "open a page", browser_owner_id="admin:1"))
    if outcome == "cancelled":
        await asyncio.wait_for(started.wait(), 5)
        execution.cancel()
        with pytest.raises(asyncio.CancelledError):
            await execution
        finish.assert_not_awaited()
        notify.assert_not_awaited()
    else:
        await asyncio.wait_for(execution, 5)
        finish.assert_awaited_once()
        assert finish.await_args.kwargs["status"] == outcome
        if outcome == "completed":
            assert finish.await_args.kwargs["response"] == "done"
        else:
            assert finish.await_args.kwargs["error"] == "scheduled execution failed"
        notify.assert_awaited_once()

    current = opened[0]
    assert current.closed and current.driver.closed
    assert not (await browser.status(current.thread_id, "admin:1"))["open"]
    assert not browser.tickets
    assert await browser.repository.artifact("admin:1", artifacts[0]["artifact_id"])
    assert (browser.root / "profiles" / current.profile_key).is_dir()
    assert current.driver.root.closed is (not shared)
    if other:
        assert not other.driver.closed
        assert (await browser.status(other.thread_id, "admin:1"))["open"]


@pytest.mark.asyncio
async def test_sub_agent_shares_parent_browser_with_its_own_tabs(browser):
    profile = await browser.create_profile("admin:1", "Work")
    await browser.select_profile("chat", "admin:1", profile["profile_id"])
    parent = await browser.ensure("chat", "admin:1")
    child = await browser.ensure("chat:sub:helper:1", "admin:1")
    assert child.profile_key == parent.profile_key and child.inherited_profile
    assert child.driver.root is parent.driver.root
    assert await child.driver.tabs() != await parent.driver.tabs()
    await browser.release_inherited_profile("chat:sub:helper:1")
    assert "chat:sub:helper:1" not in browser.sessions
    assert not parent.driver.closed


@pytest.mark.asyncio
async def test_sub_agent_starts_on_project_profile_before_parent_opens_browser(browser):
    profile = await browser.create_profile("admin:1", "Work")
    project_id = await add_project_thread(browser, "chat")
    await browser.repository.set_project_profile(project_id, "admin:1", profile["profile_id"])
    assert (await browser.ensure("chat:sub:helper:1", "admin:1")).profile_key == profile["profile_id"]
    assert (await browser.repository.binding("chat:sub:helper:1"))["profile_id"] is None


@pytest.mark.asyncio
async def test_sub_agent_does_not_borrow_another_accounts_profile(browser):
    profile = await browser.create_profile("admin:1", "Work")
    await browser.select_profile("chat", "admin:1", profile["profile_id"])
    with pytest.raises(PermissionError):
        await browser.ensure("chat:sub:helper:1", "admin:2")


@pytest.mark.asyncio
async def test_profile_lists_all_conversations_sharing_it(browser):
    profile = await browser.create_profile("admin:1", "Work")
    for thread in ("first", "second"):
        await browser.select_profile(thread, "admin:1", profile["profile_id"])
    await browser.ensure("first", "admin:1")
    await browser.ensure("second", "admin:1")
    active = next(p for p in await browser.profiles("admin:1") if p["profile_id"] == profile["profile_id"])
    assert active["open_thread_id"] == "first"
    assert active["open_thread_ids"] == ["first", "second"]


@pytest.mark.asyncio
async def test_default_profile_is_provisioned_once_and_protected(browser):
    profiles = await asyncio.gather(*(browser.repository.ensure_default_profile("admin:1") for _ in range(8)))
    assert len(set(profiles)) == 1
    await browser.initialize_profiles(["admin:1"])
    listed = await browser.profiles("admin:1")
    assert len(listed) == 1 and listed[0]["is_builtin"] and listed[0]["is_default"]
    assert (browser.root / "profiles" / profiles[0]).is_dir()
    assert not browser.sessions and not browser.browsers
    with pytest.raises(BrowserError, match="cannot be renamed"):
        await browser.rename_profile("admin:1", profiles[0], "Renamed")
    with pytest.raises(BrowserError, match="cannot be renamed"):
        await browser.delete_profile("admin:1", profiles[0])
    custom = await browser.create_profile("admin:1", "Custom")
    await browser.repository.set_default_profile("admin:1", custom["profile_id"])
    await browser.repository.set_default_profile("admin:1", None)
    assert await browser.repository.default_profile("admin:1") == profiles[0]


@pytest.mark.asyncio
async def test_legacy_profile_named_default_keeps_its_id_and_login_files(browser):
    legacy = await browser.repository.create_profile("admin:1", "Default")
    directory = browser.root / "profiles" / legacy["profile_id"]
    directory.mkdir(parents=True)
    (directory / "Cookies").write_bytes(b"saved login")
    await browser.initialize_profiles(["admin:1"])
    profile = await browser.repository.profile(legacy["profile_id"], "admin:1")
    assert profile["name"].startswith("Default (legacy ") and not profile["is_builtin"]
    assert (directory / "Cookies").read_bytes() == b"saved login"
    assert len(await browser.repository.profiles("admin:1")) == 2


@pytest.mark.asyncio
async def test_startup_provisions_defaults_for_accounts_and_legacy_browser_owners(browser):
    from agent.modules.admin_auth.models import AdminCredential

    async with browser.repository.session() as session:
        session.add(AdminCredential(id=1, username="admin", password_hash="unused"))
        session.add(BrowserBinding(thread_id="legacy", owner_id="admin:2"))
        session.add(BrowserAccountProfile(owner_id="admin:3"))
        session.add(BrowserProjectProfile(project_id=1, owner_id="admin:4", mode="inherit"))
        await session.commit()
    await browser.initialize_profiles()
    await browser.initialize_profiles()
    for index in range(1, 5):
        owner = f"admin:{index}"
        profiles = await browser.repository.profiles(owner)
        assert len(profiles) == 1 and profiles[0]["is_builtin"]
        assert (browser.root / "profiles" / profiles[0]["profile_id"]).is_dir()
    assert not browser.browsers


@pytest.mark.asyncio
async def test_temporary_profile_changes_apply_after_reopen_and_preserve_overrides(browser):
    first = await browser.ensure("temp", "admin:1")
    custom = await browser.create_profile("admin:1", "Temporary")
    await browser.repository.set_default_profile("admin:1", custom["profile_id"], temporary=True)
    assert (await browser.status("temp", "admin:1"))["profile_id"] == first.profile_key
    await browser.close("temp", "admin:1")
    assert (await browser.ensure("temp", "admin:1")).profile_key == custom["profile_id"]
    await browser.close("temp", "admin:1")
    await browser.select_profile("temp", "admin:1", first.profile_key)
    await browser.repository.set_default_profile("admin:1", None, temporary=True)
    assert (await browser.status("temp", "admin:1"))["selected_profile_id"] == first.profile_key
    await browser.delete_profile("admin:1", custom["profile_id"])
    assert await browser.repository.default_profile("admin:1", temporary=True) == first.profile_key


@pytest.mark.asyncio
async def test_deleting_custom_profile_resets_all_configuration_references(browser):
    custom = await browser.create_profile("admin:1", "Custom")
    project = await add_project_thread(browser, "project")
    await browser.repository.set_project_profile(project, "admin:1", custom["profile_id"])
    await browser.repository.set_default_profile("admin:1", custom["profile_id"])
    await browser.repository.set_default_profile("admin:1", custom["profile_id"], temporary=True)
    await browser.select_profile("override", "admin:1", custom["profile_id"])
    await browser.delete_profile("admin:1", custom["profile_id"])
    builtin = browser.repository.builtin_profile_id("admin:1")
    for thread in ("project", "override", "temporary"):
        status = await browser.status(thread, "admin:1")
        assert status["profile_mode"] == "inherit" and status["profile_id"] == builtin


@pytest.mark.asyncio
async def test_timeout_invalidates_only_sessions_sharing_the_physical_browser(browser, monkeypatch):
    first = await browser.ensure("first", "admin:1")
    second = await browser.ensure("second", "admin:1")
    custom = await browser.create_profile("admin:1", "Separate")
    await browser.select_profile("unrelated", "admin:1", custom["profile_id"])
    unrelated = await browser.ensure("unrelated", "admin:1")
    ticket = await browser.issue_ticket("second", "admin:1")

    async def stalled(**values):
        await asyncio.Future()

    first.driver.facade.run = stalled
    monkeypatch.setattr(BrowserService, "timeout", property(lambda self: 0.02))
    with pytest.raises(TimeoutError):
        await browser.execute("first", "admin:1", "stalled", "browser_run", {"code": "submit()"})
    assert first.closed and second.closed and not unrelated.closed
    assert set(browser.sessions) == {"unrelated"} and len(browser.browsers) == 1
    with pytest.raises(PermissionError):
        browser.consume_ticket(ticket)
    with pytest.raises(BrowserError, match="may already have executed"):
        await browser.execute("first", "admin:1", "stalled", "browser_run", {"code": "submit()"})


@pytest.mark.asyncio
async def test_profile_names_are_unique_per_account_and_renamable(browser):
    first = await browser.create_profile("admin:1", "Work")
    second = await browser.create_profile("admin:1", "Shop")
    await browser.create_profile("admin:2", "work")
    with pytest.raises(BrowserError, match="already exists") as caught:
        await browser.create_profile("admin:1", "WORK")
    assert caught.value.code == "profile_name_taken"
    with pytest.raises(BrowserError, match="already exists"):
        await browser.rename_profile("admin:1", second["profile_id"], "work")
    assert (await browser.rename_profile("admin:1", first["profile_id"], "WORK"))["name"] == "WORK"
    assert (await browser.rename_profile("admin:1", first["profile_id"], "Office"))["name"] == "Office"
    with pytest.raises(PermissionError):
        await browser.rename_profile("admin:2", first["profile_id"], "Stolen")


@pytest.mark.asyncio
async def test_launch_retries_after_stopping_leftover_browser(browser, monkeypatch):
    attempts = []

    async def launch(*args):
        attempts.append(args)
        if len(attempts) == 1:
            raise RuntimeError("Chrome exited before its debugging port was ready with code 21")
        return FakeDriver()

    stopped = []
    browser.launcher = launch
    monkeypatch.setattr("agent.modules.browser.service.terminate_profile_processes", lambda path: stopped.append(path) or 1)
    current = await browser.ensure("chat", "admin:1")
    assert len(attempts) == 2 and stopped == [browser.root / "profiles" / current.profile_key]


@pytest.mark.asyncio
async def test_launch_does_not_retry_without_leftover_browser(browser, monkeypatch):
    attempts = []

    async def launch(*args):
        attempts.append(args)
        raise RuntimeError("no browser")

    browser.launcher = launch
    monkeypatch.setattr("agent.modules.browser.service.terminate_profile_processes", lambda path: 0)
    with pytest.raises(BrowserError) as caught:
        await browser.ensure("chat", "admin:1")
    assert caught.value.code == "browser_unavailable" and len(attempts) == 1


@pytest.mark.asyncio
async def test_timed_out_launch_is_cleaned_up_but_not_repeated(browser, monkeypatch):
    attempts = []

    async def launch(*args):
        attempts.append(args)
        await asyncio.sleep(30)

    stopped = []
    browser.launcher = launch
    monkeypatch.setattr(BrowserService, "timeout", 0.01)
    monkeypatch.setattr("agent.modules.browser.service.terminate_profile_processes", lambda path: stopped.append(path) or 1)
    with pytest.raises(BrowserError) as caught:
        await browser.ensure("chat", "admin:1")
    assert caught.value.code == "browser_unavailable"
    assert len(attempts) == 1 and len(stopped) == 1


@pytest.mark.asyncio
async def test_failed_close_stops_browser_before_releasing_profile(browser, monkeypatch):
    class StuckDriver(FakeDriver):
        async def close(self):
            raise RuntimeError("stuck")

    async def launch(*args):
        return StuckDriver()

    stopped = []
    browser.launcher = launch
    monkeypatch.setattr("agent.modules.browser.service.terminate_profile_processes", lambda path: stopped.append(path) or 1)
    current = await browser.ensure("chat", "admin:1")
    await browser.close("chat", "admin:1")
    assert stopped == [browser.root / "profiles" / current.profile_key]
    assert "chat" not in browser.sessions


@pytest.mark.asyncio
async def test_profile_in_use_stays_complete_and_retryable(browser, monkeypatch):
    profile = await browser.create_profile("admin:1", "Work")
    directory = browser.root / "profiles" / profile["profile_id"]
    directory.mkdir(parents=True)
    (directory / "Cookies").write_text("saved", encoding="utf-8")
    original = type(directory).rename

    def locked(self, target):
        raise PermissionError(32, "in use", str(self))

    monkeypatch.setattr(type(directory), "rename", locked)
    with pytest.raises(BrowserError, match="still in use") as caught:
        await browser.delete_profile("admin:1", profile["profile_id"])
    assert caught.value.code == "profile_busy"
    assert (directory / "Cookies").read_text(encoding="utf-8") == "saved"
    assert await browser.repository.profile(profile["profile_id"], "admin:1") == profile

    monkeypatch.setattr(type(directory), "rename", original)
    await browser.delete_profile("admin:1", profile["profile_id"])
    assert not directory.exists()
    assert all(item["is_builtin"] for item in await browser.repository.profiles("admin:1"))


@pytest.mark.asyncio
async def test_deleted_profile_reports_and_purges_files_still_in_use(browser, monkeypatch):
    profile = await browser.create_profile("admin:1", "Work")
    directory = browser.root / "profiles" / profile["profile_id"]
    directory.mkdir(parents=True)
    (directory / "Cookies").write_text("saved", encoding="utf-8")
    profiles = browser.root / "profiles"
    original = shutil.rmtree

    def locked(path, *args, **kwargs):
        raise PermissionError(32, "in use", str(path))

    monkeypatch.setattr("agent.modules.browser.service.shutil.rmtree", locked)
    assert await browser.delete_profile("admin:1", profile["profile_id"]) is False
    assert all(item["is_builtin"] for item in await browser.repository.profiles("admin:1"))
    assert not directory.exists() and len(list(profiles.glob(".discarded-*"))) == 1
    # The periodic retry runs even when nothing has used a browser since the failure.
    assert browser.sweeper is not None

    await browser.purge_discarded()
    assert len(list(profiles.glob(".discarded-*"))) == 1 and browser.undeleted

    monkeypatch.setattr("agent.modules.browser.service.shutil.rmtree", original)
    await browser.purge_discarded()
    assert not list(profiles.glob(".discarded-*")) and not browser.undeleted


@pytest.mark.asyncio
async def test_deleted_profile_reports_when_nothing_remains(browser):
    profile = await browser.create_profile("admin:1", "Work")
    (browser.root / "profiles" / profile["profile_id"]).mkdir(parents=True)
    assert await browser.delete_profile("admin:1", profile["profile_id"]) is True
    other = await browser.create_profile("admin:1", "Unused")
    assert await browser.delete_profile("admin:1", other["profile_id"]) is True


@pytest.mark.asyncio
async def test_startup_purges_data_a_previous_run_could_not_remove(browser):
    leftover = browser.root / "run-artifacts" / ".discarded-earlier"
    (leftover / "nested").mkdir(parents=True)
    restarted = BrowserService(browser.container, root=browser.root, launcher=browser.launcher)
    await restarted.purge_discarded()
    assert not leftover.exists() and restarted.sweeper is None


@pytest.mark.asyncio
async def test_only_login_profiles_are_checked_for_running_browsers(browser, monkeypatch):
    stopped = []
    monkeypatch.setattr("agent.modules.browser.service.terminate_profile_processes", lambda path: stopped.append(path) or 0)
    await browser.ensure("chat", "admin:1")
    key = browser.storage_key("chat")
    legacy = browser.root / "profiles" / key
    legacy.mkdir(parents=True)
    await browser.delete_thread("chat")
    assert stopped == [browser.root / "profiles" / key]
    assert not (browser.root / "run-artifacts" / key).exists()


@pytest.mark.parametrize("first,second", [
    ("postgresql+asyncpg://user:old@db:5432/k41", "postgresql+asyncpg://user:new@db:5432/k41"),
    ("postgresql+asyncpg://user:old@db:5432/k41", "postgresql+asyncpg://other:old@db:5432/k41"),
    ("postgresql+asyncpg://db:5432/k41?user=one&password=old", "postgresql+asyncpg://db:5432/k41?user=two&password=new"),
    ("postgresql+asyncpg://user:old@db:5432/k41?PASSWORD=old", "postgresql+asyncpg://db:5432/k41?pwd=new"),
    ("postgresql+asyncpg://user:old@db:5432/k41?ssl=require", "postgresql+asyncpg://user:new@db:5432/k41?ssl=require"),
])
def test_storage_namespace_ignores_credentials(first, second):
    assert storage_namespace(first) == storage_namespace(second)


@pytest.mark.parametrize("other", [
    "postgresql+asyncpg://user:old@db:5432/other",
    "postgresql+asyncpg://user:old@other:5432/k41",
    "postgresql+asyncpg://user:old@db:5432/k41?ssl=require",
])
def test_storage_namespace_separates_databases(other):
    assert storage_namespace("postgresql+asyncpg://user:old@db:5432/k41") != storage_namespace(other)


def test_storage_namespace_keeps_location_without_credentials():
    url = "sqlite+aiosqlite:///D:/data/k41.db"
    assert storage_namespace(url) == hashlib.sha256(url.encode()).hexdigest()[:16]


def test_storage_root_adopts_earlier_location(tmp_path):
    url = "postgresql+asyncpg://user:secret@db:5432/k41"
    legacy = tmp_path / hashlib.sha256(url.encode()).hexdigest()[:16]
    (legacy / "profiles" / "saved").mkdir(parents=True)
    root = storage_root(tmp_path, url)
    assert root.name == storage_namespace(url) and (root / "profiles" / "saved").is_dir() and not legacy.exists()


def test_storage_root_retries_a_failed_move_and_keeps_logins_reachable(tmp_path, monkeypatch):
    url = "postgresql+asyncpg://user:secret@db:5432/k41"
    legacy = tmp_path / hashlib.sha256(url.encode()).hexdigest()[:16]
    (legacy / "profiles" / "saved").mkdir(parents=True)
    original = type(legacy).rename

    def locked(self, target):
        raise PermissionError(32, "in use", str(self))

    monkeypatch.setattr(type(legacy), "rename", locked)
    assert storage_root(tmp_path, url) == legacy
    assert not (tmp_path / storage_namespace(url)).exists()

    monkeypatch.setattr(type(legacy), "rename", original)
    root = storage_root(tmp_path, url)
    assert root.name == storage_namespace(url) and (root / "profiles" / "saved").is_dir() and not legacy.exists()


def test_storage_root_prefers_current_location(tmp_path):
    url = "postgresql+asyncpg://user:secret@db:5432/k41"
    legacy = tmp_path / hashlib.sha256(url.encode()).hexdigest()[:16]
    legacy.mkdir()
    (tmp_path / storage_namespace(url)).mkdir()
    assert storage_root(tmp_path, url).name == storage_namespace(url) and legacy.is_dir()


@pytest.mark.asyncio
async def test_deleting_conversation_releases_parent_and_child_browsers(browser, monkeypatch):
    from unittest.mock import AsyncMock

    from agent.modules.workflows import delete_workflow_thread_tree

    profile = await browser.repository.create_profile("admin:1", "Personal")
    await browser.select_profile("chat", "admin:1", profile["profile_id"])
    parent = await browser.ensure("chat", "admin:1")
    child = await browser.ensure("chat:sub:helper:1", "admin:1")
    child_profile_path = browser.root / "profiles" / child.profile_key
    child_artifact_path = browser.root / "run-artifacts" / browser.storage_key(child.thread_id)
    parent_artifact_path = browser.root / "run-artifacts" / browser.storage_key(parent.thread_id)
    images = []
    for thread_id in ("chat", "chat:sub:helper:1"):
        result = await browser.execute(thread_id, "admin:1", "capture", "browser_screenshot", {})
        images.append(result["artifact"]["artifact_id"])
        await browser.execute(thread_id, "admin:1", "run", "browser_run", {"code": "submit()"})
    unrelated = await browser.ensure("chat-other", "admin:1")
    lookalike = await browser.ensure("chat:submarine", "admin:1")
    profile_path = browser.root / "profiles" / parent.profile_key
    login_state = profile_path / "login-state"
    login_state.write_text("saved login", encoding="utf-8")
    parent_ticket = await browser.issue_ticket("chat", "admin:1")
    child_ticket = await browser.issue_ticket("chat:sub:helper:1", "admin:1")
    unrelated_ticket = await browser.issue_ticket("chat-other", "admin:1")
    checkpointer = InMemorySaver()
    monkeypatch.setattr("agent.modules.workflows.checkpoint.store.get_checkpointer", lambda: checkpointer)
    monkeypatch.setattr("agent.modules.tools.clear_conversation_storage", AsyncMock())

    await delete_workflow_thread_tree("chat")

    assert parent.driver.closed and child.driver.closed
    assert not unrelated.driver.closed and not lookalike.driver.closed
    assert "chat" not in browser.sessions and "chat:sub:helper:1" not in browser.sessions
    for ticket in (parent_ticket, child_ticket):
        with pytest.raises(PermissionError):
            browser.consume_ticket(ticket)
    assert browser.consume_ticket(unrelated_ticket) is unrelated
    assert await browser.repository.profile(profile["profile_id"], "admin:1") == profile
    assert login_state.read_text(encoding="utf-8") == "saved login"
    assert child_profile_path.exists()
    assert not child_artifact_path.exists() and not parent_artifact_path.exists()
    assert not await browser.repository.thread_ids("chat")
    for artifact_id in images:
        assert await browser.repository.artifact("admin:1", artifact_id) is None
        assert not (browser.root / "images" / artifact_id).exists()
    assert await browser.repository.binding("chat-other")
    assert await browser.repository.binding("chat:submarine")
    await browser.select_profile("replacement", "admin:1", profile["profile_id"])
    assert (await browser.ensure("replacement", "admin:1")).profile_key == parent.profile_key


@pytest.mark.asyncio
async def test_deletion_cleans_persisted_data_without_existing_service(browser):
    from agent.modules.browser import delete_thread_browsers

    thread = "persisted_%"
    unrelated = "persisted_ab:sub:helper:1"
    child = f"{thread}:sub:helper:1"
    first = await browser.ensure(thread, "admin:1")
    second = await browser.ensure(child, "admin:1")
    await browser.ensure(unrelated, "admin:1")
    images = []
    for target in (thread, child, unrelated):
        result = await browser.execute(target, "admin:1", "capture", "browser_screenshot", {})
        images.append(result["artifact"]["artifact_id"])
    await browser.repository.record(child, "handoff", "hash", "handoff", {
        "handoff_id": "pending", "generation": second.generation, "expires_at": 0})
    await browser.shutdown()
    browser.container._browser_service = None
    restarted = browser.container.browser_service
    restarted.root = browser.root
    try:
        await delete_thread_browsers(thread)
        await delete_thread_browsers(thread)
        assert not await restarted.repository.thread_ids(thread)
        assert await restarted.repository.binding(unrelated)
        for current in (first, second):
            assert (browser.root / "profiles" / current.profile_key).exists()
            assert not (browser.root / "run-artifacts" / browser.storage_key(current.thread_id)).exists()
        assert not (browser.root / "images" / images[0]).exists()
        assert not (browser.root / "images" / images[1]).exists()
        assert (browser.root / "images" / images[2]).exists()
    finally:
        await restarted.shutdown()


@pytest.mark.asyncio
async def test_subagent_owner_allows_management_but_handoff_fails_without_interrupt(browser, monkeypatch):
    from agent.modules.tools.builtin import browser as tools
    from agent.modules.tools.result import ToolError

    thread_id = "chat:sub:helper:1"
    runtime = SimpleNamespace(context={"browser_owner_id": "admin:1"},
                              config={"configurable": {"thread_id": thread_id}}, tool_call_id="handoff")

    def unexpected(*args):
        pytest.fail("Sub-agent handoff must fail before permission or graph interrupts")

    monkeypatch.setattr(tools, "authorize", unexpected)
    monkeypatch.setattr("langgraph.types.interrupt", unexpected)
    with pytest.raises(ToolError, match="parent agent"):
        await tools._handoff(runtime, "Please sign in")
    assert not browser.sessions
    assert await browser.repository.binding(thread_id) is None
    session = await browser.ensure(*tools.identity(runtime))
    assert (await browser.status(thread_id, "admin:1"))["open"]
    with pytest.raises(PermissionError):
        await browser.status(thread_id, "admin:2")
    with pytest.raises(BrowserError, match="delegated sub-agents"):
        await browser.handoff(thread_id, "admin:1", "handoff", "Please sign in")
    assert await browser.repository.pending_handoff(thread_id) is None
    assert session.handoff_id is None
    await browser.close(thread_id, "admin:1")
    assert session.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("name, arguments", [
    ("_snapshot", {}),
    ("_run", {"code": "return 1"}),
    ("_screenshot", {}),
    ("_close", {}),
    ("_handoff", {"reason": "Please sign in"}),
])
async def test_browser_tools_without_authenticated_owner_do_not_bind(browser, monkeypatch, name, arguments):
    from agent.modules.tools.builtin import browser as tools
    from agent.modules.tools.result import ToolError, ToolErrorCode

    runtime = SimpleNamespace(context={}, config={"configurable": {"thread_id": "chat"}}, tool_call_id="call")

    def unexpected(*args):
        pytest.fail("Unauthenticated browser tools must fail before permission or graph interrupts")

    monkeypatch.setattr(tools, "authorize", unexpected)
    monkeypatch.setattr("langgraph.types.interrupt", unexpected)
    for dashboard_open in (False, True):
        with pytest.raises(ToolError, match="unavailable in channel runs") as error:
            await getattr(tools, name)(runtime, **arguments)
        assert error.value.code == ToolErrorCode.PERMISSION_DENIED
        binding = await browser.repository.binding(browser.canonical("chat"))
        if dashboard_open:
            assert binding["owner_id"] == "admin:1"
        else:
            assert binding is None
            assert not browser.sessions
            await browser.ensure("chat", "admin:1")


@pytest.mark.asyncio
async def test_operation_scope_serializes_waiters_without_lock_internals(browser):
    class PublicLock:
        """Expose only the public lock interface to exercise lock lifecycle handling."""

        def __init__(self):
            self.lock = asyncio.Lock()

        async def __aenter__(self):
            await self.lock.acquire()

        async def __aexit__(self, *args):
            self.lock.release()

        def locked(self):
            return self.lock.locked()

    browser.operation_locks["shared"] = PublicLock()
    entered = [asyncio.Event() for _ in range(4)]
    release = [asyncio.Event() for _ in range(4)]
    order = []
    tasks = []

    async def hold(index):
        async with browser.operation_scope("shared"):
            order.append(index)
            entered[index].set()
            await release[index].wait()

    try:
        tasks.append(asyncio.create_task(hold(0)))
        await asyncio.wait_for(entered[0].wait(), 2)
        tasks.extend(asyncio.create_task(hold(index)) for index in (1, 2))
        await asyncio.sleep(0)
        tasks[1].cancel()
        with pytest.raises(asyncio.CancelledError):
            await tasks[1]
        release[0].set()
        await tasks[0]
        await asyncio.wait_for(entered[2].wait(), 2)
        tasks.append(asyncio.create_task(hold(3)))
        await asyncio.sleep(0)
        assert not entered[3].is_set(), "New arrivals must share the lock already used by a waiter"
        release[2].set()
        await asyncio.wait_for(entered[3].wait(), 2)
        assert order == [0, 2, 3]
    finally:
        for event in release:
            event.set()
        await asyncio.gather(*tasks, return_exceptions=True)
    assert not browser.operation_locks
    assert not browser.operation_users


@pytest.mark.asyncio
async def test_slow_launch_does_not_block_unrelated_tools_or_duplicate_profiles(browser):
    ready, release = asyncio.Event(), asyncio.Event()
    profile = await browser.repository.create_profile("admin:1", "Shared")
    for thread in ("slow", "same-profile"):
        await browser.select_profile(thread, "admin:1", profile["profile_id"])
    unrelated = await browser.ensure("other", "admin:1")
    launches = 0

    async def launch(*args):
        nonlocal launches
        launches += 1
        ready.set()
        await release.wait()
        return FakeDriver()

    browser.launcher = launch
    pending = asyncio.create_task(browser.ensure("slow", "admin:1"))
    duplicate = None
    try:
        await asyncio.wait_for(ready.wait(), 2)
        duplicate = asyncio.create_task(browser.ensure("same-profile", "admin:1"))
        result = await asyncio.wait_for(browser.execute("other", "admin:1", "snapshot", "browser_snapshot", {}), 2)
        assert "Login" in result["content"] and not unrelated.closed
        assert launches == 1
    finally:
        release.set()
        await pending
        if duplicate:
            shared = await duplicate
            assert shared.driver.root is (await pending).driver.root
    assert launches == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["close", "delete", "reap"])
async def test_slow_cleanup_does_not_block_unrelated_browser_tools(browser, operation):
    targets = [await browser.ensure(thread, "admin:1") for thread in ("chat", "chat:sub:helper:1", "chat:sub:helper:2")]
    await browser.ensure("other", "admin:1")
    started, release = asyncio.Event(), asyncio.Event()
    closing = 0

    async def slow_close():
        nonlocal closing
        closing += 1
        if closing == 1:
            started.set()
        await release.wait()

    for current in targets:
        current.driver.close = slow_close
        current.last_activity -= browser.idle_ttl + 1
    if operation == "close":
        task = asyncio.create_task(browser.close("chat", "admin:1"))
    elif operation == "delete":
        task = asyncio.create_task(browser.delete_thread("chat"))
    else:
        task = asyncio.create_task(browser.reap_idle())
    try:
        await asyncio.wait_for(started.wait(), 2)
        result = await asyncio.wait_for(browser.execute("other", "admin:1", "snapshot", "browser_snapshot", {}), 2)
        assert "Login" in result["content"]
        if operation == "delete":
            with pytest.raises(BrowserError, match="being deleted"):
                await browser.ensure("chat:sub:new:1", "admin:1")
    finally:
        release.set()
        await task


@pytest.mark.asyncio
async def test_deletion_waits_for_inflight_mutation_before_removing_receipts(browser, monkeypatch):
    current = await browser.ensure("chat", "admin:1")
    started, release, deleting = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original_thread_ids = browser.repository.thread_ids

    async def slow_run(**values):
        started.set()
        await release.wait()
        return "submitted"

    async def list_threads(thread_id):
        result = await original_thread_ids(thread_id)
        deleting.set()
        return result

    current.driver.facade.run = slow_run
    monkeypatch.setattr(browser.repository, "thread_ids", list_threads)
    mutation = asyncio.create_task(browser.execute("chat", "admin:1", "submit", "browser_run", {"code": "submit()"}))
    cleanup = None
    try:
        await asyncio.wait_for(started.wait(), 2)
        cleanup = asyncio.create_task(browser.delete_thread("chat"))
        await asyncio.wait_for(deleting.wait(), 2)
        assert not cleanup.done()
    finally:
        release.set()
        await mutation
        if cleanup:
            await cleanup
    assert current.closed
    assert not await original_thread_ids("chat")
    assert await browser.repository.invocation("chat", "submit") is None


@pytest.mark.asyncio
async def test_mutation_replay_is_cached_and_unknown_outcomes_are_not_retried(browser):
    session = await browser.ensure("chat", "admin:1")
    args = {"code": "submit()"}
    results = await asyncio.gather(*(browser.execute("chat", "admin:1", "call", "browser_run", args) for _ in range(2)))
    assert results[0] == results[1]
    assert session.driver.facade.submits == 1
    with pytest.raises(PermissionError):
        await browser.execute("chat", "admin:2", "call", "browser_run", args)
    session.driver.facade.failure = True
    with pytest.raises(BrowserError, match="partially executed"):
        await browser.execute("chat", "admin:1", "failed", "browser_run", args)
    with pytest.raises(BrowserError, match="already have executed"):
        await browser.execute("chat", "admin:1", "failed", "browser_run", args)
    assert session.driver.facade.submits == 2
    with pytest.raises(BrowserError, match="changed"):
        await browser.execute("chat", "admin:1", "call", "browser_run", {"code": "other()"})


@pytest.mark.asyncio
@pytest.mark.parametrize("failure, code, expected", [
    (SyntaxError("Invalid JavaScript in run code at line 1, column 1."), "invalid_input", "Invalid JavaScript"),
    (StagehandFacadeInputError(contract_error("NO_HYDRATED_SNAPSHOT_ERROR")), "invalid_input", "call browser_snapshot first"),
    (StagehandFacadeInputError(contract_error("NAVIGATED_SNAPSHOT_ERROR")), "invalid_input", "call browser_snapshot again"),
    (StagehandFacadeInputError(contract_error("STALE_SNAPSHOT_ID_ERROR", id="1-9")), "invalid_input", 'Snapshot ID "1-9"'),
    (StagehandFacadeInputError("Invalid run input: 'id' must not be empty"), "invalid_input", "must not be empty"),
])
async def test_run_failures_before_execution_say_nothing_ran(browser, failure, code, expected):
    session = await browser.ensure("chat", "admin:1")

    async def fail(**values):
        raise failure

    session.driver.facade.run = fail
    with pytest.raises(BrowserError) as caught:
        await browser.execute("chat", "admin:1", "call", "browser_run", {"code": "x"})
    assert caught.value.code == code
    assert expected in str(caught.value) and "Nothing was executed" in str(caught.value)
    assert "partially executed" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure, expected", [
    (StagehandFacadeExecutionError("ReferenceError", "document is not defined"), "Cause: ReferenceError: document is not defined"),
    (StagehandFacadeInputError(contract_error("RUN_VALUE_SIZE_ERROR")), "Cause: A run can return at most 10 MB"),
    (RPCError(SimpleNamespace(code=-32000, data=None, message="Stagehand callback batch timed out after 60000ms")), "Cause: Stagehand callback batch timed out"),
    (RPCError(SimpleNamespace(code=-32000, data=None, message="failed for https://x.test/?apiKey=secret-value")), "apiKey=[redacted]"),
])
async def test_run_failures_after_execution_keep_their_cause(browser, failure, expected):
    session = await browser.ensure("chat", "admin:1")

    async def fail(**values):
        raise failure

    session.driver.facade.run = fail
    with pytest.raises(BrowserError) as caught:
        await browser.execute("chat", "admin:1", "call", "browser_run", {"code": "x"})
    assert caught.value.code == "unknown_outcome"
    assert "partially executed" in str(caught.value) and expected in str(caught.value)
    assert "secret-value" not in str(caught.value)


@pytest.mark.asyncio
async def test_unexpected_run_failures_do_not_expose_their_message(browser):
    session = await browser.ensure("chat", "admin:1")
    session.driver.facade.failure = True
    with pytest.raises(BrowserError, match="partially executed") as caught:
        await browser.execute("chat", "admin:1", "call", "browser_run", {"code": "x"})
    assert "Lost response" not in str(caught.value)


@pytest.mark.asyncio
async def test_oversized_code_results_become_a_notice_with_a_short_preview(browser):
    session = await browser.ensure("chat", "admin:1")
    returned = {}

    async def run(**values):
        return returned["value"]

    session.driver.facade.run = run

    async def execute(call, **values):
        return (await browser.execute("chat", "admin:1", call, "browser_run", values))["content"]

    returned["value"] = "x" * MAX_RUN_RESULT_BYTES
    assert await execute("at-limit", code="read()") == returned["value"]

    # The limit counts encoded bytes, and the misleading part lies beyond the preview.
    returned["value"] = "ệ" * 1200 + "MiniMax-M2.1" * 2000
    notice = await execute("over-limit", code="read()")
    assert f"{len(returned['value'].encode())} bytes" in notice and "do not repeat steps" in notice
    assert notice.endswith("ệ" * 1000) and "M2.1" not in notice

    returned["value"] = {"items": ["y" * MAX_RUN_RESULT_BYTES]}
    assert "not returned" in await execute("structured", code="read()")

    # A replay gets the same notice instead of the full result.
    returned["value"] = "z" * (MAX_RUN_RESULT_BYTES + 1)
    first = await execute("replayed", code="read()")
    returned["value"] = "other"
    assert await execute("replayed", code="read()") == first

    # Actions return a small progress object, so they stay as they are.
    returned["value"] = {"completed": 1, "note": "z" * (MAX_RUN_RESULT_BYTES + 1)}
    progress = (await execute("actions", actions=[{"op": "click", "id": "1-1"}])).splitlines()[0]
    assert json.loads(progress) == returned["value"]


@pytest.fixture
def instant_settle(monkeypatch):
    monkeypatch.setattr(browser_service, "ACTION_SETTLE_SECONDS", 0)


@pytest.mark.asyncio
async def test_actions_report_what_they_changed(browser, instant_settle):
    session = await browser.ensure("chat", "admin:1")
    facade = session.driver.facade
    page = "\n".join(["[1-1] RootWebArea: App", *(f"  [1-{index}] button: Item number {index}" for index in range(2, 12))])
    facade.snapshot_text = page
    action = [{"op": "click", "id": "1-2"}]

    async def execute(call, **values):
        return (await browser.execute("chat", "admin:1", call, "browser_run", values))["content"]

    await browser.execute("chat", "admin:1", "read", "browser_snapshot", {})

    async def run(**values):
        facade.snapshot_text = page + "\n  [1-20] menu: Models"
        return {"completed": 1, "url": "http://localhost/"}

    facade.run = run
    progress, *changes = (await execute("changed", actions=action)).splitlines()
    assert json.loads(progress) == {"completed": 1, "url": "http://localhost/"}
    assert "  [1-20] menu: Models" in changes and "button: Item number 2" not in "\n".join(changes)

    # The action's result is the new base, so a later comparison starts from it.
    read = await browser.execute("chat", "admin:1", "again", "browser_snapshot", {})
    assert read["content"] == NO_CHANGES

    assert (await execute("nothing", actions=action)).endswith(f"\n{NO_CHANGES}")

    # A new page shares no IDs with the old one, so the agent gets all of it.
    navigated = page.replace("[1-", "[1-9")

    async def navigate(**values):
        facade.snapshot_text = navigated
        return {"completed": 1, "url": "http://localhost/next"}

    facade.run = navigate
    assert (await execute("navigated", actions=action)).endswith(f"\n{navigated}")


@pytest.mark.asyncio
async def test_code_runs_do_not_read_the_page(browser):
    session = await browser.ensure("chat", "admin:1")
    await browser.execute("chat", "admin:1", "code", "browser_run", {"code": "submit()"})
    assert session.driver.facade.snapshots == 0


@pytest.mark.asyncio
async def test_failing_to_read_the_page_does_not_fail_finished_actions(browser, instant_settle, monkeypatch):
    session = await browser.ensure("chat", "admin:1")
    facade = session.driver.facade
    action = {"actions": [{"op": "click", "id": "1-1"}]}

    async def broken(**values):
        raise ConnectionError("Lost the page")

    facade.snapshot = broken
    first = await browser.execute("chat", "admin:1", "broken", "browser_run", action)
    assert first["content"].endswith(f"\n{ACTION_OBSERVE_FAILED}")
    assert facade.submits == 1

    # The receipt is complete, so a replay repeats neither the action nor the read.
    assert await browser.execute("chat", "admin:1", "broken", "browser_run", action) == first
    assert facade.submits == 1

    # A stalled read gets its own short limit instead of the watchdog that closes the browser.
    async def stalled(**values):
        await asyncio.Event().wait()

    facade.snapshot = stalled
    monkeypatch.setattr(browser_service, "ACTION_OBSERVE_TIMEOUT_SECONDS", 0.01)
    result = await browser.execute("chat", "admin:1", "stalled", "browser_run", action)
    assert result["content"].endswith(f"\n{ACTION_OBSERVE_FAILED}")
    assert browser.sessions["chat"] is session and not session.closed


@pytest.mark.asyncio
async def test_actions_do_not_read_the_page_without_snapshot_permission(browser, instant_settle):
    session = await browser.ensure("chat", "admin:1")

    def denied():
        raise BrowserError("permission_denied", "browser_snapshot needs approval")

    result = await browser.execute("chat", "admin:1", "denied", "browser_run",
                                   {"actions": [{"op": "click", "id": "1-1"}]}, authorize_snapshot=denied)
    assert result["content"].endswith(f"\n{ACTION_OBSERVE_FAILED}")
    assert session.driver.facade.submits == 1 and session.driver.facade.snapshots == 0


@pytest.mark.asyncio
async def test_run_input_error_says_how_to_recover_from_passing_both():
    from agent.modules.tools.builtin.browser import _run
    from agent.modules.tools.result import ToolError

    with pytest.raises(ToolError, match="call again with code set to null"):
        await _run(None, code="return 1;", actions=[{"op": "click", "id": "1-1"}])
    with pytest.raises(ToolError, match="Pass exactly one of code or actions.$"):
        await _run(None)


def test_reading_the_page_after_a_slow_run_fits_inside_the_watchdog():
    assert (RUN_BATCH_TIMEOUT_MS / 1000 + browser_service.ACTION_SETTLE_SECONDS
            + browser_service.ACTION_OBSERVE_TIMEOUT_SECONDS) < MIN_OPERATION_TIMEOUT_SECONDS


@pytest.mark.asyncio
async def test_operation_watchdog_outlasts_the_run_deadline(browser, monkeypatch):
    assert browser.timeout * 1000 > RUN_BATCH_TIMEOUT_MS
    monkeypatch.setattr(browser.container.config_service, "get_int", lambda *_args: 1)
    assert browser.timeout * 1000 > RUN_BATCH_TIMEOUT_MS
    monkeypatch.setattr(browser.container.config_service, "get_int", lambda *_args: 900)
    assert browser.timeout == 300


@pytest.mark.asyncio
async def test_snapshots_are_compacted_and_return_only_changes_by_default(browser):
    session = await browser.ensure("chat", "admin:1")
    facade = session.driver.facade
    seen = []
    original = facade.snapshot

    async def snapshot(**values):
        seen.append(values)
        return await original(**values)

    facade.snapshot = snapshot
    page = "\n".join(["[1-1] RootWebArea: App", "  [1-2] button: Send", "    [1-3] image",
                      *(f"  [1-{index}] button: Item number {index}" for index in range(4, 12))])
    facade.snapshot_text = page

    async def read(call, **values):
        return (await browser.execute("chat", "admin:1", call, "browser_snapshot", values))["content"]

    first = await read("one")
    assert first == page.replace("\n    [1-3] image", "")
    assert await read("two", include_iframes=True) == NO_CHANGES
    assert seen[1] == {"include_iframes": True}

    # An agent that lost the tree, for example to compaction, can always get it back.
    assert await read("whole", full=True) == first
    assert seen[2] == {}

    facade.snapshot_text = page + "\n  [1-20] menu: Models"
    assert "[1-20] menu: Models" in (changes := await read("three"))
    assert "[1-2] button: Send" not in changes

    assert await read("four", full=True) == first + "\n  [1-20] menu: Models"

    facade.snapshot_text = page.replace("[1-", "[1-9")
    assert await read("five") == facade.snapshot_text.replace("\n    [1-93] image", "")


@pytest.mark.asyncio
async def test_snapshots_return_the_full_tree_after_nodes_move(browser):
    session = await browser.ensure("chat", "admin:1")
    facade = session.driver.facade
    page = "\n".join([
        "[1-1] RootWebArea: App",
        "  [1-2] group: Available",
        "    [1-3] button: Alpha",
        "    [1-4] button: Beta",
        "  [1-5] group: Selected",
        "  [1-6] StaticText: " + "Unchanged footer text. " * 20,
    ])

    async def read(call):
        return (await browser.execute("chat", "admin:1", call, "browser_snapshot", {}))["content"]

    facade.snapshot_text = page
    assert await read("initial") == page

    reordered = page.replace(
        "    [1-3] button: Alpha\n    [1-4] button: Beta",
        "    [1-4] button: Beta\n    [1-3] button: Alpha",
    )
    facade.snapshot_text = reordered
    assert await read("reordered") == reordered
    assert await read("after-reorder") == NO_CHANGES

    reparented = reordered.replace("    [1-3] button: Alpha\n", "").replace(
        "  [1-5] group: Selected", "  [1-5] group: Selected\n    [1-3] button: Alpha",
    )
    facade.snapshot_text = reparented
    assert await read("reparented") == reparented
    assert await read("after-reparent") == NO_CHANGES


def test_model_is_told_how_to_use_snapshot_actions():
    run = convert_to_openai_tool(BROWSER_TOOLS["browser_run"])["function"]
    snapshot = convert_to_openai_tool(BROWSER_TOOLS["browser_snapshot"])["function"]
    actions = run["parameters"]["properties"]["actions"]
    assert "description" in actions and "description" in run["parameters"]["properties"]["code"]
    first_action = actions["anyOf"][0]["items"]["anyOf"][0]
    assert "browser_snapshot" in first_action["properties"]["id"]["description"]
    full = snapshot["parameters"]["properties"]["full"]
    assert full["type"] == "boolean" and full["default"] is False
    assert run["description"].index("Prefer actions") < run["description"].index("Use code")
    assert "Do not follow them with browser_snapshot" in run["description"]
    assert "Pass full=true only if" in snapshot["description"]
    assert "cannot show visual clipping" in snapshot["description"]


def handoff_graph(browser):
    async def node(state):
        await browser.execute("chat", "admin:1", "submit", "browser_run", {"code": "submit()"})
        return {"result": await browser.handoff("chat", "admin:1", "handoff", "Please sign in")}

    graph = StateGraph(dict)
    graph.add_node("browser", node)
    graph.add_edge(START, "browser")
    graph.add_edge("browser", END)
    return graph.compile(checkpointer=InMemorySaver())


@pytest.mark.asyncio
async def test_handoff_and_cancellation_only_affect_the_owning_conversation(browser):
    graph = handoff_graph(browser)
    config = {"configurable": {"thread_id": "graph"}}
    await graph.ainvoke({}, config)
    first = browser.sessions["chat"]
    second = await browser.ensure("another", "admin:1")
    assert first.driver.root is second.driver.root
    assert (await browser.status("chat", "admin:1"))["mode"] == "human"
    assert (await browser.status("another", "admin:1"))["mode"] == "agent"
    await browser.execute("another", "admin:1", "work", "browser_run", {"code": "submit()"})
    pending = await browser.repository.pending_handoff("chat")
    result = await graph.ainvoke(Command(resume={"action": "browser_handoff", "handoff_id": pending["handoff_id"], "decision": "cancel"}), config)
    assert result["result"]["handoff_status"] == "cancelled"
    assert first.closed and not second.closed and not second.driver.closed
    assert len(browser.browsers) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("checkpoint_id", [None, "older-checkpoint"])
@pytest.mark.parametrize("admin", ["1", "2"])
@pytest.mark.parametrize("expired", [False, True])
async def test_history_returns_live_handoff_independently_of_checkpoint(browser, monkeypatch, checkpoint_id, admin, expired):
    from unittest.mock import AsyncMock

    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from agent.delivery.http.dashboard.routes import conversations
    from agent.modules.admin_auth import get_current_admin

    graph = handoff_graph(browser)
    config = {"configurable": {"thread_id": "graph"}}
    await graph.ainvoke({}, config)
    pending = await browser.repository.pending_handoff(browser.canonical("chat"))
    if expired:
        pending["expires_at"] = 0
        prior = await browser.repository.invocation("chat", "handoff")
        await browser.repository.record("chat", "handoff", prior.arguments_hash, "handoff", pending)
    monkeypatch.setattr(browser.container, "_persistence_ready", True)
    messages = AsyncMock(return_value=([], checkpoint_id or "current-checkpoint"))
    monkeypatch.setattr(conversations, "get_thread_messages_payload", messages)
    monkeypatch.setattr(conversations, "get_conversation_thread", AsyncMock(return_value={"agent_name": "default"}))
    monkeypatch.setattr(conversations, "workspace_ref_for_thread", AsyncMock(return_value=None))
    monkeypatch.setattr(conversations, "get_thread_resume_error", AsyncMock(return_value=None))
    monkeypatch.setattr("agent.modules.tools.pending_permission_requests", AsyncMock(return_value=[]))
    app = FastAPI()
    app.include_router(conversations.router)
    app.dependency_overrides[get_current_admin] = lambda: admin
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/dashboard-api/chat-history/chat", params={"checkpoint_id": checkpoint_id} if checkpoint_id else {})
    assert response.status_code == 200
    expected = {**pending, "expired": True} if expired else pending
    assert response.json().get("pending_browser_handoffs", []) == ([expected] if admin == "1" else [])
    if expired and admin == "1":
        assert not browser.sessions
    messages.assert_awaited_once_with(browser.canonical("chat"), checkpoint_id=checkpoint_id, include_branch_metadata=True)
    assert (await graph.aget_state(config)).tasks[0].interrupts
    resumed = await graph.ainvoke(Command(resume={
        "action": "browser_handoff", "handoff_id": pending["handoff_id"], "decision": "return",
    }), config)
    assert resumed["result"]["handoff_status"] == ("expired" if expired else "returned")
    assert await browser.repository.pending_handoff(browser.canonical("chat")) is None
    assert await browser.owned_handoff("chat", "admin:1") is None


@pytest.mark.asyncio
async def test_handoff_interrupt_replay_and_reconnect(browser):
    graph = handoff_graph(browser)
    config = {"configurable": {"thread_id": "graph"}}
    waiting = await graph.ainvoke({}, config)
    session = next(iter(browser.sessions.values()))
    assert "result" not in waiting
    assert (await graph.aget_state(config)).tasks[0].interrupts
    assert session.driver.facade.snapshots == 0
    status = await browser.status("chat", "admin:1")
    assert status["mode"] == "human"
    with pytest.raises(BrowserError, match="user controls"):
        await browser.execute("chat", "admin:1", "blocked", "browser_snapshot", {})
    ticket = await browser.issue_ticket("chat", "admin:1")
    assert browser.consume_ticket(ticket) is session
    with pytest.raises(PermissionError):
        browser.consume_ticket(ticket)
    assert (await browser.status("chat", "admin:1"))["mode"] == "human"
    session.driver.facade.snapshot_text = "heading Search results [2-1]"
    result = await graph.ainvoke(Command(resume={"action": "browser_handoff", "handoff_id": session.handoff_id,
                                                "decision": "return"}), config)
    assert result["result"]["handoff_status"] == "returned"
    assert result["result"]["snapshot_status"] == "captured"
    assert result["result"]["snapshot"] == "heading Search results [2-1]"
    assert session.driver.facade.snapshots == 1
    assert session.driver.facade.submits == 1
    assert session.handoff_id is None
    session.driver.facade.snapshot_text = "A later page"
    replay = await browser.handoff("chat", "admin:1", "handoff", "Please sign in")
    assert replay == result["result"]
    assert session.driver.facade.snapshots == 1


@pytest.mark.asyncio
async def test_handoff_tool_observes_selected_tab_before_next_model_step(browser, monkeypatch):
    from langchain_core.messages import AIMessage
    from langgraph.graph import MessagesState
    from langgraph.prebuilt import ToolNode

    from agent.modules.tools.builtin import browser as tools
    from agent.modules.workflows.run_config import WorkflowContext

    authorized = []

    def authorize(runtime, name, values, *, allow_interrupt=True):
        authorized.append((name, values, allow_interrupt))

    monkeypatch.setattr(tools, "authorize", authorize)
    observed = []

    async def model_step(state):
        observed.append(json.loads(state["messages"][-1].content))
        return {}

    builder = StateGraph(MessagesState, context_schema=WorkflowContext)
    builder.add_node("handoff", ToolNode([tools.browser_handoff]))
    builder.add_node("model", model_step)
    builder.add_edge(START, "handoff")
    builder.add_edge("handoff", "model")
    builder.add_edge("model", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "chat"}}
    context = WorkflowContext(browser_owner_id="admin:1")
    await graph.ainvoke({"messages": [AIMessage(content="", tool_calls=[{
        "id": "handoff", "name": "browser_handoff", "args": {"reason": "Enter your search, then return control."},
    }])]}, config, context=context)
    session = next(iter(browser.sessions.values()))
    assert not observed and session.driver.facade.snapshots == 0
    assert (await graph.aget_state(config)).tasks[0].interrupts
    session.active_tab_id = "user-selected-tab"
    original_facade = session.driver.facade
    reset_tabs = []

    async def reset_facade(artifacts, tab_id):
        reset_tabs.append(tab_id)
        session.driver.facade = FakeFacade()
        session.driver.facade.snapshot_text = "heading User search results [2-1]"

    session.driver.reset_facade = reset_facade
    result = await graph.ainvoke(Command(resume={"action": "browser_handoff", "handoff_id": session.handoff_id,
                                                "decision": "return"}), config, context=context)
    assert reset_tabs == ["user-selected-tab"]
    assert original_facade.snapshots == 0
    assert session.driver.facade.snapshots == 1
    assert observed[0]["handoff_status"] == "returned"
    assert observed[0]["snapshot_status"] == "captured"
    assert observed[0]["snapshot"] == "heading User search results [2-1]"
    assert result["messages"][-1].artifact["display_content"] == result["messages"][-1].content
    assert authorized[-1] == ("browser_snapshot", {"include_iframes": True}, False)


@pytest.mark.asyncio
@pytest.mark.parametrize("effect", ["deny", "ask"])
async def test_handoff_snapshot_respects_permissions_without_another_interrupt(browser, monkeypatch, tmp_path, effect):
    from agent.modules.tools.builtin import browser as tools
    from agent.modules.tools.coding.contracts import InvocationContext
    from agent.modules.tools.coding.models import PermissionRule
    from agent.modules.tools.coding.permissions import Permissions
    from agent.modules.tools.coding.storage import OutputStore

    context = InvocationContext(agent_name="test", workspace=str(tmp_path), thread_id="chat",
        permission_rules=(PermissionRule(action="browser_snapshot", resource="*", effect=effect),))
    monkeypatch.setattr("agent.modules.tools.coding.adapter.invocation_context", lambda runtime: context)
    monkeypatch.setattr("agent.modules.tools.coding.service.get_coding_service",
                        lambda: SimpleNamespace(permissions=Permissions(OutputStore(tmp_path))))
    runtime = SimpleNamespace(context={"browser_owner_id": "admin:1"},
                              config={"configurable": {"thread_id": "chat"}}, tool_call_id="handoff")

    async def node(state):
        return {"result": await tools._handoff(runtime, "Enter your search, then return control.")}

    builder = StateGraph(dict)
    builder.add_node("handoff", node)
    builder.add_edge(START, "handoff")
    builder.add_edge("handoff", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "chat"}}
    await graph.ainvoke({}, config)
    session = next(iter(browser.sessions.values()))
    result = await graph.ainvoke(Command(resume={"action": "browser_handoff", "handoff_id": session.handoff_id,
                                                "decision": "return"}), config)
    content = json.loads(result["result"].content)
    assert content["handoff_status"] == "returned"
    assert content["snapshot_status"] == "failed"
    assert content["snapshot_error"]["code"] == "permission_denied"
    assert "snapshot" not in content
    assert not (await graph.aget_state(config)).tasks
    assert not session.closed and session.handoff_id is None
    assert session.driver.facade.snapshots == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["error", "timeout"])
async def test_handoff_snapshot_failure_is_explicit_and_replayable(browser, monkeypatch, failure):
    graph = handoff_graph(browser)
    config = {"configurable": {"thread_id": "graph"}}
    await graph.ainvoke({}, config)
    session = next(iter(browser.sessions.values()))
    calls = 0

    async def snapshot(**values):
        nonlocal calls
        calls += 1
        if failure == "timeout":
            await asyncio.sleep(1)
        raise ConnectionError("Private page data must not appear in the error")

    monkeypatch.setattr(BrowserService, "timeout", property(lambda self: 0.02))
    session.driver.facade.snapshot = snapshot
    result = await graph.ainvoke(Command(resume={"action": "browser_handoff", "handoff_id": session.handoff_id,
                                                "decision": "return"}), config)
    receipt = result["result"]
    assert receipt["handoff_status"] == ("session_lost" if failure == "timeout" else "returned")
    assert receipt["snapshot_status"] == "failed"
    assert receipt["snapshot_error"]["code"] == ("timeout" if failure == "timeout" else "snapshot_failed")
    assert "snapshot" not in receipt and "Private page data" not in json.dumps(receipt)
    assert session.closed == (failure == "timeout")
    assert await browser.handoff("chat", "admin:1", "handoff", "Please sign in") == receipt
    assert calls == 1 and session.driver.facade.submits == 1


@pytest.mark.asyncio
async def test_handoff_cancel_skips_observation_and_closes_browser(browser):
    graph = handoff_graph(browser)
    config = {"configurable": {"thread_id": "graph"}}
    await graph.ainvoke({}, config)
    session = next(iter(browser.sessions.values()))
    result = await graph.ainvoke(Command(resume={"action": "browser_handoff", "handoff_id": session.handoff_id,
                                                "decision": "cancel"}), config)
    assert result["result"]["handoff_status"] == "cancelled"
    assert result["result"]["snapshot_status"] == "skipped"
    assert session.closed and not browser.sessions
    assert session.driver.facade.snapshots == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["error", "timeout"])
async def test_handoff_facade_reset_failure_reports_lost_session_without_snapshot(browser, monkeypatch, failure):
    graph = handoff_graph(browser)
    config = {"configurable": {"thread_id": "graph"}}
    await graph.ainvoke({}, config)
    session = next(iter(browser.sessions.values()))

    async def reset_facade(*args):
        if failure == "timeout":
            await asyncio.sleep(1)
        raise ConnectionError("Facade connection lost")

    monkeypatch.setattr(BrowserService, "timeout", property(lambda self: 0.02))
    session.driver.reset_facade = reset_facade
    result = await graph.ainvoke(Command(resume={"action": "browser_handoff", "handoff_id": session.handoff_id,
                                                "decision": "return"}), config)
    assert result["result"]["handoff_status"] == "session_lost"
    assert result["result"]["snapshot_status"] == "skipped"
    assert session.closed and session.driver.facade.snapshots == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("decision, expected", [("return", "lost"), ("cancel", "cancelled")])
async def test_handoff_survives_browser_loss_without_accepting_stale_control(browser, decision, expected):
    graph = handoff_graph(browser)
    config = {"configurable": {"thread_id": "graph"}}
    await graph.ainvoke({}, config)
    pending = (await browser.status("chat", "admin:1"))["handoff"]
    await browser.close("chat", "admin:1")
    assert (await browser.status("chat", "admin:1"))["handoff_expired"]
    with pytest.raises(BrowserError, match="expired handoff"):
        await browser.ensure("chat", "admin:1")
    result = await graph.ainvoke(Command(resume={"action": "browser_handoff", "handoff_id": pending["handoff_id"],
                                                "decision": decision}), config)
    assert expected in result["result"]["content"]
    assert result["result"]["handoff_status"] == ("session_lost" if decision == "return" else "cancelled")
    assert result["result"]["snapshot_status"] == "skipped"
    assert not browser.sessions


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup", ["status", "ensure", "reap"])
@pytest.mark.parametrize("decision", ["return", "cancel"])
async def test_expired_handoff_releases_control_and_remains_safe_to_replay(browser, cleanup, decision):
    graph = handoff_graph(browser)
    config = {"configurable": {"thread_id": "graph"}}
    await graph.ainvoke({}, config)
    current = browser.sessions["chat"]
    prior = await browser.repository.invocation("chat", "handoff")
    payload = await browser.repository.pending_handoff("chat")
    assert payload["expires_at"] > 0
    payload["expires_at"] = 0
    await browser.repository.record("chat", "handoff", prior.arguments_hash, "handoff", payload)
    if cleanup == "status":
        status = await browser.status("chat", "admin:1")
        assert status["handoff"]["handoff_id"] == payload["handoff_id"]
        assert status["handoff"]["expired"] and status["handoff_expired"]
    elif cleanup == "ensure":
        assert await browser.ensure("chat", "admin:1") is not current
    else:
        await browser.reap_idle()
    assert current.closed
    assert await browser.repository.pending_handoff("chat") is None
    assert (await browser.repository.invocation("chat", "handoff")).status == "expired"
    assert (await browser.owned_handoff("chat", "admin:1"))["expired"]
    assert await browser.owned_handoff("chat", "admin:2") is None
    replacement = await browser.ensure("chat", "admin:1")
    status = await browser.status("chat", "admin:1")
    assert status["open"] and status["handoff_expired"]
    result = await graph.ainvoke(Command(resume={"action": "browser_handoff", "handoff_id": payload["handoff_id"],
                                               "decision": decision}), config)
    assert result["result"]["handoff_status"] == ("expired" if decision == "return" else "cancelled")
    assert result["result"]["snapshot_status"] == "skipped"
    assert (await browser.repository.invocation("chat", "handoff")).status == "completed"
    assert await browser.owned_handoff("chat", "admin:1") is None
    assert (await browser.status("chat", "admin:1"))["handoff"] is None
    assert await browser.handoff("chat", "admin:1", "handoff", "Please sign in") == result["result"]
    assert not (await graph.aget_state(config)).tasks
    assert current.driver.facade.snapshots == replacement.driver.facade.snapshots == 0
    assert not replacement.closed
    assert replacement.handoff_id is None
    assert current.driver.facade.submits == 1


@pytest.mark.asyncio
async def test_legacy_handoff_without_expiry_does_not_permanently_block_thread(browser):
    await browser.bind("chat", "admin:1")
    await browser.repository.record("chat", "legacy", "hash", "handoff", {
        "handoff_id": "orphan", "generation": "old-process", "thread_id": "chat"})
    assert await browser.ensure("chat", "admin:1")
    assert await browser.repository.pending_handoff("chat") is None
    assert (await browser.repository.invocation("chat", "legacy")).status == "expired"


@pytest.mark.asyncio
async def test_expired_handoff_can_resume_after_service_restart_and_agent_switch(browser, monkeypatch):
    from langchain_core.messages import AIMessage
    from agent.modules.workflows.nodes.tool import _include_resumed_browser_handoff

    async def node(state):
        return {"result": await browser.container.browser_service.handoff(
            "chat", "admin:1", "handoff", "Please sign in")}

    builder = StateGraph(dict)
    builder.add_node("handoff", node)
    builder.add_edge(START, "handoff")
    builder.add_edge("handoff", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "chat"}}
    await graph.ainvoke({}, config)
    payload = await browser.repository.pending_handoff("chat")
    prior = await browser.repository.invocation("chat", "handoff")
    payload["expires_at"] = 0
    await browser.repository.record("chat", "handoff", prior.arguments_hash, "handoff", payload)
    await browser.reap_idle()
    await browser.shutdown()
    restored = BrowserService(browser.container, root=browser.root, launcher=browser.launcher)
    monkeypatch.setattr(browser.container, "_browser_service", restored)
    monkeypatch.setattr(browser.container, "_persistence_ready", True)
    try:
        assert (await restored.owned_handoff("chat", "admin:1"))["expired"]
        state = {"messages": [AIMessage(content="", tool_calls=[{
            "id": "handoff", "name": "browser_handoff", "args": {"reason": "Please sign in"}}])]}
        tools = await _include_resumed_browser_handoff(state, [], config)
        assert [tool.name for tool in tools] == ["browser_handoff"]
        result = await graph.ainvoke(Command(resume={"action": "browser_handoff",
            "handoff_id": payload["handoff_id"], "decision": "return"}), config)
        assert result["result"]["handoff_status"] == "expired"
        assert not restored.sessions
        assert await restored.owned_handoff("chat", "admin:1") is None
        assert not (await graph.aget_state(config)).tasks
    finally:
        await restored.shutdown()


@pytest.mark.asyncio
async def test_screenshot_owner_and_profile_survive_closing(browser):
    await browser.ensure("chat", "admin:1")
    result = await browser.execute("chat", "admin:1", "capture", "browser_screenshot", {})
    artifact_id = result["artifact"]["artifact_id"]
    assert await browser.repository.artifact("admin:1", artifact_id)
    assert await browser.repository.artifact("admin:2", artifact_id) is None
    directory = next(iter(browser.sessions.values())).profile_key
    await browser.close("chat", "admin:1")
    assert (browser.root / "profiles" / directory).is_dir()


def test_batch_rejection_and_resume_schema():
    assert browser_batch_error([{"name": "browser_run"}, {"name": "browser_snapshot"}])
    assert browser_batch_error([{"name": "browser_handoff"}, {"name": "bash"}])
    assert browser_batch_error([{"name": "browser_run"}, {"name": "read"}]) is None
    assert BrowserHandoffResume(action="browser_handoff", handoff_id="id", decision="return")
    from langchain_core.messages import AIMessage
    from agent.modules.workflows.nodes.tool import _include_pending_control_tools
    state = {"messages": [AIMessage(content="", tool_calls=[{
        "id": "uncatalogued", "name": "browser_handoff", "args": {"reason": "Sign in"}}])]}
    assert _include_pending_control_tools(state, []) == []


def test_batch_guard_uses_browser_registration(monkeypatch):
    from agent.modules.tools.builtin import browser as tools

    async def extra():
        return "snapshot"

    monkeypatch.setattr(tools, "BROWSER_TOOLS", dict(tools.BROWSER_TOOLS))
    tools.registered("browser_new_operation", extra, "Read another browser snapshot.")
    assert browser_batch_error([{"name": "browser_new_operation"}, {"name": "browser_snapshot"}])
    assert browser_batch_error([{"name": "browser_new_operation"}, {"name": "read"}]) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("image_type", ["jpeg", "png"])
async def test_tool_node_injects_trusted_owner_and_keeps_model_images(browser, monkeypatch, tmp_path, image_type):
    from langchain_core.messages import AIMessage
    from langgraph.prebuilt import ToolNode
    from langgraph.runtime import Runtime
    from agent.modules.tools.builtin import browser as tools
    from agent.modules.workflows.run_config import WorkflowContext

    authorized = []
    monkeypatch.setattr(tools, "authorize", lambda runtime, name, values: authorized.append(name))
    workspace = tmp_path / "active-workspace"
    workspace.mkdir()
    (workspace / ".git").mkdir()
    runtime = Runtime(context=WorkflowContext(browser_owner_id="admin:1", working_dir=str(workspace)))
    config = {"configurable": {"thread_id": "chat"}}
    node = ToolNode([tools.browser_run, tools.browser_screenshot])
    result = await node.ainvoke({"messages": [AIMessage(content="", tool_calls=[{
        "id": "call", "name": "browser_run", "args": {"code": "submit()"}}])]}, config=config, runtime=runtime)
    assert result["messages"][0].status == "success"
    assert next(iter(browser.sessions.values())).owner_id == "admin:1"
    result = await node.ainvoke({"messages": [AIMessage(content="", tool_calls=[{
        "id": "image", "name": "browser_screenshot", "args": {"type": image_type}}])]}, config=config, runtime=runtime)
    message = result["messages"][0]
    assert message.content[1]["type"] == "image"
    assert message.content[1]["mime_type"] == f"image/{image_type}"
    assert message.artifact["browser"]["image_url"].startswith("/dashboard-api/browser/artifacts/")
    artifact = message.artifact["browser"]
    path = f".k41-agent/browser/screenshots/{artifact['artifact_id']}.{image_type}"
    assert artifact["workspace_path"] == path
    assert (workspace / path).read_bytes() == b"image"
    assert path in message.content[0]["text"]
    assert message.artifact["display_content"] == message.content[0]["text"]
    assert "/dashboard-api/" not in message.content[0]["text"]
    assert ".k41-agent/" in (workspace / ".git/info/exclude").read_text()
    assert authorized == ["browser_run", "browser_screenshot"]
    assert "runtime" not in tools.browser_run.tool_call_schema.model_json_schema()["properties"]


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["daytona", "modal"])
async def test_screenshot_writes_to_active_remote_workspace(browser, monkeypatch, backend):
    from unittest.mock import AsyncMock

    from agent.modules.tools.builtin import browser as tools
    from agent.modules.workspaces import WorkspaceRef

    workspace = WorkspaceRef(backend=backend, locator="active-sandbox", label="Active sandbox",
                             metadata={"root": "/workspace/repo"})
    file_io = SimpleNamespace(write_bytes=AsyncMock())
    get_file_io = AsyncMock(return_value=file_io)
    monkeypatch.setattr("agent.modules.workspaces.get_workspace_file_io", get_file_io)
    monkeypatch.setattr(tools, "authorize", lambda *args: None)
    runtime = SimpleNamespace(context={"workspace": workspace, "browser_owner_id": "admin:1"},
                              config={"configurable": {"thread_id": "chat:sub:helper:1"}}, tool_call_id="capture")

    result = await tools._screenshot(runtime, type="png", full_page=True)

    get_file_io.assert_awaited_once_with(workspace, thread_id="chat:sub:helper:1")
    path = result.artifact["browser"]["workspace_path"]
    file_io.write_bytes.assert_awaited_once_with(path, b"image")
    assert path.startswith(".k41-agent/browser/screenshots/") and path.endswith(".png")
    assert path in result.content[0]["text"]


@pytest.mark.asyncio
async def test_screenshot_workspace_write_failure_is_explicit(browser, monkeypatch, tmp_path):
    from unittest.mock import AsyncMock

    from agent.modules.tools.builtin import browser as tools
    from agent.modules.tools.result import ToolError, ToolErrorCode

    file_io = SimpleNamespace(write_bytes=AsyncMock(side_effect=OSError("disk full")))
    monkeypatch.setattr("agent.modules.workspaces.get_workspace_file_io", AsyncMock(return_value=file_io))
    monkeypatch.setattr(tools, "authorize", lambda *args: None)
    runtime = SimpleNamespace(context={"working_dir": str(tmp_path), "browser_owner_id": "admin:1"},
                              config={"configurable": {"thread_id": "chat"}}, tool_call_id="capture")

    with pytest.raises(ToolError, match="Failed to save browser screenshot to the active workspace: disk full") as caught:
        await tools._screenshot(runtime)
    assert caught.value.code == ToolErrorCode.EXECUTION_ERROR


@pytest.mark.asyncio
async def test_ticket_expiry_and_status_do_not_create_browsers(browser):
    import time

    assert not (await browser.status("empty", "admin:1"))["open"]
    assert await browser.repository.binding(browser.canonical("empty")) is None
    with pytest.raises(BrowserError):
        await browser.issue_ticket("empty", "admin:1")
    current = await browser.ensure("chat", "admin:1")
    ticket = await browser.issue_ticket("chat", "admin:1")
    browser.tickets[ticket] = (current.thread_id, current.owner_id, current.generation, time.monotonic() - 1)
    with pytest.raises(PermissionError):
        browser.consume_ticket(ticket)
    await browser.close("chat", "admin:1")
    with pytest.raises(BrowserError):
        await browser.issue_ticket("chat", "admin:1")
    assert not browser.sessions


@pytest.mark.asyncio
async def test_timeout_closes_browser_and_never_repeats_uncertain_mutation(browser, monkeypatch):
    session = await browser.ensure("chat", "admin:1")
    monkeypatch.setattr(BrowserService, "timeout", property(lambda self: 0.02))

    async def stalled(**values):
        await asyncio.sleep(1)

    session.driver.facade.run = stalled
    with pytest.raises(TimeoutError):
        await browser.execute("chat", "admin:1", "timeout", "browser_run", {"code": "submit()"})
    assert session.driver.closed and not browser.sessions
    with pytest.raises(BrowserError, match="already have executed"):
        await browser.execute("chat", "admin:1", "timeout", "browser_run", {"code": "submit()"})


@pytest.mark.asyncio
async def test_idle_cleanup_and_close_replay_preserve_profiles(browser):
    session = await browser.ensure("chat", "admin:1")
    profile = session.profile_key
    session.last_activity -= browser.idle_ttl + 1
    await browser.reap_idle()
    assert session.driver.closed and not browser.sessions
    assert (browser.root / "profiles" / profile).exists()
    await browser.execute("chat", "admin:1", "close", "browser_close", {})
    reopened = await browser.ensure("chat", "admin:1")
    await browser.execute("chat", "admin:1", "close", "browser_close", {})
    assert not reopened.closed


@pytest.mark.asyncio
async def test_handoff_consumes_old_permission_resume_without_releasing_control(browser):
    from langgraph.types import interrupt

    permitted = False

    async def node(state):
        nonlocal permitted
        if not permitted:
            interrupt({"type": "permission_request"})
            permitted = True
        return {"result": await browser.handoff("chat", "admin:1", "handoff", "Please sign in")}

    builder = StateGraph(dict)
    builder.add_node("browser", node)
    builder.add_edge(START, "browser")
    builder.add_edge("browser", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "graph"}}
    await graph.ainvoke({}, config)
    await graph.ainvoke(Command(resume={"action": "permission", "decision": "allow_once"}), config)
    pending = await browser.repository.owned_handoff(browser.canonical("chat"), "admin:1")
    assert pending and await browser.repository.owned_handoff(browser.canonical("chat"), "admin:2") is None
    result = await graph.ainvoke(Command(resume={"action": "browser_handoff", "handoff_id": pending["handoff_id"], "decision": "cancel"}), config)
    assert "cancelled" in result["result"]["content"]
    assert not browser.sessions


def test_browser_permission_rules_are_enforced(tmp_path, monkeypatch):
    from agent.modules.tools.builtin import browser as tools
    from agent.modules.tools.coding.contracts import InvocationContext
    from agent.modules.tools.coding.models import PermissionRule
    from agent.modules.tools.coding.permissions import Permissions
    from agent.modules.tools.coding.storage import OutputStore
    from agent.modules.tools.result import ToolError

    context = InvocationContext(agent_name="test", workspace=str(tmp_path), thread_id="chat",
        permission_rules=(PermissionRule(action="browser_run", resource="*", effect="deny"),))
    monkeypatch.setattr("agent.modules.tools.coding.adapter.invocation_context", lambda runtime: context)
    monkeypatch.setattr("agent.modules.tools.coding.service.get_coding_service",
                        lambda: SimpleNamespace(permissions=Permissions(OutputStore(tmp_path))))
    with pytest.raises(ToolError, match="Policy denies browser_run"):
        tools.authorize(SimpleNamespace(), "browser_run", {"code": "submit()"})


@pytest.mark.asyncio
async def test_handoff_is_refused_in_scheduled_runs(browser, monkeypatch):
    from agent.modules.tools.builtin import browser as tools
    from agent.modules.tools.result import ToolError

    runtime = SimpleNamespace(context={"browser_owner_id": "admin:1"}, tool_call_id="handoff", config={
        "configurable": {"thread_id": "scheduled"},
        "metadata": {"usage_context": {"platform": "bg", "user_id": "123", "channel_id": "run"}},
    })

    def unexpected(*args, **kwargs):
        pytest.fail("A scheduled handoff must fail before permission or graph interrupts")

    monkeypatch.setattr(tools, "authorize", unexpected)
    monkeypatch.setattr("langgraph.types.interrupt", unexpected)
    with pytest.raises(ToolError, match="scheduled run"):
        await tools._handoff(runtime, "Please sign in")
    assert not browser.sessions

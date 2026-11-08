"""Local browser sessions with durable ownership and replay-safe mutations."""

import asyncio
import base64
import contextlib
import hashlib
import json
import logging
import secrets
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from sqlalchemy import URL, make_url

from agent.modules.browser.driver import StagehandDriver, terminate_profile_processes
from agent.modules.browser.models import BrowserAccessDenied
from agent.modules.browser.repository import BrowserRepository
from agent.modules.browser.snapshot import compact_snapshot, diff_snapshot


logger = logging.getLogger(__name__)

CREDENTIAL_QUERY_KEYS = frozenset({"user", "username", "password", "passwd", "pwd", "sslpassword"})
# Stays above the facade's 60 second run deadline. That deadline fails one call and leaves
# the browser usable, while this watchdog closes every conversation sharing the browser.
MIN_OPERATION_TIMEOUT_SECONDS = 75
MAX_FAILURE_CAUSE_CHARS = 2000
# Far below the general tool output limit, because a page dump this size is markup and
# script data. Keeping its first bytes would still mislead, so only a short preview remains.
MAX_RUN_RESULT_BYTES = 8 * 1024
RUN_RESULT_PREVIEW_CHARS = 1000
# Reading the page after actions shares the watchdog with the run itself: 60 + 0.5 + 10 < 75.
ACTION_SETTLE_SECONDS = 0.5
ACTION_OBSERVE_TIMEOUT_SECONDS = 10
ACTION_OBSERVE_FAILED = ("The actions completed, but the page could not be read afterwards. "
                         "Call browser_snapshot before relying on the page or its IDs.")


def storage_namespace(database_url: str) -> str:
    """Identify a database without its credentials, so rotating them keeps saved logins."""
    url = make_url(database_url)
    query = {key: value for key, value in url.query.items() if key.casefold() not in CREDENTIAL_QUERY_KEYS}
    identity = database_url
    if url.username is not None or url.password is not None or len(query) != len(url.query):
        identity = URL.create(url.drivername, host=url.host, port=url.port,
                              database=url.database, query=query).render_as_string()
    return hashlib.sha256(identity.encode()).hexdigest()[:16]


def storage_root(base: Path, database_url: str) -> Path:
    """Locate a database's browser storage, adopting the location used by earlier versions."""
    current = base / storage_namespace(database_url)
    legacy = base / hashlib.sha256(database_url.encode()).hexdigest()[:16]
    if legacy != current and legacy.is_dir() and not current.exists():
        try:
            legacy.rename(current)
        except OSError:
            logger.warning("Could not move saved browser logins to their new location; retrying at the next start.", exc_info=True)
            # Creating the new location now would end the retries and hide the saved logins.
            return legacy
    return current


# Waiting past the deadline by this much guarantees the record counts as expired.
HANDOFF_EXPIRY_GRACE_SECONDS = 1.0
DISCARDED_PREFIX = ".discarded-"
HANDOFF_CANCELLED ={"handoff_status": "cancelled", "snapshot_status": "skipped",
                     "content": "HANDOFF CANCELLED: Browser handoff was cancelled by the user. The manual interaction was aborted. Browser control is now with you (the agent). Acknowledge that the user cancelled this browser task and do not continue the cancelled action. Ask the user what they would like to do next. Do not say you failed to transfer control."}


class BrowserError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def run_failure(exc: Exception) -> BrowserError:
    """Tell the agent why a browser_run failed, and whether anything can have executed."""
    from stagehand.rpc_client import RPCError
    from stagehand_middleware.facade import StagehandFacadeExecutionError, StagehandFacadeInputError
    from stagehand_middleware.facade.tools import sanitize_error_message

    message = str(exc)
    # Syntax and input checks, and the snapshot ID checks that precede the first action.
    if isinstance(exc, SyntaxError) or (isinstance(exc, StagehandFacadeInputError) and (
            "call snapshot" in message or message.startswith("Invalid run input:"))):
        return BrowserError("invalid_input", f"{message.replace('call snapshot', 'call browser_snapshot')} Nothing was executed.")
    notice = "The browser action failed and may have partially executed. Inspect the page before creating a new action."
    if isinstance(exc, StagehandFacadeExecutionError):
        cause = f"{exc.name}: {message}"
    elif isinstance(exc, StagehandFacadeInputError):
        cause = message
    elif isinstance(exc, RPCError):
        cause = sanitize_error_message(message)
    else:
        return BrowserError("unknown_outcome", notice)
    return BrowserError("unknown_outcome", f"{notice} Cause: {cause[:MAX_FAILURE_CAUSE_CHARS]}")


def bounded_run_result(content: str) -> str:
    """Replace an oversized code result with instructions for reading less."""
    size = len(content.encode("utf-8", "replace"))
    if size <= MAX_RUN_RESULT_BYTES:
        return content
    return (f"The result was {size} bytes, over the {MAX_RUN_RESULT_BYTES} byte limit, so it was not returned. "
            "The code did run, so do not repeat steps that change the page. Read again with a narrower query: "
            "return only the fields you need, leave out script and style elements, and avoid outerHTML. "
            "Use browser_snapshot to find elements. For long text, return it in parts with slice().\n"
            f"Preview:\n{content[:RUN_RESULT_PREVIEW_CHARS]}")


@dataclass
class BrowserSession:
    thread_id: str
    owner_id: str
    profile_key: str
    driver: object
    generation: str = field(default_factory=lambda: uuid4().hex)
    last_activity: float = field(default_factory=time.monotonic)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    handoff_id: str | None = None
    handoff_expires_at: float | None = None
    controller: str | None = None
    active_tab_id: str | None = None
    closed: bool = False
    revision: int = 0
    subscribers: set[asyncio.Event] = field(default_factory=set)
    observer: asyncio.Task | None = None
    inherited_profile: bool = False
    snapshot_tree: str | None = None

    def notify(self):
        self.revision += 1
        for event in self.subscribers:
            event.set()


@dataclass
class ProfileBrowser:
    owner_id: str
    profile_id: str
    driver: object
    threads: set[str] = field(default_factory=set)


class BrowserService:
    def __init__(self, container, *, root: Path | None = None, launcher=None):
        self.container = container
        self.repository = BrowserRepository(container)
        configured = container.config_service.get_str("browser.storage_root", "")
        self.root = root or storage_root(Path(configured).expanduser() if configured else Path.home() / ".k41-agent" / "browser",
                                         container.database_url)
        self.launcher = launcher or StagehandDriver.launch
        self.sessions: dict[str, BrowserSession] = {}
        self.browsers: dict[tuple[str, str], ProfileBrowser] = {}
        self.operation_locks: dict[str, asyncio.Lock] = {}
        self.operation_users: dict[str, int] = {}
        self.deleting_threads: set[str] = set()
        self.tickets = {}
        self.sweeper = None
        self.closing = False
        self.undeleted: set[str] = set()

    async def initialize_profiles(self, owner_ids=None):
        """Provision saved defaults without launching Chrome."""
        if owner_ids is None:
            from sqlalchemy import select
            from agent.modules.admin_auth import AdminCredential
            from agent.modules.browser.models import BrowserAccountProfile, BrowserBinding, BrowserProfile, BrowserProjectProfile

            async with self.repository.session() as session:
                admins = await session.scalars(select(AdminCredential.id))
                owners = await session.scalars(select(BrowserProfile.owner_id).union(
                    select(BrowserAccountProfile.owner_id), select(BrowserBinding.owner_id),
                    select(BrowserProjectProfile.owner_id)))
                owner_ids = {*(f"admin:{admin}" for admin in admins), *owners}
        for owner_id in owner_ids:
            profile_id = await self.repository.ensure_default_profile(owner_id)
            (self.root / "profiles" / profile_id).mkdir(parents=True, exist_ok=True, mode=0o700)

    @property
    def timeout(self):
        return max(MIN_OPERATION_TIMEOUT_SECONDS, min(300, self.container.config_service.get_int("browser.operation_timeout_seconds", MIN_OPERATION_TIMEOUT_SECONDS)))

    @property
    def idle_ttl(self):
        return max(1, self.container.config_service.get_int("browser.idle_ttl_seconds", 600))

    @staticmethod
    def canonical(thread_id):
        from agent.shared.thread_ids import resolve_thread_id

        if not thread_id:
            raise BrowserError("invalid_input", "Browser tools require a conversation thread.")
        return resolve_thread_id(thread_id)

    async def bind(self, thread_id, owner_id):
        thread_id = self.canonical(thread_id)
        async with self.thread_scope(thread_id):
            return await self.repository.bind(thread_id, owner_id)

    async def ensure(self, thread_id, owner_id):
        thread_id = self.canonical(thread_id)
        async with self.thread_scope(thread_id):
            return await self._ensure(thread_id, owner_id)

    async def _ensure(self, thread_id, owner_id):
        binding = await self.repository.bind(thread_id, owner_id)
        await self._expire_handoffs(thread_id)
        current = self.sessions.get(thread_id)
        if current:
            if current.closed:
                raise BrowserError("session_lost", "Browser is closing. Retry after it has finished closing.")
            return current
        if await self.repository.pending_handoff(thread_id):
            raise BrowserError("handoff_expired", "Continue or cancel the expired handoff before opening another browser session.")
        profile_id, inherited = await self._session_profile(thread_id, owner_id, binding)
        profile_key = profile_id
        # Reserve only this profile during launch; unrelated conversations stay usable.
        async with self.operation_scope(f"profile:{profile_key}"):
            await self.repository.profile(profile_id, owner_id)
            shared_key = (owner_id, profile_id)
            shared = self.browsers.get(shared_key)
            profile = self.root / "profiles" / profile_key
            artifact_root = self.root / "run-artifacts" / self.storage_key(thread_id)
            try:
                profile.mkdir(parents=True, exist_ok=True, mode=0o700)
                artifact_root.mkdir(parents=True, exist_ok=True, mode=0o700)
                if shared is None:
                    shared = ProfileBrowser(owner_id, profile_id, await self._launch(profile, artifact_root))
                    self.browsers[shared_key] = shared
                driver = await asyncio.wait_for(shared.driver.create_session(artifact_root), self.timeout)
            except BaseException as exc:
                if shared is not None and not shared.threads:
                    self.browsers.pop(shared_key, None)
                    with contextlib.suppress(Exception):
                        await asyncio.shield(shared.driver.close())
                if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                    raise
                logger.exception("Could not start the local browser")
                raise BrowserError("browser_unavailable", "Could not start Chrome. Run 'k41 browser install' on the agent host to install and check Chrome, or 'k41 browser check' to diagnose an existing installation. Check browser.executable_path, profile locks, and server logs for the original error.") from exc
            current = BrowserSession(thread_id, owner_id, profile_key, driver, inherited_profile=inherited)
            self.sessions[thread_id] = current
            shared.threads.add(thread_id)
            if hasattr(driver, "watch_tabs"):
                current.observer = asyncio.create_task(self._observe_tabs(current))
            return current

    async def _session_profile(self, thread_id, owner_id, binding):
        """Return the named profile a session should use and whether it comes from the parent."""
        if binding.get("profile_mode") == "profile" and binding["profile_id"]:
            return binding["profile_id"], False
        if ":sub:" in thread_id:
            parent = thread_id.split(":sub:", 1)[0]
            active = self.sessions.get(parent)
            if active is not None:
                if active.owner_id != owner_id:
                    raise BrowserAccessDenied("This browser belongs to another authenticated account.")
                return active.profile_key, True
            return await self.repository.project_default(thread_id, owner_id), True
        return await self.repository.project_default(thread_id, owner_id), False

    async def _launch(self, profile, artifact_root):
        executable = self.container.config_service.get_str("browser.executable_path", "")
        loop = asyncio.get_running_loop()
        # Both attempts share one deadline, because the profile stays reserved throughout.
        deadline = loop.time() + self.timeout
        try:
            return await asyncio.wait_for(self.launcher(profile, artifact_root, executable), self.timeout)
        except Exception:
            stopped = await asyncio.to_thread(terminate_profile_processes, profile)
            if not stopped or loop.time() >= deadline:
                raise
        logger.warning("Stopped a leftover browser that was holding a login profile.")
        return await asyncio.wait_for(self.launcher(profile, artifact_root, executable), deadline - loop.time())

    async def _observe_tabs(self, current):
        while not current.closed:
            try:
                await current.driver.watch_tabs(current.notify)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Status reconciliation also checks browser health after observer loss.
                current.notify()
                await asyncio.sleep(5)

    async def status(self, thread_id, owner_id):
        thread_id = self.canonical(thread_id)
        async with self.thread_scope(thread_id):
            return await self._status(thread_id, owner_id)

    async def owned_handoff(self, thread_id, owner_id):
        thread_id = self.canonical(thread_id)
        async with self.thread_scope(thread_id):
            binding = await self.repository.binding(thread_id)
            if not binding or binding["owner_id"] != owner_id:
                return None
            await self._expire_handoffs(thread_id)
            return await self.repository.pending_handoff(thread_id, include_expired=True)

    async def _status(self, thread_id, owner_id):
        binding = await self.repository.binding(thread_id)
        if binding and binding["owner_id"] != owner_id:
            raise BrowserAccessDenied("This browser belongs to another authenticated account.")
        await self._expire_handoffs(thread_id)
        binding = binding or {"thread_id": thread_id, "owner_id": owner_id,
                              "profile_id": None, "profile_mode": "inherit"}
        current = self.sessions.get(thread_id)
        tabs = []
        if current:
            try:
                tabs = await current.driver.tabs()
            except Exception:
                if not current.lock.locked():
                    await self._dispose(current, fatal=True)
                    current = None
        return await self._status_snapshot(thread_id, owner_id, binding, current, tabs)

    async def viewer_status(self, current):
        """Read viewer state without waiting behind an agent's browser operation."""
        binding = await self.repository.binding(current.thread_id)
        if not binding or binding["owner_id"] != current.owner_id:
            raise BrowserAccessDenied("This browser belongs to another authenticated account.")
        tabs = await current.driver.tabs() if not current.closed else []
        return await self._status_snapshot(current.thread_id, current.owner_id, binding,
                                          None if current.closed else current, tabs)

    async def _status_snapshot(self, thread_id, owner_id, binding, current, tabs):
        pending = await self.repository.pending_handoff(thread_id, include_expired=True)
        effective = current.profile_key if current else (await self._session_profile(thread_id, owner_id, binding))[0]
        return {**binding, "selected_profile_id": binding["profile_id"], "profile_id": effective,
                "open": current is not None,
                "generation": current.generation if current else None,
                "revision": current.revision if current else 0,
                "mode": "human" if current and current.handoff_id else "agent",
                "tabs": tabs, "handoff": pending,
                "handoff_expired": bool(pending and (pending.get("expired") or not current or pending["generation"] != current.generation)),
                "images": await self.repository.images(owner_id, thread_id)}

    async def select_profile(self, thread_id, owner_id, profile_id):
        thread_id = self.canonical(thread_id)
        async with self.thread_scope(thread_id):
            if thread_id in self.sessions:
                raise BrowserError("profile_busy", "Close the browser before changing its profile.")
            if profile_id:
                async with self.operation_scope(f"profile:{profile_id}"):
                    await self.repository.select_profile(thread_id, owner_id, profile_id)
            else:
                await self.repository.select_profile(thread_id, owner_id, profile_id)

    async def profiles(self, owner_id):
        await self.initialize_profiles([owner_id])
        holders: dict[str, list[str]] = {}
        for item in self.sessions.values():
            if item.owner_id == owner_id:
                holders.setdefault(item.profile_key, []).append(item.thread_id)
        default = await self.repository.default_profile(owner_id)
        projects = await self.repository.profile_projects(owner_id)
        return [{**profile, "open_thread_ids": holders.get(profile["profile_id"], []),
                 "open_thread_id": next(iter(holders.get(profile["profile_id"], [])), None),
                 "is_default": profile["profile_id"] == default,
                 "projects": projects.get(profile["profile_id"], [])}
                for profile in await self.repository.profiles(owner_id)]

    @contextlib.asynccontextmanager
    async def _available_profile_name(self, owner_id, name, profile_id=None):
        async with self.operation_scope(f"profile-names:{owner_id}"):
            if any(item["name"].casefold() == name.casefold() and item["profile_id"] != profile_id
                   for item in await self.repository.profiles(owner_id)):
                raise BrowserError("profile_name_taken", "A login profile with this name already exists.")
            yield

    async def create_profile(self, owner_id, name):
        await self.initialize_profiles([owner_id])
        async with self._available_profile_name(owner_id, name):
            return await self.repository.create_profile(owner_id, name)

    async def rename_profile(self, owner_id, profile_id, name):
        async with self.operation_scope(f"profile:{profile_id}"), self._available_profile_name(owner_id, name, profile_id):
            return await self.repository.rename_profile(owner_id, profile_id, name)

    async def delete_profile(self, owner_id, profile_id) -> bool:
        """Delete a profile and report whether its saved logins have already left the disk."""
        async with self.operation_scope(f"profile:{profile_id}"):
            await self.repository.profile(profile_id, owner_id)
            self.repository.assert_custom_profile(owner_id, profile_id)
            if any(item.profile_key == profile_id for item in self.sessions.values()):
                raise BrowserError("profile_busy", "Close the browser before deleting its profile.")
            removed = await self._remove_directory("profiles", profile_id)
            await self.repository.delete_profile(owner_id, profile_id)
            return removed

    async def close(self, thread_id, owner_id, *, allow_human=True):
        thread_id = self.canonical(thread_id)
        async with self.thread_scope(thread_id):
            await self._close(thread_id, owner_id, allow_human=allow_human)

    async def release_inherited_profile(self, thread_id):
        """Release a finished helper's tabs while preserving its parent's session."""
        thread_id = self.canonical(thread_id)
        if thread_id not in self.sessions:
            return
        async with self.thread_scope(thread_id):
            current = self.sessions.get(thread_id)
            if current and ":sub:" in thread_id:
                await self._close_session(current)

    async def _close(self, thread_id, owner_id, *, allow_human=True):
        await self.repository.bind(thread_id, owner_id)
        current = self.sessions.get(thread_id)
        if current:
            async with current.lock:
                if not allow_human:
                    self.assert_agent(current)
                await self._dispose(current)

    async def _detach(self, current):
        current.closed = True
        current.controller = None
        current.notify()
        if current.observer:
            current.observer.cancel()
            await asyncio.gather(current.observer, return_exceptions=True)
            current.observer = None
        if self.sessions.get(current.thread_id) is current:
            self.sessions.pop(current.thread_id, None)
        self.tickets = {token: ticket for token, ticket in self.tickets.items()
                        if ticket[0] != current.thread_id or ticket[2] != current.generation}

    async def _dispose(self, current, *, fatal=False):
        async with self.operation_scope(f"profile:{current.profile_key}"):
            key = (current.owner_id, current.profile_key)
            shared = self.browsers.get(key)
            if shared is None:
                if not current.closed:
                    await self._detach(current)
                    await current.driver.close()
                return
            if not fatal:
                try:
                    # The scoped driver starts its cleanup deadline after it
                    # acquires Chrome's lock, so another thread's healthy action
                    # cannot make an ordinary close kill the shared browser.
                    await current.driver.close()
                except Exception:
                    logger.warning("Browser tab cleanup failed for session %s", current.generation)
                    fatal = True
            affected = [item for item in self.sessions.values()
                        if (item.owner_id, item.profile_key) == key] if fatal else [current]
            for item in affected:
                await self._detach(item)
                shared.threads.discard(item.thread_id)
            if not shared.threads:
                self.browsers.pop(key, None)
                try:
                    await asyncio.wait_for(shared.driver.close(), 15)
                except Exception:
                    logger.warning("Browser cleanup failed for profile %s", current.profile_key)
                    with contextlib.suppress(Exception):
                        await asyncio.to_thread(terminate_profile_processes, self.root / "profiles" / current.profile_key)

    async def _close_session(self, current):
        async with current.lock:
            await self._dispose(current)

    @staticmethod
    def storage_key(thread_id: str) -> str:
        return hashlib.sha256(thread_id.encode()).hexdigest()

    @staticmethod
    def matches_thread(candidate: str, thread_id: str) -> bool:
        return candidate == thread_id or candidate.startswith(f"{thread_id}:sub:")

    async def _remove_directory(self, directory: str, key: str) -> bool:
        """Return False while the removed data is still on disk waiting to be purged."""
        discarded = await self._discard(directory, key)
        if discarded is None:
            return True
        await self.purge_discarded()
        return not discarded.exists()

    async def _discard(self, directory: str, key: str) -> Path | None:
        """Set a managed directory aside for purging and return its new location, if it existed."""
        parent = self.root / directory
        target = parent / key
        if target.is_symlink() or target.is_junction() or target.resolve().parent != parent.resolve():
            raise BrowserError("invalid_storage", "The directory is not a managed browser directory.")
        if target.is_dir():
            if directory == "profiles":
                # Only a login profile can be a browser's user data directory.
                await asyncio.to_thread(terminate_profile_processes, target)
            return await asyncio.to_thread(self._discard_directory, parent, target)
        if target.exists():
            raise BrowserError("invalid_storage", "The directory is not a managed browser directory.")
        return None

    @staticmethod
    def _discard_directory(parent: Path, target: Path) -> Path:
        # Moving first means a file still in use leaves the directory complete instead of half deleted.
        discarded = parent / f"{DISCARDED_PREFIX}{uuid4().hex}"
        try:
            target.rename(discarded)
        except OSError as exc:
            raise BrowserError("profile_busy", "Browser files are still in use. Try again in a moment.") from exc
        return discarded

    async def purge_discarded(self) -> None:
        """Remove data set aside for deletion, retrying periodically whatever is still in use."""
        async with self.operation_scope("purge-discarded"):
            await asyncio.to_thread(self._purge_discarded)
        if self.undeleted:
            self._start_sweeper()

    def _purge_discarded(self) -> None:
        undeleted = set()
        for directory in ("profiles", "run-artifacts"):
            for leftover in (self.root / directory).glob(f"{DISCARDED_PREFIX}*"):
                try:
                    shutil.rmtree(leftover)
                except OSError:
                    undeleted.add(leftover.name)
                    if leftover.name not in self.undeleted:
                        logger.warning("Could not remove discarded browser data yet.", exc_info=True)
        self.undeleted = undeleted

    async def delete_thread(self, thread_id: str) -> None:
        """Delete conversation data, retaining only explicitly named login profiles."""
        thread_id = self.canonical(thread_id)
        async with self.operation_scope(f"delete:{thread_id.split(':sub:', 1)[0]}"):
            self.deleting_threads.add(thread_id)
            try:
                targets = {thread_id, *await self.repository.thread_ids(thread_id)}
                targets.update(key.removeprefix("thread:") for key in self.operation_locks
                               if key.startswith("thread:") and self.matches_thread(key.removeprefix("thread:"), thread_id))
                targets.update(key for key in self.sessions if self.matches_thread(key, thread_id))
                async with contextlib.AsyncExitStack() as stack:
                    for target in sorted(targets):
                        await stack.enter_async_context(self.operation_scope(f"thread:{target}"))
                    # Disposals run concurrently, without holding any unrelated thread's lock.
                    await asyncio.gather(*(self._close_session(current) for current in list(self.sessions.values())
                                           if self.matches_thread(current.thread_id, thread_id)))
                    self.tickets = {token: ticket for token, ticket in self.tickets.items()
                                    if not self.matches_thread(ticket[0], thread_id)}
                    for artifact_id in await self.repository.thread_artifact_ids(thread_id):
                        parent = self.root / "images"
                        path = parent / artifact_id
                        if path.is_symlink() or path.resolve().parent != parent.resolve():
                            raise BrowserError("invalid_storage", "The image is not a managed browser artifact.")
                        await asyncio.to_thread(path.unlink, missing_ok=True)
                    try:
                        for target in targets:
                            key = self.storage_key(target)
                            await self._discard("profiles", key)
                            await self._discard("run-artifacts", key)
                    finally:
                        await self.purge_discarded()
                    await self.repository.delete_thread(thread_id)
            finally:
                self.deleting_threads.discard(thread_id)

    def assert_agent(self, current):
        if current.closed:
            raise BrowserError("session_lost", "Browser session was closed. Open it again and take a fresh snapshot.")
        if current.handoff_id:
            raise BrowserError("human_control", "The user controls this browser. Wait for the handoff to finish.")

    @contextlib.asynccontextmanager
    async def operation_scope(self, key):
        lock = self.operation_locks.setdefault(key, asyncio.Lock())
        # Count holders and waiters before acquisition, including cancelled waiters.
        self.operation_users[key] = self.operation_users.get(key, 0) + 1
        try:
            async with lock:
                yield
        finally:
            self.operation_users[key] -= 1
            if self.operation_users[key] == 0:
                self.operation_users.pop(key)
                self.operation_locks.pop(key, None)

    @contextlib.asynccontextmanager
    async def thread_scope(self, thread_id):
        async with self.operation_scope(f"thread:{thread_id}"):
            if self.closing or any(self.matches_thread(thread_id, target) for target in self.deleting_threads):
                raise BrowserError("session_lost", "The browser service is shutting down or the conversation is being deleted.")
            self._start_sweeper()
            yield

    def _start_sweeper(self):
        if self.sweeper is None and not self.closing:
            self.sweeper = asyncio.create_task(self._sweep())

    async def execute(self, thread_id, owner_id, call_id, name, values, authorize_snapshot=None):
        thread_id = self.canonical(thread_id)
        async with self.thread_scope(thread_id):
            return await self._execute(thread_id, owner_id, call_id, name, values, authorize_snapshot)

    async def _read_tree(self, current, options, *, full=False):
        """Read the page and keep its tree as the base of the next comparison."""
        tree = compact_snapshot(await current.driver.facade.snapshot(**options))
        changes = diff_snapshot(current.snapshot_tree, tree) if current.snapshot_tree and not full else None
        current.snapshot_tree = tree
        return changes or tree

    async def _observe_actions(self, current, authorize_snapshot):
        """Report what completed actions changed; failing to read the page does not undo them."""
        try:
            if authorize_snapshot is not None:
                authorize_snapshot()
            await asyncio.sleep(ACTION_SETTLE_SECONDS)
            async with asyncio.timeout(ACTION_OBSERVE_TIMEOUT_SECONDS):
                return await self._read_tree(current, {"include_iframes": True})
        except Exception as exc:
            logger.info("Could not read the page after browser actions: %s", type(exc).__name__)
            return ACTION_OBSERVE_FAILED

    async def _execute(self, thread_id, owner_id, call_id, name, values, authorize_snapshot=None):
        await self.repository.bind(thread_id, owner_id)
        # Serialize replay checks as well as driver calls, including across session replacement.
        key = self.repository.invocation_key(thread_id, call_id)
        async with self.operation_scope(key):
            fingerprint = hashlib.sha256(json.dumps([name, values], sort_keys=True).encode()).hexdigest()
            journaled = name in {"browser_run", "browser_close"}
            if journaled:
                if not call_id:
                    raise BrowserError("invalid_input", "Browser mutations require a tool call identifier.")
                prior = await self.repository.invocation(thread_id, call_id)
                if prior:
                    if prior.arguments_hash != fingerprint:
                        raise BrowserError("invalid_input", "Tool call arguments changed during replay.")
                    if prior.status == "completed":
                        return json.loads(prior.result)
                    raise BrowserError("unknown_outcome", "This browser action may already have executed. Inspect a fresh snapshot before issuing a new action; do not replay this call.")
            if name == "browser_close":
                await self.repository.record(thread_id, call_id, fingerprint, "running")
                await self._close(thread_id, owner_id, allow_human=False)
                result = {"content": "Browser closed. Its login profile has been preserved."}
                await self.repository.record(thread_id, call_id, fingerprint, "completed", result)
                return result
            current = await self._ensure(thread_id, owner_id)
            async with current.lock:
                self.assert_agent(current)
                current.last_activity = time.monotonic()
                if journaled:
                    await self.repository.record(thread_id, call_id, fingerprint, "running")
                try:
                    async with asyncio.timeout(self.timeout):
                        facade = current.driver.facade
                        if name == "browser_run":
                            value = await facade.run(**values)
                            content = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
                            if "code" in values:
                                result = {"content": bounded_run_result(content)}
                            else:
                                result = {"content": f"{content}\n{await self._observe_actions(current, authorize_snapshot)}"}
                        elif name == "browser_snapshot":
                            options = {key: value for key, value in values.items() if key != "full"}
                            result = {"content": await self._read_tree(current, options, full=values.get("full", False))}
                        elif name == "browser_screenshot":
                            image = await facade.screenshot(**values)
                            artifact = await self.save_image(owner_id, thread_id, image)
                            result = {"content": f"Browser screenshot captured.\nBrowser image: {artifact['image_url']}",
                                      "image": image, "artifact": artifact}
                        else:
                            raise BrowserError("invalid_input", "Unknown browser operation.")
                except (TimeoutError, asyncio.CancelledError):
                    # Cancelling the RPC alone does not cancel browser-side JavaScript.
                    await asyncio.shield(self._dispose(current, fatal=True))
                    raise
                except Exception as exc:
                    if journaled:
                        raise run_failure(exc) from exc
                    raise
                finally:
                    current.last_activity = time.monotonic()
                    current.notify()
                if journaled:
                    await self.repository.record(thread_id, call_id, fingerprint, "completed", result)
                return result

    async def save_image(self, owner_id, thread_id, image):
        raw = base64.b64decode(image["data"], validate=True)
        if len(raw) > 20 * 1024 * 1024:
            raise BrowserError("output_limit", "Browser screenshot exceeds the image size limit.")
        artifact_id = uuid4().hex
        directory = self.root / "images"
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        await asyncio.to_thread((directory / artifact_id).write_bytes, raw)
        await self.repository.save_artifact(owner_id, thread_id, artifact_id, image["mimeType"])
        return {"artifact_id": artifact_id, "image_url": f"/dashboard-api/browser/artifacts/{artifact_id}",
                "mime_type": image["mimeType"]}

    async def handoff(self, thread_id, owner_id, call_id, reason, *, authorize_snapshot=None, wait_for_human=None):
        """Hand the browser to the user.

        Inside a graph run this interrupts. A caller outside any run passes
        ``wait_for_human``, which receives the request and returns the decision.
        """
        thread_id = self.canonical(thread_id)
        self.assert_handoff_supported(thread_id)
        if wait_for_human is not None:
            return await self._handoff_outside_graph(thread_id, owner_id, call_id, reason, wait_for_human, authorize_snapshot)
        key = self.repository.invocation_key(thread_id, call_id)
        async with self.thread_scope(thread_id), self.operation_scope(key):
            return await self._handoff(thread_id, owner_id, call_id, reason, authorize_snapshot=authorize_snapshot)

    @staticmethod
    def assert_handoff_supported(thread_id: str) -> None:
        # Delegation consumes text output and cannot surface or resume child interrupts.
        if ":sub:" in thread_id or thread_id.startswith("sub_"):
            raise BrowserError("approval_unavailable", "Browser handoff is unavailable in delegated sub-agents. Report the need for user interaction to the parent agent so it can request a browser handoff.")

    async def _handoff(self, thread_id, owner_id, call_id, reason, *, authorize_snapshot=None):
        from langgraph.types import interrupt
        from agent.modules.browser.models import BrowserHandoffResume

        thread_id = self.canonical(thread_id)
        result, payload, expired_receipt = await self._open_handoff(thread_id, owner_id, call_id, reason)
        if result is not None:
            return result
        while True:
            raw_response = interrupt(payload)
            # An earlier permission interrupt may disappear on replay because
            # its grant was persisted. Consume that old value without handback.
            if not isinstance(raw_response, dict) or raw_response.get("action") != "browser_handoff":
                continue
            response = BrowserHandoffResume.model_validate(raw_response)
            if response.handoff_id == payload["handoff_id"]:
                break
        return await self._settle_handoff(thread_id, call_id, reason, payload, expired_receipt, response.decision, authorize_snapshot)

    async def _handoff_outside_graph(self, thread_id, owner_id, call_id, reason, wait_for_human, authorize_snapshot):
        """Wait without the thread's locks, because the viewer and status need them meanwhile."""
        key = self.repository.invocation_key(thread_id, call_id)
        async with self.thread_scope(thread_id), self.operation_scope(key):
            result, payload, _ = await self._open_handoff(thread_id, owner_id, call_id, reason)
        if result is not None:
            return result

        async def settle(decision):
            async with self.thread_scope(thread_id), self.operation_scope(key):
                await self._expire_handoffs(thread_id)
                recorded = await self._recorded_handoff(thread_id, call_id, reason)
                if recorded is None:
                    return {"handoff_status": "session_lost", "snapshot_status": "skipped",
                            "content": "Browser session was lost while waiting. Start a new browser session and inspect it before continuing."}
                result, payload, expired_receipt = recorded
                if result is not None:
                    return result
                if decision is None and expired_receipt is not None:
                    # The record stays for the workflow, which still waits for the user's answer.
                    return expired_receipt
                return await self._settle_handoff(thread_id, call_id, reason, payload, expired_receipt, decision or "cancel", authorize_snapshot)

        try:
            # The caller holds a request open meanwhile, so the wait must end with the handoff.
            decision = await asyncio.wait_for(wait_for_human(payload), payload["expires_at"] - time.time() + HANDOFF_EXPIRY_GRACE_SECONDS)
        except TimeoutError:
            return await settle(None)
        except asyncio.CancelledError:
            # Nobody can answer anymore, and the browser must not stay under human control.
            try:
                await asyncio.shield(settle("cancel"))
            except Exception:
                logger.warning("A browser handoff could not be released after its turn ended.", exc_info=True)
            raise
        return await settle(decision)

    @staticmethod
    def _handoff_fingerprint(reason):
        return hashlib.sha256(reason.encode()).hexdigest()

    async def _recorded_handoff(self, thread_id, call_id, reason):
        """Return (finished result, request, expired receipt), or None if this call never started."""
        prior = await self.repository.invocation(thread_id, call_id)
        if prior is None:
            return None
        if prior.arguments_hash != self._handoff_fingerprint(reason):
            raise BrowserError("invalid_input", "Handoff call changed during replay.")
        if prior.status == "completed":
            return json.loads(prior.result), None, None
        if prior.status not in {"handoff", "expired"}:
            raise BrowserError("invalid_input", "Handoff call changed during replay.")
        payload = json.loads(prior.result)
        if prior.status == "expired":
            return None, payload["handoff"], payload["receipt"]
        return None, payload, None

    async def _open_handoff(self, thread_id, owner_id, call_id, reason):
        """Give the browser to the user, unless this call already did."""
        if not owner_id.startswith("admin:") or not call_id:
            raise BrowserError("approval_unavailable", "Browser handoff requires an authenticated dashboard conversation.")
        await self.repository.bind(thread_id, owner_id)
        await self._expire_handoffs(thread_id)
        recorded = await self._recorded_handoff(thread_id, call_id, reason)
        if recorded is not None:
            return recorded
        current = await self._ensure(thread_id, owner_id)
        async with current.lock:
            self.assert_agent(current)
            current.handoff_id = uuid4().hex
            current.handoff_expires_at = time.time() + self.idle_ttl
            current.active_tab_id = None
            current.last_activity = time.monotonic()
            payload = {"type": "browser_handoff_request", "handoff_id": current.handoff_id,
                       "generation": current.generation, "reason": reason,
                       "expires_at": current.handoff_expires_at,
                       "tool_call_id": call_id, "thread_id": thread_id}
            try:
                await self.repository.record(thread_id, call_id, self._handoff_fingerprint(reason), "handoff", payload)
            except BaseException:
                current.handoff_id = None
                current.handoff_expires_at = None
                raise
            finally:
                current.notify()
        return None, payload, None

    async def _settle_handoff(self, thread_id, call_id, reason, payload, expired_receipt, decision, authorize_snapshot):
        """Take the browser back after the user's decision and record the receipt."""
        current = self.sessions.get(thread_id)
        if expired_receipt is not None:
            result = HANDOFF_CANCELLED if decision == "cancel" else expired_receipt
        elif not current or current.generation != payload["generation"]:
            result = HANDOFF_CANCELLED if decision == "cancel" else {
                "handoff_status": "session_lost", "snapshot_status": "skipped",
                "content": "Browser session was lost while waiting. Start a new browser session and inspect it before continuing."}
        else:
            async with current.lock:
                current.handoff_id = None
                current.handoff_expires_at = None
                current.controller = None
                current.notify()
                current.last_activity = time.monotonic()
                if decision == "cancel":
                    await self._dispose(current)
                else:
                    # Reset snapshot IDs held by the previous facade after human interaction.
                    if hasattr(current.driver, "reset_facade"):
                        try:
                            async with asyncio.timeout(self.timeout):
                                await current.driver.reset_facade(self.root / "run-artifacts" / self.storage_key(thread_id),
                                                                  current.active_tab_id)
                        except asyncio.CancelledError:
                            await asyncio.shield(self._dispose(current, fatal=True))
                            raise
                        except Exception:
                            await self._dispose(current, fatal=True)
                current.active_tab_id = None
                if decision == "cancel":
                    result = HANDOFF_CANCELLED
                elif current.closed:
                    result = {"handoff_status": "session_lost", "snapshot_status": "skipped",
                              "content": "Browser session was lost while returning control. Start a new session and inspect it before continuing."}
                else:
                    result = await self._handoff_snapshot(current, authorize_snapshot)
        await self.repository.record(thread_id, call_id, self._handoff_fingerprint(reason), "completed", result)
        if current:
            current.notify()
        return result

    async def abandon_handoffs(self, thread_id, *, expired_only=False):
        """Finish handoffs that no workflow will resume, so they stop blocking this thread's browser.

        ``expired_only`` leaves a handoff that the user can still answer in place.
        """
        thread_id = self.canonical(thread_id)
        if not await self.repository.pending_handoff(thread_id, include_expired=True):
            return
        async with self.thread_scope(thread_id):
            await self.repository.finish_handoffs(thread_id, HANDOFF_CANCELLED, expired_only=expired_only)
            current = self.sessions.get(thread_id)
            if not expired_only and current and current.handoff_id:
                await self._close_session(current)

    async def _handoff_snapshot(self, current, authorize_snapshot):
        """Observe the returned browser under its lock before completing the handoff receipt."""
        result = {"handoff_status": "returned", "snapshot_status": "failed",
                  "content": "HANDOFF COMPLETE: Browser control has been returned to you (the agent). The user has completed their manual interaction and returned control back to you. You (the agent) are now in full control of the browser. However, a fresh snapshot could not be captured. Call browser_snapshot to inspect the current page before continuing. Do not tell the user to interact or return control."}
        try:
            if authorize_snapshot is not None:
                authorize_snapshot()
            async with asyncio.timeout(self.timeout):
                snapshot = compact_snapshot(await current.driver.facade.snapshot(include_iframes=True))
            current.snapshot_tree = snapshot
        except asyncio.CancelledError:
            await asyncio.shield(self._dispose(current, fatal=True))
            raise
        except TimeoutError:
            await self._dispose(current, fatal=True)
            result.update(handoff_status="session_lost",
                          content="Browser control returned, but the fresh snapshot timed out and the browser was closed. Start a new browser session and inspect it before continuing.",
                          snapshot_error={"code": "timeout", "message": "The post-handoff snapshot timed out."})
        except BrowserError as exc:
            result["snapshot_error"] = {"code": exc.code, "message": str(exc)}
        except Exception:
            result["snapshot_error"] = {"code": "snapshot_failed", "message": "Could not read the current browser page. Call browser_snapshot to retry before continuing."}
        else:
            result.update(snapshot_status="captured", snapshot=snapshot,
                          content="HANDOFF COMPLETE: Browser control has been returned to you (the agent). The user has finished their manual interaction in the browser and handed control back to you. You (the agent) are now in full control of the browser. Do NOT tell the user they have control. Do NOT repeat instructions to enter input or return control. Inspect the attached snapshot of the current page and immediately proceed with the requested task or summarize what was done.")
        finally:
            current.last_activity = time.monotonic()
        return result

    async def issue_ticket(self, thread_id, owner_id):
        thread_id = self.canonical(thread_id)
        async with self.thread_scope(thread_id):
            await self.repository.bind(thread_id, owner_id)
            await self._expire_handoffs(thread_id)
            return self._issue_ticket(thread_id, owner_id)

    def _issue_ticket(self, thread_id, owner_id):
        current = self.sessions.get(thread_id)
        if current is None or current.closed:
            raise BrowserError("session_lost", "Open the browser before connecting a viewer.")
        now = time.monotonic()
        self.tickets = {key: value for key, value in self.tickets.items() if value[3] > now}
        token = secrets.token_urlsafe(32)
        self.tickets[token] = (current.thread_id, owner_id, current.generation, now + 60)
        return token

    def consume_ticket(self, token):
        ticket = self.tickets.pop(token, None)
        if not ticket or ticket[3] < time.monotonic():
            raise BrowserAccessDenied("Viewer ticket is invalid or expired.")
        current = self.sessions.get(ticket[0])
        if not current or current.closed or current.owner_id != ticket[1] or current.generation != ticket[2]:
            raise BrowserAccessDenied("Browser session no longer exists.")
        return current

    async def _sweep(self):
        while True:
            await asyncio.sleep(min(30, self.idle_ttl))
            # Startup and every deletion purge by themselves; only their leftovers need retries.
            if self.undeleted:
                await self.purge_discarded()
            await self.reap_idle()

    async def reap_idle(self):
        targets = set(self.sessions) | await self.repository.handoff_threads()

        async def reap(thread_id):
            # A busy conversation must not delay cleanup of other conversations.
            lock = self.operation_locks.get(f"thread:{thread_id}")
            if lock and lock.locked():
                return
            try:
                async with self.thread_scope(thread_id):
                    await self._expire_handoffs(thread_id)
                    current = self.sessions.get(thread_id)
                    if current and not current.lock.locked() and time.monotonic() - current.last_activity >= self.idle_ttl:
                        await self._close_session(current)
            except BrowserError as exc:
                if exc.code != "session_lost":
                    raise

        await asyncio.gather(*(reap(thread_id) for thread_id in targets))

    async def _expire_handoffs(self, thread_id):
        expired = await self.repository.expire_handoffs(thread_id)
        current = self.sessions.get(thread_id)
        if current and (any(payload["handoff_id"] == current.handoff_id for payload in expired)
                        or (current.handoff_expires_at is not None and current.handoff_expires_at <= time.time())):
            await self._close_session(current)

    async def shutdown(self):
        self.closing = True
        if self.sweeper:
            self.sweeper.cancel()
            await asyncio.gather(self.sweeper, return_exceptions=True)
            self.sweeper = None
        targets = set(self.sessions)
        targets.update(key.removeprefix("thread:") for key in self.operation_locks if key.startswith("thread:"))

        async def release(thread_id):
            async with self.operation_scope(f"thread:{thread_id}"):
                current = self.sessions.get(thread_id)
                if current:
                    await self._close_session(current)

        await asyncio.gather(*(release(thread_id) for thread_id in targets))
        self.tickets.clear()

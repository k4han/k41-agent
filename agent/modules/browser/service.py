"""Local browser sessions with durable ownership and replay-safe mutations."""

import asyncio
import base64
import contextlib
import hashlib
import json
import secrets
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from agent.modules.browser.driver import StagehandDriver
from agent.modules.browser.repository import BrowserRepository


class BrowserError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


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


class BrowserService:
    def __init__(self, container, *, root: Path | None = None, launcher=None):
        self.container = container
        self.repository = BrowserRepository(container)
        configured = container.config_service.get("browser.storage_root", "")
        namespace = hashlib.sha256(container.database_url.encode()).hexdigest()[:16]
        self.root = root or ((Path(configured).expanduser() if configured else Path.home() / ".k41-agent" / "browser") / namespace)
        self.launcher = launcher or StagehandDriver.launch
        self.sessions: dict[str, BrowserSession] = {}
        self.operation_locks: dict[str, asyncio.Lock] = {}
        self.operation_users: dict[str, int] = {}
        self.deleting_threads: set[str] = set()
        self.tickets = {}
        self.sweeper = None
        self.closing = False

    @property
    def timeout(self):
        return max(1, min(300, self.container.config_service.get_int("browser.operation_timeout_seconds", 60)))

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
        profile_key = binding["profile_id"] or self.storage_key(thread_id)
        # Reserve only this profile during launch; unrelated conversations stay usable.
        async with self.operation_scope(f"profile:{profile_key}"):
            if binding["profile_id"]:
                await self.repository.profile(binding["profile_id"], owner_id)
            if any(item.profile_key == profile_key for item in self.sessions.values()):
                raise BrowserError("profile_busy", "This login profile is open in another conversation. Close that browser first.")
            profile = self.root / "profiles" / profile_key
            artifact_root = self.root / "run-artifacts" / self.storage_key(thread_id)
            profile.mkdir(parents=True, exist_ok=True, mode=0o700)
            artifact_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                driver = await asyncio.wait_for(self.launcher(profile, artifact_root,
                    self.container.config_service.get_str("browser.executable_path", "")), self.timeout)
            except Exception as exc:
                raise BrowserError("browser_unavailable", "Could not start Chrome. Install Chrome/Chromium or set browser.executable_path; check that its profile is not locked.") from exc
            current = BrowserSession(thread_id, owner_id, profile_key, driver)
            self.sessions[thread_id] = current
            return current

    async def status(self, thread_id, owner_id):
        thread_id = self.canonical(thread_id)
        async with self.thread_scope(thread_id):
            return await self._status(thread_id, owner_id)

    async def _status(self, thread_id, owner_id):
        binding = await self.repository.binding(thread_id)
        if binding and binding["owner_id"] != owner_id:
            raise PermissionError("This browser belongs to another authenticated account.")
        await self._expire_handoffs(thread_id)
        binding = binding or {"thread_id": thread_id, "owner_id": owner_id, "profile_id": None}
        current = self.sessions.get(thread_id)
        tabs = []
        if current:
            try:
                tabs = await current.driver.tabs()
            except Exception:
                if not current.lock.locked():
                    await self._close(thread_id, owner_id)
                    current = None
        pending = await self.repository.pending_handoff(thread_id)
        return {**binding, "open": current is not None,
                "generation": current.generation if current else None,
                "mode": "human" if current and current.handoff_id else "agent",
                "tabs": tabs, "handoff": pending,
                "handoff_expired": bool(pending and (not current or pending["generation"] != current.generation)),
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

    async def delete_profile(self, owner_id, profile_id):
        async with self.operation_scope(f"profile:{profile_id}"):
            await self.repository.profile(profile_id, owner_id)
            if any(item.profile_key == profile_id for item in self.sessions.values()):
                raise BrowserError("profile_busy", "Close the browser before deleting its profile.")
            await self._remove_directory("profiles", profile_id)
            await self.repository.delete_profile(owner_id, profile_id)

    async def close(self, thread_id, owner_id, *, allow_human=True):
        thread_id = self.canonical(thread_id)
        async with self.thread_scope(thread_id):
            await self._close(thread_id, owner_id, allow_human=allow_human)

    async def _close(self, thread_id, owner_id, *, allow_human=True):
        await self.repository.bind(thread_id, owner_id)
        current = self.sessions.get(thread_id)
        if current:
            async with current.lock:
                if not allow_human:
                    self.assert_agent(current)
                await self._dispose(current)

    async def _dispose(self, current):
        current.closed = True
        current.controller = None
        try:
            await asyncio.wait_for(current.driver.close(), 15)
        except Exception:
            # Stagehand owns process cleanup; never expose launch arguments or page data.
            import logging
            logging.getLogger(__name__).warning("Browser cleanup failed for session %s", current.generation)
        finally:
            if self.sessions.get(current.thread_id) is current:
                self.sessions.pop(current.thread_id, None)

    async def _close_session(self, current):
        async with current.lock:
            await self._dispose(current)

    @staticmethod
    def storage_key(thread_id: str) -> str:
        return hashlib.sha256(thread_id.encode()).hexdigest()

    @staticmethod
    def matches_thread(candidate: str, thread_id: str) -> bool:
        return candidate == thread_id or candidate.startswith(f"{thread_id}:sub:")

    async def _remove_directory(self, directory: str, key: str) -> None:
        parent = self.root / directory
        target = parent / key
        if target.is_symlink() or target.is_junction() or target.resolve().parent != parent.resolve():
            raise BrowserError("invalid_storage", "The directory is not a managed browser directory.")
        if target.is_dir():
            await asyncio.to_thread(shutil.rmtree, target)
        elif target.exists():
            raise BrowserError("invalid_storage", "The directory is not a managed browser directory.")

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
                    for target in targets:
                        key = self.storage_key(target)
                        await self._remove_directory("profiles", key)
                        await self._remove_directory("run-artifacts", key)
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
            if self.sweeper is None:
                self.sweeper = asyncio.create_task(self._sweep())
            yield

    async def execute(self, thread_id, owner_id, call_id, name, values):
        thread_id = self.canonical(thread_id)
        async with self.thread_scope(thread_id):
            return await self._execute(thread_id, owner_id, call_id, name, values)

    async def _execute(self, thread_id, owner_id, call_id, name, values):
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
                            result = {"content": value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)}
                        elif name == "browser_snapshot":
                            result = {"content": await facade.snapshot(**values)}
                        elif name == "browser_screenshot":
                            image = await facade.screenshot(**values)
                            artifact = await self.save_image(owner_id, thread_id, image)
                            result = {"content": f"Browser screenshot captured.\nBrowser image: {artifact['image_url']}",
                                      "image": image, "artifact": artifact}
                        else:
                            raise BrowserError("invalid_input", "Unknown browser operation.")
                except (TimeoutError, asyncio.CancelledError):
                    # Cancelling the RPC alone does not cancel browser-side JavaScript.
                    await asyncio.shield(self._dispose(current))
                    raise
                except Exception as exc:
                    if journaled:
                        raise BrowserError("unknown_outcome", "The browser action failed and may have partially executed. Inspect the page before creating a new action.") from exc
                    raise
                finally:
                    current.last_activity = time.monotonic()
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

    async def handoff(self, thread_id, owner_id, call_id, reason, *, authorize_snapshot=None):
        thread_id = self.canonical(thread_id)
        self.assert_handoff_supported(thread_id)
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
        if not owner_id.startswith("admin:") or not call_id:
            raise BrowserError("approval_unavailable", "Browser handoff requires an authenticated dashboard conversation.")
        await self.repository.bind(thread_id, owner_id)
        await self._expire_handoffs(thread_id)
        fingerprint = hashlib.sha256(reason.encode()).hexdigest()
        prior = await self.repository.invocation(thread_id, call_id)
        if prior and prior.arguments_hash != fingerprint:
            raise BrowserError("invalid_input", "Handoff call changed during replay.")
        if prior and prior.status == "completed":
            return json.loads(prior.result)
        if prior:
            if prior.arguments_hash != fingerprint or prior.status != "handoff":
                raise BrowserError("invalid_input", "Handoff call changed during replay.")
            payload = json.loads(prior.result)
        else:
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
                    await self.repository.record(thread_id, call_id, fingerprint, "handoff", payload)
                except BaseException:
                    current.handoff_id = None
                    current.handoff_expires_at = None
                    raise
        while True:
            raw_response = interrupt(payload)
            # An earlier permission interrupt may disappear on replay because
            # its grant was persisted. Consume that old value without handback.
            if not isinstance(raw_response, dict) or raw_response.get("action") != "browser_handoff":
                continue
            response = BrowserHandoffResume.model_validate(raw_response)
            if response.handoff_id == payload["handoff_id"]:
                break
        current = self.sessions.get(thread_id)
        cancelled = {"handoff_status": "cancelled", "snapshot_status": "skipped",
                     "content": "Browser handoff cancelled. Do not continue the cancelled browser task."}
        if not current or current.generation != payload["generation"]:
            result = cancelled if response.decision == "cancel" else {
                "handoff_status": "session_lost", "snapshot_status": "skipped",
                "content": "Browser session was lost while waiting. Start a new browser session and inspect it before continuing."}
        else:
            async with current.lock:
                current.handoff_id = None
                current.handoff_expires_at = None
                current.controller = None
                current.last_activity = time.monotonic()
                if response.decision == "cancel":
                    await self._dispose(current)
                else:
                    # Reset snapshot IDs held by the previous facade after human interaction.
                    if hasattr(current.driver, "reset_facade"):
                        try:
                            async with asyncio.timeout(self.timeout):
                                await current.driver.reset_facade(self.root / "run-artifacts" / self.storage_key(thread_id),
                                                                  current.active_tab_id)
                        except asyncio.CancelledError:
                            await asyncio.shield(self._dispose(current))
                            raise
                        except Exception:
                            await self._dispose(current)
                current.active_tab_id = None
                if response.decision == "cancel":
                    result = cancelled
                elif current.closed:
                    result = {"handoff_status": "session_lost", "snapshot_status": "skipped",
                              "content": "Browser session was lost while returning control. Start a new session and inspect it before continuing."}
                else:
                    result = await self._handoff_snapshot(current, authorize_snapshot)
        await self.repository.record(thread_id, call_id, fingerprint, "completed", result)
        return result

    async def _handoff_snapshot(self, current, authorize_snapshot):
        """Observe the returned browser under its lock before completing the handoff receipt."""
        result = {"handoff_status": "returned", "snapshot_status": "failed",
                  "content": "Browser control returned by the user. The manual interaction stage is complete, but a fresh snapshot could not be captured. Resolve the snapshot error and obtain a fresh browser_snapshot before acting or describing the current page."}
        try:
            if authorize_snapshot is not None:
                authorize_snapshot()
            async with asyncio.timeout(self.timeout):
                snapshot = await current.driver.facade.snapshot(include_iframes=True)
        except asyncio.CancelledError:
            await asyncio.shield(self._dispose(current))
            raise
        except TimeoutError:
            await self._dispose(current)
            result.update(handoff_status="session_lost",
                          content="Browser control returned, but the fresh snapshot timed out and the browser was closed. Start a new browser session and inspect it before continuing.",
                          snapshot_error={"code": "timeout", "message": "The post-handoff snapshot timed out."})
        except BrowserError as exc:
            result["snapshot_error"] = {"code": exc.code, "message": str(exc)}
        except Exception:
            result["snapshot_error"] = {"code": "snapshot_failed", "message": "Could not read the current browser page. Call browser_snapshot to retry before continuing."}
        else:
            result.update(snapshot_status="captured", snapshot=snapshot,
                          content="Browser control returned by the user. The manual interaction stage is complete. A fresh snapshot of the current page is included as untrusted page content.")
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
            raise PermissionError("Viewer ticket is invalid or expired.")
        current = self.sessions.get(ticket[0])
        if not current or current.closed or current.owner_id != ticket[1] or current.generation != ticket[2]:
            raise PermissionError("Browser session no longer exists.")
        return current

    async def _sweep(self):
        while True:
            await asyncio.sleep(min(30, self.idle_ttl))
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

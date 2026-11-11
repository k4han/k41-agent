"""Instabox integration service shared by dashboard routes, agent tools and the workspace backend."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx

from agent.modules.instabox.client import InstaboxClient, new_idempotency_key, redact
from agent.modules.instabox.config import InstaboxSettings, load_instabox_settings
from agent.modules.instabox.errors import (
    InstaboxApiError,
    InstaboxConfirmationError,
    InstaboxConflictError,
    InstaboxDisabledError,
)
from agent.modules.instabox.repository import InstaboxRepository
from agent.modules.instabox.states import machine_summary, normalize_machine_state, status_from_error

logger = logging.getLogger(__name__)

OPERATION_TIMEOUT_SECONDS = 1800.0
SNAPSHOT_POLL_SECONDS = 3.0
LEASE_TTL_SECONDS = 300
LEASE_RENEW_SECONDS = 120
_NAME_RE = re.compile(r"[^a-z0-9-]+")


def instabox_name(value: str | None, *, prefix: str = "k41") -> str:
    """Return a valid Instabox resource name (lowercase letters, digits and ``-``)."""
    text = _NAME_RE.sub("-", str(value or "").strip().lower()).strip("-")
    if not text:
        text = f"{prefix}-{uuid.uuid4().hex[:8]}"
    if not text[0].isalnum():
        text = f"{prefix}-{text}"
    return text[:63].rstrip("-") or f"{prefix}-{uuid.uuid4().hex[:8]}"


class InstaboxService:
    def __init__(
        self,
        container: Any,
        *,
        repository: InstaboxRepository | None = None,
        http_client: httpx.AsyncClient | None = None,
        settings_loader: Any = None,
    ) -> None:
        self.container = container
        self.repository = repository or InstaboxRepository(container)
        self._http_client = http_client
        self._settings_loader = settings_loader or load_instabox_settings
        self._client: InstaboxClient | None = None
        self._client_fingerprint: tuple[str, str] | None = None
        self._client_lock = asyncio.Lock()
        self._watchers: dict[str, asyncio.Task] = {}
        self._closing = False
        self._desktop = None
        self._machine_locks: dict[str, asyncio.Lock] = {}
        self._resume_task: asyncio.Task | None = None

    # ------------------------------------------------------------------ #
    #  Client lifecycle
    # ------------------------------------------------------------------ #

    def settings(self) -> InstaboxSettings:
        return self._settings_loader()

    async def client(self) -> InstaboxClient:
        settings = self.settings()
        if not settings.enabled:
            raise InstaboxDisabledError("Instabox workspace backend is disabled.")
        if not settings.api_key:
            raise InstaboxDisabledError("Instabox API key is not configured.")
        async with self._client_lock:
            if self._client is None or self._client_fingerprint != settings.fingerprint:
                previous = self._client
                self._client = InstaboxClient(
                    api_key=settings.api_key, api_url=settings.api_url, client=self._http_client
                )
                self._client_fingerprint = settings.fingerprint
                if previous is not None:
                    with contextlib.suppress(Exception):
                        await previous.close()
            return self._client

    @property
    def desktop(self):
        if self._desktop is None:
            from agent.modules.instabox.desktop import DesktopService

            self._desktop = DesktopService(self)
        return self._desktop

    def machine_lock(self, machine_id: str) -> asyncio.Lock:
        lock = self._machine_locks.get(machine_id)
        if lock is None:
            lock = self._machine_locks[machine_id] = asyncio.Lock()
        return lock

    def start(self) -> None:
        """Resume tracking in the background so startup never waits on the network."""
        if self._resume_task is None or self._resume_task.done():
            self._resume_task = asyncio.create_task(
                self.resume_pending_operations(), name="instabox-resume-operations"
            )

    async def close(self) -> None:
        self._closing = True
        if self._resume_task is not None:
            self._resume_task.cancel()
            await asyncio.gather(self._resume_task, return_exceptions=True)
            self._resume_task = None
        watchers = list(self._watchers.values())
        for task in watchers:
            task.cancel()
        if watchers:
            await asyncio.gather(*watchers, return_exceptions=True)
        self._watchers.clear()
        if self._desktop is not None:
            await self._desktop.close()
        if self._client is not None:
            with contextlib.suppress(Exception):
                await self._client.close()
            self._client = None

    # ------------------------------------------------------------------ #
    #  Durable operations
    # ------------------------------------------------------------------ #

    async def submit(
        self,
        *,
        kind: str,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        machine_id: str | None = None,
        target: dict[str, Any] | None = None,
        created_by: str | None = None,
        wait: bool = False,
        timeout: float = OPERATION_TIMEOUT_SECONDS,
        auxiliary_reservation_id: int | None = None,
    ) -> dict[str, Any]:
        """Persist, send and track one asynchronous change.

        The record (with its ``Idempotency-Key``) is stored before the request
        so that a crash can be recovered by resending the same request.
        """
        try:
            client = await self.client()
            key = new_idempotency_key()
            request_spec = {"method": method, "path": path, "body": body, "headers": headers or {}}
            operation_target = {**(target or {}), "request": request_spec}
            if auxiliary_reservation_id is not None:
                operation_target["auxiliary_reservation_id"] = auxiliary_reservation_id
            record = await self.repository.create_operation(
                idempotency_key=key,
                kind=kind,
                target=operation_target,
                machine_id=machine_id,
                created_by=created_by,
            )
        except BaseException:
            await self._cancel_reservation(auxiliary_reservation_id)
            raise
        try:
            response = await client.request(
                method, path, json=body, headers=headers, idempotency_key=key
            )
        except InstaboxApiError as exc:
            await self._update_operation(key, status="failed", error=exc.to_payload())
            raise
        record = await self._record_response(key, kind, response, machine_id)
        if record["status"] in {"succeeded", "failed"}:
            return record
        task = self._watch(key)
        if wait:
            return await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        return record

    async def _cancel_reservation(self, record_id: int | None) -> None:
        if record_id is not None:
            from agent.modules.workspaces import cancel_reservation

            await asyncio.shield(cancel_reservation(record_id))

    async def _update_operation(self, key: str, **fields: Any) -> dict[str, Any]:
        """Settle resource ownership before marking an operation terminal.

        Pending operations retain their reservation across cancellation, timeout
        and restart. If settlement fails, startup can retry the pending record.
        """
        if fields.get("status") in {"succeeded", "failed"}:
            record = await self.repository.get_operation_by_key(key)
            if record is not None:
                settled = {**record, **fields}
                target = settled.get("target") or {}
                reservation = target.get("auxiliary_reservation_id")
                if reservation is not None:
                    if settled["status"] == "failed":
                        await self._cancel_reservation(reservation)
                    elif settled.get("machine_id"):
                        from agent.modules.workspaces import activate_thread_machine

                        try:
                            await activate_thread_machine(reservation, settled["machine_id"])
                        except LookupError:
                            logger.warning("Completed Instabox machine %s has a released reservation %s.",
                                           settled["machine_id"], reservation)
                if settled["status"] == "succeeded" and settled["kind"] == "snapshot_create":
                    ownership = target.get("snapshot_owner")
                    snapshot_id = (settled.get("result") or {}).get("id") or settled.get("operation_id")
                    if ownership and snapshot_id:
                        from agent.modules.workspaces import SNAPSHOT, record_thread_resource

                        await record_thread_resource(
                            ownership["thread_id"], SNAPSHOT, "instabox", snapshot_id,
                            root=ownership.get("root") or "", purpose=f"snapshot of {settled.get('machine_id')}",
                        )
                if settled["status"] == "succeeded" and settled["kind"] in {"stop", "delete"}:
                    from agent.modules.tools import forget_machine

                    machine_id = settled.get("machine_id")
                    if machine_id:
                        await forget_machine("instabox", machine_id, deleted=settled["kind"] == "delete")
                        if settled["kind"] == "delete":
                            with contextlib.suppress(Exception):
                                await self.repository.delete_port_token(machine_id)
        return await self.repository.update_operation(key, **fields)

    async def _record_response(
        self, key: str, kind: str, response: Any, machine_id: str | None
    ) -> dict[str, Any]:
        payload = response if isinstance(response, dict) else {}
        if kind in {"snapshot_create"}:
            state = str(payload.get("state") or payload.get("status") or "pending")
            status = "succeeded" if state == "ready" else "failed" if state == "failed" else "running"
            return await self._update_operation(
                key,
                operation_id=payload.get("id"),
                machine_id=machine_id,
                status=status,
                phase=state,
                result=payload if status != "running" else None,
            )
        if not payload:
            # 204 responses (for example a delete already finished) have no operation.
            return await self._update_operation(key, status="succeeded", result=None)
        operation_state = str(payload.get("state") or "queued")
        fields: dict[str, Any] = {
            "operation_id": payload.get("id"),
            "machine_id": payload.get("machineId") or machine_id,
            "status": operation_state if operation_state in {"queued", "running", "succeeded", "failed"} else "running",
            "phase": payload.get("phase"),
        }
        if operation_state in {"succeeded", "failed"}:
            fields["result"] = redact(payload)
            if operation_state == "failed":
                fields["error"] = redact(payload.get("error"))
        return await self._update_operation(key, **fields)

    def _watch(self, key: str) -> asyncio.Task:
        existing = self._watchers.get(key)
        if existing is not None and not existing.done():
            return existing
        task = asyncio.create_task(self._watch_operation(key), name=f"instabox-op-{key}")
        self._watchers[key] = task
        task.add_done_callback(lambda _task, k=key: self._watchers.pop(k, None))
        return task

    async def _watch_operation(self, key: str) -> dict[str, Any]:
        record = await self.repository.get_operation_by_key(key)
        if record is None:
            raise LookupError(f"Unknown Instabox operation {key}")
        client = await self.client()
        if record["kind"] == "snapshot_create":
            return await self._watch_snapshot(client, record)
        operation_id = record.get("operation_id")
        while not self._closing:
            try:
                operation = await client.get_operation(operation_id, wait_seconds=25)
            except InstaboxApiError as exc:
                if exc.is_not_found:
                    return await self._update_operation(
                        key, status="failed", error=exc.to_payload()
                    )
                raise
            state = str(operation.get("state") or "")
            if state in {"succeeded", "failed"}:
                return await self._update_operation(
                    key,
                    status=state,
                    phase=operation.get("phase"),
                    machine_id=operation.get("machineId") or record.get("machine_id"),
                    result=redact(operation),
                    error=redact(operation.get("error")) if state == "failed" else None,
                )
            await self._update_operation(
                key, status=state or "running", phase=operation.get("phase")
            )
        raise asyncio.CancelledError

    async def _watch_snapshot(self, client: InstaboxClient, record: dict[str, Any]) -> dict[str, Any]:
        key = record["idempotency_key"]
        snapshot_id = record.get("operation_id")
        while not self._closing:
            snapshot = await client.get_snapshot(snapshot_id)
            state = str(snapshot.get("state") or snapshot.get("status") or "")
            if state in {"ready", "failed"}:
                return await self._update_operation(
                    key,
                    status="succeeded" if state == "ready" else "failed",
                    phase=state,
                    result=snapshot,
                    error=snapshot.get("error") if state == "failed" else None,
                )
            await asyncio.sleep(SNAPSHOT_POLL_SECONDS)
        raise asyncio.CancelledError

    async def resume_pending_operations(self) -> None:
        """Resume tracking after a restart; resend requests that never got an answer."""
        try:
            settings = self.settings()
        except Exception:
            return
        if not settings.enabled or not settings.api_key:
            return
        try:
            pending = await self.repository.list_operations(active_only=True, limit=200)
        except Exception:
            logger.debug("Instabox operation table is unavailable.", exc_info=True)
            return
        for record in pending:
            try:
                await self._resume_one(record)
            except Exception as exc:
                logger.warning("Could not resume Instabox operation %s: %s", record.get("id"), redact(str(exc)))

    async def _resume_one(self, record: dict[str, Any]) -> None:
        key = record["idempotency_key"]
        if record["status"] == "submitting" or not record.get("operation_id"):
            request = (record.get("target") or {}).get("request") or {}
            if not request.get("method") or not request.get("path"):
                await self._update_operation(
                    key, status="failed", error={"code": "unrecoverable", "message": "Request details were lost."}
                )
                return
            client = await self.client()
            # Same Idempotency-Key: Instabox returns the original response instead of acting twice.
            response = await client.request(
                request["method"],
                request["path"],
                json=request.get("body"),
                headers=request.get("headers") or None,
                idempotency_key=key,
            )
            record = await self._record_response(key, record["kind"], response, record.get("machine_id"))
            if record["status"] in {"succeeded", "failed"}:
                return
        self._watch(key)

    async def operation(self, record_id: int) -> dict[str, Any] | None:
        return await self.repository.get_operation(record_id)

    async def operations(self, *, machine_id: str | None = None, active_only: bool = False) -> list[dict[str, Any]]:
        return await self.repository.list_operations(machine_id=machine_id, active_only=active_only)

    # ------------------------------------------------------------------ #
    #  Machines
    # ------------------------------------------------------------------ #

    async def list_machines(self) -> list[dict[str, Any]]:
        client = await self.client()
        return [machine_summary(machine) async for machine in client.iter_machines()]

    async def get_machine(self, machine_id: str) -> dict[str, Any]:
        client = await self.client()
        return machine_summary(await client.get_machine(machine_id))

    async def machine_status(self, machine_id: str) -> str:
        """Read-only status probe; never wakes the machine."""
        try:
            client = await self.client()
            machine = await client.get_machine(machine_id)
        except Exception as exc:
            return status_from_error(exc)
        return normalize_machine_state(machine.get("state"))

    def create_body(
        self,
        *,
        name: str | None = None,
        snapshot_id: str | None = None,
        template: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        settings = self.settings()
        body = settings.create_payload(snapshot_id=snapshot_id)
        if template and "snapshotId" not in body:
            body["templateId"] = template
        if name:
            body["name"] = instabox_name(name)
        if metadata:
            body["metadata"] = {str(k)[:63]: str(v)[:255] for k, v in metadata.items() if v not in (None, "")}
        return body

    async def create_machine(
        self,
        *,
        name: str | None = None,
        snapshot_id: str | None = None,
        template: str | None = None,
        metadata: dict[str, str] | None = None,
        created_by: str | None = None,
        wait: bool = False,
        auxiliary_reservation_id: int | None = None,
    ) -> dict[str, Any]:
        try:
            body = self.create_body(name=name, snapshot_id=snapshot_id, template=template, metadata=metadata)
        except BaseException:
            await self._cancel_reservation(auxiliary_reservation_id)
            raise
        return await self.submit(
            kind="create",
            method="POST",
            path="/v1/machines",
            body=body,
            target={"name": body.get("name"), "snapshot_id": body.get("snapshotId"), "template": body.get("templateId")},
            created_by=created_by,
            wait=wait,
            auxiliary_reservation_id=auxiliary_reservation_id,
        )

    async def machine_action(
        self,
        machine_id: str,
        action: str,
        *,
        body: dict[str, Any] | None = None,
        created_by: str | None = None,
        wait: bool = False,
        auxiliary_reservation_id: int | None = None,
    ) -> dict[str, Any]:
        if action not in {"start", "stop", "suspend", "resume", "fork", "restore"}:
            raise ValueError(f"Unsupported machine action: {action}")
        return await self.submit(
            kind=action,
            method="POST",
            path=f"/v1/machines/{machine_id}/{action}",
            body=body or {},
            machine_id=machine_id if action != "fork" else None,
            target={"source_machine_id": machine_id} if action == "fork" else {"machine_id": machine_id},
            created_by=created_by,
            wait=wait,
            auxiliary_reservation_id=auxiliary_reservation_id,
        )

    async def fork_machine(
        self, machine_id: str, *, name: str | None = None, created_by: str | None = None, wait: bool = False,
        auxiliary_reservation_id: int | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if name:
            body["name"] = instabox_name(name)
        return await self.machine_action(machine_id, "fork", body=body, created_by=created_by, wait=wait,
                                         auxiliary_reservation_id=auxiliary_reservation_id)

    async def delete_machine(
        self, machine_id: str, *, confirm: str, created_by: str | None = None, wait: bool = False
    ) -> dict[str, Any]:
        if not confirm or confirm.strip() != machine_id.strip():
            raise InstaboxConfirmationError("Type the machine ID to confirm deletion.")
        return await self.submit(
            kind="delete",
            method="DELETE",
            path=f"/v1/machines/{machine_id}",
            headers={"X-Confirm-Delete": machine_id},
            machine_id=machine_id,
            target={"machine_id": machine_id},
            created_by=created_by,
            wait=wait,
        )

    async def ensure_running(self, machine_id: str, *, timeout: float = 600.0) -> dict[str, Any]:
        """Start or resume a machine when a workspace needs it, then wait until it runs."""
        client = await self.client()
        machine = await client.get_machine(machine_id)
        state = str(machine.get("state") or "")
        if state == "running":
            return machine
        if state in {"suspended", "stopped"}:
            action = "resume" if state == "suspended" else "start"
            try:
                await self.machine_action(machine_id, action, wait=True)
            except InstaboxApiError as exc:
                if exc.code != "operation_conflict" or not exc.operation_id:
                    raise
                await client.wait_operation(exc.operation_id, timeout=timeout)
        elif machine.get("operationId"):
            await client.wait_operation(str(machine["operationId"]), timeout=timeout)
        machine = await client.get_machine(machine_id)
        if machine.get("state") != "running":
            raise InstaboxApiError(
                f"Instabox machine {machine_id} is {machine.get('state')}.",
                status=409,
                code="machine_not_running",
            )
        return machine

    @contextlib.asynccontextmanager
    async def activity_lease(self, machine_id: str, lease_id: str | None = None) -> AsyncIterator[None]:
        """Keep a machine awake while a long task runs."""
        lease = lease_id or f"k41-{uuid.uuid4().hex}"
        try:
            client = await self.client()
            await client.put_activity_lease(machine_id, lease, ttl_seconds=LEASE_TTL_SECONDS)
        except Exception as exc:
            logger.debug("Instabox activity lease failed for %s: %s", machine_id, redact(str(exc)))
            yield
            return

        async def renew() -> None:
            while True:
                await asyncio.sleep(LEASE_RENEW_SECONDS)
                with contextlib.suppress(Exception):
                    await client.put_activity_lease(machine_id, lease, ttl_seconds=LEASE_TTL_SECONDS)

        renewer = asyncio.create_task(renew())
        try:
            yield
        finally:
            renewer.cancel()
            await asyncio.gather(renewer, return_exceptions=True)
            with contextlib.suppress(Exception):
                await client.delete_activity_lease(machine_id, lease)

    async def templates(self) -> list[dict[str, Any]]:
        return await (await self.client()).list_templates()

    # ------------------------------------------------------------------ #
    #  Ports
    # ------------------------------------------------------------------ #

    async def list_ports(self, machine_id: str) -> dict[str, Any]:
        payload = await (await self.client()).list_ports(machine_id)
        ports = []
        for item in payload.get("ports") or []:
            entry = redact(dict(item))
            port_number = item.get("port")
            if str(item.get("access") or "") == "private" and port_number is not None:
                entry["has_saved_token"] = await self.repository.port_token(machine_id, int(port_number)) is not None
            ports.append(entry)
        return {**{k: v for k, v in payload.items() if k != "ports"}, "ports": ports}

    async def publish_port(
        self,
        machine_id: str,
        port: int,
        *,
        access: str = "private",
        name: str | None = None,
        rotate_token: bool = False,
    ) -> dict[str, Any]:
        if access not in {"public", "private"}:
            raise ValueError("Port access must be public or private.")
        result = await (await self.client()).publish_port(
            machine_id, port, access=access, name=name, rotate_token=rotate_token
        )
        token = result.get("token")
        url = str(result.get("url") or "")
        if access == "private" and token:
            await self.repository.save_port_token(machine_id, int(port), str(token), url)
        elif access == "public":
            await self.repository.delete_port_token(machine_id, int(port))
        payload = redact({key: value for key, value in result.items() if key not in {"token", "tokenUrl"}})
        payload["link"] = await self.port_link(machine_id, int(port), fallback_url=url, access=access)
        return payload

    async def set_port_access(self, machine_id: str, port: int, access: str) -> dict[str, Any]:
        return await self.publish_port(machine_id, port, access=access)

    async def rotate_port_token(self, machine_id: str, port: int) -> dict[str, Any]:
        return await self.publish_port(machine_id, port, access="private", rotate_token=True)

    async def port_link(
        self, machine_id: str, port: int, *, fallback_url: str = "", access: str | None = None
    ) -> dict[str, Any]:
        """Return an openable link; private links need the stored token."""
        if access == "public":
            return {"url": fallback_url, "access": "public"}
        saved = await self.repository.port_token(machine_id, int(port))
        if saved is None:
            if access is None:
                ports = (await self.list_ports(machine_id)).get("ports") or []
                match = next((item for item in ports if int(item.get("port") or 0) == int(port)), None)
                if match is None:
                    raise LookupError(f"Port {port} is not published.")
                if str(match.get("access") or "public") == "public":
                    return {"url": match.get("url") or "", "access": "public"}
            return {"url": fallback_url, "access": "private", "token_missing": True}
        token, url = saved
        base = url or fallback_url
        separator = "&" if "?" in base else "?"
        return {"url": f"{base}{separator}_token={token}" if base else "", "access": "private"}

    async def close_port(self, machine_id: str, port: int) -> None:
        await (await self.client()).delete_port(machine_id, port)
        await self.repository.delete_port_token(machine_id, int(port))

    # ------------------------------------------------------------------ #
    #  Snapshots
    # ------------------------------------------------------------------ #

    async def list_snapshots(self) -> list[dict[str, Any]]:
        return await (await self.client()).list_snapshots()

    async def get_snapshot(self, snapshot_id: str) -> dict[str, Any]:
        return await (await self.client()).get_snapshot(snapshot_id)

    async def create_snapshot(
        self,
        machine_id: str,
        *,
        name: str,
        description: str | None = None,
        root_only: bool = False,
        created_by: str | None = None,
        wait: bool = False,
        owner_thread_id: str | None = None,
        workspace_root: str = "",
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"name": instabox_name(name, prefix="snap")}
        if description:
            body["description"] = description
        if root_only:
            body["rootOnly"] = True
        target: dict[str, Any] = {"machine_id": machine_id, "name": body["name"]}
        if owner_thread_id:
            from agent.modules.workspaces import root_thread_id

            target["snapshot_owner"] = {"thread_id": root_thread_id(owner_thread_id), "root": workspace_root}
        return await self.submit(
            kind="snapshot_create",
            method="POST",
            path=f"/v1/machines/{machine_id}/snapshots",
            body=body,
            machine_id=machine_id,
            target=target,
            created_by=created_by,
            wait=wait,
        )

    async def require_ready_snapshot(self, snapshot_id: str) -> dict[str, Any]:
        snapshot = await self.get_snapshot(snapshot_id)
        state = str(snapshot.get("state") or snapshot.get("status") or "")
        if state != "ready":
            raise InstaboxConflictError(
                f"Snapshot {snapshot_id} is {state or 'not ready'}; wait until it is ready.",
                code="snapshot_not_ready",
            )
        return snapshot

    async def delete_snapshot(self, snapshot_id: str, *, confirm: str) -> None:
        if not confirm or confirm.strip() != snapshot_id.strip():
            raise InstaboxConfirmationError("Type the snapshot ID to confirm deletion.")
        await (await self.client()).delete_snapshot(
            snapshot_id, confirm=snapshot_id, idempotency_key=new_idempotency_key()
        )

    async def create_machine_from_snapshot(
        self,
        snapshot_id: str,
        *,
        name: str | None = None,
        metadata: dict[str, str] | None = None,
        created_by: str | None = None,
        wait: bool = False,
        auxiliary_reservation_id: int | None = None,
    ) -> dict[str, Any]:
        try:
            await self.require_ready_snapshot(snapshot_id)
        except BaseException:
            await self._cancel_reservation(auxiliary_reservation_id)
            raise
        return await self.create_machine(
            name=name, snapshot_id=snapshot_id, metadata=metadata, created_by=created_by, wait=wait,
            auxiliary_reservation_id=auxiliary_reservation_id,
        )

    async def restore_machine(
        self,
        machine_id: str,
        snapshot_id: str,
        *,
        confirm_machine: str,
        confirm_snapshot: str,
        created_by: str | None = None,
    ) -> dict[str, Any]:
        from agent.modules.instabox.restore import restore_machine

        return await restore_machine(
            self,
            machine_id,
            snapshot_id,
            confirm_machine=confirm_machine,
            confirm_snapshot=confirm_snapshot,
            created_by=created_by,
        )


def get_instabox_service(container: Any = None) -> InstaboxService:
    from agent.bootstrap.container import require_active_container

    active = require_active_container(container)
    with active._lazy_lock:
        service = getattr(active, "_instabox_service", None)
        if service is None:
            service = InstaboxService(active)
            active._instabox_service = service
        return service


__all__ = ["InstaboxService", "get_instabox_service", "instabox_name"]

"""Async REST client for the Instabox API (https://api.instabox.sh/v1)."""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import quote

import httpx

from agent.modules.instabox.errors import InstaboxApiError

logger = logging.getLogger(__name__)

TERMINAL_OPERATION_STATES = frozenset({"succeeded", "failed"})
MAX_EXEC_TIMEOUT_SECONDS = 600
MAX_OPERATION_WAIT_SECONDS = 25
DEFAULT_REQUEST_TIMEOUT_SECONDS = 30.0
MAX_RETRIES = 3

_SECRET_PATTERNS = (
    re.compile(r"ibk_[A-Za-z0-9_\-]+"),
    re.compile(r"ibp_[A-Za-z0-9_\-]+"),
    re.compile(r"(_token=)[^&\s\"']+"),
    re.compile(r"(instabox-access-token\.)[^,\s\"']+"),
)


def redact(value: Any) -> Any:
    """Remove API keys and port tokens from strings, dicts and lists."""
    if isinstance(value, str):
        text = value
        for pattern in _SECRET_PATTERNS:
            if pattern.groups:
                text = pattern.sub(lambda match: match.group(1) + "[REDACTED]", text)
            else:
                text = pattern.sub("[REDACTED]", text)
        return text
    if isinstance(value, dict):
        return {
            key: ("[REDACTED]" if str(key).lower() in {"token", "api_key", "apikey", "x-api-key"} else redact(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def new_idempotency_key() -> str:
    return f"k41-{uuid.uuid4().hex}"


def _quote(segment: str) -> str:
    return quote(str(segment), safe="")


def _parse_error(response: httpx.Response) -> InstaboxApiError:
    payload: dict[str, Any] = {}
    try:
        body = response.json()
        if isinstance(body, dict):
            payload = body.get("error") if isinstance(body.get("error"), dict) else body
    except Exception:
        payload = {}
    retry_after: float | None = None
    raw_retry = payload.get("retryAfterSeconds") or response.headers.get("Retry-After")
    if raw_retry not in (None, ""):
        try:
            retry_after = float(raw_retry)
        except (TypeError, ValueError):
            retry_after = None
    code = str(payload.get("code") or "")
    message = str(payload.get("message") or response.reason_phrase or "Instabox request failed")
    return InstaboxApiError(
        redact(message),
        status=response.status_code,
        code=code,
        retryable=bool(payload.get("retryable")) or response.status_code in {429, 502, 503, 504},
        retry_after=retry_after,
        operation_id=payload.get("operationId") or None,
        request_id=payload.get("requestId") or response.headers.get("X-Request-Id"),
        details={k: v for k, v in payload.items() if k not in {"message"}},
    )


class InstaboxClient:
    """Thin async wrapper over the Instabox REST API.

    The client is shared for the application lifetime and closed by the
    container. Tests inject an ``httpx.AsyncClient`` with a ``MockTransport``.
    """

    def __init__(
        self,
        *,
        api_key: str,
        api_url: str = "https://api.instabox.sh",
        client: httpx.AsyncClient | None = None,
        timeout: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
        max_retries: int = MAX_RETRIES,
        sleep: Any = None,
    ) -> None:
        if not api_key:
            raise ValueError("Instabox API key is required.")
        self.api_key = api_key
        self.api_url = api_url.rstrip("/")
        self.max_retries = max(0, max_retries)
        self._sleep = sleep or asyncio.sleep
        self._external_client = client is not None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=min(10.0, timeout)),
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=64),
        )

    async def close(self) -> None:
        if not self._external_client:
            await self._client.aclose()

    # ------------------------------------------------------------------ #
    #  Transport
    # ------------------------------------------------------------------ #

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {"X-API-Key": self.api_key, "Accept": "application/json"}
        if extra:
            headers.update(extra)
        return headers

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | list[tuple[str, Any]] | None = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
        idempotency_key: str | None = None,
        retry: bool | None = None,
        timeout: float | None = None,
        expect_json: bool = True,
    ) -> Any:
        """Send a request and decode JSON.

        Only GET requests and requests carrying an ``Idempotency-Key`` are
        retried. Commands are never retried because the first attempt may
        already have run.
        """
        extra = dict(headers or {})
        if idempotency_key:
            extra["Idempotency-Key"] = idempotency_key
        if content is not None:
            extra.setdefault("Content-Type", "application/octet-stream")
        can_retry = retry if retry is not None else (method.upper() == "GET" or bool(idempotency_key))
        attempts = (self.max_retries + 1) if can_retry else 1
        url = f"{self.api_url}{path}"
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                response = await self._client.request(
                    method,
                    url,
                    json=json,
                    params=params,
                    content=content,
                    headers=self._headers(extra),
                    timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT,
                )
            except httpx.TransportError as exc:
                last_error = exc
                if attempt + 1 >= attempts:
                    raise
                await self._sleep(min(8.0, 0.5 * (2 ** attempt)))
                continue
            if response.status_code < 400:
                if not expect_json:
                    return response
                if response.status_code == 204 or not response.content:
                    return None
                try:
                    return response.json()
                except ValueError:
                    return response.text
            error = _parse_error(response)
            last_error = error
            if not error.retryable or attempt + 1 >= attempts:
                raise error
            delay = error.retry_after if error.retry_after is not None else 0.5 * (2 ** attempt)
            logger.debug(
                "Retrying Instabox %s %s after %s (%s)", method, path, delay, error.code
            )
            await self._sleep(min(30.0, max(0.0, delay)))
        assert last_error is not None
        raise last_error

    # ------------------------------------------------------------------ #
    #  Machines
    # ------------------------------------------------------------------ #

    async def list_machines(
        self,
        *,
        limit: int | None = None,
        cursor: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        params: list[tuple[str, Any]] = []
        if limit:
            params.append(("limit", int(limit)))
        if cursor:
            params.append(("cursor", cursor))
        for key, value in (metadata or {}).items():
            params.append(("metadata", f"{key}:{value}"))
        payload = await self.request("GET", "/v1/machines", params=params or None)
        return payload if isinstance(payload, dict) else {"machines": payload or []}

    async def iter_machines(
        self, *, metadata: dict[str, str] | None = None, page_size: int = 100
    ) -> AsyncIterator[dict[str, Any]]:
        cursor: str | None = None
        while True:
            page = await self.list_machines(limit=page_size, cursor=cursor, metadata=metadata)
            for machine in page.get("machines") or []:
                yield machine
            cursor = page.get("nextCursor")
            if not cursor:
                return

    async def get_machine(self, machine_id: str) -> dict[str, Any]:
        return await self.request("GET", f"/v1/machines/{_quote(machine_id)}")

    async def create_machine(self, body: dict[str, Any], *, idempotency_key: str) -> dict[str, Any]:
        return await self.request("POST", "/v1/machines", json=body, idempotency_key=idempotency_key)

    async def machine_action(
        self,
        machine_id: str,
        action: str,
        *,
        body: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any] | None:
        if action not in {"start", "stop", "suspend", "resume", "wake", "fork", "restore"}:
            raise ValueError(f"Unsupported Instabox machine action: {action}")
        key = idempotency_key if action != "wake" else None
        return await self.request(
            "POST",
            f"/v1/machines/{_quote(machine_id)}/{action}",
            json=body if body is not None else ({} if action != "wake" else None),
            idempotency_key=key,
        )

    async def delete_machine(
        self, machine_id: str, *, confirm: str, idempotency_key: str
    ) -> dict[str, Any] | None:
        return await self.request(
            "DELETE",
            f"/v1/machines/{_quote(machine_id)}",
            headers={"X-Confirm-Delete": confirm},
            idempotency_key=idempotency_key,
        )

    async def get_operation(self, operation_id: str, *, wait_seconds: int = 0) -> dict[str, Any]:
        params = {"wait": f"{min(MAX_OPERATION_WAIT_SECONDS, max(0, int(wait_seconds)))}s"} if wait_seconds else None
        return await self.request(
            "GET",
            f"/v1/operations/{_quote(operation_id)}",
            params=params,
            timeout=float(MAX_OPERATION_WAIT_SECONDS + 15) if wait_seconds else None,
        )

    async def wait_operation(
        self, operation_id: str, *, timeout: float = 900.0
    ) -> dict[str, Any]:
        deadline = time.monotonic() + max(1.0, timeout)
        while True:
            remaining = deadline - time.monotonic()
            operation = await self.get_operation(
                operation_id, wait_seconds=int(min(MAX_OPERATION_WAIT_SECONDS, max(1, remaining)))
            )
            if str(operation.get("state") or "") in TERMINAL_OPERATION_STATES:
                return operation
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Instabox operation {operation_id} did not finish in time.")

    # ------------------------------------------------------------------ #
    #  Commands and files
    # ------------------------------------------------------------------ #

    async def exec(
        self,
        machine_id: str,
        *,
        cmd: str | None = None,
        argv: list[str] | None = None,
        cwd: str | None = None,
        envs: dict[str, str] | None = None,
        stdin: str | None = None,
        timeout_seconds: int = 60,
    ) -> dict[str, Any]:
        if (cmd is None) == (argv is None):
            raise ValueError("Set exactly one of cmd or argv.")
        timeout_seconds = max(1, min(MAX_EXEC_TIMEOUT_SECONDS, int(timeout_seconds)))
        body: dict[str, Any] = {"timeoutSeconds": timeout_seconds}
        if cmd is not None:
            body["cmd"] = cmd
        else:
            body["argv"] = argv
        if cwd:
            body["cwd"] = cwd
        if envs:
            body["envs"] = envs
        if stdin is not None:
            body["stdin"] = stdin
        return await self.request(
            "POST",
            f"/v1/machines/{_quote(machine_id)}/exec",
            json=body,
            retry=False,
            timeout=float(timeout_seconds + 30),
        )

    async def stat_path(self, machine_id: str, path: str) -> dict[str, Any]:
        return await self.request(
            "GET", f"/v1/machines/{_quote(machine_id)}/files", params={"path": path}
        )

    async def read_file(self, machine_id: str, path: str) -> bytes:
        response = await self.request(
            "GET",
            f"/v1/machines/{_quote(machine_id)}/files/content",
            params={"path": path},
            expect_json=False,
            timeout=300.0,
        )
        return bytes(response.content)

    async def write_file(
        self, machine_id: str, path: str, content: bytes, *, mode: str | None = None
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"path": path, "parents": "true"}
        if mode:
            params["mode"] = mode
        # A PUT with the same bytes is safe to repeat.
        return await self.request(
            "PUT",
            f"/v1/machines/{_quote(machine_id)}/files",
            params=params,
            content=content,
            retry=True,
            timeout=300.0,
        )

    # ------------------------------------------------------------------ #
    #  Ports
    # ------------------------------------------------------------------ #

    async def list_ports(self, machine_id: str) -> dict[str, Any]:
        payload = await self.request("GET", f"/v1/machines/{_quote(machine_id)}/ports")
        return payload if isinstance(payload, dict) else {"ports": payload or []}

    async def publish_port(
        self,
        machine_id: str,
        port: int,
        *,
        access: str = "private",
        name: str | None = None,
        rotate_token: bool = False,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"port": int(port), "access": access, "protocol": "http"}
        if name:
            body["name"] = name
        if rotate_token:
            body["rotateToken"] = True
        # Republishing keeps the same URL, so a retry is harmless unless it rotates.
        return await self.request(
            "POST",
            f"/v1/machines/{_quote(machine_id)}/ports",
            json=body,
            retry=not rotate_token,
        )

    async def delete_port(self, machine_id: str, port: int) -> None:
        await self.request(
            "DELETE", f"/v1/machines/{_quote(machine_id)}/ports/{int(port)}", retry=True
        )

    # ------------------------------------------------------------------ #
    #  Snapshots
    # ------------------------------------------------------------------ #

    async def create_snapshot(
        self,
        machine_id: str,
        *,
        name: str,
        description: str | None = None,
        root_only: bool = False,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"name": name}
        if description:
            body["description"] = description
        if root_only:
            body["rootOnly"] = True
        return await self.request(
            "POST",
            f"/v1/machines/{_quote(machine_id)}/snapshots",
            json=body,
            idempotency_key=idempotency_key,
        )

    async def list_snapshots(self) -> list[dict[str, Any]]:
        payload = await self.request("GET", "/v1/snapshots")
        if isinstance(payload, dict):
            return list(payload.get("snapshots") or payload.get("images") or [])
        return list(payload or [])

    async def get_snapshot(self, snapshot_id: str) -> dict[str, Any]:
        return await self.request("GET", f"/v1/snapshots/{_quote(snapshot_id)}")

    async def delete_snapshot(self, snapshot_id: str, *, confirm: str, idempotency_key: str) -> None:
        await self.request(
            "DELETE",
            f"/v1/snapshots/{_quote(snapshot_id)}",
            headers={"X-Confirm-Delete": confirm},
            idempotency_key=idempotency_key,
        )

    # ------------------------------------------------------------------ #
    #  Desktop, leases and account
    # ------------------------------------------------------------------ #

    async def desktop(self, machine_id: str, action: str = "status", **fields: Any) -> dict[str, Any]:
        body: dict[str, Any] = {"action": action}
        body.update({key: value for key, value in fields.items() if value is not None})
        return await self.request(
            "POST",
            f"/v1/machines/{_quote(machine_id)}/desktop",
            json=body,
            retry=action in {"status", "takeover", "handback"},
            timeout=120.0,
        )

    def vnc_url(self, machine_id: str) -> str:
        base = self.api_url
        if base.startswith("https://"):
            base = "wss://" + base[len("https://"):]
        elif base.startswith("http://"):
            base = "ws://" + base[len("http://"):]
        return f"{base}/v1/machines/{_quote(machine_id)}/vnc"

    def vnc_headers(self) -> dict[str, str]:
        return {"X-API-Key": self.api_key}

    async def put_activity_lease(self, machine_id: str, lease_id: str, *, ttl_seconds: int = 300) -> None:
        await self.request(
            "PUT",
            f"/v1/machines/{_quote(machine_id)}/activity-leases/{_quote(lease_id)}",
            json={"ttlSeconds": max(30, min(3600, int(ttl_seconds)))},
            retry=True,
        )

    async def delete_activity_lease(self, machine_id: str, lease_id: str) -> None:
        await self.request(
            "DELETE",
            f"/v1/machines/{_quote(machine_id)}/activity-leases/{_quote(lease_id)}",
            retry=True,
        )

    async def list_templates(self) -> list[dict[str, Any]]:
        payload = await self.request("GET", "/v1/templates")
        if isinstance(payload, dict):
            return list(payload.get("templates") or [])
        return list(payload or [])

    async def me(self) -> dict[str, Any]:
        return await self.request("GET", "/v1/me")


__all__ = [
    "InstaboxClient",
    "MAX_EXEC_TIMEOUT_SECONDS",
    "TERMINAL_OPERATION_STATES",
    "new_idempotency_key",
    "redact",
]

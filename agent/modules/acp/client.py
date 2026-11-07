"""ACP client: protocol methods and per-session routing over one agent process.

Payloads stay plain dicts instead of strict models. Real agents send values
outside the published schema, for example a non-spec ``stopReason``, and this
client must tolerate them instead of failing a finished turn.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future
from typing import Any, Protocol

from agent.modules.acp.errors import METHOD_NOT_FOUND_CODE
from agent.modules.acp.jsonrpc import JsonRpcConnection
from agent.modules.acp.process import AgentCommand, AgentProcess

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = 1
CANCELLED_OUTCOME = {"outcome": {"outcome": "cancelled"}}
CLIENT_INFO = {"name": "k41-agent", "title": "K41 Agent", "version": "1"}


class SessionListener(Protocol):
    """Receives one session's traffic on the reader thread; must not block."""

    def on_update(self, update: dict[str, Any]) -> None: ...

    def on_permission_request(self, rpc_id: int | str, params: dict[str, Any]) -> None: ...


class AcpClient:
    """One initialized agent process. Sessions share it and are routed by id."""

    def __init__(
        self,
        command: AgentCommand,
        *,
        on_closed: Callable[[], None] | None = None,
    ) -> None:
        self.process = AgentProcess(command)
        # Sessions this process knows about; they end with it.
        self.live_sessions: set[str] = set()
        self._listeners: dict[str, SessionListener] = {}
        self._lock = threading.Lock()
        self.initialize_result: dict[str, Any] = {}
        self.connection = JsonRpcConnection(
            self.process.stdout,
            self.process.stdin,
            on_notification=self._on_notification,
            on_request=self._on_request,
            on_close=on_closed,
        )
        self.connection.start()

    @property
    def closed(self) -> bool:
        return self.connection.closed

    @property
    def agent_capabilities(self) -> dict[str, Any]:
        capabilities = self.initialize_result.get("agentCapabilities")
        return capabilities if isinstance(capabilities, dict) else {}

    @property
    def auth_methods(self) -> list[dict[str, Any]]:
        methods = self.initialize_result.get("authMethods")
        return [method for method in methods if isinstance(method, dict)] if isinstance(methods, list) else []

    def supports_resume(self) -> bool:
        capabilities = self.agent_capabilities.get("sessionCapabilities")
        return isinstance(capabilities, dict) and capabilities.get("resume") is not None

    def supports_load(self) -> bool:
        return bool(self.agent_capabilities.get("loadSession"))

    def supports_image(self) -> bool:
        capabilities = self.agent_capabilities.get("promptCapabilities")
        return isinstance(capabilities, dict) and bool(capabilities.get("image"))

    def supports_system_prompt_append(self) -> bool:
        meta = self.agent_capabilities.get("_meta")
        vendor = meta.get("antigravity") if isinstance(meta, dict) else None
        system_prompt = vendor.get("systemPrompt") if isinstance(vendor, dict) else None
        return isinstance(system_prompt, dict) and bool(system_prompt.get("append"))

    def supports_http_mcp(self) -> bool:
        capabilities = self.agent_capabilities.get("mcpCapabilities")
        return isinstance(capabilities, dict) and bool(capabilities.get("http"))

    def supports_logout(self) -> bool:
        capabilities = self.agent_capabilities.get("auth")
        return isinstance(capabilities, dict) and capabilities.get("logout") is not None

    async def _call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        result = await asyncio.wrap_future(self.connection.request(method, params))
        return result if isinstance(result, dict) else {}

    async def initialize(self) -> dict[str, Any]:
        # No fs or terminal capability: the agent uses its own tools and the
        # client never reads or writes files on its behalf.
        self.initialize_result = await self._call("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "clientCapabilities": {},
            "clientInfo": CLIENT_INFO,
        })
        return self.initialize_result

    async def authenticate(self, method_id: str) -> dict[str, Any]:
        return await self._call("authenticate", {"methodId": method_id})

    async def logout(self) -> dict[str, Any]:
        return await self._call("logout", {})

    async def new_session(
        self,
        cwd: str,
        *,
        meta: dict[str, Any] | None = None,
        mcp_servers: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"cwd": cwd, "mcpServers": mcp_servers or []}
        if meta:
            params["_meta"] = meta
        return await self._call("session/new", params)

    async def resume_session(self, session_id: str, cwd: str, *, mcp_servers: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        return await self._call("session/resume", {"sessionId": session_id, "cwd": cwd, "mcpServers": mcp_servers or []})

    async def load_session(self, session_id: str, cwd: str, *, mcp_servers: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        return await self._call("session/load", {"sessionId": session_id, "cwd": cwd, "mcpServers": mcp_servers or []})

    async def set_config_option(self, session_id: str, config_id: str, value: str) -> dict[str, Any]:
        return await self._call("session/set_config_option", {
            "sessionId": session_id, "configId": config_id, "value": value,
        })

    def prompt(self, session_id: str, blocks: list[dict[str, Any]]) -> Future[Any]:
        return self.connection.request("session/prompt", {"sessionId": session_id, "prompt": blocks})

    def cancel(self, session_id: str) -> None:
        self.connection.notify("session/cancel", {"sessionId": session_id})

    def respond_permission(self, rpc_id: int | str, outcome: dict[str, Any]) -> None:
        self.connection.respond(rpc_id, outcome)

    def select_option(self, rpc_id: int | str, option_id: str) -> None:
        self.respond_permission(rpc_id, {"outcome": {"outcome": "selected", "optionId": option_id}})

    def subscribe(self, session_id: str, listener: SessionListener) -> None:
        with self._lock:
            self._listeners[session_id] = listener

    def unsubscribe(self, session_id: str, listener: SessionListener) -> None:
        with self._lock:
            if self._listeners.get(session_id) is listener:
                del self._listeners[session_id]

    def stop(self) -> None:
        """Blocking; call from a worker thread."""
        self.process.stop()
        self.connection.close()

    def _listener(self, session_id: Any) -> SessionListener | None:
        with self._lock:
            return self._listeners.get(session_id)

    def _on_notification(self, method: str, params: dict[str, Any]) -> None:
        if method != "session/update":
            return
        listener = self._listener(params.get("sessionId"))
        update = params.get("update")
        if listener is not None and isinstance(update, dict):
            listener.on_update(update)

    def _on_request(self, rpc_id: int | str, method: str, params: dict[str, Any]) -> None:
        if method != "session/request_permission":
            self.connection.respond_error(rpc_id, METHOD_NOT_FOUND_CODE, "Method not found")
            return
        listener = self._listener(params.get("sessionId"))
        if listener is None:
            # No turn owns this session, so nothing may be approved on its behalf.
            self.respond_permission(rpc_id, CANCELLED_OUTCOME)
            return
        listener.on_permission_request(rpc_id, params)

"""Serve K41 tools to ACP agents through a loopback MCP endpoint.

An agent runs its own loop and only reaches client tools through the ``mcpServers``
list it receives on ``session/new`` and ``session/resume``. Each attached session
gets its own bearer token, so a token only ever reaches that session's tools.

K41 is the approval authority for bridged tools, including calls made without a
provider permission request. ACP allow_once responses correlate tool results with
transcript call IDs; they do not grant permission to execute a K41 operation.
The shared tool node enforces policy before any side effect.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
import logging
import secrets
import socket
import threading
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

import uvicorn
from mcp import types as mcp_types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from uvicorn.protocols.http.h11_impl import H11Protocol

from agent.modules.acp.errors import AcpError
from agent.modules.acp.translator import mcp_call_arguments

if TYPE_CHECKING:
    from agent.modules.acp.turns import AcpTurn

logger = logging.getLogger(__name__)

BRIDGE_SERVER_NAME = "k41"
_ENDPOINT_PATH = "/mcp"
_GRACEFUL_SHUTDOWN_SECONDS = 1
_CLOSE_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class ToolOutput:
    """A tool result, independent of how it reaches the agent."""

    text: str
    images: tuple[tuple[str, str], ...] = ()  # (base64 data, mime type)
    is_error: bool = False


def tool_output(content: Any, *, is_error: bool = False) -> ToolOutput:
    """Convert LangChain tool message content."""
    if isinstance(content, str):
        return ToolOutput(content, is_error=is_error)
    texts: list[str] = []
    images: list[tuple[str, str]] = []
    for block in content if isinstance(content, list) else []:
        if isinstance(block, str):
            texts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            texts.append(str(block.get("text") or ""))
        elif isinstance(block, dict) and block.get("type") == "image" and block.get("base64"):
            images.append((str(block["base64"]), str(block.get("mime_type") or "image/png")))
    return ToolOutput("\n".join(texts), tuple(images), is_error)


class ToolProvider(Protocol):
    """The tools one turn may use. Implemented by the graph, which owns the run context."""

    async def tool_names(self) -> frozenset[str]: ...

    async def list_tools(self) -> list[dict[str, Any]]: ...

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolOutput: ...


def bridged_tool_name(tool_call: dict[str, Any]) -> str | None:
    """Return the K41 tool an ACP tool call targets, if it targets one."""
    meta = tool_call.get("_meta")
    mcp = meta.get("mcp") if isinstance(meta, dict) else None
    if isinstance(mcp, dict) and mcp.get("server") == BRIDGE_SERVER_NAME and mcp.get("tool"):
        return str(mcp["tool"])
    return None


def _canonical(arguments: Any) -> str:
    return json.dumps(arguments, sort_keys=True, ensure_ascii=False, default=str)


def _error_result(text: str) -> mcp_types.CallToolResult:
    return mcp_types.CallToolResult(content=[mcp_types.TextContent(type="text", text=text)], isError=True)


def _call_result(output: ToolOutput) -> mcp_types.CallToolResult:
    content: list[Any] = [mcp_types.TextContent(type="text", text=output.text)] if output.text or not output.images else []
    content.extend(mcp_types.ImageContent(type="image", data=data, mimeType=mime) for data, mime in output.images)
    return mcp_types.CallToolResult(content=content, isError=output.is_error)


class SessionTools:
    """Tool access of one attached agent session. Fed by the reader thread and the event loop."""

    def __init__(self, names: frozenset[str], provider: ToolProvider | None = None, port: int = 0) -> None:
        self.token = secrets.token_urlsafe(32)
        self.names = names
        self.port = port
        self.turn: AcpTurn | None = None
        # Set before the session opens, because an agent may connect while it does.
        self.provider = provider
        self._lock = threading.Lock()
        self._approvals: list[tuple[str, str, str]] = []
        self._running: set[asyncio.Task[Any]] = set()

    def begin_turn(self, turn: AcpTurn, provider: ToolProvider | None) -> None:
        with self._lock:
            self.turn = turn
            self.provider = provider
            self._approvals.clear()

    def end_turn(self, turn: AcpTurn) -> None:
        """Release what a finished turn held. Until the next turn the token reaches no tools."""
        with self._lock:
            if self.turn is turn:
                self.turn = None
                self.provider = None
                self._approvals.clear()

    def approve(self, tool_call: dict[str, Any], tool: str) -> None:
        # rawInput is optional in ACP, while an MCP call always carries an arguments object.
        arguments = mcp_call_arguments(tool_call)
        entry = (str(tool_call.get("toolCallId") or ""), tool, _canonical({} if arguments is None else arguments))
        with self._lock:
            self._approvals.append(entry)

    def take_approval(self, tool: str, arguments: dict[str, Any]) -> str | None:
        """Consume the approval for this exact call and return its ACP tool call id."""
        wanted = _canonical(arguments)
        with self._lock:
            for entry in self._approvals:
                if entry[1] == tool and entry[2] == wanted:
                    self._approvals.remove(entry)
                    return entry[0]
        return None

    def track(self, task: asyncio.Task[Any]) -> None:
        with self._lock:
            self._running.add(task)
        task.add_done_callback(self._untrack)

    def _untrack(self, task: asyncio.Task[Any]) -> None:
        with self._lock:
            self._running.discard(task)

    def cancel_running(self) -> None:
        with self._lock:
            running = list(self._running)
        for task in running:
            try:
                task.get_loop().call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass


class _EmbeddedServer(uvicorn.Server):
    """Runs inside the host process, whose own server owns signal handling."""

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


class _EmbeddedConfig(uvicorn.Config):
    """Leaves logging alone, because uvicorn's loggers are shared with the host's server."""

    def configure_logging(self) -> None:
        pass


class _UnloggedProtocol(H11Protocol):
    """Keeps bridge requests out of the host's access log."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.access_log = False


class ToolBridge:
    """A loopback HTTP listener shared by every session of one service."""

    def __init__(self) -> None:
        self._sessions: dict[str, SessionTools] = {}
        self._server: _EmbeddedServer | None = None
        self._task: asyncio.Task[None] | None = None
        self._port = 0

    # Sessions ----------------------------------------------------------------

    def open_session(self, names: frozenset[str], provider: ToolProvider | None = None) -> SessionTools:
        session = SessionTools(names, provider, self._port)
        self._sessions[session.token] = session
        return session

    def revoke(self, session: SessionTools) -> None:
        self._sessions.pop(session.token, None)
        session.cancel_running()

    def is_reachable(self, session: SessionTools) -> bool:
        """Whether the URL given to a session still works. A restarted listener has a new port."""
        return session.port == self._port

    def server_entry(self, session: SessionTools) -> dict[str, Any]:
        """The ACP ``McpServer`` value for a session."""
        return {
            "type": "http",
            "name": BRIDGE_SERVER_NAME,
            "url": f"http://127.0.0.1:{session.port}{_ENDPOINT_PATH}",
            "headers": [{"name": "Authorization", "value": f"Bearer {session.token}"}],
        }

    def _session_for(self, authorization: str | None) -> SessionTools | None:
        scheme, _, token = (authorization or "").partition(" ")
        return self._sessions.get(token) if scheme.lower() == "bearer" else None

    # Requests ----------------------------------------------------------------

    def _build_mcp_server(self) -> Server:
        server: Server = Server("k41-agent")

        def request_session() -> SessionTools | None:
            request = server.request_context.request
            return self._session_for(request.headers.get("authorization")) if request is not None else None

        @server.list_tools()
        async def list_tools() -> list[mcp_types.Tool]:
            session = request_session()
            if session is None or session.provider is None:
                return []
            return [mcp_types.Tool(**tool) for tool in await session.provider.list_tools()]

        @server.call_tool(validate_input=False)
        async def call_tool(name: str, arguments: dict[str, Any]) -> mcp_types.CallToolResult:
            session = request_session()
            if session is None:
                return _error_result("This tool session has ended.")
            return await self._call(session, name, arguments)

        return server

    @staticmethod
    async def _call(session: SessionTools, name: str, arguments: dict[str, Any]) -> mcp_types.CallToolResult:
        turn, provider = session.turn, session.provider
        if turn is None or provider is None or turn.done or turn.cancel_requested:
            return _error_result("No agent turn is running, so this tool call was refused.")
        tool_call_id = session.take_approval(name, arguments)
        task = asyncio.ensure_future(provider.call_tool(name, arguments))
        session.track(task)
        try:
            output = await task
        except asyncio.CancelledError:
            if asyncio.current_task().cancelling():
                raise
            # Stopped by the turn ending; this request itself is still alive.
            output = ToolOutput("The tool call was cancelled.", is_error=True)
        except Exception as exc:
            logger.exception("K41 tool %s failed for an ACP agent.", name)
            output = ToolOutput(f"The tool failed: {exc}", is_error=True)
        if tool_call_id:
            turn.record_tool_output(tool_call_id, output.text)
        return _call_result(output)

    def _build_app(self) -> Starlette:
        manager = StreamableHTTPSessionManager(app=self._build_mcp_server(), json_response=True, stateless=True)
        bridge = self

        class Endpoint:
            # A class, because Starlette wraps plain functions as request handlers.
            async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
                if bridge._session_for(Request(scope).headers.get("authorization")) is None:
                    await PlainTextResponse("Unauthorized", status_code=401)(scope, receive, send)
                    return
                await manager.handle_request(scope, receive, send)

        @contextlib.asynccontextmanager
        async def lifespan(_app: Starlette) -> AsyncIterator[None]:
            async with manager.run():
                yield

        # No GET route: the bridge sends no server-initiated messages, and MCP lets
        # a server answer 405 instead of opening that stream.
        return Starlette(routes=[Route(_ENDPOINT_PATH, Endpoint(), methods=["POST"])], lifespan=lifespan)

    # Lifetime ----------------------------------------------------------------

    def _launch(self) -> None:
        from agent.bootstrap.container import activate_context_container, get_active_container

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        self._port = listener.getsockname()[1]
        self._server = _EmbeddedServer(_EmbeddedConfig(
            self._build_app(),
            http=_UnloggedProtocol,
            ws="none",
            server_header=False,
            timeout_graceful_shutdown=_GRACEFUL_SHUTDOWN_SECONDS,
        ))
        # Started from a graph node, whose context holds that run's LangGraph config.
        # The listener outlives the node and tools must not see it, so the context
        # starts empty apart from the container.
        context = contextvars.Context()
        context.run(activate_context_container, get_active_container())
        self._task = asyncio.get_running_loop().create_task(self._server.serve(sockets=[listener]), context=context)

    async def ensure_started(self) -> None:
        if self._task is None or self._task.done():
            self._launch()
        while not self._server.started:
            if self._task.done():
                raise AcpError("The local tool endpoint for ACP agents could not start.")
            await asyncio.sleep(0.01)

    async def close(self) -> None:
        for session in list(self._sessions.values()):
            self.revoke(session)
        task, server = self._task, self._server
        self._task = self._server = None
        if task is None or server is None or task.done():
            return
        server.should_exit = True
        try:
            await asyncio.wait_for(task, _CLOSE_TIMEOUT_SECONDS)
        except (TimeoutError, asyncio.CancelledError):
            logger.warning("The ACP tool endpoint did not stop in time.")

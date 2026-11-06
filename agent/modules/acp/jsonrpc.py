"""JSON-RPC 2.0 over newline-delimited JSON, driven by threads.

The dashboard server runs on a selector event loop on Windows, where asyncio
subprocess pipes are unavailable. Reader and writer threads keep this usable on
every event loop, and keep a full stdin pipe from ever blocking the loop.

Message payloads can hold prompts, file contents and credentials, so nothing in
this module logs them.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
from collections.abc import Callable
from concurrent.futures import Future
from typing import Any, BinaryIO

from agent.modules.acp.errors import AcpConnectionClosed, AcpRpcError

logger = logging.getLogger(__name__)

MAX_LINE_BYTES = 256 * 1024 * 1024

NotificationHandler = Callable[[str, dict[str, Any]], None]
RequestHandler = Callable[[int | str, str, dict[str, Any]], None]
CloseHandler = Callable[[], None]


class JsonRpcConnection:
    """One peer connection. Callbacks run on the reader thread and must not block."""

    def __init__(
        self,
        reader: BinaryIO,
        writer: BinaryIO,
        *,
        on_notification: NotificationHandler,
        on_request: RequestHandler,
        on_close: CloseHandler | None = None,
        name: str = "acp",
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._on_notification = on_notification
        self._on_request = on_request
        self._on_close = on_close
        self._lock = threading.Lock()
        self._next_id = 0
        self._pending: dict[int, Future[Any]] = {}
        self._closed = False
        self._outgoing: queue.SimpleQueue[bytes | None] = queue.SimpleQueue()
        self._read_thread = threading.Thread(target=self._read_loop, name=f"{name}-rpc-read", daemon=True)
        self._write_thread = threading.Thread(target=self._write_loop, name=f"{name}-rpc-write", daemon=True)

    def start(self) -> None:
        self._read_thread.start()
        self._write_thread.start()

    @property
    def closed(self) -> bool:
        return self._closed

    def request(self, method: str, params: dict[str, Any]) -> Future[Any]:
        """Send a request. The future resolves with its result or an AcpError."""
        future: Future[Any] = Future()
        with self._lock:
            if self._closed:
                future.set_exception(AcpConnectionClosed("The agent connection is closed."))
                return future
            self._next_id += 1
            request_id = self._next_id
            self._pending[request_id] = future
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        return future

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def respond(self, request_id: int | str, result: Any) -> None:
        self._send({"jsonrpc": "2.0", "id": request_id, "result": result})

    def respond_error(self, request_id: int | str, code: int, message: str) -> None:
        self._send({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})

    def close(self) -> None:
        """Fail outstanding requests and stop the writer. Safe to call repeatedly."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            pending = list(self._pending.values())
            self._pending.clear()
        self._outgoing.put(None)
        for future in pending:
            if not future.done():
                future.set_exception(AcpConnectionClosed("The agent connection closed."))
        if self._on_close:
            try:
                self._on_close()
            except Exception:
                logger.exception("ACP close handler failed.")

    def _send(self, message: dict[str, Any]) -> None:
        if not self._closed:
            self._outgoing.put(json.dumps(message, ensure_ascii=False).encode("utf-8") + b"\n")

    def _write_loop(self) -> None:
        while (payload := self._outgoing.get()) is not None:
            try:
                self._writer.write(payload)
                self._writer.flush()
            except (OSError, ValueError):
                self.close()
                return

    def _read_loop(self) -> None:
        try:
            while line := self._reader.readline(MAX_LINE_BYTES + 1):
                if len(line) > MAX_LINE_BYTES:
                    logger.warning("ACP agent sent an oversized message; closing the connection.")
                    return
                if line.strip():
                    self._dispatch(line)
        except (OSError, ValueError):
            logger.debug("ACP reader stopped.", exc_info=True)
        finally:
            self.close()

    def _dispatch(self, line: bytes) -> None:
        try:
            message = json.loads(line)
        except ValueError:
            # Agents and their helpers may write stray text to stdout.
            logger.debug("Ignoring a non-JSON line from the ACP agent (%d bytes).", len(line))
            return
        if not isinstance(message, dict):
            return
        method = message.get("method")
        try:
            if isinstance(method, str):
                params = message.get("params")
                params = params if isinstance(params, dict) else {}
                if "id" in message:
                    self._on_request(message["id"], method, params)
                else:
                    self._on_notification(method, params)
            else:
                self._complete(message)
        except Exception:
            logger.exception("ACP message handler failed for method %r.", method)

    def _complete(self, message: dict[str, Any]) -> None:
        with self._lock:
            future = self._pending.pop(message.get("id"), None) if isinstance(message.get("id"), int) else None
        if future is None or future.done():
            return
        error = message.get("error")
        if isinstance(error, dict):
            future.set_exception(AcpRpcError(
                int(error.get("code", 0) or 0), str(error.get("message") or "ACP request failed."), error.get("data"),
            ))
        else:
            future.set_result(message.get("result"))

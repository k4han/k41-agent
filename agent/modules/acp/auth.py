"""Sign-in for ACP agents, including a relay for dashboards on another machine.

agy_acp_server prints its OAuth link to stderr and listens on a random loopback
port. When K41 runs elsewhere, the user's browser cannot reach that port, so the
user pastes the final redirect URL and K41 replays it locally.

The listener answers 200 to any callback and then discards stored credentials if
the callback was wrong, so every check here happens before anything is forwarded.
Callback URLs hold an authorization code: they are never logged or stored.
"""

from __future__ import annotations

import asyncio
import hmac
import ipaddress
import logging
import re
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit

from agent.modules.acp.agents import get_agent_definition
from agent.modules.acp.errors import AcpError, AcpNotInstalled, AcpRpcError
from agent.modules.acp.installer import delete_api_key, is_installed, read_api_key, write_api_key
from agent.modules.acp.service import AcpService

logger = logging.getLogger(__name__)

LINK_PATTERN = re.compile(r"https://\S+")
SERVER_SIGN_IN_TIMEOUT_SECONDS = 300
PROBE_TTL_SECONDS = 300.0
_LINK_WAIT_SECONDS = 15.0
_COMPLETE_WAIT_SECONDS = 20.0


class AcpAuthError(AcpError):
    """A sign-in request was refused; the message is safe to show."""


class CallbackRejected(AcpAuthError):
    """A pasted callback failed validation. Nothing was forwarded."""


@dataclass
class SignInAttempt:
    id: str
    agent_id: str
    method_id: str
    started_at: float = field(default_factory=time.time)
    status: str = "pending"
    error: str = ""
    authorization_url: str = ""
    redirect_port: int = 0
    redirect_path: str = "/"
    state: str = ""
    task: asyncio.Task | None = None
    link_ready: asyncio.Event = field(default_factory=asyncio.Event)
    forwarded: bool = False

    def public(self) -> dict[str, Any]:
        return {
            "attempt_id": self.id,
            "method_id": self.method_id,
            "status": self.status,
            "error": self.error,
            "authorization_url": self.authorization_url if self.status == "pending" else "",
            "expires_at": self.started_at + SERVER_SIGN_IN_TIMEOUT_SECONDS,
        }


def parse_authorization_link(link: str, allowed_hosts: tuple[str, ...]) -> tuple[str, int, str, str] | None:
    """Return (url, redirect port, redirect path, state) for a link worth showing."""
    parts = urlsplit(link)
    if parts.scheme != "https" or parts.hostname not in allowed_hosts:
        return None
    query = parse_qs(parts.query)
    state = (query.get("state") or [""])[0]
    redirect = urlsplit((query.get("redirect_uri") or [""])[0])
    if not state or redirect.scheme != "http" or redirect.hostname not in {"127.0.0.1", "localhost"} or not redirect.port:
        return None
    return link, redirect.port, redirect.path or "/", state


def validate_callback(callback_url: str, *, port: int, path: str, state: str) -> str:
    """Return the only URL a callback may be forwarded to, or raise CallbackRejected."""
    try:
        parts = urlsplit(callback_url.strip())
        actual_port = parts.port
    except ValueError as exc:
        raise CallbackRejected("That is not a valid URL. Paste the full address from the browser.") from exc
    if parts.scheme != "http":
        raise CallbackRejected("The address must start with http://127.0.0.1.")
    if parts.username or parts.password:
        raise CallbackRejected("The address must not contain credentials.")
    hostname = parts.hostname or ""
    try:
        loopback = hostname == "localhost" or ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        loopback = False
    if not loopback:
        raise CallbackRejected("The address must point at 127.0.0.1.")
    if actual_port != port or (parts.path or "/") != path:
        raise CallbackRejected("This address belongs to a different sign-in attempt. Start again and paste its final address.")
    query = parse_qs(parts.query)
    if not hmac.compare_digest((query.get("state") or [""])[0], state):
        raise CallbackRejected("This address belongs to a different sign-in attempt. Start again and paste its final address.")
    if not query.get("code") and not query.get("error"):
        raise CallbackRejected("The address has no authorization code. Finish signing in, then paste the final address.")
    # Rebuilt from validated parts, so no other host can ever be contacted.
    return f"http://127.0.0.1:{port}{path}?{parts.query}"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def _forward_callback(url: str) -> None:
    # Bypasses any proxy: this request must only ever reach this machine.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(url, timeout=10) as response:
            response.read(1024)
    except urllib.error.HTTPError as exc:
        exc.close()


class AcpAuthManager:
    def __init__(self, service: AcpService, *, data_root=None) -> None:
        self.service = service
        self.data_root = data_root
        self._attempts: dict[str, SignInAttempt] = {}
        self._probes: dict[str, tuple[float, dict[str, Any]]] = {}

    def _definition(self, agent_id: str):
        definition = get_agent_definition(agent_id)
        if definition is None:
            raise AcpAuthError(f"Unknown ACP agent: {agent_id}.")
        return definition

    def _active_attempt(self, agent_id: str) -> SignInAttempt | None:
        attempt = self._attempts.get(agent_id)
        return attempt if attempt is not None and attempt.status == "pending" else None

    def attempt_status(self, agent_id: str) -> dict[str, Any] | None:
        attempt = self._attempts.get(agent_id)
        return attempt.public() if attempt is not None else None

    def invalidate(self, agent_id: str) -> None:
        self._probes.pop(agent_id, None)

    # Status -----------------------------------------------------------------

    async def status(self, agent_id: str, *, refresh: bool = False) -> dict[str, Any]:
        """Ask the agent itself: opening a session is the only reliable sign-in check."""
        self._definition(agent_id)
        if not is_installed(agent_id, self.data_root):
            return {"signed_in": False, "state": "not_installed", "methods": [], "config_options": []}
        cached = self._probes.get(agent_id)
        if cached is not None and not refresh and time.monotonic() - cached[0] < PROBE_TTL_SECONDS:
            return cached[1]
        result = await self._probe(agent_id)
        self._probes[agent_id] = (time.monotonic(), result)
        return result

    async def _probe(self, agent_id: str) -> dict[str, Any]:
        from agent.modules.acp.installer import agent_root

        self.service.hold(agent_id)
        try:
            client = await self.service.client_for(agent_id)
            methods = self._supported_methods(agent_id, client.auth_methods)
            cwd = agent_root(agent_id, self.data_root) / "work"
            cwd.mkdir(parents=True, exist_ok=True)
            try:
                session = await client.new_session(str(cwd))
            except AcpRpcError as exc:
                if exc.auth_required:
                    return {"signed_in": False, "state": "signed_out", "methods": methods, "config_options": []}
                return {"signed_in": False, "state": "error", "error": exc.message, "methods": methods, "config_options": []}
            meta = session.get("_meta") if isinstance(session.get("_meta"), dict) else {}
            tier = (meta.get("antigravity") or {}).get("tier") if isinstance(meta.get("antigravity"), dict) else None
            return {
                "signed_in": True,
                "state": "signed_in",
                "plan": str(tier.get("name") or "") if isinstance(tier, dict) else "",
                "uses_api_key": bool(read_api_key(agent_id, self.data_root)),
                "can_sign_out": client.supports_logout(),
                "methods": methods,
                "config_options": _public_config_options(session.get("configOptions")),
            }
        except AcpNotInstalled:
            return {"signed_in": False, "state": "not_installed", "methods": [], "config_options": []}
        except AcpError as exc:
            return {"signed_in": False, "state": "error", "error": str(exc), "methods": [], "config_options": []}
        finally:
            self.service.release(agent_id)

    def _supported_methods(self, agent_id: str, advertised: list[dict[str, Any]]) -> list[dict[str, str]]:
        supported = self._definition(agent_id).supported_auth_methods
        methods = {str(method.get("id")): method for method in advertised}
        return [
            {"id": method_id, "name": str(methods[method_id].get("name") or method_id),
             "description": str(methods[method_id].get("description") or "")}
            for method_id in supported if method_id in methods
        ]

    # Browser sign-in --------------------------------------------------------

    async def start_sign_in(self, agent_id: str, method_id: str) -> dict[str, Any]:
        definition = self._definition(agent_id)
        if method_id not in definition.supported_auth_methods or dict(definition.api_key_env_by_method).get(method_id):
            raise AcpAuthError("This sign-in method cannot be started from here.")
        if self._active_attempt(agent_id) is not None:
            raise AcpAuthError("A sign-in is already in progress. Finish or cancel it first.")
        self.service.hold(agent_id)
        try:
            client = await self.service.client_for(agent_id)
            attempt = SignInAttempt(id=uuid.uuid4().hex, agent_id=agent_id, method_id=method_id)
            self._attempts[agent_id] = attempt
            loop = asyncio.get_running_loop()
            allowed = definition.allowed_auth_hosts

            def on_stderr(line: str) -> None:
                match = LINK_PATTERN.search(line)
                parsed = parse_authorization_link(match.group(0), allowed) if match else None
                if parsed is not None and not attempt.authorization_url:
                    attempt.authorization_url, attempt.redirect_port, attempt.redirect_path, attempt.state = parsed
                    loop.call_soon_threadsafe(attempt.link_ready.set)

            client.process.on_stderr_line = on_stderr
            attempt.task = asyncio.create_task(self._finish_sign_in(attempt, client))
            done_waiter = asyncio.ensure_future(asyncio.shield(attempt.task))
            link_waiter = asyncio.ensure_future(attempt.link_ready.wait())
            try:
                await asyncio.wait({done_waiter, link_waiter}, timeout=_LINK_WAIT_SECONDS, return_when=asyncio.FIRST_COMPLETED)
            finally:
                done_waiter.cancel()
                link_waiter.cancel()
            return attempt.public()
        except BaseException:
            if self._active_attempt(agent_id) is None:
                self.service.release(agent_id)
            raise

    async def _finish_sign_in(self, attempt: SignInAttempt, client) -> None:
        try:
            await client.authenticate(attempt.method_id)
            attempt.status = "succeeded"
        except AcpRpcError as exc:
            attempt.status = "failed"
            attempt.error = exc.message
        except AcpError as exc:
            attempt.status = "failed" if attempt.status == "pending" else attempt.status
            attempt.error = attempt.error or str(exc)
        finally:
            client.process.on_stderr_line = None
            attempt.authorization_url = ""
            attempt.state = ""
            self.invalidate(attempt.agent_id)
            self.service.release(attempt.agent_id)

    async def complete_sign_in(self, agent_id: str, attempt_id: str, callback_url: str) -> dict[str, Any]:
        """Validate a pasted redirect URL, replay it locally, and wait for the result."""
        attempt = self._active_attempt(agent_id)
        if attempt is None or attempt.id != attempt_id or not attempt.state:
            raise CallbackRejected("This sign-in attempt has ended. Start again.")
        if attempt.forwarded:
            raise CallbackRejected("This sign-in attempt was already completed.")
        target = validate_callback(callback_url, port=attempt.redirect_port, path=attempt.redirect_path, state=attempt.state)
        attempt.forwarded = True
        try:
            await asyncio.to_thread(_forward_callback, target)
        except OSError as exc:
            logger.info("ACP sign-in callback could not be delivered: %s", type(exc).__name__)
            raise AcpAuthError("The sign-in listener could not be reached. Start again.") from None
        if attempt.task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(attempt.task), _COMPLETE_WAIT_SECONDS)
            except TimeoutError:
                pass
        return attempt.public()

    async def cancel_sign_in(self, agent_id: str, attempt_id: str) -> dict[str, Any]:
        """ACP cannot cancel authenticate, so ending the attempt means ending the process."""
        attempt = self._active_attempt(agent_id)
        if attempt is None or attempt.id != attempt_id:
            raise AcpAuthError("This sign-in attempt has ended.")
        attempt.status = "cancelled"
        await self.service.restart_client(agent_id)
        if attempt.task is not None:
            await asyncio.gather(attempt.task, return_exceptions=True)
        return attempt.public()

    # API key ----------------------------------------------------------------

    async def set_api_key(self, agent_id: str, api_key: str) -> dict[str, Any]:
        definition = self._definition(agent_id)
        method = next((method for method, _variable in definition.api_key_env_by_method), None)
        if method is None:
            raise AcpAuthError("This agent does not accept an API key.")
        self._assert_idle(agent_id)
        value = api_key.strip()
        if not value or len(value) > 4096 or any(character.isspace() for character in value):
            raise AcpAuthError("That API key does not look valid.")
        previous = read_api_key(agent_id, self.data_root)
        write_api_key(agent_id, value, self.data_root)
        # The agent reads the key from its environment, which is fixed at launch.
        await self.service.restart_client(agent_id)
        self.service.hold(agent_id)
        try:
            client = await self.service.client_for(agent_id)
            await client.authenticate(method)
        except AcpError as exc:
            if previous:
                write_api_key(agent_id, previous, self.data_root)
            else:
                delete_api_key(agent_id, self.data_root)
            await self.service.restart_client(agent_id)
            message = exc.message if isinstance(exc, AcpRpcError) else str(exc)
            raise AcpAuthError(f"The agent did not accept this API key: {message}") from None
        finally:
            self.service.release(agent_id)
            self.invalidate(agent_id)
        return await self.status(agent_id, refresh=True)

    async def sign_out(self, agent_id: str) -> dict[str, Any]:
        self._assert_idle(agent_id)
        self.service.hold(agent_id)
        try:
            client = await self.service.client_for(agent_id)
            if client.supports_logout():
                await client.logout()
        finally:
            self.service.release(agent_id)
        delete_api_key(agent_id, self.data_root)
        await self.service.restart_client(agent_id)
        self.invalidate(agent_id)
        return await self.status(agent_id, refresh=True)

    def _assert_idle(self, agent_id: str) -> None:
        if self._active_attempt(agent_id) is not None:
            raise AcpAuthError("A sign-in is in progress. Finish or cancel it first.")
        if self.service.agent_is_busy(agent_id):
            raise AcpAuthError("The agent is busy. Wait for running turns to finish.")


def _public_config_options(options: Any) -> list[dict[str, Any]]:
    """Selectable session settings. `mode` is excluded: permissions are K41's decision."""
    public = []
    for option in options if isinstance(options, list) else []:
        if not isinstance(option, dict) or option.get("type") != "select" or option.get("category") == "mode":
            continue
        public.append({
            "id": str(option.get("id") or ""),
            "name": str(option.get("name") or option.get("id") or ""),
            "category": str(option.get("category") or ""),
            "current_value": option.get("currentValue"),
            "options": [
                {"value": str(item.get("value")), "name": str(item.get("name") or item.get("value"))}
                for item in option.get("options") or [] if isinstance(item, dict)
            ],
        })
    return public

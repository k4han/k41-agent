"""Read CLI authentication status without managing shared credentials."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from agent.modules.acp.errors import AcpError, AcpNotInstalled, AcpRpcError
from agent.modules.acp.launcher import cli_details, runtime_key
from agent.modules.acp.service import AcpService

PROBE_TTL_SECONDS = 300.0


def auth_status(state: str, **details: Any) -> dict[str, Any]:
    """Build the read-only authentication status the dashboard reads."""
    return {"signed_in": state == "signed_in", "state": state, "config_options": [], **details}


class AcpAuthManager:
    def __init__(self, service: AcpService) -> None:
        self.service = service
        self._probes: dict[str, tuple[float, dict[str, Any]]] = {}

    def invalidate(self, agent_id: str) -> None:
        for key in list(self._probes):
            if key.startswith(f"{agent_id}:"):
                self._probes.pop(key, None)

    async def status(
        self, agent_id: str, *, refresh: bool = False, agent_name: str | None = None, cwd: str | None = None,
    ) -> dict[str, Any]:
        """Check sign-in; the retired agent_name argument is accepted for old clients."""
        key = runtime_key(agent_id, cwd)
        details = await asyncio.to_thread(cli_details, refresh=refresh)
        if not details["executable_path"]:
            return auth_status("not_installed")
        if details["error"]:
            return auth_status("error", error=details["error"])
        if refresh:
            self.invalidate(agent_id)
            # Running turns retain their processes until they finish.
            if not self.service.agent_is_busy(agent_id):
                await self.service.restart_client(agent_id)
        cached = self._probes.get(key)
        if cached is not None and not refresh and time.monotonic() - cached[0] < PROBE_TTL_SECONDS:
            return cached[1]
        result = await self._probe(agent_id, cwd=cwd)
        self._probes[key] = (time.monotonic(), result)
        return result

    async def _probe(self, agent_id: str, *, cwd: str | None = None) -> dict[str, Any]:
        key = runtime_key(agent_id, cwd)
        self.service.hold(key)
        try:
            client = await self.service.client_for(agent_id, cwd=cwd)
            try:
                session = await client.new_session(str(Path(cwd or Path.home()).resolve()))
            except AcpRpcError as exc:
                if exc.auth_required:
                    return auth_status("signed_out")
                return auth_status("error", error=exc.message)
            meta = session.get("_meta") if isinstance(session.get("_meta"), dict) else {}
            tier = (meta.get("antigravity") or {}).get("tier") if isinstance(meta.get("antigravity"), dict) else None
            return auth_status(
                "signed_in",
                plan=str(tier.get("name") or "") if isinstance(tier, dict) else "",
                config_options=_public_config_options(session.get("configOptions")),
            )
        except AcpNotInstalled:
            return auth_status("not_installed")
        except AcpError as exc:
            return auth_status("error", error=str(exc))
        finally:
            self.service.release(key)


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

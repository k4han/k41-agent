from __future__ import annotations

from dataclasses import asdict
from typing import Any

from agent.modules.mcp.install_repository import McpInstallRepository, normalize_server_name
from agent.modules.mcp.registry_client import McpRegistryClient, McpSearchResult


class McpInstallError(ValueError):
    pass


def _target_by_id(server: McpSearchResult, target_id: str):
    for target in server.install_targets:
        if target.id == target_id:
            return target
    raise McpInstallError(f"Install target not found: {target_id}")


def _default_target_id(server: McpSearchResult) -> str:
    if not server.install_targets:
        raise McpInstallError("This MCP server has no install targets.")
    remote = next(
        (target for target in server.install_targets if target.transport == "streamable_http"),
        None,
    )
    return (remote or server.install_targets[0]).id


def _validate_required_inputs(server: McpSearchResult, target_id: str, values: dict[str, Any]) -> None:
    target = _target_by_id(server, target_id)
    missing = [
        input_item.key
        for input_item in target.required_inputs
        if input_item.required and not str(values.get(input_item.key) or "").strip()
    ]
    if missing:
        raise McpInstallError("Missing required MCP input(s): " + ", ".join(sorted(missing)))


def _server_install_name(registry_name: str, server_name: str) -> str:
    if server_name.strip():
        return normalize_server_name(server_name)
    tail = registry_name.rstrip("/").split("/")[-1] if registry_name else "mcp-server"
    return normalize_server_name(tail)


class McpMarketplaceService:
    def __init__(
        self,
        *,
        registry_client: McpRegistryClient | None = None,
        repository: McpInstallRepository | None = None,
    ) -> None:
        self.registry_client = registry_client or McpRegistryClient()
        self._repository = repository
        self._owns_repository = repository is None

    @property
    def repository(self) -> McpInstallRepository:
        if self._repository is None:
            self._repository = McpInstallRepository()
        return self._repository

    def close(self) -> None:
        if self._owns_repository and self._repository is not None:
            self._repository.close()
            self._repository = None

    async def search(self, query: str, *, cursor: str = "", limit: int = 20) -> dict[str, Any]:
        result = await self.registry_client.search(query, cursor=cursor, limit=limit)
        from agent.modules.mcp.registry_client import serialize_search_result

        return {
            "servers": [serialize_search_result(server) for server in result.servers],
            "next_cursor": result.next_cursor,
            "count": result.count,
        }

    async def get_server_version(self, server_name: str, version: str) -> dict[str, Any]:
        result = await self.registry_client.get_server_version(server_name, version or "latest")
        from agent.modules.mcp.registry_client import serialize_search_result

        return serialize_search_result(result)

    async def install(
        self,
        *,
        agent_name: str,
        registry_name: str,
        version: str,
        target_id: str = "",
        server_name: str = "",
        input_values: dict[str, Any] | None = None,
        auth_method: str = "secret",
    ) -> dict[str, Any]:
        if not agent_name.strip():
            raise McpInstallError("Agent name is required.")
        if not registry_name.strip():
            raise McpInstallError("Registry server name is required.")

        server = await self.registry_client.get_server_version(
            registry_name,
            version or "latest",
        )
        chosen_target_id = target_id or _default_target_id(server)
        target = _target_by_id(server, chosen_target_id)
        values = {str(k): v for k, v in (input_values or {}).items()}

        if auth_method == "oauth":
            raise McpInstallError(
                "OAuth authentication is not supported for this registry server."
            )

        _validate_required_inputs(server, chosen_target_id, values)
        result = self.repository.install_registry_server(
            agent_name=agent_name.strip(),
            server_name=_server_install_name(registry_name, server_name),
            registry_name=server.registry_name,
            registry_version=server.version,
            title=server.title,
            description=server.description,
            verified=server.verified,
            transport=target.transport,
            command=target.command,
            args=list(target.args),
            url=target.url,
            env_template=dict(target.env_template),
            headers_template=dict(target.headers_template),
            credential_payload=values,
            registry_metadata={
                "registry_name": server.registry_name,
                "version": server.version,
                "target_id": target.id,
                "target": asdict(target),
            },
        )
        return {
            "status": "installed",
            "install_id": result["install_id"],
            "credential_ref": result["credential_ref"],
            "server_name": result["server_name"],
        }


__all__ = ["McpInstallError", "McpMarketplaceService"]

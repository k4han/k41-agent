from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import httpx

from agent.modules.mcp.install_repository import normalize_server_name

OFFICIAL_MCP_REGISTRY_BASE_URL = "https://registry.modelcontextprotocol.io/v0.1"


@dataclass(frozen=True, slots=True)
class McpRegistryInput:
    key: str
    label: str
    description: str = ""
    required: bool = False
    secret: bool = False
    default: str = ""
    placeholder: str = ""
    source: str = "input"


@dataclass(frozen=True, slots=True)
class McpInstallTarget:
    id: str
    label: str
    transport: str
    registry_type: str = ""
    runtime_hint: str = ""
    command: str = ""
    args: tuple[str, ...] = ()
    url: str = ""
    env_template: dict[str, str] = field(default_factory=dict)
    headers_template: dict[str, str] = field(default_factory=dict)
    required_inputs: tuple[McpRegistryInput, ...] = ()


@dataclass(frozen=True, slots=True)
class McpSearchResult:
    registry_name: str
    title: str
    description: str
    version: str
    is_latest: bool
    verified: bool
    repository_url: str = ""
    website_url: str = ""
    install_targets: tuple[McpInstallTarget, ...] = ()
    required_inputs: tuple[McpRegistryInput, ...] = ()
    auth_summary: str = ""


@dataclass(frozen=True, slots=True)
class McpSearchResponse:
    servers: tuple[McpSearchResult, ...]
    next_cursor: str = ""
    count: int = 0


def _registry_meta(entry: dict[str, Any]) -> dict[str, Any]:
    meta = entry.get("_meta") if isinstance(entry.get("_meta"), dict) else {}
    official = meta.get("io.modelcontextprotocol.registry/official")
    return official if isinstance(official, dict) else {}


def _input_from_raw(key: str, raw: dict[str, Any], *, source: str) -> McpRegistryInput:
    label = key.replace("_", " ").replace("-", " ").strip().title() or key
    return McpRegistryInput(
        key=key,
        label=label,
        description=str(raw.get("description") or ""),
        required=bool(raw.get("isRequired", False)),
        secret=bool(raw.get("isSecret", False)),
        default=str(raw.get("default") or raw.get("value") or ""),
        placeholder=str(raw.get("placeholder") or ""),
        source=source,
    )


def _inputs_from_variables(
    variables: dict[str, Any] | None,
    *,
    source: str,
) -> list[McpRegistryInput]:
    if not isinstance(variables, dict):
        return []
    inputs: list[McpRegistryInput] = []
    for key, raw in variables.items():
        if isinstance(raw, dict):
            inputs.append(_input_from_raw(str(key), raw, source=source))
    return inputs


def _dedupe_inputs(inputs: list[McpRegistryInput]) -> tuple[McpRegistryInput, ...]:
    by_key: dict[str, McpRegistryInput] = {}
    for item in inputs:
        existing = by_key.get(item.key)
        if existing is None:
            by_key[item.key] = item
            continue
        by_key[item.key] = McpRegistryInput(
            key=item.key,
            label=existing.label or item.label,
            description=existing.description or item.description,
            required=existing.required or item.required,
            secret=existing.secret or item.secret,
            default=existing.default or item.default,
            placeholder=existing.placeholder or item.placeholder,
            source=existing.source,
        )
    return tuple(by_key[key] for key in sorted(by_key))


def _argument_tokens(raw: dict[str, Any]) -> tuple[str, ...]:
    arg_type = str(raw.get("type") or "").strip().lower()
    name = str(raw.get("name") or "").strip()
    value = raw.get("value")
    value_text = "" if value is None else str(value)
    value_format = str(raw.get("format") or "").strip().lower()
    if arg_type == "named" and name:
        if value_text.lower() == "false" and value_format == "boolean":
            return ()
        if value_text.lower() == "true" and value_format == "boolean":
            return (name,)
        if value_text:
            return (name, value_text)
        return (name,)
    if value_text:
        return (value_text,)
    return ()


def _runtime_command(package: dict[str, Any]) -> str:
    runtime_hint = str(package.get("runtimeHint") or "").strip()
    if runtime_hint:
        return runtime_hint
    registry_type = str(package.get("registryType") or "").strip().lower()
    if registry_type == "npm":
        return "npx"
    if registry_type == "pypi":
        return "uvx"
    if registry_type == "oci":
        return "docker"
    return registry_type or "mcp"


def _package_identifier_arg(package: dict[str, Any]) -> str:
    identifier = str(package.get("identifier") or "").strip()
    version = str(package.get("version") or "").strip()
    registry_type = str(package.get("registryType") or "").strip().lower()
    if not identifier or not version:
        return identifier
    if registry_type == "npm" and "@" not in identifier.lstrip("@"):
        return f"{identifier}@{version}"
    if registry_type == "pypi" and "==" not in identifier:
        return f"{identifier}=={version}"
    return identifier


def _package_target(index: int, package: dict[str, Any]) -> McpInstallTarget:
    registry_type = str(package.get("registryType") or "").strip()
    command = _runtime_command(package)
    runtime_args = package.get("runtimeArguments")
    package_args = package.get("packageArguments")
    args: list[str] = []
    inputs: list[McpRegistryInput] = []

    if isinstance(runtime_args, list) and runtime_args:
        for raw in runtime_args:
            if not isinstance(raw, dict):
                continue
            args.extend(_argument_tokens(raw))
            inputs.extend(_inputs_from_variables(raw.get("variables"), source="runtime"))
    else:
        if command == "npx":
            args.append("-y")
        identifier = _package_identifier_arg(package)
        if identifier:
            args.append(identifier)

    if isinstance(package_args, list):
        for raw in package_args:
            if not isinstance(raw, dict):
                continue
            args.extend(_argument_tokens(raw))
            inputs.extend(_inputs_from_variables(raw.get("variables"), source="package"))

    env_template: dict[str, str] = {}
    env_vars = package.get("environmentVariables")
    if isinstance(env_vars, list):
        for raw in env_vars:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name") or "").strip()
            if not name:
                continue
            value = str(raw.get("value") or f"{{{name}}}")
            env_template[name] = value
            inputs.append(_input_from_raw(name, raw, source="env"))
            inputs.extend(_inputs_from_variables(raw.get("variables"), source="env"))

    transport = package.get("transport") if isinstance(package.get("transport"), dict) else {}
    transport_type = str(transport.get("type") or "stdio").strip()
    return McpInstallTarget(
        id=f"package:{index}",
        label=f"{registry_type or 'package'} via {command}",
        transport=transport_type,
        registry_type=registry_type,
        runtime_hint=str(package.get("runtimeHint") or ""),
        command=command,
        args=tuple(args),
        env_template=env_template,
        required_inputs=_dedupe_inputs(inputs),
    )


def _remote_target(index: int, remote: dict[str, Any]) -> McpInstallTarget:
    inputs: list[McpRegistryInput] = []
    headers_template: dict[str, str] = {}
    headers = remote.get("headers")
    if isinstance(headers, list):
        for raw in headers:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name") or "").strip()
            if not name:
                continue
            headers_template[name] = str(raw.get("value") or f"{{{name}}}")
            inputs.append(_input_from_raw(name, raw, source="header"))
            inputs.extend(_inputs_from_variables(raw.get("variables"), source="header"))
    inputs.extend(_inputs_from_variables(remote.get("variables"), source="url"))
    transport_type = str(remote.get("type") or "streamable-http").strip()
    if transport_type == "streamable-http":
        transport_type = "streamable_http"
    return McpInstallTarget(
        id=f"remote:{index}",
        label=f"Remote {transport_type}",
        transport=transport_type,
        url=str(remote.get("url") or ""),
        headers_template=headers_template,
        required_inputs=_dedupe_inputs(inputs),
    )


def normalize_registry_entry(entry: dict[str, Any]) -> McpSearchResult:
    server = entry.get("server") if isinstance(entry.get("server"), dict) else entry
    server = server if isinstance(server, dict) else {}
    official = _registry_meta(entry)
    packages = server.get("packages") if isinstance(server.get("packages"), list) else []
    remotes = server.get("remotes") if isinstance(server.get("remotes"), list) else []
    targets: list[McpInstallTarget] = []
    for index, package in enumerate(packages):
        if isinstance(package, dict):
            targets.append(_package_target(index, package))
    for index, remote in enumerate(remotes):
        if isinstance(remote, dict):
            targets.append(_remote_target(index, remote))

    required_inputs = _dedupe_inputs(
        [input_item for target in targets for input_item in target.required_inputs]
    )
    auth_summary = "No credentials required"
    if required_inputs:
        secrets = [item.label for item in required_inputs if item.secret]
        required = [item.label for item in required_inputs if item.required]
        if secrets:
            auth_summary = "Requires secrets: " + ", ".join(secrets)
        elif required:
            auth_summary = "Requires inputs: " + ", ".join(required)
        else:
            auth_summary = "Optional configuration available"

    repository = server.get("repository") if isinstance(server.get("repository"), dict) else {}
    title = str(server.get("title") or "").strip()
    registry_name = str(server.get("name") or "").strip()
    return McpSearchResult(
        registry_name=registry_name,
        title=title or registry_name.split("/")[-1] or normalize_server_name(registry_name),
        description=str(server.get("description") or ""),
        version=str(server.get("version") or ""),
        is_latest=bool(official.get("isLatest", False)),
        verified=str(official.get("status") or "") == "active",
        repository_url=str(repository.get("url") or ""),
        website_url=str(server.get("websiteUrl") or ""),
        install_targets=tuple(targets),
        required_inputs=required_inputs,
        auth_summary=auth_summary,
    )


class McpRegistryClient:
    def __init__(self, base_url: str = OFFICIAL_MCP_REGISTRY_BASE_URL) -> None:
        self.base_url = base_url.rstrip("/")

    async def search(
        self,
        query: str,
        *,
        cursor: str = "",
        limit: int = 20,
    ) -> McpSearchResponse:
        params: dict[str, Any] = {"limit": max(1, min(int(limit), 50))}
        if query.strip():
            params["search"] = query.strip()
        if cursor.strip():
            params["cursor"] = cursor.strip()
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(f"{self.base_url}/servers", params=params)
            response.raise_for_status()
            payload = response.json()
        return self._normalize_list_response(payload)

    async def get_server_version(self, server_name: str, version: str = "latest") -> McpSearchResult:
        encoded_name = quote(server_name, safe="")
        encoded_version = quote(version or "latest", safe="")
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                f"{self.base_url}/servers/{encoded_name}/versions/{encoded_version}"
            )
            response.raise_for_status()
            payload = response.json()
        return normalize_registry_entry(payload)

    @staticmethod
    def _normalize_list_response(payload: dict[str, Any]) -> McpSearchResponse:
        raw_servers = payload.get("servers") if isinstance(payload, dict) else []
        entries = [
            normalize_registry_entry(entry)
            for entry in raw_servers or []
            if isinstance(entry, dict)
        ]
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        return McpSearchResponse(
            servers=tuple(entries),
            next_cursor=str(metadata.get("nextCursor") or ""),
            count=int(metadata.get("count") or len(entries)),
        )


def serialize_registry_input(input_item: McpRegistryInput) -> dict[str, Any]:
    return {
        "key": input_item.key,
        "label": input_item.label,
        "description": input_item.description,
        "required": input_item.required,
        "secret": input_item.secret,
        "default": input_item.default,
        "placeholder": input_item.placeholder,
        "source": input_item.source,
    }


def serialize_install_target(target: McpInstallTarget) -> dict[str, Any]:
    return {
        "id": target.id,
        "label": target.label,
        "transport": target.transport,
        "registry_type": target.registry_type,
        "runtime_hint": target.runtime_hint,
        "command": target.command,
        "args": list(target.args),
        "url": target.url,
        "env_template": target.env_template,
        "headers_template": target.headers_template,
        "required_inputs": [serialize_registry_input(item) for item in target.required_inputs],
    }


def serialize_search_result(result: McpSearchResult) -> dict[str, Any]:
    return {
        "registry_name": result.registry_name,
        "title": result.title,
        "description": result.description,
        "version": result.version,
        "is_latest": result.is_latest,
        "verified": result.verified,
        "repository_url": result.repository_url,
        "website_url": result.website_url,
        "install_targets": [serialize_install_target(target) for target in result.install_targets],
        "required_inputs": [serialize_registry_input(item) for item in result.required_inputs],
        "auth_summary": result.auth_summary,
    }


__all__ = [
    "McpInstallTarget",
    "McpRegistryClient",
    "McpRegistryInput",
    "McpSearchResponse",
    "McpSearchResult",
    "OFFICIAL_MCP_REGISTRY_BASE_URL",
    "normalize_registry_entry",
    "serialize_install_target",
    "serialize_registry_input",
    "serialize_search_result",
]

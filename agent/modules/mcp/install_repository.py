from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from agent.modules.mcp.db_models import AgentMCPInstall, MCPCredential, MCPServerInstall
from agent.modules.mcp.models import MCPServerConfig, MCPTransport
from agent.modules.mcp.repository import bump_db_revision
from agent.shared.infrastructure.config_file import DEFAULT_CONFIG_PATH
from agent.shared.infrastructure.db.engine import _normalize_url_to_sync, get_database_url

logger = logging.getLogger(__name__)

_SERVER_NAME_INVALID = re.compile(r"[^A-Za-z0-9_-]+")
_TEMPLATE_PATTERN = re.compile(r"\{([^{}]+)\}")


def _invalidate_config_cache() -> None:
    """Notify the config repository that the DB state has changed."""
    try:
        bump_db_revision()
    except Exception:  # pragma: no cover - defensive
        pass


def normalize_server_name(value: str) -> str:
    normalized = _SERVER_NAME_INVALID.sub("_", str(value or "").strip())
    normalized = normalized.strip("_")
    return normalized or "mcp_server"


def _default_key_path() -> Path:
    return DEFAULT_CONFIG_PATH.parent / "data" / "runtime_config.key"


class MissingFernetKeyError(RuntimeError):
    """Raised when the on-disk MCP credential key is missing or corrupt."""


def _load_or_create_fernet_key(path: Path) -> bytes:
    if path.exists():
        existing = path.read_bytes().strip()
        if not existing:
            raise MissingFernetKeyError(
                f"MCP credential key file at {path} is empty. "
                "Restore the original key to decrypt existing credentials, "
                "or delete the file to generate a new key (existing "
                "credentials will become unrecoverable)."
            )
        return existing
    key = Fernet.generate_key()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(key)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    logger.warning(
        "Generated new MCP credential key at %s. Back up this file together with the database; "
        "without it, previously encrypted credentials cannot be decrypted.",
        path,
    )
    return key


def _json_loads(value: str, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _substitute_template(value: str, variables: dict[str, Any]) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        raw = variables.get(key, "")
        return "" if raw is None else str(raw)

    return _TEMPLATE_PATTERN.sub(replace, value)


def _resolve_list_template(values: list[Any], variables: dict[str, Any]) -> list[str]:
    return [
        _substitute_template(str(item), variables)
        for item in values
        if str(item).strip()
    ]


def _resolve_map_template(values: dict[str, Any], variables: dict[str, Any]) -> dict[str, str]:
    resolved: dict[str, str] = {}
    for key, value in values.items():
        key_text = str(key).strip()
        if not key_text:
            continue
        resolved[key_text] = _substitute_template(str(value), variables)
    return resolved


@lru_cache(maxsize=4)
def _get_sync_engine(database_url: str) -> Engine:
    sync_url = _normalize_url_to_sync(database_url)
    return create_engine(sync_url, echo=False, future=True)


def _resolve_engine(database_url: str | None) -> tuple[Engine, bool]:
    """Return ``(engine, owns_engine)``.

    When ``database_url`` is provided we own a dedicated engine. Otherwise we
    share a cached engine built from the resolved global database URL.
    """
    if database_url:
        return _get_sync_engine(database_url), True
    return _get_sync_engine(get_database_url()), False


class McpInstallRepository:
    def __init__(self, database_url: str | None = None, *, key_path: Path | None = None) -> None:
        self._engine, self._owns_engine = _resolve_engine(database_url)
        self._session_maker = sessionmaker(self._engine, class_=Session, expire_on_commit=False)
        self._key_path = key_path or _default_key_path()
        self._fernet: Fernet | None = None

    def close(self) -> None:
        if self._owns_engine:
            self._engine.dispose()

    def list_server_configs(self) -> list[MCPServerConfig]:
        try:
            with self._session_maker() as session:
                installs = session.execute(select(MCPServerInstall)).scalars().all()
                return [self._row_to_config(session, row) for row in installs]
        except OperationalError:
            return []

    def get_server_config(self, server_name: str) -> MCPServerConfig | None:
        try:
            with self._session_maker() as session:
                row = self._get_server_by_name(session, server_name)
                if row is None:
                    return None
                return self._row_to_config(session, row)
        except OperationalError:
            return None

    def list_agent_server_names(self, agent_name: str) -> list[str]:
        try:
            with self._session_maker() as session:
                rows = session.execute(
                    select(MCPServerInstall.server_name)
                    .join(
                        AgentMCPInstall,
                        AgentMCPInstall.mcp_server_install_id == MCPServerInstall.id,
                    )
                    .where(
                        AgentMCPInstall.agent_name == agent_name,
                        AgentMCPInstall.enabled,
                        MCPServerInstall.enabled,
                    )
                ).scalars().all()
                return sorted(str(row) for row in rows)
        except OperationalError:
            return []

    def list_agent_installs(self, agent_name: str) -> list[dict[str, Any]]:
        try:
            with self._session_maker() as session:
                rows = session.execute(
                    select(AgentMCPInstall, MCPServerInstall)
                    .join(
                        MCPServerInstall,
                        MCPServerInstall.id == AgentMCPInstall.mcp_server_install_id,
                    )
                    .where(AgentMCPInstall.agent_name == agent_name)
                    .order_by(MCPServerInstall.server_name)
                ).all()
                return [
                    self._serialize_agent_install(agent_install, server_install)
                    for agent_install, server_install in rows
                ]
        except OperationalError:
            return []

    def list_all_installs(self) -> list[dict[str, Any]]:
        try:
            with self._session_maker() as session:
                rows = session.execute(
                    select(MCPServerInstall).order_by(MCPServerInstall.server_name)
                ).scalars().all()
                return [self._serialize_server_install(row) for row in rows]
        except OperationalError:
            return []

    def list_all_agent_installs(self) -> dict[str, list[dict[str, Any]]]:
        """Return ``{agent_name: [install, ...]}`` for every agent in a single query."""
        try:
            with self._session_maker() as session:
                rows = session.execute(
                    select(AgentMCPInstall, MCPServerInstall)
                    .join(
                        MCPServerInstall,
                        MCPServerInstall.id == AgentMCPInstall.mcp_server_install_id,
                    )
                    .order_by(AgentMCPInstall.agent_name, MCPServerInstall.server_name)
                ).all()
                grouped: dict[str, list[dict[str, Any]]] = {}
                for agent_install, server_install in rows:
                    grouped.setdefault(str(agent_install.agent_name), []).append(
                        self._serialize_agent_install(agent_install, server_install)
                    )
                return grouped
        except OperationalError:
            return {}

    def install_registry_server(
        self,
        *,
        agent_name: str,
        server_name: str,
        registry_name: str,
        registry_version: str,
        title: str,
        description: str,
        verified: bool,
        transport: str,
        command: str = "",
        args: list[str] | None = None,
        url: str = "",
        env_template: dict[str, str] | None = None,
        headers_template: dict[str, str] | None = None,
        credential_payload: dict[str, Any] | None = None,
        registry_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        credential_ref = ""
        with self._session_maker.begin() as session:
            existing_server = self._get_server_by_name(
                session, normalize_server_name(server_name)
            )
            previous_credential_ref = (
                str(existing_server.credential_ref or "") if existing_server else ""
            )
            if credential_payload:
                credential_ref = self._create_credential(
                    session,
                    kind="secret",
                    payload=credential_payload,
                )

            server = self._upsert_server_install(
                session,
                server_name=server_name,
                registry_name=registry_name,
                registry_version=registry_version,
                source_type="registry",
                title=title,
                description=description,
                verified=verified,
                transport=transport,
                command=command,
                args=args or [],
                url=url,
                env_template=env_template or {},
                headers_template=headers_template or {},
                credential_ref=credential_ref,
                registry_metadata=registry_metadata or {},
                enabled=True,
            )
            if (
                previous_credential_ref
                and previous_credential_ref != credential_ref
            ):
                self._delete_credential(session, previous_credential_ref)
            agent_install = self._upsert_agent_install(
                session,
                agent_name=agent_name,
                server_install_id=int(server.id),
                enabled=True,
            )
            session.flush()
            _invalidate_config_cache()
            return {
                "install_id": int(agent_install.id),
                "server_install_id": int(server.id),
                "server_name": server.server_name,
                "credential_ref": credential_ref,
            }

    def create_custom_server(
        self,
        *,
        server_name: str,
        config: MCPServerConfig,
        credential_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        credential_ref = ""
        with self._session_maker.begin() as session:
            if credential_payload:
                credential_ref = self._create_credential(
                    session,
                    kind="secret",
                    payload=credential_payload,
                )
            server = self._upsert_server_install(
                session,
                server_name=server_name,
                registry_name="",
                registry_version="",
                source_type="custom",
                title=server_name,
                description="",
                verified=False,
                transport=str(config.transport),
                command=config.command,
                args=list(config.args),
                url=config.url,
                env_template={
                    key: f"{{env.{key}}}"
                    for key in config.env
                },
                headers_template={
                    key: f"{{headers.{key}}}"
                    for key in config.headers
                },
                credential_ref=credential_ref,
                registry_metadata={},
                enabled=config.enabled,
            )
            session.flush()
            _invalidate_config_cache()
            return {"server_install_id": int(server.id), "server_name": server.server_name}

    def update_custom_server(
        self,
        *,
        server_name: str,
        config: MCPServerConfig,
        credential_payload: dict[str, Any] | None = None,
    ) -> bool:
        normalized = normalize_server_name(server_name)
        with self._session_maker.begin() as session:
            server = self._get_server_by_name(session, normalized)
            if server is None:
                return False
            previous_credential_ref = str(server.credential_ref or "")
            credential_ref = previous_credential_ref
            existing_payload = self._load_credential_payload(
                session, previous_credential_ref
            )
            merged_payload = self._merge_credential_payload(
                existing_payload,
                config,
                credential_payload,
            )
            if merged_payload is not None and (merged_payload or existing_payload):
                credential_ref = self._create_credential(
                    session,
                    kind="secret",
                    payload=merged_payload,
                )
            elif not merged_payload and existing_payload:
                # The form cleared all env/headers/credentials. Drop the
                # orphaned credential so secrets do not linger in the DB.
                credential_ref = ""
            server.transport = str(config.transport)
            server.command = config.command
            server.args_json = _json_dumps(list(config.args))
            server.url = config.url
            server.env_template_json = _json_dumps(
                {key: f"{{env.{key}}}" for key in config.env}
            )
            server.headers_template_json = _json_dumps(
                {key: f"{{headers.{key}}}" for key in config.headers}
            )
            server.credential_ref = credential_ref
            server.enabled = bool(config.enabled)
            if (
                previous_credential_ref
                and previous_credential_ref != credential_ref
            ):
                self._delete_credential(session, previous_credential_ref)
            _invalidate_config_cache()
            return True

    def delete_server(self, server_name: str) -> bool:
        normalized = normalize_server_name(server_name)
        with self._session_maker.begin() as session:
            server = self._get_server_by_name(session, normalized)
            if server is None:
                return False
            agent_rows = session.execute(
                select(AgentMCPInstall).where(
                    AgentMCPInstall.mcp_server_install_id == server.id
                )
            ).scalars().all()
            for row in agent_rows:
                session.delete(row)
            session.delete(server)
            _invalidate_config_cache()
            return True

    def toggle_server(self, server_name: str, enabled: bool) -> bool:
        normalized = normalize_server_name(server_name)
        with self._session_maker.begin() as session:
            server = self._get_server_by_name(session, normalized)
            if server is None:
                return False
            server.enabled = bool(enabled)
            _invalidate_config_cache()
            return True

    def toggle_agent_install(self, agent_name: str, install_id: int, enabled: bool) -> bool:
        with self._session_maker.begin() as session:
            row = session.get(AgentMCPInstall, install_id)
            if row is None or row.agent_name != agent_name:
                return False
            row.enabled = bool(enabled)
            _invalidate_config_cache()
            return True

    def bind_agent_server(
        self,
        *,
        agent_name: str,
        server_name: str,
        enabled: bool = True,
    ) -> dict[str, Any] | None:
        with self._session_maker.begin() as session:
            server = self._get_server_by_name(session, server_name)
            if server is None:
                return None
            agent_install = self._upsert_agent_install(
                session,
                agent_name=agent_name,
                server_install_id=int(server.id),
                enabled=enabled,
            )
            session.flush()
            _invalidate_config_cache()
            return self._serialize_agent_install(agent_install, server)

    def delete_agent_install(self, agent_name: str, install_id: int) -> bool:
        with self._session_maker.begin() as session:
            row = session.get(AgentMCPInstall, install_id)
            if row is None or row.agent_name != agent_name:
                return False
            session.delete(row)
            _invalidate_config_cache()
            return True

    def seed_server_install(
        self,
        *,
        server_name: str,
        config: MCPServerConfig,
        source_type: str = "legacy",
    ) -> int:
        credential_payload = {
            "env": dict(config.env),
            "headers": dict(config.headers),
        }
        with self._session_maker.begin() as session:
            existing = self._get_server_by_name(session, server_name)
            if existing is not None:
                return int(existing.id)
            credential_ref = ""
            if config.env or config.headers:
                credential_ref = self._create_credential(
                    session,
                    kind="secret",
                    payload=credential_payload,
                )
            server = self._upsert_server_install(
                session,
                server_name=server_name,
                registry_name="",
                registry_version="",
                source_type=source_type,
                title=server_name,
                description="",
                verified=False,
                transport=str(config.transport),
                command=config.command,
                args=list(config.args),
                url=config.url,
                env_template={key: f"{{env.{key}}}" for key in config.env},
                headers_template={key: f"{{headers.{key}}}" for key in config.headers},
                credential_ref=credential_ref,
                registry_metadata={},
                enabled=config.enabled,
            )
            session.flush()
            _invalidate_config_cache()
            return int(server.id)

    def seed_agent_install(self, *, agent_name: str, server_name: str) -> bool:
        with self._session_maker.begin() as session:
            server = self._get_server_by_name(session, server_name)
            if server is None:
                return False
            self._upsert_agent_install(
                session,
                agent_name=agent_name,
                server_install_id=int(server.id),
                enabled=True,
            )
            _invalidate_config_cache()
            return True

    def _row_to_config(self, session: Session, row: MCPServerInstall) -> MCPServerConfig:
        credential_payload = self._load_credential_payload(session, row.credential_ref)
        variables = self._flatten_credential_payload(credential_payload)
        args = _resolve_list_template(_json_loads(row.args_json, []), variables)
        env = _resolve_map_template(_json_loads(row.env_template_json, {}), variables)
        headers = _resolve_map_template(
            _json_loads(row.headers_template_json, {}),
            variables,
        )
        url = _substitute_template(row.url or "", variables)
        transport = MCPTransport.HTTP if str(row.transport) in {"http", "streamable_http"} else MCPTransport.STDIO
        return MCPServerConfig(
            name=row.server_name,
            transport=transport,
            command=row.command or "",
            args=tuple(args),
            env=env,
            url=url,
            headers=headers,
            enabled=bool(row.enabled),
        )

    def _create_credential(self, session: Session, *, kind: str, payload: dict[str, Any]) -> str:
        credential_ref = f"mcpcred_{uuid4().hex}"
        row = MCPCredential(
            credential_ref=credential_ref,
            kind=kind,
            payload_json=self._encrypt_json(payload),
        )
        session.add(row)
        return credential_ref

    def _delete_credential(self, session: Session, credential_ref: str) -> None:
        """Delete a credential row by ref, no-op if it does not exist."""
        if not credential_ref:
            return
        row = session.execute(
            select(MCPCredential).where(MCPCredential.credential_ref == credential_ref)
        ).scalar_one_or_none()
        if row is not None:
            session.delete(row)

    def _load_credential_payload(self, session: Session, credential_ref: str) -> dict[str, Any]:
        if not credential_ref:
            return {}
        row = session.execute(
            select(MCPCredential).where(MCPCredential.credential_ref == credential_ref)
        ).scalar_one_or_none()
        if row is None:
            return {}
        try:
            return self._decrypt_json(row.payload_json)
        except (InvalidToken, json.JSONDecodeError, UnicodeDecodeError):
            return {}

    @staticmethod
    def _flatten_credential_payload(payload: dict[str, Any]) -> dict[str, Any]:
        flat: dict[str, Any] = {}
        for key, value in payload.items():
            if isinstance(value, dict):
                for nested_key, nested_value in value.items():
                    flat[f"{key}.{nested_key}"] = nested_value
                continue
            flat[str(key)] = value
        return flat

    @staticmethod
    def _merge_credential_payload(
        existing: dict[str, Any],
        config: MCPServerConfig,
        incoming: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """Build the new credential payload for ``update_custom_server``.

        ``incoming`` is the raw payload from the request body. When the form
        submits a value, it overrides the existing credential. When the form
        submits an empty string for a key that already has a value, the
        existing value is preserved (lets the user save other fields without
        re-typing secrets).

        Returns ``None`` when the form explicitly removed every key (caller
        drops the credential reference).
        """
        if incoming is None:
            return dict(existing) if existing else None

        normalized_existing = McpInstallRepository._normalize_legacy_payload(existing)
        merged: dict[str, Any] = {}
        form_groups = {
            "env": dict(config.env),
            "headers": dict(config.headers),
        }
        for group_key, form_values in form_groups.items():
            group_existing = normalized_existing.get(group_key, {})
            next_values: dict[str, Any] = {}
            for key, value in form_values.items():
                key_text = str(key)
                if value == "" and key_text in group_existing:
                    next_values[key_text] = group_existing[key_text]
                else:
                    next_values[key_text] = value
            if next_values:
                merged[group_key] = next_values

        # Carry over any non-env/header groups the caller may have sent
        # (e.g. future secret kinds). Strip the "env"/"headers" entries to
        # avoid duplicates with the normalized versions above.
        for key, value in incoming.items():
            if key in {"env", "headers"}:
                continue
            if not isinstance(value, dict):
                merged[str(key)] = value
                continue
            existing_values = existing.get(key)
            existing_map = existing_values if isinstance(existing_values, dict) else existing
            next_values = {}
            for nested_key, nested_value in value.items():
                nested_key_text = str(nested_key)
                if nested_value == "" and nested_key_text in existing_map:
                    next_values[nested_key_text] = existing_map[nested_key_text]
                else:
                    next_values[nested_key_text] = nested_value
            if next_values:
                merged[str(key)] = next_values

        if not merged and not config.env and not config.headers:
            return None
        return merged

    @staticmethod
    def _normalize_legacy_payload(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """Map a credential payload to ``{"env": ..., "headers": ...}``.

        Legacy payloads (created before the dashboard migration) stored env
        and header values at the top level. Treat any such flat string keys
        as belonging to the ``env`` group so subsequent edits can preserve
        them when the form leaves the field blank.
        """
        result: dict[str, dict[str, Any]] = {}
        for key, value in payload.items():
            if key in {"env", "headers"} and isinstance(value, dict):
                result[key] = dict(value)
            elif isinstance(value, str):
                result.setdefault("env", {})[str(key)] = value
        return result

    def _encrypt_json(self, payload: dict[str, Any]) -> str:
        raw = _json_dumps(payload)
        return self._get_fernet().encrypt(raw.encode("utf-8")).decode("ascii")

    def _decrypt_json(self, payload_json: str) -> dict[str, Any]:
        raw = self._get_fernet().decrypt(payload_json.encode("ascii")).decode("utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}

    def _get_fernet(self) -> Fernet:
        if self._fernet is None:
            self._fernet = Fernet(_load_or_create_fernet_key(self._key_path))
        return self._fernet

    def _get_server_by_name(self, session: Session, server_name: str) -> MCPServerInstall | None:
        normalized = normalize_server_name(server_name)
        if not normalized:
            return None
        normalized_lower = normalized.lower()
        candidates = session.execute(
            select(MCPServerInstall).where(
                func.lower(MCPServerInstall.server_name) == normalized_lower
            )
        ).scalars().all()
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        target = normalized_lower.replace("-", "_")
        for row in candidates:
            if (
                str(row.server_name or "").lower().replace("-", "_") == target
            ):
                return row
        return None

    def _upsert_server_install(
        self,
        session: Session,
        *,
        server_name: str,
        registry_name: str,
        registry_version: str,
        source_type: str,
        title: str,
        description: str,
        verified: bool,
        transport: str,
        command: str,
        args: list[str],
        url: str,
        env_template: dict[str, str],
        headers_template: dict[str, str],
        credential_ref: str,
        registry_metadata: dict[str, Any],
        enabled: bool,
    ) -> MCPServerInstall:
        normalized_name = normalize_server_name(server_name)
        row = self._get_server_by_name(session, normalized_name)
        if row is None:
            row = MCPServerInstall(server_name=normalized_name)
            session.add(row)
        row.registry_name = registry_name
        row.registry_version = registry_version
        row.source_type = source_type
        row.title = title or normalized_name
        row.description = description or ""
        row.verified = bool(verified)
        row.transport = transport
        row.command = command
        row.args_json = _json_dumps(args)
        row.url = url
        row.env_template_json = _json_dumps(env_template)
        row.headers_template_json = _json_dumps(headers_template)
        row.credential_ref = credential_ref
        row.registry_metadata_json = _json_dumps(registry_metadata)
        row.enabled = bool(enabled)
        session.flush()
        return row

    def _upsert_agent_install(
        self,
        session: Session,
        *,
        agent_name: str,
        server_install_id: int,
        enabled: bool,
    ) -> AgentMCPInstall:
        row = session.execute(
            select(AgentMCPInstall).where(
                AgentMCPInstall.agent_name == agent_name,
                AgentMCPInstall.mcp_server_install_id == server_install_id,
            )
        ).scalar_one_or_none()
        if row is None:
            row = AgentMCPInstall(
                agent_name=agent_name,
                mcp_server_install_id=server_install_id,
            )
            session.add(row)
        row.enabled = bool(enabled)
        session.flush()
        return row

    @staticmethod
    def _serialize_server_install(row: MCPServerInstall) -> dict[str, Any]:
        return {
            "id": row.id,
            "server_name": row.server_name,
            "registry_name": row.registry_name,
            "registry_version": row.registry_version,
            "source_type": row.source_type,
            "title": row.title,
            "description": row.description,
            "verified": row.verified,
            "transport": row.transport,
            "enabled": row.enabled,
        }

    def _serialize_agent_install(
        self,
        agent_install: AgentMCPInstall,
        server_install: MCPServerInstall,
    ) -> dict[str, Any]:
        data = self._serialize_server_install(server_install)
        data.update(
            {
                "install_id": agent_install.id,
                "agent_name": agent_install.agent_name,
                "agent_enabled": agent_install.enabled,
            }
        )
        return data


__all__ = [
    "McpInstallRepository",
    "MissingFernetKeyError",
    "normalize_server_name",
]

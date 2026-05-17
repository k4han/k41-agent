from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from agent.modules.github.config import DEFAULT_MENTION_TRIGGERS, DEFAULT_TRIGGER_LABEL
from agent.modules.github.models import (
    GitHubInstallation,
    GitHubRepositoryBinding,
    GitHubWebhookDelivery,
)
from agent.shared.infrastructure.db.base import utcnow
from agent.shared.infrastructure.db.session import get_async_session

logger = logging.getLogger(__name__)


def _json_list(values: tuple[str, ...] | list[str]) -> str:
    return json.dumps([str(value).strip() for value in values if str(value).strip()])


def load_mention_triggers(raw: str) -> list[str]:
    try:
        parsed = json.loads(raw or "[]")
    except json.JSONDecodeError:
        parsed = []
    if not isinstance(parsed, list):
        parsed = []
    values = [str(value).strip() for value in parsed if str(value).strip()]
    return values or list(DEFAULT_MENTION_TRIGGERS)


def _serialize_binding(binding: GitHubRepositoryBinding) -> dict[str, Any]:
    return {
        "id": binding.id,
        "repository_id": binding.repository_id,
        "installation_id": binding.installation_id,
        "full_name": binding.full_name,
        "account_login": binding.account_login,
        "private": binding.private,
        "default_branch": binding.default_branch,
        "enabled": binding.enabled,
        "agent_name": binding.agent_name,
        "trigger_label": binding.trigger_label or DEFAULT_TRIGGER_LABEL,
        "mention_triggers": load_mention_triggers(binding.mention_triggers_json),
        "notify_platform": binding.notify_platform or "",
        "notify_external_id": binding.notify_external_id or "",
        "notify_channel_id": binding.notify_channel_id or "",
        "last_synced_at": binding.last_synced_at.isoformat() if binding.last_synced_at else None,
        "created_at": binding.created_at.isoformat() if binding.created_at else None,
        "updated_at": binding.updated_at.isoformat() if binding.updated_at else None,
    }


class GitHubRepositoryStore:
    async def list_bindings(self) -> list[dict[str, Any]]:
        session = await get_async_session()
        async with session:
            stmt = select(GitHubRepositoryBinding).order_by(GitHubRepositoryBinding.full_name)
            result = await session.execute(stmt)
            return [_serialize_binding(item) for item in result.scalars().all()]

    async def get_binding_by_repository_id(
        self,
        repository_id: int,
    ) -> GitHubRepositoryBinding | None:
        session = await get_async_session()
        async with session:
            stmt = select(GitHubRepositoryBinding).where(
                GitHubRepositoryBinding.repository_id == repository_id
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def update_binding(
        self,
        repository_id: int,
        *,
        enabled: bool,
        agent_name: str,
        trigger_label: str,
        mention_triggers: list[str],
        notify_platform: str = "",
        notify_external_id: str = "",
        notify_channel_id: str = "",
    ) -> dict[str, Any]:
        session = await get_async_session()
        async with session:
            stmt = select(GitHubRepositoryBinding).where(
                GitHubRepositoryBinding.repository_id == repository_id
            )
            result = await session.execute(stmt)
            binding = result.scalar_one_or_none()
            if binding is None:
                raise KeyError(f"GitHub repository '{repository_id}' is not synced.")

            binding.enabled = enabled
            binding.agent_name = agent_name.strip()
            binding.trigger_label = trigger_label.strip() or DEFAULT_TRIGGER_LABEL
            binding.mention_triggers_json = _json_list(mention_triggers or list(DEFAULT_MENTION_TRIGGERS))
            binding.notify_platform = notify_platform.strip()
            binding.notify_external_id = notify_external_id.strip()
            binding.notify_channel_id = notify_channel_id.strip()
            await session.commit()
            await session.refresh(binding)
            return _serialize_binding(binding)

    async def mark_delivery_seen(
        self,
        delivery_id: str,
        *,
        event: str,
        action: str,
        repository_full_name: str,
    ) -> bool:
        session = await get_async_session()
        async with session:
            delivery = GitHubWebhookDelivery(
                delivery_id=delivery_id,
                event=event,
                action=action,
                repository_full_name=repository_full_name,
            )
            session.add(delivery)
            try:
                await session.commit()
                return True
            except IntegrityError:
                await session.rollback()
                return False

    async def upsert_installation(self, installation: dict[str, Any]) -> None:
        session = await get_async_session()
        async with session:
            installation_id = int(installation.get("id") or installation.get("installation_id"))
            account = installation.get("account") or {}
            stmt = select(GitHubInstallation).where(
                GitHubInstallation.installation_id == installation_id
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                row = GitHubInstallation(installation_id=installation_id)
                session.add(row)

            row.account_login = str(account.get("login") or "")
            row.account_type = str(account.get("type") or "")
            row.repository_selection = str(installation.get("repository_selection") or "")
            await session.commit()

    async def upsert_repository(
        self,
        repository: dict[str, Any],
        *,
        installation_id: int,
        default_agent: str,
        default_trigger_label: str,
        default_mention_triggers: tuple[str, ...],
    ) -> None:
        session = await get_async_session()
        async with session:
            repository_id = int(repository["id"])
            owner = repository.get("owner") or {}
            stmt = select(GitHubRepositoryBinding).where(
                GitHubRepositoryBinding.repository_id == repository_id
            )
            result = await session.execute(stmt)
            binding = result.scalar_one_or_none()
            if binding is None:
                binding = GitHubRepositoryBinding(
                    repository_id=repository_id,
                    enabled=False,
                    agent_name=default_agent,
                    trigger_label=default_trigger_label,
                    mention_triggers_json=_json_list(default_mention_triggers),
                )
                session.add(binding)

            full_name = str(repository["full_name"])
            binding.installation_id = int(installation_id)
            binding.full_name = full_name
            binding.account_login = str(owner.get("login") or full_name.split("/", 1)[0])
            binding.private = bool(repository.get("private", False))
            binding.default_branch = str(repository.get("default_branch") or "main")
            binding.last_synced_at = utcnow()
            await session.commit()


_store: GitHubRepositoryStore | None = None


def get_github_repository_store() -> GitHubRepositoryStore:
    global _store
    if _store is None:
        _store = GitHubRepositoryStore()
    return _store


__all__ = [
    "GitHubRepositoryStore",
    "get_github_repository_store",
]

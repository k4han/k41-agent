"""Persistence for Instabox operations and private port tokens."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import delete, select

from agent.modules.instabox.models import InstaboxOperation, InstaboxPortToken
from agent.shared.infrastructure.db.base import utcnow
from agent.shared.infrastructure.secret_cipher import SecretCipher, default_key_path

ACTIVE_OPERATION_STATUSES = ("submitting", "queued", "running")


def _loads(value: str | None, default: Any) -> Any:
    try:
        return json.loads(value) if value else default
    except ValueError:
        return default


def operation_payload(row: InstaboxOperation) -> dict[str, Any]:
    return {
        "id": row.id,
        "idempotency_key": row.idempotency_key,
        "kind": row.kind,
        "operation_id": row.operation_id,
        "machine_id": row.machine_id,
        "target": _loads(row.target_json, {}),
        "status": row.status,
        "phase": row.phase,
        "result": _loads(row.result_json, None),
        "error": _loads(row.error_json, None),
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


class InstaboxRepository:
    def __init__(self, container: Any, *, cipher: SecretCipher | None = None) -> None:
        self.container = container
        self._cipher = cipher

    def session(self):
        return self.container._async_session_maker()

    @property
    def cipher(self) -> SecretCipher:
        if self._cipher is None:
            self._cipher = SecretCipher(default_key_path())
        return self._cipher

    # Operations -------------------------------------------------------- #

    async def create_operation(
        self,
        *,
        idempotency_key: str,
        kind: str,
        target: dict[str, Any],
        machine_id: str | None = None,
        created_by: str | None = None,
    ) -> dict[str, Any]:
        async with self.session() as session:
            row = InstaboxOperation(
                idempotency_key=idempotency_key,
                kind=kind,
                machine_id=machine_id,
                target_json=json.dumps(target),
                status="submitting",
                created_by=created_by,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return operation_payload(row)

    async def update_operation(self, idempotency_key: str, **fields: Any) -> dict[str, Any] | None:
        async with self.session() as session:
            row = await session.scalar(
                select(InstaboxOperation).where(InstaboxOperation.idempotency_key == idempotency_key)
            )
            if row is None:
                return None
            for key, value in fields.items():
                if key in {"result", "error", "target"}:
                    setattr(row, f"{key}_json", json.dumps(value))
                else:
                    setattr(row, key, value)
            row.updated_at = utcnow()
            await session.commit()
            await session.refresh(row)
            return operation_payload(row)

    async def get_operation(self, record_id: int) -> dict[str, Any] | None:
        async with self.session() as session:
            row = await session.get(InstaboxOperation, record_id)
            return operation_payload(row) if row else None

    async def get_operation_by_key(self, idempotency_key: str) -> dict[str, Any] | None:
        async with self.session() as session:
            row = await session.scalar(
                select(InstaboxOperation).where(InstaboxOperation.idempotency_key == idempotency_key)
            )
            return operation_payload(row) if row else None

    async def list_operations(
        self, *, machine_id: str | None = None, active_only: bool = False, limit: int = 50
    ) -> list[dict[str, Any]]:
        query = select(InstaboxOperation).order_by(InstaboxOperation.id.desc()).limit(max(1, min(500, limit)))
        if machine_id:
            query = query.where(InstaboxOperation.machine_id == machine_id)
        if active_only:
            query = query.where(InstaboxOperation.status.in_(ACTIVE_OPERATION_STATUSES))
        async with self.session() as session:
            rows = (await session.execute(query)).scalars().all()
            return [operation_payload(row) for row in rows]

    # Port tokens ------------------------------------------------------- #

    async def save_port_token(self, machine_id: str, port: int, token: str, url: str) -> None:
        encrypted = self.cipher.encrypt(token)
        async with self.session() as session:
            row = await session.scalar(
                select(InstaboxPortToken).where(
                    InstaboxPortToken.machine_id == machine_id, InstaboxPortToken.port == int(port)
                )
            )
            if row is None:
                row = InstaboxPortToken(machine_id=machine_id, port=int(port), token_encrypted=encrypted, url=url)
                session.add(row)
            else:
                row.token_encrypted = encrypted
                row.url = url
                row.updated_at = utcnow()
            await session.commit()

    async def port_token(self, machine_id: str, port: int) -> tuple[str, str] | None:
        async with self.session() as session:
            row = await session.scalar(
                select(InstaboxPortToken).where(
                    InstaboxPortToken.machine_id == machine_id, InstaboxPortToken.port == int(port)
                )
            )
            if row is None:
                return None
            try:
                return self.cipher.decrypt(row.token_encrypted), row.url
            except Exception:
                return None

    async def delete_port_token(self, machine_id: str, port: int | None = None) -> None:
        query = delete(InstaboxPortToken).where(InstaboxPortToken.machine_id == machine_id)
        if port is not None:
            query = query.where(InstaboxPortToken.port == int(port))
        async with self.session() as session:
            await session.execute(query)
            await session.commit()


__all__ = ["ACTIVE_OPERATION_STATUSES", "InstaboxRepository", "operation_payload"]

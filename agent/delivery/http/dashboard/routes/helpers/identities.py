from __future__ import annotations

from datetime import datetime
from typing import Any

from agent.modules.users import get_pairing_service


def serialize_datetime(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def serialize_identity(identity: Any) -> dict[str, Any]:
    return {
        "id": getattr(identity, "id", None),
        "user_id": getattr(identity, "user_id", None),
        "platform": getattr(identity, "platform", ""),
        "external_id": getattr(identity, "external_id", ""),
        "created_at": serialize_datetime(getattr(identity, "created_at", None)),
        "updated_at": serialize_datetime(getattr(identity, "updated_at", None)),
    }


async def paired_identities() -> list[dict[str, Any]]:
    pairing_service = get_pairing_service()
    identities = await pairing_service.list_paired_identities()
    return [serialize_identity(identity) for identity in identities]

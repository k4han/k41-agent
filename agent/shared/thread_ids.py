from uuid import UUID, uuid5
from contextvars import ContextVar


THREAD_ID_NAMESPACE = UUID("cfd33f24-6b6f-5dc0-a61d-88a088c6347a")
thread_id_aliases_var: ContextVar[dict[str, str] | None] = ContextVar("thread_id_aliases", default=None)


def canonical_thread_id(thread_id: str) -> str:
    """Encode a legacy root ID while retaining internal sub-agent routing."""
    root, separator, suffix = thread_id.partition(":sub:")
    return uuid5(THREAD_ID_NAMESPACE, root).hex + separator + suffix


def _thread_aliases(*, reverse: bool = False) -> dict[str, str]:
    aliases = thread_id_aliases_var.get()
    if aliases is not None:
        return {new: old for old, new in aliases.items()} if reverse else aliases

    try:
        from agent.bootstrap.container import require_active_container

        container = require_active_container()
        return container._conversation_thread_storage_ids if reverse else container._conversation_thread_aliases
    except (ImportError, RuntimeError, AttributeError):
        return {}


def resolve_thread_id(thread_id: str) -> str:
    """Resolve persisted legacy links to their canonical conversation ID."""
    root, separator, suffix = str(thread_id).partition(":sub:")
    return _thread_aliases().get(root, root) + separator + suffix


def storage_thread_id(thread_id: str) -> str:
    """Keep migrated conversations attached to their existing physical files."""
    root, separator, suffix = str(thread_id).partition(":sub:")
    legacy_root = _thread_aliases(reverse=True).get(root, root)
    return legacy_root + separator + suffix


def thread_storage_aliases(thread_id: str) -> dict[str, str]:
    """Send only this conversation's storage identity to a sandbox worker."""
    root = resolve_thread_id(thread_id).split(":sub:", 1)[0]
    legacy = storage_thread_id(root)
    return {legacy: root} if legacy != root else {}


class SessionManager:
    """Build stable thread identifiers per platform, user, and channel."""

    @staticmethod
    def make_thread_id(
        platform: str,
        user_id: str,
        channel_id: str = "",
    ) -> str:
        parts = [str(getattr(platform, "value", platform)), str(user_id)]
        if channel_id:
            parts.append(str(channel_id))
        return canonical_thread_id("_".join(parts))

    @staticmethod
    def parse_thread_id(thread_id: str) -> tuple[str, str, str]:
        """Parse a thread ID into (platform, user_id, channel_id).

        Returns an empty string for channel_id when not present.
        Raises ValueError if the thread ID format is invalid.
        """
        parts = storage_thread_id(thread_id).split(":sub:", 1)[0].split("_", 2)
        if len(parts) < 2:
            raise ValueError(f"Invalid thread ID format: '{thread_id}'")
        platform = parts[0]
        user_id = parts[1]
        channel_id = parts[2] if len(parts) == 3 else ""
        return platform, user_id, channel_id

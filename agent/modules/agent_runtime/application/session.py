class SessionManager:
    """Build stable thread identifiers per platform, user, and channel."""

    @staticmethod
    def make_thread_id(
        platform: str,
        user_id: str,
        channel_id: str = "",
    ) -> str:
        parts = [platform, str(user_id)]
        if channel_id:
            parts.append(str(channel_id))
        return "_".join(parts)

    @staticmethod
    def parse_thread_id(thread_id: str) -> tuple[str, str, str]:
        """Parse a thread ID into (platform, user_id, channel_id).

        Returns an empty string for channel_id when not present.
        Raises ValueError if the thread ID format is invalid.
        """
        parts = thread_id.split("_", 2)
        if len(parts) < 2:
            raise ValueError(f"Invalid thread ID format: '{thread_id}'")
        platform = parts[0]
        user_id = parts[1]
        channel_id = parts[2] if len(parts) == 3 else ""
        return platform, user_id, channel_id

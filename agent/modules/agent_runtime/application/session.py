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

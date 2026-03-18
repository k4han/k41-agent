# agent/core/session.py


class SessionManager:
    """
    Tạo thread_id nhất quán theo platform + user + channel.
    Đảm bảo:
      - Cùng user, cùng platform → cùng lịch sử chat
      - Khác platform → lịch sử độc lập
      - Khác channel (Discord/Telegram) → lịch sử độc lập
    """

    @staticmethod
    def make_thread_id(
        platform:   str,
        user_id:    str,
        channel_id: str = "",
    ) -> str:
        """
        Ví dụ:
          telegram_123456_789chat
          discord_987654_channel42
          api_user@email.com_
        """
        parts = [platform, str(user_id)]
        if channel_id:
            parts.append(str(channel_id))
        return "_".join(parts)

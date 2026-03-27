# agent/adapters/base.py

from abc import ABC, abstractmethod
from agent.core.session import SessionManager


class BaseAdapter(ABC):
    """
    Interface chung cho tất cả platform adapters.
    Mỗi adapter nhận raw request từ platform, normalize về dict chuẩn,
    rồi gọi core/runner.
    """

    @abstractmethod
    async def handle(self, raw_request): ...

    def normalize(
        self,
        platform:     str,
        user_id:      str,
        user_input:   str,
        workflow:     str        = "chat_agent",
        service_type: str        = "default",
        working_dir:  str | None = None,
        max_context_tokens: int  = 50_000,
        channel_id:   str        = "",
    ) -> dict:
        """Chuẩn hóa về dict params cho run_agent / run_agent_full."""
        return {
            "workflow":     workflow,
            "user_input":   user_input,
            "service_type": service_type,
            "working_dir":  working_dir,
            "max_context_tokens": max_context_tokens,
            "thread_id":    SessionManager.make_thread_id(
                                platform, user_id, channel_id
                            ),
        }

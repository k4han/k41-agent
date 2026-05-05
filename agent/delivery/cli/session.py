"""CLI session state: tracks the active agent and conversation thread."""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field

from agent.modules.agent_runtime import SessionManager

CLI_PLATFORM = "cli"
CLI_USER_ID = "local"


def _new_channel_suffix() -> str:
    return secrets.token_hex(4)


@dataclass
class CLISession:
    """Holds runtime state for a CLI chat session."""

    agent_name: str = "default"
    channel_id: str = field(default_factory=_new_channel_suffix)

    @property
    def thread_id(self) -> str:
        return SessionManager.make_thread_id(
            CLI_PLATFORM,
            CLI_USER_ID,
            self.channel_id,
        )

    def reset_thread(self) -> str:
        """Generate a brand-new thread id and return it."""
        self.channel_id = _new_channel_suffix()
        return self.thread_id

    def use_thread(self, channel_id: str) -> str:
        """Switch to an explicit channel id (resume a previous thread)."""
        self.channel_id = channel_id
        return self.thread_id


__all__ = ["CLISession", "CLI_PLATFORM", "CLI_USER_ID"]

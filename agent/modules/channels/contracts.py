from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


ChannelTextMode = Literal["markdown", "html", "plain"]
ChannelRunner = Callable[[], Awaitable[None]]
ChannelReply = Callable[["OutboundMessage"], Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class ChannelSettingSection:
    id: str
    title: str
    subtitle: str = ""
    default_collapsed: bool = False


@dataclass(frozen=True, slots=True)
class ChannelSettingField:
    name: str
    label: str
    description: str = ""
    input_type: str = "text"
    required: bool = False
    secret: bool = False
    section: str = "general"
    default: Any = ""

    def config_key(self, channel_name: str) -> str:
        return f"channels.{channel_name}.{self.name}"


@dataclass(frozen=True, slots=True)
class InboundMessage:
    platform: str
    user_id: str
    channel_id: str
    text: str
    is_private: bool
    raw: Any = None
    reply: ChannelReply | None = None


@dataclass(frozen=True, slots=True)
class OutboundMessage:
    text: str
    mode: ChannelTextMode = "markdown"
    reply_to: Any = None
    update_target: Any = None


@dataclass(frozen=True, slots=True)
class ParsedCommand:
    name: str
    args: list[str] = field(default_factory=list)
    raw_args: str = ""


class ChatChannelAdapter(Protocol):
    name: str
    title: str
    summary: str
    tagline: str
    capabilities: frozenset[str]
    settings_schema: tuple[ChannelSettingField, ...]
    settings_sections: tuple[ChannelSettingSection, ...]

    def create_runner(self) -> ChannelRunner:
        ...

    async def send(self, destination: str, message: OutboundMessage) -> bool:
        ...

    async def test_connection(self) -> Any:
        ...

    async def sync_commands(self, commands: Sequence[Any]) -> None:
        ...


__all__ = [
    "ChannelReply",
    "ChannelRunner",
    "ChannelSettingField",
    "ChannelSettingSection",
    "ChannelTextMode",
    "ChatChannelAdapter",
    "InboundMessage",
    "OutboundMessage",
    "ParsedCommand",
]
